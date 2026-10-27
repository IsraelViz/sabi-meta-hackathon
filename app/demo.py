"""Public "Try Sabi" demo: an owner phone and a customer phone in the browser.

WhatsApp test numbers only reach a handful of verified phones, so judges
can't message the real number. This runs the same Agent, ledger and
approvals, but each visitor gets their own private copy of the demo shop:
nothing one visitor does is visible to another, and nothing reaches the real
shop, WhatsApp or Instagram.

The customer phone plays Mama Tunde, a regular who owes money, so orders,
payment claims and reminders all land on it.

Cost and abuse limits: sessions expire, there is a cap on live sessions, a
per-session message cap, and a daily cap across all visitors.
"""
import asyncio
import base64
import secrets
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.agent.orchestrator import Agent
from app.config import settings
from app.jobs.daily_summary import summary_message
from app.ledger import reports
from app.ledger.demo_week import add_history
from app.ledger.seed import demo_store
from app.promo.publish import LocalMedia

OWNER = "demo-owner"
CUSTOMER = "demo-customer"          # Mama Tunde's "phone" in the demo
CUSTOMER_CONTACT = "c-tunde"


class DemoPublisher:
    """The demo never posts to a real account: it records what would have been posted."""
    platforms = ["instagram (demo, not really posted)"]

    def publish(self, url, caption):
        return {"instagram": "demo-" + secrets.token_hex(4)}


@dataclass
class Session:
    id: str
    agent: Agent
    media_dir: Path
    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    messages: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DemoRegistry:
    def __init__(self, llm, *, max_sessions=200, ttl_seconds=2 * 3600, max_messages=40, daily_messages=2000,
                 promo_factory=None, counter=None):
        self.llm = llm
        self.max_sessions, self.ttl, self.max_messages, self.daily_cap = max_sessions, ttl_seconds, max_messages, daily_messages
        self.promo_factory = promo_factory
        self.counter = counter
        self.sessions: "OrderedDict[str, Session]" = OrderedDict()
        self.root = Path(tempfile.mkdtemp(prefix="sabi-demo-"))
        self._day, self._today_count = date.today(), 0
        self._lock = threading.Lock()

    def create(self):
        with self._lock:
            self._expire()
            while len(self.sessions) >= self.max_sessions:      # drop the least recently used
                self._drop(next(iter(self.sessions)))
            sid = secrets.token_urlsafe(12)
            media_dir = self.root / sid
            store = demo_store()
            add_history(store, media=LocalMedia(media_dir, f"/demo/api/{sid}"))
            store.set_contact_phone(CUSTOMER_CONTACT, CUSTOMER)
            from app.promo.flow import PromoServices, default_services
            base = self.promo_factory(media_dir, sid) if self.promo_factory else default_services()
            promo = PromoServices(base.cutter, base.backgrounds, LocalMedia(media_dir, f"/demo/api/{sid}"),
                                  DemoPublisher())
            agent = Agent(store, self.llm, OWNER, settings.auto_post_limit_kobo, counter=self.counter, promo=promo)
            self.sessions[sid] = Session(sid, agent, media_dir)
            return self.sessions[sid]

    def get(self, sid):
        with self._lock:
            self._expire()
            s = self.sessions.get(sid)
            if s is None:
                raise HTTPException(404, "demo session expired; start a new one")
            s.last_used = time.time()
            self.sessions.move_to_end(sid)
            return s

    def charge(self, s):
        """Count one message against the session and the daily budget, or refuse it."""
        with self._lock:
            if date.today() != self._day:
                self._day, self._today_count = date.today(), 0
            if s.messages >= self.max_messages:
                raise HTTPException(429, f"This demo session has used its {self.max_messages} messages. "
                                         f"Start a new one to keep going.")
            if self._today_count >= self.daily_cap:
                raise HTTPException(429, "The demo has reached today's limit. Please try again tomorrow.")
            s.messages += 1
            self._today_count += 1

    def _expire(self):
        cutoff = time.time() - self.ttl
        for sid in [sid for sid, s in self.sessions.items() if s.last_used < cutoff]:
            self._drop(sid)

    def _drop(self, sid):
        s = self.sessions.pop(sid, None)
        if s:
            shutil.rmtree(s.media_dir, ignore_errors=True)


class DemoMessage(BaseModel):
    who: str = Field(pattern="^(owner|customer)$")
    text: str | None = Field(default=None, max_length=1000)
    button_id: str | None = Field(default=None, max_length=300)
    button_title: str | None = Field(default=None, max_length=40)
    image: str | None = Field(default=None, max_length=8_000_000)    # data URL from the browser
    audio: str | None = Field(default=None, max_length=8_000_000)    # data URL from MediaRecorder


def _bubble(out, sid):
    return {"to": "owner" if out.to == OWNER else "customer", "body": out.body, "buttons": out.buttons,
            "image": out.image_url, "template": bool(out.template)}


def _books(store):
    pnl = reports.profit_and_loss(store, "today")
    return {
        "sales_today_kobo": pnl["sales_kobo"], "profit_today_kobo": pnl["net_profit_kobo"],
        "cash": reports.cash_position(store), "debtors": reports.debtors(store)[:4],
        "stock": [{"name": p.name, "qty": store.stock_on_hand(p.id), "unit": p.base_unit}
                  for p in store.list_products()],
        "recent": [{"kind": e.kind, "memo": e.memo, "status": e.status, "seq": e.seq,
                    "total_kobo": e.total_debits}
                   for e in reversed(store.list_entries()[-6:]) if e.kind != "opening"],
        "posts": [{"status": p["status"], "caption": p["caption"]} for p in store.list_posts()[:2]],
    }


def make_router(registry_ref):
    """`registry_ref()` returns the DemoRegistry (created lazily so tests can inject one)."""
    router = APIRouter(prefix="/demo/api")

    @router.post("/session")
    def new_session():
        s = registry_ref().create()
        shop = s.agent.store.business.get("name", "Demo shop")
        return {"id": s.id, "shop": shop, "model": getattr(s.agent.llm, "model", None),
                "max_messages": registry_ref().max_messages, "books": _books(s.agent.store),
                "intro": [
                    {"to": "owner", "body": f"Welcome to {shop}'s books 👋 You are the owner. Try a voice note "
                                            "or type: \"I sell 2 carton Indomie give Chioma, she go pay Friday\""},
                    {"to": "customer", "body": "You are Mama Tunde, a regular customer. Try: "
                                               "\"How much for Peak milk? I want 6 tins\""}]}

    @router.post("/{sid}/message")
    async def message(sid: str, m: DemoMessage):
        reg = registry_ref()
        s = reg.get(sid)
        reg.charge(s)
        sender = OWNER if m.who == "owner" else CUSTOMER
        kwargs = dict(sender=sender, channel="demo", profile_name="Mama Tunde" if m.who == "customer" else None)
        heard = None
        if m.audio:
            from app.media.transcribe import transcribe
            try:
                heard = await transcribe(base64.b64decode(m.audio.split(",", 1)[1]))
            except Exception as e:
                raise HTTPException(502, f"Couldn't transcribe the voice note: {e}")
            if not heard:
                raise HTTPException(422, "I couldn't hear that voice note. Try again or type it.")
            kwargs.update(text=heard, transcript_of="voice_note")
        elif m.button_id:
            kwargs.update(button_id=m.button_id, button_title=m.button_title)
        elif m.image:
            if not m.image.startswith("data:image/"):
                raise HTTPException(422, "image must be a data:image URL")
            kwargs.update(text=m.text or "(photo, no caption)", image_data_url=m.image)
        elif m.text:
            kwargs.update(text=m.text)
        else:
            raise HTTPException(422, "send text, a button, a photo or a voice note")
        async with s.lock:   # one message at a time per session
            ctx = await asyncio.to_thread(lambda: s.agent.handle(**kwargs))
        return {"heard": heard, "messages": [_bubble(o, sid) for o in ctx.outbox],
                "tools": [c["tool"] for c in ctx.tool_log], "books": _books(s.agent.store),
                "left": reg.max_messages - s.messages}

    @router.post("/{sid}/summary")
    def evening_summary(sid: str):
        """The 8 pm summary, on demand."""
        s = registry_ref().get(sid)
        msg = summary_message(s.agent.store)
        return {"messages": [{"to": "owner", "body": msg["body"], "buttons": msg["buttons"], "image": None}],
                "books": _books(s.agent.store)}

    @router.get("/{sid}/books")
    def books(sid: str):
        return _books(registry_ref().get(sid).agent.store)

    @router.get("/{sid}/media/{name}")
    def media(sid: str, name: str):
        s = registry_ref().get(sid)
        path = (s.media_dir / name).resolve()
        if s.media_dir.resolve() not in path.parents or not path.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(path)

    return router
