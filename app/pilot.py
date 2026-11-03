"""Routes for pilot shops: the owner's chat with Sabi, her customers' chat link, and the admin page.

    /api/chat ...        the owner app's Chat tab (her shop key, like the rest of /api)
    /c/<slug>            the page her customers chat on; /c/api/<slug>/... behind it
    /admin/              create shops, hand out keys, read the chats and feedback (ADMIN_TOKEN)

Phones poll for new messages: replies that arrive later (the 8 pm summary, a
customer the owner is handling, a follow-up) show up within a few seconds.
"""
import asyncio
import base64
import hmac
import logging
import re
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.shops import KINDS, OWNER, WEB, as_json

log = logging.getLogger("sabi.pilot")
CID = re.compile(r"^[A-Za-z0-9_-]{16,40}$")
CUSTOMER_DAILY = 60          # messages per customer per 24 hours
WEB_DIR = Path(__file__).resolve().parents[1] / "web"


class ChatIn(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    button_id: str | None = Field(default=None, max_length=300)
    button_title: str | None = Field(default=None, max_length=40)
    image: str | None = Field(default=None, max_length=8_000_000)    # data URL
    audio: str | None = Field(default=None, max_length=8_000_000)    # data URL from MediaRecorder


class CustomerIn(ChatIn):
    cid: str = Field(pattern=CID.pattern)
    name: str | None = Field(default=None, max_length=60)


def _shops(request: Request):
    shops = getattr(request.app.state, "shops", None)
    if shops is None:
        raise HTTPException(404, "pilot shops are not switched on (SHOPS_DB)")
    return shops


def _decode(data_url, kind):
    if not data_url.startswith(f"data:{kind}/") or "," not in data_url:
        raise HTTPException(422, f"{kind} must be a data:{kind}/... URL")
    head, b64 = data_url.split(",", 1)
    try:
        return base64.b64decode(b64), head[5:].split(";")[0]
    except ValueError:
        raise HTTPException(422, f"that {kind} couldn't be read")


async def _inbound(shops, shop, m: ChatIn, sender):
    """What to hand the agent, and how her own message shows in the chat."""
    kwargs, shown, image = {}, None, None
    if m.audio:
        from app.media.transcribe import shop_words, transcribe
        if not settings.can_transcribe():
            raise HTTPException(503, "Voice notes aren't switched on yet. Please type it.")
        audio, mime = _decode(m.audio, "audio")
        try:
            heard = await transcribe(audio, keywords=shop_words(shop.store))
        except Exception:
            log.exception("transcription failed")
            raise HTTPException(502, "Couldn't hear that voice note. Please try again or type it.")
        if not heard:
            raise HTTPException(422, "I couldn't hear that voice note. Please try again or type it.")
        kwargs.update(text=heard, transcript_of="voice_note")
        if sender == OWNER:   # her voice note is the receipt behind the entry
            from app.media.evidence_store import PrivateMedia
            kwargs["media_url"] = PrivateMedia(settings.private_media_dir).save(audio, mime)
        shown = f"🎙 “{heard}”"
    elif m.button_id:
        kwargs.update(button_id=m.button_id, button_title=m.button_title)
        shown = m.button_title or "(tapped a button)"
    elif m.image:
        data, mime = _decode(m.image, "image")
        from app.promo.publish import LocalMedia
        saved = LocalMedia(shops.media_root / shop.id, f"/shops-media/{shop.id}").save(data, "jpg" if "jpeg" in mime else "png")
        image = saved["url"]
        kwargs.update(text=m.text or "(photo, no caption)", image_data_url=m.image)
        shown = m.text or ""
    elif m.text and m.text.strip():
        kwargs.update(text=m.text.strip())
        shown = m.text.strip()
    else:
        raise HTTPException(422, "send text, a button, a photo or a voice note")
    return kwargs, shown, image


async def _run(shops, shop, chat, kwargs, shown, image, sender_name=None):
    if shops.used_today(shop.id) >= shops.daily_messages:
        raise HTTPException(429, "Sabi has reached today's message limit for this shop. It resets in 24 hours.")
    first = shops.log(shop.id, chat, "in", shown, image=image, sender_name=sender_name)
    started = time.monotonic()

    def handle():
        with shop.lock:   # one message at a time per shop: the books and approvals stay in order
            return shop.agent.handle(sender=chat, channel="app" if chat == OWNER else "web", **kwargs)

    ctx = await asyncio.to_thread(handle)
    latency = int((time.monotonic() - started) * 1000)
    shops.deliver(shop, ctx.outbox, tools=[c["tool"] for c in ctx.tool_log], latency_ms=latency, notify=chat != OWNER)
    return first


# --- the owner's chat ----------------------------------------------------------------

def owner_router(require_owner):
    router = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])

    def shop_of(request):
        shop = getattr(request.state, "shop", None)
        if shop is None:
            raise HTTPException(404, "chat is for pilot shops; the main shop talks to Sabi on WhatsApp")
        shop.base_url = settings.public_base_url or str(request.base_url).rstrip("/")
        return shop

    @router.post("/logout")
    def logout(request: Request):
        """Sign this device out. Her other devices, and her shop key, keep working."""
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        _shops(request).accounts.logout(token)
        return {"ok": True}

    @router.get("/chat")
    def chat_history(request: Request, after: int = 0):
        shop = shop_of(request)
        return {"messages": as_json(_shops(request).messages(shop.id, OWNER, after))}

    @router.post("/chat")
    async def chat_send(m: ChatIn, request: Request):
        shops, shop = _shops(request), shop_of(request)
        kwargs, shown, image = await _inbound(shops, shop, m, OWNER)
        first = await _run(shops, shop, OWNER, kwargs, shown, image)
        return {"messages": as_json(shops.messages(shop.id, OWNER, first - 1))}

    @router.get("/chat/link")
    def customer_link(request: Request):
        shop = shop_of(request)
        return {"url": f"{shop.base_url}/c/{shop.slug}", "slug": shop.slug}

    class Feedback(BaseModel):
        note: str = Field(default="", max_length=2000)
        rating: int | None = Field(default=None, ge=-1, le=1)
        message_id: int | None = None

    @router.post("/feedback")
    def feedback(f: Feedback, request: Request):
        shop = shop_of(request)
        _shops(request).feedback(shop.id, f.note, f.rating, f.message_id)
        return {"ok": True}

    class PushSub(BaseModel):
        endpoint: str = Field(max_length=1000)
        keys: dict
        expirationTime: float | None = None

    @router.get("/push/key")
    def push_key(request: Request):
        shop_of(request)
        return {"key": _shops(request).push_key()}

    @router.post("/push")
    def push_subscribe(sub: PushSub, request: Request):
        shop = shop_of(request)
        if not sub.endpoint.startswith("https://"):
            raise HTTPException(422, "not a push address")
        _shops(request).subscribe(shop.id, sub.model_dump())
        return {"ok": True}

    @router.delete("/push")
    def push_unsubscribe(sub: PushSub, request: Request):
        shop_of(request)
        _shops(request).unsubscribe(sub.endpoint)
        return {"ok": True}

    @router.post("/push/test")
    def push_test(request: Request):
        shop = shop_of(request)
        _shops(request).notify(shop.id, "Sabi", "Notifications are on. I'll tell you when a customer needs you.")
        return {"ok": True}

    @router.post("/consent")
    def consent(request: Request):
        _shops(request).consent(shop_of(request).id)
        return {"ok": True}

    @router.delete("/shop")
    def delete_shop(request: Request):
        _shops(request).delete(shop_of(request).id)
        return {"ok": True}

    return router


# --- her customers' chat link ----------------------------------------------------------

def join_router():
    """The public sign-up and sign-in pages' API. No code is needed to join; limits per address and per
    day keep it from being abused, and each shop has its own daily message cap."""
    from app.accounts import AuthError, check_secret, device_name
    from app.agent.tools import normalize_phone

    router = APIRouter(prefix="/join/api")
    attempts: dict[str, deque] = defaultdict(deque)

    def limit(request, what, most, per):
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
        q, t = attempts[f"{what}:{ip}"], time.time()
        while q and t - q[0] > per:
            q.popleft()
        if len(q) >= most:
            raise HTTPException(429, "Too many tries from here. Please wait a little and try again.")
        q.append(t)
        day = attempts[f"{what}:all"]
        while day and t - day[0] > 86400:
            day.popleft()
        if what == "register" and len(day) >= JOINS_PER_DAY:
            raise HTTPException(429, "Sabi has had a lot of new shops today. Please try again tomorrow.")
        day.append(t)

    def phone_of(raw):
        phone = normalize_phone(raw or "")
        if not phone:
            raise HTTPException(422, "That phone number doesn't look right. Use the one on your WhatsApp, e.g. 0803 123 4567.")
        return phone

    class Join(BaseModel):
        shop_name: str = Field(min_length=2, max_length=60)
        kind: str = Field(default="shop", pattern="^(shop|online|books)$")
        owner_name: str = Field(min_length=1, max_length=60)
        phone: str = Field(max_length=20)            # checked below, so she gets plain words
        secret: str = Field(max_length=72)
        remember: bool = True
        consent: bool
        address: str = Field(default="", max_length=200)
        hours: str = Field(default="", max_length=200)
        delivery: str = Field(default="", max_length=300)
        payment: str = Field(default="", max_length=300)
        returns: str = Field(default="", max_length=200)
        about: str = Field(default="", max_length=400)
        instagram: str = Field(default="", max_length=100)
        facebook: str = Field(default="", max_length=200)
        website: str = Field(default="", max_length=200)

    class Login(BaseModel):
        phone: str = Field(max_length=20)
        secret: str = Field(max_length=72)
        remember: bool = True

    @router.get("/phone")
    def phone_check(phone: str, request: Request):
        limit(request, "check", 30, 600)
        p = normalize_phone(phone or "")
        return {"valid": bool(p), "taken": bool(p) and _shops(request).accounts.phone_taken(p)}

    @router.post("/register")
    def register(j: Join, request: Request):
        if not j.consent:
            raise HTTPException(422, "Please tick that you agree to how Sabi keeps your shop's data.")
        problem = check_secret(j.secret)
        if problem:
            raise HTTPException(422, f"PIN or password: {problem}.")
        phone = phone_of(j.phone)
        shops = _shops(request)
        if shops.accounts.phone_taken(phone):
            raise HTTPException(409, "This number already has a Sabi shop. Sign in instead.")
        limit(request, "register", 5, 86400)
        row = shops.register(j.shop_name, j.kind, j.owner_name, phone, j.secret,
                             shop_info=shop_info_text(j), payment_details=j.payment.strip(),
                             links={"instagram": j.instagram.strip(), "facebook": j.facebook.strip(),
                                    "website": j.website.strip()})
        token, expires = shops.accounts.start_session(row["id"], j.remember, device_name(request.headers.get("user-agent")))
        base = settings.public_base_url or str(request.base_url).rstrip("/")
        return {"token": token, "expires_at": expires.isoformat(), "shop": row["name"],
                "chat_url": f"{base}/c/{row['slug']}"}

    @router.post("/login")
    def login(body: Login, request: Request):
        limit(request, "login", 20, 900)
        try:
            sid, token, expires = _shops(request).accounts.login(
                phone_of(body.phone), body.secret, body.remember, device_name(request.headers.get("user-agent")))
        except AuthError as e:
            raise HTTPException(401, str(e))
        return {"token": token, "expires_at": expires.isoformat(), "shop": _shops(request).row(sid)["name"]}

    return router


JOINS_PER_DAY = 200


def shop_info_text(j):
    """What customers are told, from her answers, in plain sentences she can edit later in Shop ⚙."""
    parts = []
    if j.about.strip():
        parts.append(j.about.strip().rstrip(".") + ".")
    if j.address.strip():
        parts.append(f"We are at {j.address.strip().rstrip('.')}.")
    if j.hours.strip():
        parts.append(f"Open {j.hours.strip().rstrip('.')}.")
    if j.delivery.strip():
        parts.append(f"Delivery: {j.delivery.strip().rstrip('.')}.")
    if j.payment.strip():
        parts.append(f"Payment: {j.payment.strip().rstrip('.')}.")
    if j.returns.strip():
        parts.append(f"Returns: {j.returns.strip().rstrip('.')}.")
    return " ".join(parts)


def customer_router():
    router = APIRouter()
    recent: dict[str, deque] = defaultdict(lambda: deque(maxlen=CUSTOMER_DAILY))

    def shop_by_slug(request, slug):
        shop = _shops(request).by_slug(slug)
        if shop is None:
            raise HTTPException(404, "no shop at this link")
        return shop

    @router.get("/c/{slug}")
    def page(slug: str):
        return FileResponse(WEB_DIR / "chat" / "index.html", headers={"Cache-Control": "no-cache"})

    @router.get("/c/api/{slug}")
    def shop_card(slug: str, request: Request):
        shop = shop_by_slug(request, slug)
        b = shop.store.business
        return {"name": b.get("name") or shop.name, "kind": KINDS.get(shop.kind, "Shop"),
                "voice": settings.can_transcribe(),
                "about": (b.get("shop_info") or "").split("\n")[0][:160]}

    @router.get("/c/api/{slug}/messages")
    def customer_messages(slug: str, cid: str, request: Request, after: int = 0):
        if not CID.match(cid):
            raise HTTPException(422, "bad chat id")
        shop = shop_by_slug(request, slug)
        return {"messages": as_json(_shops(request).messages(shop.id, WEB + cid, after))}

    @router.post("/c/api/{slug}/message")
    async def customer_message(slug: str, m: CustomerIn, request: Request):
        shops, shop = _shops(request), shop_by_slug(request, slug)
        q = recent[f"{shop.id}:{m.cid}"]
        if len(q) == q.maxlen and time.time() - q[0] < 86400:
            raise HTTPException(429, "You've sent a lot of messages today. Please call the shop instead.")
        q.append(time.time())
        name = (m.name or "").strip() or None
        kwargs, shown, image = await _inbound(shops, shop, m, WEB + m.cid)
        kwargs["profile_name"] = name
        first = await _run(shops, shop, WEB + m.cid, kwargs, shown, image, sender_name=name)
        return {"messages": as_json(shops.messages(shop.id, WEB + m.cid, first - 1))}

    @router.get("/shops-media/{sid}/media/{name}")
    def shop_media(sid: str, name: str, request: Request):
        """Flyers and photos in the chats. Names are random, so a link can't be guessed."""
        root = (_shops(request).media_root / sid).resolve()
        path = (root / name).resolve()
        if root not in path.parents or not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    return router


# --- admin -------------------------------------------------------------------------------

def require_admin(request: Request):
    from app.api import LOCAL_HOSTS
    token = getattr(request.app.state, "admin_token", "") or request.app.state.owner_token
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if token:
        if given and hmac.compare_digest(given.encode(), token.encode()):
            return
        raise HTTPException(401, "wrong or missing admin key")
    forwarded = any(h in request.headers for h in ("x-forwarded-for", "cf-connecting-ip", "forwarded"))
    if request.client and request.client.host in LOCAL_HOSTS and not forwarded:
        return
    raise HTTPException(401, "set ADMIN_TOKEN to use the admin page from another device")


def admin_router():
    router = APIRouter(prefix="/admin/api", dependencies=[Depends(require_admin)])

    class NewShop(BaseModel):
        name: str = Field(min_length=1, max_length=60)
        kind: str = Field(default="shop", pattern="^(shop|online|books)$")
        owner_name: str | None = Field(default=None, max_length=60)

    def links(request, row, token=None):
        base = settings.public_base_url or str(request.base_url).rstrip("/")
        out = {"chat_url": f"{base}/c/{row['slug']}"}
        if token:
            out["join_url"] = f"{base}/app/#key={token}"
        return out

    @router.get("/shops")
    def shops(request: Request):
        rows = _shops(request).stats()
        return as_json([{**r, **links(request, r)} for r in rows])

    @router.post("/shops")
    def create(s: NewShop, request: Request):
        try:
            row, token = _shops(request).create(s.name, s.kind, s.owner_name)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return as_json({"shop": row, **links(request, row, token)})

    @router.post("/shops/{sid}/key")
    def new_key(sid: str, request: Request):
        """A new app key (lost phone, or the link went to the wrong person). The old key stops working."""
        shops = _shops(request)
        row = shops.row(sid)
        if row is None:
            raise HTTPException(404, "no such shop")
        from app.shops import _hash
        token = secrets.token_urlsafe(24)
        shops.db.execute("update pilot.shops set token_hash = %s where id = %s", (_hash(token), sid))
        return links(request, row, token)

    @router.get("/shops/{sid}/transcript")
    def transcript(sid: str, request: Request):
        return as_json(_shops(request).transcript(sid))

    @router.get("/feedback")
    def feedback(request: Request):
        return as_json(_shops(request).all_feedback())

    class Public(BaseModel):
        public: bool

    @router.post("/feedback/{fid}/public")
    def feedback_public(fid: int, p: Public, request: Request):
        """Show (or hide) one tester's words on the public results page. Ask her first."""
        _shops(request).set_feedback_public(fid, p.public)
        return {"ok": True}

    @router.get("/results")
    def results(request: Request):
        return as_json(_shops(request).results())

    return router
