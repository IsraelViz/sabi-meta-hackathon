"""API for the owner app: read the books, and set up what Sabi tells customers.

The books are read-only here: every entry goes through the agent and its checks.
The only writes are the shop's profile (shop info, links, payment details), which
the owner edits herself.

Every route needs the owner's token (OWNER_APP_TOKEN) as a Bearer header.
With no token configured, only requests from this machine are allowed, for
local development. Media (voice notes, photos, flyer files) is served through
the same check: it is the owner's private evidence, never a public URL.
"""
import hmac
import mimetypes
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from app import money
from app.ledger import reports
from app.ledger.reports import headline_kobo

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
def require_owner(request: Request):
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    shops = getattr(request.app.state, "shops", None)
    if shops is not None and given:
        shop = shops.by_token(given)   # a pilot shop's own key: every route then reads her books
        if shop is not None:
            request.state.shop = shop
            return
    token = request.app.state.owner_token
    if not token:
        # A tunnel or proxy also connects from 127.0.0.1, but says who it is forwarding for.
        forwarded = any(h in request.headers for h in ("x-forwarded-for", "cf-connecting-ip", "forwarded"))
        if request.client and request.client.host in LOCAL_HOSTS and not forwarded:
            return
        raise HTTPException(401, "set OWNER_APP_TOKEN to use the app from another device")
    if not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "wrong or missing token")


router = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])


def _store(request: Request):
    shop = getattr(request.state, "shop", None)
    store = shop.store if shop else request.app.state.store
    money.use(store)
    return store


ITEM_ACCOUNT = {"sale": "sales", "purchase": "inventory", "stock_loss": "stock_loss", "opening": "inventory"}


def _shown(entry, store):
    """A reversal is shown with the amount and items of the entry it cancels."""
    if entry.kind == "reversal" and entry.reverses_id:
        return store.get_entry(entry.reverses_id) or entry
    return entry


def _names(store):
    products = {p.id: p.name for p in store.list_products()}
    contacts = {c.id: c.name for c in store.list_contacts()}
    accounts = {code: a["name"] for code, a in store.accounts.items()}
    return products, contacts, accounts


def _entry_summary(e, names, store, reversed_by=None):
    products, contacts, _ = names
    shown = _shown(e, store)
    item_account = ITEM_ACCOUNT.get(shown.kind)
    items = [f"{l.qty} × {products.get(l.product_id, '?')}" for l in shown.lines
             if l.account == item_account and l.qty and l.product_id]
    return {"id": e.id, "seq": e.seq, "date": e.entry_date, "kind": e.kind, "memo": e.memo, "status": e.status,
            "amount_kobo": headline_kobo(shown), "contact": contacts.get(e.contact_id), "contact_id": e.contact_id,
            "items": items, "due_date": e.due_date, "created_by": e.created_by, "reverses_id": e.reverses_id,
            "reversed_by": reversed_by, "has_evidence": bool(e.evidence_id)}


def _reversals(entries):
    return {e.reverses_id: e.id for e in entries if e.reverses_id}


@router.get("/summary")
def summary(request: Request):
    store = _store(request)
    debt = reports.debtors(store)
    entries = [e for e in store.list_entries() if e.kind != "opening"]
    names = _names(store)
    rev = _reversals(entries)
    return {
        "shop": getattr(store, "business", {}).get("name", "My shop"),
        "today": reports.profit_and_loss(store, "today"),
        "week": reports.profit_and_loss(store, "week"),
        "cash": reports.cash_position(store),
        "owed_total_kobo": sum(d["owes_kobo"] for d in debt),
        "debtors": debt[:5],
        "low_stock": reports.low_stock(store),
        "pending_questions": len(store.pending_approvals()),
        "sales": {k: v for k, v in reports.sales_pipeline(store).items() if k != "leads"},
        "recent": [_entry_summary(e, names, store, rev.get(e.id)) for e in reversed(entries[-6:])],
    }


@router.get("/report/{kind}")
def report(kind: str, request: Request, period: str = "today"):
    try:
        return reports.report(_store(request), kind, period)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/entries")
def entries(request: Request, since_seq: int = 0, before_seq: int | None = None, limit: int = 50,
            kind: str | None = None, contact_id: str | None = None):
    """Newest first. `since_seq` is the sync cursor; `before_seq` pages back through history."""
    store = _store(request)
    all_entries = [e for e in store.list_entries(since_seq=since_seq) if e.kind != "opening"]
    rev = _reversals(all_entries)
    rows = [e for e in all_entries
            if (before_seq is None or e.seq < before_seq) and (kind is None or e.kind == kind)
            and (contact_id is None or e.contact_id == contact_id
                 or any(l.contact_id == contact_id for l in e.lines))]
    names = _names(store)
    page = list(reversed(rows))[:max(1, min(limit, 200))]
    return {"entries": [_entry_summary(e, names, store, rev.get(e.id)) for e in page],
            "next_before_seq": page[-1].seq if len(rows) > len(page) else None,
            "latest_seq": max((e.seq for e in all_entries), default=since_seq)}


@router.get("/entries/{entry_id}")
def entry_detail(entry_id: str, request: Request):
    store = _store(request)
    e = store.get_entry(entry_id)
    if e is None:
        raise HTTPException(404, "no such entry")
    names = _names(store)
    products, contacts, accounts = names
    rev = _reversals(store.list_entries())
    ev = store.get_evidence(e.evidence_id) if e.evidence_id else None
    from app.ledger.explain import explain
    return {
        "why": explain(store, e),
        **_entry_summary(e, names, store, rev.get(e.id)),
        "lines": [{"account": l.account, "account_name": accounts.get(l.account, l.account),
                   "debit_kobo": l.debit_kobo, "credit_kobo": l.credit_kobo, "qty": l.qty,
                   "product": products.get(l.product_id), "contact": contacts.get(l.contact_id)} for l in e.lines],
        "evidence": _evidence(ev) if ev else None,
    }


def _evidence(ev):
    media = ev.get("media_url") or ""
    return {"id": ev["id"], "created_at": ev.get("created_at"), "channel": ev.get("channel"),
            "original_text": ev.get("original_text"), "normalized_text": ev.get("normalized_text"),
            "extracted_json": ev.get("extracted_json"), "model": ev.get("model"),
            "prompt_version": ev.get("prompt_version"),
            "media": {"kind": _media_kind(media), "url": f"/api/evidence/{ev['id']}/media"}
            if media.startswith("private:") else None}


def _media_kind(media_url):
    mime = mimetypes.guess_type(media_url.removeprefix("private:"))[0] or ""
    return "audio" if mime.startswith("audio") else "image" if mime.startswith("image") else "file"


@router.get("/evidence/{evidence_id}/media")
def evidence_media(evidence_id: str, request: Request):
    ev = _store(request).get_evidence(evidence_id)
    media = (ev or {}).get("media_url") or ""
    if not media.startswith("private:"):
        raise HTTPException(404, "no stored media for this evidence")
    return _file(request.app.state.private_media_dir, media.removeprefix("private:"))


def _file(directory, name):
    base = Path(directory).resolve()
    path = (base / name).resolve()
    if base not in path.parents or not path.is_file():   # no ../ tricks
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/debtors")
def debtors(request: Request):
    return reports.debtors(_store(request))


@router.get("/debtors/{contact_id}")
def debtor_statement(contact_id: str, request: Request):
    """Every receivable movement for one customer, oldest first, with a running balance."""
    store = _store(request)
    c = store.get_contact(contact_id)
    if c is None:
        raise HTTPException(404, "no such contact")
    rows, balance = [], 0
    for e in store.list_entries():
        moves = [l for l in e.lines if l.account == "ar" and l.contact_id == contact_id]
        if not moves:
            continue
        change = sum(l.debit_kobo - l.credit_kobo for l in moves)
        balance += change
        rows.append({"entry_id": e.id, "date": e.entry_date, "kind": e.kind, "memo": e.memo, "status": e.status,
                     "change_kobo": change, "balance_kobo": balance, "due_date": e.due_date})
    return {"contact": {"id": c.id, "name": c.name, "phone": c.phone}, "owes_kobo": balance, "rows": rows}


@router.get("/sales")
def sales_pipeline(request: Request, days: int = 1):
    """Customer chats by stage: asking, ordered, paid, lost; who is waiting for her; what they wanted that we lack."""
    return reports.sales_pipeline(_store(request), days=max(1, min(days, 31)))


@router.get("/products")
def products(request: Request):
    store = _store(request)
    return [{"id": p.id, "name": p.name, "unit": p.base_unit, "price_kobo": p.sell_price_kobo,
             "avg_cost_kobo": p.avg_cost_kobo, "stock": store.stock_on_hand(p.id), "reserved": store.reserved(p.id),
             "reorder_level": p.reorder_level, "photo": p.photo} for p in store.list_products()]


PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@router.put("/products/{product_id}/photo")
async def product_photo(product_id: str, request: Request):
    """Her own picture of a product: customers who send a photo of it, or ask to see it, get this one.
    The body is the image itself (JPEG, PNG or WebP)."""
    from app.promo.publish import LocalMedia

    store = _store(request)
    if store.get_product(product_id) is None:
        raise HTTPException(404, "no such product")
    ext = PHOTO_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip().lower())
    if ext is None:
        raise HTTPException(415, "send a JPEG, PNG or WebP image")
    data = await request.body()
    if not data or len(data) > 8_000_000:
        raise HTTPException(413, "the picture must be under 8 MB")
    shop = getattr(request.state, "shop", None)
    media = (LocalMedia(request.app.state.shops.media_root / shop.id, f"/shops-media/{shop.id}") if shop
             else LocalMedia())
    if not media.base:   # checked first, so a refused upload leaves no file behind
        raise HTTPException(409, "set PUBLIC_BASE_URL so customers can be sent the picture")
    saved = media.save(data, ext)
    store.set_product_photo(product_id, saved["url"])
    return {"product_id": product_id, "photo": saved["url"]}


@router.get("/posts")
def posts(request: Request):
    store = _store(request)
    names = _names(store)[0]
    return [{"id": p["id"], "created_at": p["created_at"], "product": names.get(p.get("product_id")),
             "reason": p.get("reason"), "caption": p.get("caption"), "caption_source": p.get("caption_source"),
             "status": p.get("status"), "platform_ids": p.get("platform_ids"), "error": p.get("error"),
             "tools": p.get("tools"), "image": f"/api/posts/{p['id']}/image" if p.get("asset_path") else None}
            for p in store.list_posts()]


@router.get("/posts/{post_id}/image")
def post_image(post_id: str, request: Request):
    p = _store(request).get_post(post_id)
    if not p or not p.get("asset_path"):
        raise HTTPException(404, "no image")
    path = Path(p["asset_path"])
    return _file(path.parent, path.name)


@router.get("/runs")
def runs(request: Request, limit: int = 20):
    return _store(request).recent_runs(limit)


# --- the shop's profile: what Sabi tells customers ------------------------------------

class Links(BaseModel):
    website: str | None = Field(default=None, max_length=300)
    instagram: str | None = Field(default=None, max_length=300)
    facebook: str | None = Field(default=None, max_length=300)
    whatsapp: str | None = Field(default=None, max_length=300)


class Profile(BaseModel):
    shop_info: str | None = Field(default=None, max_length=6000)
    payment_details: str | None = Field(default=None, max_length=500)
    links: Links | None = None
    docs: list[dict] | None = Field(default=None, max_length=12)   # pages from /learn, kept for shop_details


def _profile(store):
    b = getattr(store, "business", {})
    return {"name": b.get("name"), "shop_info": b.get("shop_info", ""), "payment_details": b.get("payment_details", ""),
            "links": b.get("links") or {}, "updated_at": b.get("profile_updated_at"),
            "sources": [{"source": d["source"], "url": d["url"], "title": d["title"]} for d in b.get("knowledge") or []]}


@router.get("/business")
def business(request: Request):
    return _profile(_store(request))


@router.put("/business")
def save_business(p: Profile, request: Request):
    from app.agent import knowledge
    store = _store(request)
    if p.payment_details is not None:
        store.business = {**store.business, "payment_details": p.payment_details.strip()}
    links = {k: v.strip() for k, v in p.links.model_dump().items() if v and v.strip()} if p.links else None
    docs = [knowledge.Doc(**{k: str(d.get(k, ""))[:knowledge.DOC_CHARS] for k in ("source", "url", "title", "text")}).__dict__
            for d in p.docs] if p.docs is not None else None
    knowledge.save(store, p.shop_info if p.shop_info is not None else store.business.get("shop_info", ""), links, docs)
    return _profile(store)


@router.post("/business/learn")
def learn_business(links: Links, request: Request):
    """Read the shop's pages and draft the shop info. Nothing is saved until she saves it."""
    from dataclasses import asdict

    from app.agent import knowledge
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(503, "the AI model isn't connected")
    store = _store(request)
    given = {k: v for k, v in links.model_dump().items() if v}
    all_links = knowledge._links(store, given)
    docs, status = knowledge.read_all(all_links)
    if not docs:
        return {"shop_info": None, "status": status, "docs": [], "links": all_links, "products_seen": [], "missing": []}
    draft = knowledge.summarise(llm, docs, store.business.get("shop_info", ""))
    return {**draft, "status": status, "links": all_links, "docs": [asdict(d) for d in docs]}


# --- business statement ---------------------------------------------------------------

@router.get("/statement", response_class=HTMLResponse)
def statement_page(request: Request, days: int = 90):
    from app.ledger.statement import page
    return HTMLResponse(page(_store(request), days=max(7, min(days, 366))),
                        headers={"Cache-Control": "private, no-store"})


@router.post("/statement/link")
def statement_link(request: Request):
    """A link she can send to a lender: signed, expires in 7 days."""
    from app.ledger.statement import LINK_DAYS, make_token
    from app.config import settings
    base = settings.public_base_url or str(request.base_url).rstrip("/")
    shop = getattr(request.state, "shop", None)
    return {"url": f"{base}/s/{make_token(shop=shop.id if shop else '')}", "days": LINK_DAYS}


@router.get("/me")
def me(request: Request):
    from app import prefs
    from app.agent import language
    shop = getattr(request.state, "shop", None)
    store = _store(request)
    return {"ok": True, "shop": getattr(store, "business", {}).get("name"), "today": date.today(),
            "chat": shop is not None, "kind": shop.kind if shop else None,
            "low_data": prefs.get(store)["low_data"], "push": shop is not None,
            "language": language.for_owner(store), "currency": _currency(store)}


def _currency(store):
    """How the app writes her money: ₦12,500 or 12 500 F CFA."""
    c = money.of(store)
    return {"code": c.code, "symbol": c.symbol, "after": c.after, "group": c.group, "cents": c.cents}


# --- her settings, her score, her coach ----------------------------------------------------

@router.get("/prefs")
def get_prefs(request: Request):
    from datetime import datetime

    from app import prefs
    from app.agent.language import CHOICES
    from app.ledger.reports import LAGOS
    p = prefs.get(_store(request))
    nxt = prefs.next_summary(p, datetime.now(LAGOS))
    return {**prefs.public(p), "summary_text": prefs.describe_summary(p), "next_summary": nxt,
            "weekdays": prefs.WEEKDAYS, "tones": prefs.TONES, "languages": CHOICES,
            "currencies": [{"code": c.code, "label": c.label} for c in money.CURRENCIES.values()]}


@router.put("/prefs")
def put_prefs(changes: dict, request: Request):
    from app import prefs
    allowed = {k: v for k, v in changes.items() if not str(k).startswith("_")}
    _, errors = prefs.save(_store(request), allowed)
    if errors:
        raise HTTPException(422, "; ".join(errors))
    return get_prefs(request)


@router.get("/score")
def score(request: Request):
    from app.ledger.score import sabi_score
    return sabi_score(_store(request))


@router.get("/coach")
def coach_tips(request: Request):
    from app.ledger import coach
    return {"tips": coach.tips(_store(request), limit=5)}
