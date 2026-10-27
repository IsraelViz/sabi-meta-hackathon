"""WhatsApp Cloud API: parse webhooks, verify signatures, send replies, fetch media."""
import hashlib
import hmac
from dataclasses import dataclass

import httpx

from app.config import settings

GRAPH = "https://graph.facebook.com"


@dataclass
class Inbound:
    message_id: str
    sender: str
    kind: str                        # text | button | audio | image | other
    text: str | None = None
    button_id: str | None = None
    button_title: str | None = None
    media_id: str | None = None
    mime_type: str | None = None
    profile_name: str | None = None


def verify_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    if not app_secret:
        return True   # development without an app secret configured
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


def parse_webhook(payload: dict) -> list[Inbound]:
    out = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {c.get("wa_id"): c.get("profile", {}).get("name") for c in value.get("contacts", [])}
            for m in value.get("messages", []):
                sender, kind = m.get("from"), m.get("type")
                msg = Inbound(m.get("id"), sender, "other", profile_name=names.get(sender))
                if kind == "text":
                    msg.kind, msg.text = "text", m["text"].get("body")
                elif kind == "interactive":
                    reply = m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}
                    msg.kind, msg.button_id, msg.button_title = "button", reply.get("id"), reply.get("title")
                elif kind == "button":   # quick reply on a template message
                    msg.kind, msg.button_id, msg.button_title = "button", m["button"].get("payload"), m["button"].get("text")
                elif kind in ("audio", "voice"):
                    media = m.get(kind, {})
                    msg.kind, msg.media_id, msg.mime_type = "audio", media.get("id"), media.get("mime_type")
                elif kind in ("image", "document"):
                    media = m.get(kind, {})
                    msg.kind, msg.media_id, msg.mime_type = "image", media.get("id"), media.get("mime_type")
                    msg.text = media.get("caption")
                out.append(msg)
    return out


def _headers():
    return {"Authorization": f"Bearer {settings.wa_access_token}"}


async def download_media(media_id) -> tuple[bytes, str]:
    async with httpx.AsyncClient(timeout=60) as client:
        meta = (await client.get(f"{GRAPH}/{settings.graph_version}/{media_id}", headers=_headers())).raise_for_status().json()
        data = (await client.get(meta["url"], headers=_headers())).raise_for_status().content
    return data, meta.get("mime_type", "application/octet-stream")


def build_payload(to, body, buttons=None, image_url=None):
    if image_url and buttons:
        # Flyer preview with Post / Change / Skip under it, in one message
        return {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {
            "type": "button",
            "header": {"type": "image", "image": {"link": image_url}},
            "body": {"text": body[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"][:256], "title": b["title"][:20]}}
                                   for b in buttons[:3]]}}}
    if image_url:
        return {"messaging_product": "whatsapp", "to": to, "type": "image",
                "image": {"link": image_url, "caption": body[:1024]}}
    if buttons:
        return {"messaging_product": "whatsapp", "to": to, "type": "interactive", "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"][:256], "title": b["title"][:20]}}
                                   for b in buttons[:3]]}}}
    return {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": body[:4096]}}


async def send(to, body, buttons=None, image_url=None):
    payload = build_payload(to, body, buttons, image_url)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{GRAPH}/{settings.graph_version}/{settings.wa_phone_number_id}/messages",
                              headers=_headers(), json=payload)
    r.raise_for_status()
    return r.json()
