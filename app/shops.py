"""Pilot shops: real traders trying Sabi, each with her own books.

Each shop's ledger lives in its own Postgres schema (shop_<id>), with the same
tables and triggers as the main shop, so nothing one tester records can reach
another. The pilot's own tables (the shops, every chat message, feedback) live
in the `pilot` schema.

Instead of WhatsApp, the owner talks to Sabi in the owner app's Chat tab, and
her customers use the shop's chat link (/c/<slug>). The same Agent, tools,
checks and approvals run behind both. Every message in and out is logged: it is
how the chats reach each phone, and how the pilot is reviewed afterwards.
"""
import hashlib
import json
import logging
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.agent.orchestrator import Agent
from app.market import market_view
from app.config import settings
from app.ledger.pg_store import PostgresStore
from app.ledger.seed import chart_of_accounts

log = logging.getLogger("sabi.shops")
OWNER = "owner"            # the owner's "phone" in every pilot shop
WEB = "web:"               # customers on the chat link are "web:<their browser's id>"
KINDS = {"shop": "Shop", "online": "Online seller", "books": "Bookseller"}

SCHEMA = """
create schema if not exists pilot;
create table if not exists pilot.shops (
  id          text primary key,
  slug        text unique not null,
  name        text not null,
  kind        text not null default 'shop',
  owner_name  text,
  token_hash  text unique not null,
  created_at  timestamptz not null default now(),
  consent_at  timestamptz,
  deleted_at  timestamptz
);
create table if not exists pilot.messages (
  id          bigserial primary key,
  shop_id     text not null,
  chat        text not null,                    -- 'owner', or 'web:<id>' for a customer
  role        text not null check (role in ('in', 'out', 'note')),
  body        text,
  buttons     jsonb,
  image       text,
  sender_name text,
  tools       jsonb,
  latency_ms  int,
  error       text,
  created_at  timestamptz not null default now()
);
create index if not exists messages_chat on pilot.messages (shop_id, chat, id);
create table if not exists pilot.feedback (
  id          bigserial primary key,
  shop_id     text not null,
  message_id  bigint,
  rating      int,
  note        text,
  created_at  timestamptz not null default now()
);
alter table pilot.feedback add column if not exists public boolean not null default false;  -- shown on /results
create table if not exists pilot.push (                     -- phones that asked for notifications
  endpoint    text primary key,
  shop_id     text not null,
  sub         jsonb not null,
  created_at  timestamptz not null default now()
);
create table if not exists pilot.settings (
  key         text primary key,
  value       text not null
);
"""


def connect_url(value):
    """A Postgres URL, or "pgserver:<folder>": an embedded Postgres whose data stays in that folder."""
    if not value.startswith("pgserver:"):
        return value
    import time

    import pgserver
    folder = Path(value.removeprefix("pgserver:")).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    for attempt in range(10):
        try:
            uri = _running(folder) or pgserver.get_server(folder, cleanup_mode=None).get_uri()
            psycopg.connect(uri).close()
            return uri
        except Exception:
            if attempt == 9:
                raise
            log.warning("embedded Postgres not ready yet; retrying")
            time.sleep(2)


def _running(folder):
    """The Postgres a previous run left going in this folder (it outlives the server, so data and
    restarts are fast), if it is up and answering. pgserver loses track of it once its parent exits."""
    try:
        lines = (folder / "postmaster.pid").read_text().splitlines()
        uri = f"postgresql://postgres:@{lines[5].strip() or '127.0.0.1'}:{int(lines[3])}/postgres"
        psycopg.connect(uri, connect_timeout=3).close()
        return uri
    except (OSError, IndexError, ValueError, psycopg.Error):
        return None


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "shop"


class SelfPublisher:
    """Pilot shops have no Instagram or Facebook connected: after Post, Sabi offers her Status and her customers."""
    platforms = []

    def publish(self, url, caption):
        return {}, {}


@dataclass
class Shop:
    id: str
    slug: str
    name: str
    kind: str
    store: object
    agent: Agent
    lock: threading.Lock = field(default_factory=threading.Lock)   # one message at a time per shop
    base_url: str = ""      # where her app was last opened, for links Sabi gives her


class Shops:
    def __init__(self, dsn, llm, *, media_root, daily_messages=400, promo_factory=None, counter=None):
        self.dsn = dsn
        self.llm = llm
        self.media_root = Path(media_root)
        self.daily_messages = daily_messages
        self.promo_factory = promo_factory    # tests: (media_dir, shop_id) -> PromoServices
        self.counter = counter
        self.db = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
        self.db.execute(SCHEMA)
        self._open: dict[str, Shop] = {}
        self._lock = threading.Lock()

    # --- shops -----------------------------------------------------------------
    def create(self, name, kind="shop", owner_name=None):
        """A new, empty shop. Returns (row, token): the token is her key to the app, shown once."""
        name = name.strip()[:60]
        if not name:
            raise ValueError("the shop needs a name")
        sid, token = secrets.token_hex(4), secrets.token_urlsafe(24)
        slug, n = slugify(name), 2
        while self.db.execute("select 1 from pilot.shops where slug = %s", (slug,)).fetchone():
            slug, n = f"{slugify(name)}-{n}", n + 1
        store = PostgresStore(self.dsn, schema=f"shop_{sid}")
        store.apply_schema()
        store.seed_accounts(chart_of_accounts())
        store.business = {"name": name, "shop_info": "", "payment_details": ""}
        store.conn.close()
        self.db.execute("insert into pilot.shops (id, slug, name, kind, owner_name, token_hash) "
                        "values (%s, %s, %s, %s, %s, %s)",
                        (sid, slug, name, kind if kind in KINDS else "shop", (owner_name or "").strip()[:60] or None,
                         _hash(token)))
        return self.row(sid), token

    def row(self, sid):
        return self.db.execute("select * from pilot.shops where id = %s and deleted_at is null", (sid,)).fetchone()

    def rows(self):
        return self.db.execute("select * from pilot.shops where deleted_at is null order by created_at").fetchall()

    def by_token(self, token):
        if not token:
            return None
        r = self.db.execute("select id from pilot.shops where token_hash = %s and deleted_at is null",
                            (_hash(token),)).fetchone()
        return self.get(r["id"]) if r else None

    def by_slug(self, slug):
        r = self.db.execute("select id from pilot.shops where slug = %s and deleted_at is null", (slug,)).fetchone()
        return self.get(r["id"]) if r else None

    def get(self, sid):
        with self._lock:
            if sid in self._open:
                return self._open[sid]
            r = self.row(sid)
            if r is None:
                return None
            self._open[sid] = shop = self._load(r)
            return shop

    def all(self):
        return [s for s in (self.get(r["id"]) for r in self.rows()) if s]

    def _load(self, r):
        from app.ledger.statement import make_token
        from app.promo.flow import PromoServices, default_services
        from app.promo.publish import LocalMedia

        store = PostgresStore(self.dsn, schema=f"shop_{r['id']}")
        store.apply_schema()                        # shops made before an upgrade get its new columns
        store.seed_accounts(chart_of_accounts())    # ...and its new accounts (savings)
        media = LocalMedia(self.media_root / r["id"], f"/shops-media/{r['id']}")
        base = self.promo_factory(media.dir, r["id"]) if self.promo_factory else None

        def promo():
            b = base or default_services()
            return PromoServices(b.cutter, b.backgrounds, media, SelfPublisher())

        shop = Shop(r["id"], r["slug"], r["name"], r["kind"], store, None)
        shop.agent = Agent(store, self.llm, OWNER, settings.auto_post_limit_kobo, counter=self.counter,
                           promo=_Lazy(promo), market=lambda pid: market_view(self, shop, pid),
                           statement_link=lambda: f"{shop.base_url or settings.public_base_url}/s/{make_token(shop=r['id'])}")
        return shop

    def consent(self, sid):
        self.db.execute("update pilot.shops set consent_at = coalesce(consent_at, now()) where id = %s", (sid,))

    def delete(self, sid):
        """Her books, chats and media are erased; only the fact that a shop existed is kept."""
        with self._lock:
            shop = self._open.pop(sid, None)
        if shop:
            shop.store.conn.close()
        self.db.execute(f"drop schema if exists shop_{sid} cascade")
        self.db.execute("delete from pilot.messages where shop_id = %s", (sid,))
        self.db.execute("delete from pilot.feedback where shop_id = %s", (sid,))
        self.db.execute("update pilot.shops set deleted_at = now(), token_hash = %s, name = 'deleted', owner_name = null "
                        "where id = %s", (_hash(secrets.token_hex(16)), sid))
        import shutil
        shutil.rmtree(self.media_root / sid, ignore_errors=True)

    # --- the chat log ----------------------------------------------------------
    def log(self, sid, chat, role, body, *, buttons=None, image=None, sender_name=None, tools=None,
            latency_ms=None, error=None):
        return self.db.execute(
            "insert into pilot.messages (shop_id, chat, role, body, buttons, image, sender_name, tools, latency_ms, error)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
            (sid, chat, role, body, Jsonb(buttons or []), image, sender_name,
             Jsonb(tools) if tools is not None else None, latency_ms, error)).fetchone()["id"]

    def messages(self, sid, chat, after=0, limit=200):
        rows = self.db.execute(
            "select id, role, body, buttons, image, created_at from pilot.messages"
            " where shop_id = %s and chat = %s and id > %s order by id desc limit %s",
            (sid, chat, after, limit)).fetchall()
        return [_public(r) for r in reversed(rows)]

    def used_today(self, sid):
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        return self.db.execute("select count(*) as n from pilot.messages where shop_id = %s and role = 'in'"
                               " and created_at > %s", (sid, since)).fetchone()["n"]

    def deliver(self, shop, outbox, tools=None, latency_ms=None, error=None, notify=False):
        """Put each reply in the chat it is for. Returns the new message ids by chat.
        `notify`: the owner didn't start this (a customer did), so her phone is told."""
        out = {}
        for o in outbox:
            if o.to == OWNER or o.to.startswith(WEB):
                mid = self.log(shop.id, o.to, "out", o.body, buttons=o.buttons, image=o.image_url,
                               tools=tools if o.to == OWNER else None, latency_ms=latency_ms, error=error)
                out.setdefault(o.to, []).append(mid)
                if notify and o.to == OWNER:
                    self.notify(shop.id, "Sabi needs you", o.body[:140])
                if o.reminder_id:
                    shop.store.update_reminder(o.reminder_id, status="sent")
                continue
            # A customer the owner told Sabi about, with a phone number but no chat link: she sends it herself.
            note = (f"📤 I can't message {o.to} directly yet. Send it yourself:\n\n{o.body}\n\n"
                    f"https://wa.me/{re.sub(r'[^0-9]', '', o.to)}?text={quote(o.body)}")
            out.setdefault(OWNER, []).append(self.log(shop.id, OWNER, "out", note))
            if o.reminder_id:
                shop.store.update_reminder(o.reminder_id, status="sent")
        return out

    def feedback(self, sid, note, rating=None, message_id=None):
        self.db.execute("insert into pilot.feedback (shop_id, message_id, rating, note) values (%s, %s, %s, %s)",
                        (sid, message_id, rating, (note or "")[:2000]))

    # --- phone notifications (Web Push) -----------------------------------------
    def _vapid(self):
        """The server's push key pair, made once and kept in the pilot database."""
        from py_vapid import Vapid
        row = self.db.execute("select value from pilot.settings where key = 'vapid_pem'").fetchone()
        if row:
            return Vapid.from_pem(row["value"].encode())
        v = Vapid()
        v.generate_keys()
        pem = v.private_pem().decode()
        self.db.execute("insert into pilot.settings (key, value) values ('vapid_pem', %s) on conflict do nothing", (pem,))
        return self._vapid()

    def push_key(self):
        """The public key a phone needs to subscribe (base64url, uncompressed point)."""
        import base64
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        raw = self._vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def subscribe(self, sid, sub):
        self.db.execute("insert into pilot.push (endpoint, shop_id, sub) values (%s, %s, %s)"
                        " on conflict (endpoint) do update set shop_id = excluded.shop_id, sub = excluded.sub",
                        (sub["endpoint"], sid, Jsonb(sub)))

    def unsubscribe(self, endpoint):
        self.db.execute("delete from pilot.push where endpoint = %s", (endpoint,))

    def notify(self, sid, title, body, url="/app/#/chat"):
        """Tell her phones, in the background. A phone that has gone away is forgotten."""
        subs = self.db.execute("select endpoint, sub from pilot.push where shop_id = %s", (sid,)).fetchall()
        if not subs:
            return
        payload = json.dumps({"title": title, "body": body, "url": url})

        def send():
            from pywebpush import WebPushException, webpush
            vapid = self._vapid()
            for s in subs:
                try:
                    webpush(s["sub"], payload, vapid_private_key=vapid,
                            vapid_claims={"sub": settings.vapid_subject}, ttl=12 * 3600)
                except WebPushException as e:
                    if e.response is not None and e.response.status_code in (404, 410):
                        self.unsubscribe(s["endpoint"])
                    else:
                        log.warning("push failed: %s", e)
                except Exception:
                    log.exception("push failed")

        threading.Thread(target=send, daemon=True).start()

    # --- for the admin page ------------------------------------------------------
    def stats(self):
        out = []
        for r in self.rows():
            shop = self.get(r["id"])
            m = self.db.execute(
                "select count(*) filter (where role = 'in' and chat = 'owner') as owner_in,"
                " count(*) filter (where role = 'in' and chat <> 'owner') as customer_in,"
                " count(distinct chat) filter (where chat <> 'owner') as customers,"
                " count(*) filter (where error is not null) as errors,"
                " max(created_at) filter (where role = 'in') as last_active,"
                " avg(latency_ms) filter (where latency_ms is not null) as avg_latency_ms"
                " from pilot.messages where shop_id = %s", (r["id"],)).fetchone()
            fb = self.db.execute("select count(*) as n from pilot.feedback where shop_id = %s", (r["id"],)).fetchone()
            entries = [e for e in shop.store.list_entries() if e.kind != "opening"]
            out.append({**{k: r[k] for k in ("id", "slug", "name", "kind", "owner_name", "created_at", "consent_at")},
                        **m, "avg_latency_ms": round(m["avg_latency_ms"] or 0),
                        "entries": len(entries), "products": len(shop.store.list_products()),
                        "feedback": fb["n"]})
        return out

    def transcript(self, sid, limit=500):
        return self.db.execute("select * from pilot.messages where shop_id = %s order by id desc limit %s",
                               (sid, limit)).fetchall()[::-1]

    def all_feedback(self):
        return self.db.execute("select f.*, s.name as shop, s.kind from pilot.feedback f join pilot.shops s on s.id = f.shop_id"
                               " order by f.id desc limit 200").fetchall()

    def set_feedback_public(self, fid, public):
        self.db.execute("update pilot.feedback set public = %s where id = %s", (public, fid))

    def results(self):
        """The pilot in numbers, for the public results page: totals only, no names, no chats.
        Feedback appears only when the admin chose to show it."""
        shops = self.stats()
        sid = [s["id"] for s in shops]
        m = self.db.execute(
            "select count(*) filter (where role = 'in') as messages,"
            " count(*) filter (where role = 'in' and chat <> 'owner') as customer_messages,"
            " count(distinct (shop_id, chat)) filter (where chat <> 'owner') as customers,"
            " count(*) filter (where role = 'in' and image is not null) as photos,"
            " count(*) filter (where role = 'in' and body like '🎙%%') as voice_notes,"
            " percentile_cont(0.5) within group (order by latency_ms) filter (where latency_ms is not null) as median_ms,"
            " count(*) filter (where error is not null) as errors,"
            " count(*) filter (where role = 'out') as replies,"
            " min(created_at) as first, max(created_at) as last"
            " from pilot.messages where shop_id = any(%s)", (sid,)).fetchone()
        ratings = self.db.execute("select count(*) filter (where rating < 0) as down from pilot.feedback"
                                  " where shop_id = any(%s)", (sid,)).fetchone()
        quotes = self.db.execute("select f.note, s.kind from pilot.feedback f join pilot.shops s on s.id = f.shop_id"
                                 " where f.public and f.note <> '' order by f.id desc limit 12").fetchall()
        orders = sales = 0
        for s in self.all():
            from app.ledger.reports import orders_taken
            took = orders_taken(s.store, days=365)
            orders += took["count"]
            sales += -s.store.account_balance("sales")
        return {"shops": len(shops), "by_kind": {KINDS[k]: sum(1 for s in shops if s["kind"] == k) for k in KINDS},
                "entries": sum(s["entries"] for s in shops), "products": sum(s["products"] for s in shops),
                "messages": m["messages"], "customer_messages": m["customer_messages"], "customers": m["customers"],
                "photos": m["photos"], "voice_notes": m["voice_notes"], "replies": m["replies"],
                "median_reply_ms": round(m["median_ms"] or 0), "errors": m["errors"],
                "error_rate_pct": round(100 * m["errors"] / m["replies"], 1) if m["replies"] else 0,
                "thumbs_down": ratings["down"], "orders": orders, "sales_kobo": sales,
                "from": m["first"], "to": m["last"],
                "quotes": [{"text": q["note"], "who": KINDS.get(q["kind"], "Trader")} for q in quotes]}


class _Lazy:
    """PromoServices built on first use (the cut-out model loads only if she makes a post)."""

    def __init__(self, make):
        self._make, self._value = make, None

    def __getattr__(self, name):
        if self._value is None:
            self._value = self._make()
        return getattr(self._value, name)


def _public(r):
    return {"id": r["id"], "role": r["role"], "body": r["body"], "buttons": r["buttons"] or [], "image": r["image"],
            "at": r["created_at"].isoformat()}


def as_json(rows):
    return json.loads(json.dumps(rows, default=str))
