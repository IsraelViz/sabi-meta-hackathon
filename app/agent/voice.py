"""Answers as voice notes, when someone asks: for this answer, or always until they say stop.

The owner's choice is one of her settings; a customer's is kept on their contact. The voice
itself is made on the way out (app/media/speech.py), so the agent only marks the reply.
"""
from app import prefs


def set_mode(ctx, mode):
    if mode not in ("this_once", "always", "off"):
        return {"error": "mode is this_once, always or off"}
    ctx.voice_reply = mode != "off"
    if mode != "this_once":
        on = mode == "always"
        if ctx.persona == "owner":
            prefs.save(ctx.store, {"voice_replies": on})
        elif ctx.contact_id:
            ctx.store.set_contact_voice(ctx.contact_id, on)
    return {"status": "saved", "mode": mode,
            "note": "Your answer goes out as a voice note; keep it short and easy to hear." if mode != "off"
                    else "Back to text. Say so in one line."}


def always(store, ctx):
    if ctx.persona == "owner":
        return bool(prefs.get(store).get("voice_replies"))
    contact = store.get_contact(ctx.contact_id) if ctx.contact_id else None
    return bool(contact and contact.voice_replies)
