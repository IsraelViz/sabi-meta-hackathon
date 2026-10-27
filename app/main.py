"""FastAPI service: WhatsApp webhook in, agent, replies out.

Run locally:  uvicorn app.main:app --reload --port 8000
"""
import asyncio
import base64
import logging
from collections import deque
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from app.agent.orchestrator import Agent
from app.channels import whatsapp
from app.config import settings
from app.jobs import daily_summary
from app.ledger import reports
from app.ledger.seed import demo_store
from app.media.transcribe import transcribe
from app.models.llm import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sabi")


def make_store():
    if settings.store == "memory":
        return demo_store()
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
            audio, _ = await whatsapp.download_media(msg.media_id)
            text = await transcribe(audio)
            if not text:
                await whatsapp.send(msg.sender, "I couldn't hear that voice note. Please send it again or type it.")
                return
            if msg.sender == settings.owner_phone:
                await whatsapp.send(msg.sender, f"🎙 I heard: “{text}”")
            kwargs.update(text=text, transcript_of="voice_note", media_url=f"wa-media:{msg.media_id}")
        elif msg.kind == "image":
            data, mime = await whatsapp.download_media(msg.media_id)
            kwargs.update(text=msg.text or "(photo, no caption)", media_url=f"wa-media:{msg.media_id}",
                          image_data_url=f"data:{mime};base64,{base64.b64encode(data).decode()}")
        else:
            await whatsapp.send(msg.sender, "I can read text, voice notes and photos for now.")
            return
        ctx = await asyncio.to_thread(lambda: agent.handle(**kwargs))
        for out in ctx.outbox:
            await whatsapp.send(out.to, out.body, out.buttons, out.image_url)
    except Exception:
        log.exception("failed to process message %s", msg.message_id)


# --- read-only API for the owner app ---------------------------------------

@app.get("/api/report/{kind}")
def api_report(kind: str, period: str = "today"):
    try:
        return reports.report(store, kind, period)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/entries")
def api_entries(since_seq: int = 0):
    out = []
    for e in store.list_entries(since_seq=since_seq):
        ev = store.get_evidence(e.evidence_id) if e.evidence_id else None
        out.append({"id": e.id, "seq": e.seq, "date": e.entry_date, "kind": e.kind, "memo": e.memo,
                    "status": e.status, "reverses_id": e.reverses_id, "created_by": e.created_by,
                    "due_date": e.due_date, "contact_id": e.contact_id,
                    "lines": [l.__dict__ for l in e.lines],
                    "evidence": ev and {k: ev.get(k) for k in ("channel", "original_text", "normalized_text",
                                                                "media_url", "model", "prompt_version", "created_at")}})
    return out


@app.get("/api/runs")
def api_runs(limit: int = 20):
    return store.recent_runs(limit)
