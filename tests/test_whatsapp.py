import hashlib
import hmac
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from app.channels.whatsapp import build_payload, parse_webhook, verify_signature
from app.jobs.daily_summary import seconds_until, summary_message
from app.ledger.seed import demo_store


def wrap(message, name="Nkechi"):
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"wa_id": message["from"], "profile": {"name": name}}],
        "messages": [message]}}]}]}


def test_parse_text_voice_image_and_button():
    text = parse_webhook(wrap({"from": "234801", "id": "m1", "type": "text", "text": {"body": "sold 2 rice"}}))[0]
    assert (text.kind, text.text, text.profile_name) == ("text", "sold 2 rice", "Nkechi")
    voice = parse_webhook(wrap({"from": "234801", "id": "m2", "type": "audio",
                                "audio": {"id": "media9", "mime_type": "audio/ogg; codecs=opus"}}))[0]
    assert (voice.kind, voice.media_id) == ("audio", "media9")
    image = parse_webhook(wrap({"from": "234801", "id": "m3", "type": "image",
                                "image": {"id": "media7", "mime_type": "image/jpeg", "caption": "receipt"}}))[0]
    assert (image.kind, image.text) == ("image", "receipt")
    tap = parse_webhook(wrap({"from": "234801", "id": "m4", "type": "interactive",
                              "interactive": {"type": "button_reply", "button_reply": {"id": "ap:abc:0", "title": "Confirm"}}}))[0]
    assert (tap.kind, tap.button_id) == ("button", "ap:abc:0")


def test_status_updates_are_ignored():
    assert parse_webhook({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) == []


def test_signature():
    body = json.dumps({"a": 1}).encode()
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, good, "secret")
    assert not verify_signature(body, "sha256=00", "secret")
    assert not verify_signature(body, None, "secret")


def test_buttons_payload_respects_whatsapp_limits():
    p = build_payload("234801", "Pick", [{"id": f"ap:x:{i}", "title": "A very long button title here"} for i in range(5)])
    buttons = p["interactive"]["action"]["buttons"]
    assert len(buttons) == 3 and all(len(b["reply"]["title"]) <= 20 for b in buttons)


def test_summary_schedule_and_text():
    lagos = ZoneInfo("Africa/Lagos")
    assert seconds_until(20, datetime(2026, 10, 27, 19, 0, tzinfo=lagos)) == 3600
    assert seconds_until(20, datetime(2026, 10, 27, 21, 0, tzinfo=lagos)) == 23 * 3600
    msg = summary_message(demo_store())
    assert "Today's books" in msg["body"] and len(msg["buttons"]) == 2
    assert all(len(b["title"]) <= 20 for b in msg["buttons"])
