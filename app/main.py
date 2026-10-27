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
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.agent.orchestrator import Agent
from app.api import router as api_router
from app.channels import whatsapp
from app.config import settings
from app.jobs import daily_summary
from app.ledger.seed import demo_store
from app.media.evidence_store import PrivateMedia
from app.media.transcribe import transcribe
from app.models.llm import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sabi")
WEB_DIR = Path(__file__).resolve().parents[1] / "web"
private_media = PrivateMedia(settings.private_media_dir)


def make_store():
    if settings.store == "memory":
        store = demo_store()
        if settings.demo_history:   # a week of trading so the owner app has something to show
            from app.ledger.demo_week import add_history
            from app.promo.publish import LocalMedia
            add_history(store, media=LocalMedia())
        return store
    if settings.store == "postgres":
        from app.ledger.pg_store import PostgresStore

        pg = PostgresStore(settings.database_url)
        pg.apply_schema()
        if pg.is_empty():   # first run: load the demo shop
            demo_store(store=pg)
        return pg
    raise RuntimeError(f"STORE must be 'memory' or 'postgres', not {settings.store!r}")


store = make_store()
agent = Agent(store, LLMClient() if settings.model_api_key or settings.llm_backend == "baseten" else None,
              settings.owner_phone, settings.auto_post_limit_kobo)
_seen_ids: deque = deque(maxlen=2000)   # Meta retries webhooks; process each message once


@asynccontextmanager
async def lifespan(_app):
    task = None
    if settings.owner_phone and settings.wa_access_token:
        task = asyncio.create_task(daily_summary.run_forever(store, whatsapp.send, settings.owner_phone))
    yield
    if task:
        task.cancel()


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
            text = await transcribe(audio)
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
app.state.owner_token = settings.owner_app_token
app.state.private_media_dir = settings.private_media_dir
app.include_router(api_router)
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


@app.get("/")
def root():
    return RedirectResponse("/app/")