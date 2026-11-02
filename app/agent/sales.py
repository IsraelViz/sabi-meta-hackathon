"""Sales: every customer chat is a lead, kept by code from what happens in it.

- Interest is noted when Sabi looks up a product for the customer; things the
  shop doesn't sell (or has run out of) are noted too, so the owner sees
  what she is missing.
- An order moves the lead to `ordered`; the owner confirming payment, `paid`.
- `hand_to_owner` passes a chat to the owner (discounts, bulk, complaints,
  anything Sabi doesn't know). Until she hands it back, Sabi stays quiet in
  that chat and relays each customer message to her. She can tap Reply and
  type her answer, and Sabi sends it from the shop's number.
- One follow-up per deal, only inside WhatsApp's 24-hour window: an unpaid
  order is reminded, a quiet lead is asked if they still want the item.
"""
from datetime import timedelta

from app.agent.approvals import buttons_for
from app.ledger.reports import lead_stage, with_owner
from app.ledger.store import now
from app.normalize.amounts import naira

FOLLOW_UP_AFTER = timedelta(hours=3)
FREE_TEXT_WINDOW = timedelta(hours=23)    # WhatsApp allows free text for 24 h after their last message
HANDOFF_FOR = timedelta(hours=12)
NEW_DEAL_AFTER = timedelta(days=1)        # a customer who paid or went quiet starts a new deal
KEEP = 10
SURE_MATCH = 0.8      # below this, "golden morn" finding "Golden Penny spaghetti" is not what they asked for


def lead_for(store, contact_id):
    lead = store.get_lead(contact_id)
    if lead is None or (lead_stage(store, lead) in ("paid", "lost") and now() - lead["updated_at"] > NEW_DEAL_AFTER):
        lead = store.save_lead(contact_id, stage="asking", interests=[], unmet=[], order_id=None,
                               followed_up_at=None, created_at=now(),
                               owner_said=(lead or {}).get("owner_said") or [])
    return lead


def _add(items, value):
    return ([v for v in items if v != value] + [value])[-KEEP:]


def note_lookup(ctx, query, matches):
    """After find_product for a customer: what they asked about, and what we couldn't offer."""
    lead = lead_for(ctx.store, ctx.contact_id)
    interests, unmet = lead["interests"], lead["unmet"]
    best = matches[0] if matches else None
    if best is None or ctx.match_scores.get(best["product_id"], 0) < SURE_MATCH:
        unmet = _add(unmet, query.strip().lower()[:40])
    else:
        interests = _add(interests, best["product_id"])
        if not best.get("in_stock"):
            unmet = _add(unmet, f"{best['name']} (out of stock)")
    ctx.store.save_lead(ctx.contact_id, interests=interests, unmet=unmet)


def note_photo(ctx, seen, match):
    """After a customer's photo: the product it showed, or what they wanted that we couldn't offer."""
    lead = lead_for(ctx.store, ctx.contact_id)
    interests, unmet = lead["interests"], lead["unmet"]
    if match is None:
        unmet = _add(unmet, f"{seen.strip().lower()[:40]} (photo)")
    else:
        interests = _add(interests, match["product_id"])
        if not match["in_stock"]:
            unmet = _add(unmet, f"{match['name']} (out of stock)")
    ctx.store.save_lead(ctx.contact_id, interests=interests, unmet=unmet)


def on_order(store, contact_id, order):
    lead = lead_for(store, contact_id)
    interests = lead["interests"]
    for item in order["items"]:
        interests = _add(interests, item["product_id"])
    store.save_lead(contact_id, stage="ordered", order_id=order["id"], interests=interests, followed_up_at=None)


def on_paid(store, contact_id, order_id):
    if store.get_lead(contact_id):
        store.save_lead(contact_id, stage="paid", order_id=order_id)


# --- handing a chat to the owner -------------------------------------------

def hand_to_owner(ctx, reason, summary):
    """Tool: the customer needs the owner. Sabi goes quiet in this chat until she hands it back."""
    lead_for(ctx.store, ctx.contact_id)
    ctx.store.save_lead(ctx.contact_id, handoff_reason=reason[:200], handoff_until=now() + HANDOFF_FOR)
    name = ctx.store.get_contact(ctx.contact_id).name
    _ask_owner(ctx, ctx.contact_id, f"🙋 {name} needs you: {reason}\n{summary}")
    return {"status": "handed_to_owner",
            "tell_customer": "Say you have passed this to the owner, who will reply here soon."}


def relay(ctx, text):
    """The owner is handling this chat: pass the customer's message on instead of answering it."""
    lead = ctx.store.get_lead(ctx.contact_id)
    ctx.store.save_lead(ctx.contact_id, handoff_until=max(lead["handoff_until"], now() + timedelta(hours=2)))
    _ask_owner(ctx, ctx.contact_id, f"💬 {ctx.store.get_contact(ctx.contact_id).name}: {text}")


def _ask_owner(ctx, contact_id, text, reply_label="Reply"):
    # Only the newest message about a customer keeps its buttons live.
    for a in ctx.store.pending_approvals():
        if a["kind"] == "handoff" and a["payload"]["contact_id"] == contact_id:
            ctx.store.answer_approval(a["id"], "rejected", "superseded")
    a = ctx.store.add_approval("handoff", {"contact_id": contact_id}, text,
                               [{"label": reply_label, "action": "reply"},
                                {"label": "Let Sabi handle it", "action": "resume"}])
    ctx.tell_owner(text, buttons_for(a))


def settle_handoff(a, option, ctx):
    contact = ctx.store.get_contact(a["payload"]["contact_id"])
    if option.get("action") == "reply":
        ctx.store.update_approval_payload(a["id"], {**a["payload"], "awaiting_text": True})
        ctx.say(f"Type your reply to {contact.name}. I'll send it exactly as you write it.")
        return
    ctx.store.save_lead(contact.id, handoff_until=None, handoff_reason=None)
    ctx.say(f"OK, I'll take care of {contact.name} again.")


def send_owner_reply(a, text, ctx):
    """The owner typed her answer after tapping Reply: send it to the customer from the shop's number."""
    contact = ctx.store.get_contact(a["payload"]["contact_id"])
    lead = lead_for(ctx.store, contact.id)
    ctx.store.save_lead(contact.id, handoff_until=now() + HANDOFF_FOR,
                        owner_said=(lead.get("owner_said") or [])[-4:] + [text[:300]])
    ctx.say(text, to=contact.phone)
    _ask_owner(ctx, contact.id, f"Sent to {contact.name} ✓ I'll pass their next message to you. "
                                "Tap “Let Sabi handle it” when you're done.", reply_label="Reply again")


# --- follow-ups --------------------------------------------------------------

def followups_due(store, at=None):
    """(phone, text) for each lead worth one gentle nudge now. Marks them so nobody is nudged twice."""
    at = at or now()
    pay = getattr(store, "business", {}).get("payment_details", "")
    out = []
    for lead in store.list_leads():
        if lead.get("followed_up_at") or with_owner(lead, at):
            continue
        contact = store.get_contact(lead["contact_id"])
        if not contact or not contact.phone or not contact.last_inbound_at:
            continue
        if not FOLLOW_UP_AFTER <= at - contact.last_inbound_at < FREE_TEXT_WINDOW:
            continue
        stage = lead_stage(store, lead)
        text = None
        if stage == "ordered":
            order = store.get_order(lead["order_id"])
            if order["status"] == "open":
                items = ", ".join(f"{i['qty']} × {store.get_product(i['product_id']).name}" for i in order["items"])
                text = (f"Hi {contact.name}, your order ({items}, {naira(order['total_kobo'])}) is still set aside for you. "
                        + (pay + " " if pay else "") + "Any questions, just ask.")
        elif stage == "asking":
            for pid in reversed(lead.get("interests") or []):
                p = store.get_product(pid)
                if p and store.stock_on_hand(pid) - store.reserved(pid) > 0:
                    text = (f"Hi {contact.name}, still interested in the {p.name}? It's {naira(p.sell_price_kobo)} per "
                            f"{p.base_unit} and we have it. Tell me how many and I'll set it aside.")
                    break
        if text:
            store.save_lead(lead["contact_id"], followed_up_at=at)
            out.append((contact.phone, text))
    return out


async def run_forever(store, send, every_seconds=600):
    import asyncio
    import logging

    log = logging.getLogger("sabi.jobs")
    while True:
        await asyncio.sleep(every_seconds)
        try:
            for phone, text in followups_due(store):
                await send(phone, text)
        except Exception:
            log.exception("follow-ups failed")
