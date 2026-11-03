"""FastAPI service: WhatsApp webhook in, agent, replies out.

Run locally:  uvicorn app.main:app --reload --port 8000
"""
import asyncio
import base64
import logging
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.agent.orchestrator import Agent
from app.api import router as api_router
from app.channels import whatsapp
from app.config import settings
from app.agent import sales
from app.jobs import daily_summary
from app.jobs import pilot as pilot_jobs
from app.ledger import reports
from app.ledger.seed import add_demo_history, demo_shop_path, demo_store
from app.media.evidence_store import PrivateMedia
from app.media.transcribe import shop_words, transcribe
from app.models.llm import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sabi")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
private_media = PrivateMedia(settings.private_media_dir)


def load_shop_once(path):
    """First run on Postgres: the shop and its history in one transaction, on its own connection.
    All or nothing, so a restart halfway (a far-away database makes this take minutes) leaves the
    database empty and the next start tries again. It runs in the background so the server answers
    meanwhile and the host doesn't restart it for being slow."""
    from app.ledger.pg_store import PostgresStore
    from app.promo.publish import LocalMedia

    seed = PostgresStore(settings.database_url)
    try:
        with seed.conn.transaction():
            if not seed.is_empty():   # another instance got there first
                return
            demo_store(store=seed, path=path)
            if settings.demo_history:
                add_demo_history(seed, path, media=LocalMedia())
        log.info("loaded %s into the database", Path(path).name)
    except Exception:
        log.exception("loading the shop failed; it will be tried again on the next start")
    finally:
        seed.conn.close()


def make_store():
    shop = {"path": settings.shop_file or demo_shop_path()}
    if settings.store == "memory":
        store = demo_store(**shop)
        if settings.demo_history:   # trading history so the owner app has something to show
            from app.promo.publish import LocalMedia
            add_demo_history(store, shop["path"], media=LocalMedia())
        return store
    if settings.store == "postgres":
        from app.ledger.pg_store import PostgresStore

        pg = PostgresStore(settings.database_url)
        pg.apply_schema()
        from app.ledger.seed import chart_of_accounts
        pg.seed_accounts(chart_of_accounts())    # adds accounts introduced since the shop was made
        from app.media import durable
        durable.start(settings.database_url)     # pictures and voice notes outlive a host that wipes its disk
        if pg.is_empty():   # first run: load the shop file, or the demo shop
            import threading
            threading.Thread(target=load_shop_once, args=(shop["path"],), daemon=True).start()
        return pg
    raise RuntimeError(f"STORE must be 'memory' or 'postgres', not {settings.store!r}")


store = make_store()
def _statement_link():
    from app.ledger.statement import make_token
    return f"{settings.public_base_url or 'http://127.0.0.1:8000'}/s/{make_token()}"


agent = Agent(store, LLMClient() if settings.has_llm() else None,
              settings.owner_phone, settings.auto_post_limit_kobo, statement_link=_statement_link)
shops = None
if settings.shops_db:   # pilot: real testers, each with her own books, chatting in the app
    from app.shops import Shops, connect_url
    shops = Shops(connect_url(settings.shops_db), agent.llm, media_root=Path(settings.media_dir) / "shops",
                  daily_messages=settings.shop_daily_messages)
_seen_ids: deque = deque(maxlen=2000)   # Meta retries webhooks; process each message once


@asynccontextmanager
async def lifespan(_app):
    task = None
    if settings.owner_phone and settings.wa_access_token:
        task = asyncio.create_task(daily_summary.run_forever(store, whatsapp.send, settings.owner_phone, agent.llm))
    followups = asyncio.create_task(sales.run_forever(store, whatsapp.send)) if settings.wa_access_token else None
    pilot = asyncio.create_task(pilot_jobs.run_forever(shops)) if shops else None
    yield
    for t in (task, followups, pilot):
        if t:
            t.cancel()


app = FastAPI(title="Sabi", lifespan=lifespan)
# Flyers must be reachable at a public URL for Instagram and the WhatsApp preview.
Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")


@app.get("/health")
def health():
    return {"ok": True, "model": agent.llm.model if agent.llm else None, "backend": settings.llm_backend,
            "entries": len(store.list_entries())}


@app.get("/webhook/whatsapp")
def verify(mode: str = Query(alias="hub.mode", default=""), token: str = Query(alias="hub.verify_token", default=""),
           challenge: str = Query(alias="hub.challenge", default="")):
    if mode == "subscribe" and token and token == settings.wa_verify_token:
        return PlainTextResponse(challenge)
    raise HTTPException(403, "verification failed")


@app.post("/webhook/whatsapp")
async def receive(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not whatsapp.verify_signature(body, request.headers.get("x-hub-signature-256"), settings.meta_app_secret):
        raise HTTPException(401, "bad signature")
    for msg in whatsapp.parse_webhook(await request.json()):
        if msg.message_id in _seen_ids:
            continue
        _seen_ids.append(msg.message_id)
        background.add_task(process, msg)
    return {"ok": True}   # answer fast; Meta retries slow webhooks


async def process(msg: whatsapp.Inbound):
    try:
        kwargs = dict(sender=msg.sender, channel="whatsapp", profile_name=msg.profile_name)
        if msg.kind == "text":
            kwargs["text"] = msg.text
        elif msg.kind == "button":
            kwargs.update(button_id=msg.button_id, button_title=msg.button_title)
        elif msg.kind == "audio":
            audio, mime = await whatsapp.download_media(msg.media_id)
            # Keep the voice note itself: it is the receipt behind every number it produces.
            media_url = private_media.save(audio, mime)
            if not settings.can_transcribe():
                await whatsapp.send(msg.sender, "I can't listen to voice notes yet. Please type it for me.")
                return
            text = await transcribe(audio, keywords=shop_words(store))
            if not text:
                await whatsapp.send(msg.sender, "I couldn't hear that voice note. Please send it again or type it.")
                return
            if msg.sender == settings.owner_phone:
                await whatsapp.send(msg.sender, f"🎙 I heard: “{text}”")
            kwargs.update(text=text, transcript_of="voice_note", media_url=media_url)
        elif msg.kind == "image":
            data, mime = await whatsapp.download_media(msg.media_id)
            kwargs.update(text=msg.text or "(photo, no caption)", media_url=private_media.save(data, mime),
                          image_data_url=f"data:{mime};base64,{base64.b64encode(data).decode()}")
        else:
            await whatsapp.send(msg.sender, "I can read text, voice notes and photos for now.")
            return
        ctx = await asyncio.to_thread(lambda: agent.handle(**kwargs))
        for out in ctx.outbox:
            try:
                await whatsapp.send(out.to, out.body, out.buttons, out.image_url, out.template)
            except Exception as e:
                log.exception("send to %s failed", out.to)
                reminder_id = out.reminder_id
                if reminder_id:   # a reminder didn't go out: record it and tell her
                    store.update_reminder(reminder_id, status="failed", error=str(e)[:500])
                    await whatsapp.send(settings.owner_phone, f"⚠ The payment reminder didn't send: {e}")
    except Exception:
        log.exception("failed to process message %s", msg.message_id)


# --- owner app ------------------------------------------------------------

app.state.store = store
app.state.llm = agent.llm   # the owner app's "learn from my pages" uses the same model
app.state.owner_token = settings.owner_app_token
app.state.private_media_dir = settings.private_media_dir
app.state.shops = shops
app.state.admin_token = settings.admin_token
app.include_router(api_router)
if shops:
    from app import pilot
    from app.api import require_owner
    app.include_router(pilot.owner_router(require_owner))
    app.include_router(pilot.customer_router())
    app.include_router(pilot.admin_router())
    app.mount("/chat", StaticFiles(directory=WEB_DIR / "chat"), name="customer-chat")
    app.mount("/admin", StaticFiles(directory=WEB_DIR / "admin", html=True), name="admin")
app.mount("/app", StaticFiles(directory=WEB_DIR, html=True), name="owner-app")

_demo_registry = None


def demo_registry():
    global _demo_registry
    if _demo_registry is None:
        from app.demo import DemoRegistry
        _demo_registry = DemoRegistry(agent.llm, max_sessions=settings.demo_max_sessions,
                                      max_messages=settings.demo_max_messages,
                                      daily_messages=settings.demo_daily_messages)
    return _demo_registry


if settings.demo_enabled:
    from app.demo import make_router as make_demo_router
    app.include_router(make_demo_router(demo_registry))
    app.mount("/demo", StaticFiles(directory=WEB_DIR / "demo", html=True), name="demo")


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page():
    """Meta needs a privacy policy and data deletion address before the app can go live."""
    return HTMLResponse((WEB_DIR / "privacy.html").read_text(encoding="utf-8"))


@app.get("/results", response_class=HTMLResponse)
def results_page():
    """The pilot in numbers, public: totals only. Quotes appear only if the admin chose to show them."""
    return HTMLResponse((WEB_DIR / "results.html").read_text(encoding="utf-8"))


@app.get("/results/api")
def results_api():
    from app.shops import as_json
    if shops is None:
        raise HTTPException(404, "no pilot is running")
    return as_json(shops.results())


@app.get("/s/{token}", response_class=HTMLResponse)
def shared_statement(token: str):
    """The business statement she shared with a lender. The signed link stops working after 7 days."""
    from app.ledger.statement import page, token_shop
    shop_id = token_shop(token)
    books = store if shop_id == "" else (shops.get(shop_id).store if shops and shop_id and shops.get(shop_id) else None)
    if books is None:
        return HTMLResponse("<p style='font-family:system-ui;padding:24px'>This statement link has expired. "
                            "Ask the shop owner for a new one.</p>", status_code=404)
    return HTMLResponse(page(books),
                        headers={"Cache-Control": "private, no-store", "X-Robots-Tag": "noindex"})


@app.get("/")
def root():
    return RedirectResponse("/app/")