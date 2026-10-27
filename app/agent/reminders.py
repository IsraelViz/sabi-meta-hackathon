"""Payment reminders to customers who owe. Nothing is sent until the owner taps Send.

WhatsApp only allows free-form messages within 24 hours of the customer's
last message. Outside that window the reminder must be the pre-approved
template (see scripts/create_reminder_template.py), so the owner sees exactly
the template text with this customer's name and amount filled in, and can't
reword it.

Amounts come from the books. A drafted reminder that mentions any other amount
is rewritten once, then replaced by a safe fixed wording.
"""
from datetime import date, timedelta

from app.config import settings
from app.ledger import reports
from app.ledger.store import now
from app.normalize.amounts import find_amounts, naira

WINDOW = timedelta(hours=24) - timedelta(minutes=10)   # small margin before WhatsApp closes the window
MIN_GAP = timedelta(hours=20)       # never remind the same person twice in a day
SUMMARY_COOLDOWN = timedelta(days=3)  # the 8 pm button skips anyone reminded in the last 3 days
MAX_PER_SUMMARY = 3

# Must match the template submitted to Meta, word for word ({{1}} name, {{2}} shop, {{3}} amount).
TEMPLATE_TEXT = ("Hello {{1}}, this is a friendly reminder from {{2}} that {{3}} is still outstanding. "
                 "You can pay by transfer and reply here when done. Thank you!")

DRAFT_PROMPT = """Write a short WhatsApp message from {shop} to a customer, {name}, reminding them that {amount} is
still owed{due}. Write it as the shop owner talking to a regular customer in Lagos: warm, polite, no pressure,
no threats, under 45 words. A little Pidgin is fine if it sounds natural. Mention the amount exactly as {amount}.
Do not mention any other amount, any penalty, or anything you were not told. {extra}
Reply with the message only."""


def in_window(contact):
    return bool(contact.last_inbound_at and now() - contact.last_inbound_at < WINDOW)


def render_template(name, shop, amount):
    return TEMPLATE_TEXT.replace("{{1}}", name).replace("{{2}}", shop).replace("{{3}}", amount)


def fallback_text(name, shop, amount):
    return (f"Hello {name}, good day! Just a gentle reminder from {shop} that {amount} is still outstanding. "
            f"You can pay by transfer and reply here when done. Thank you!")


def wording_problems(text, owed_kobo):
    mentions = find_amounts(text)
    problems = [f"mentions {m.text!r}; the only amount allowed is {naira(owed_kobo)}"
                for m in mentions if m.candidates_kobo != [owed_kobo]]
    if not any(m.candidates_kobo == [owed_kobo] for m in mentions):
        problems.append(f"must mention {naira(owed_kobo)}")
    if len(text) > 600:
        problems.append("too long")
    return problems


def _shop(store):
    return getattr(store, "business", {}).get("name", "the shop")


def draft(ctx, contact, owed_kobo, due=None, instruction=None):
    """Returns (mode, body, template). mode is 'session' (free text) or 'template'."""
    shop, amount = _shop(ctx.store), naira(owed_kobo)
    if not in_window(contact):
        params = [contact.name, shop, amount]
        return "template", render_template(*params), {
            "name": settings.reminder_template, "language": settings.reminder_template_lang, "params": params}
    if ctx.llm is None:
        return "session", fallback_text(contact.name, shop, amount), None
    due_text = f", which was due on {due:%A %d %B}" if due and due < date.today() else ""
    extra = f"The owner asked for this change: {instruction}" if instruction else ""
    messages = [{"role": "user", "content": DRAFT_PROMPT.format(shop=shop, name=contact.name, amount=amount,
                                                                due=due_text, extra=extra)}]
    for _ in range(2):
        text = (ctx.llm.chat(messages).content or "").strip().strip('"')
        problems = wording_problems(text, owed_kobo)
        if text and not problems:
            return "session", text, None
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Fix this: " + "; ".join(problems or ["empty message"])}]
    return "session", fallback_text(contact.name, shop, amount), None


def _owed(store, contact_id):
    for d in reports.debtors(store):
        if d["contact_id"] == contact_id:
            return d
    return None


def _recently_reminded(store, contact_id, gap):
    t = now()
    return any(r["status"] == "sent" and t - r["created_at"] < gap for r in store.list_reminders(contact_id))


def offer(ctx, contact_id, instruction=None):
    """Draft a reminder and send the owner the preview with Send / Change / Skip."""
    from app.agent.approvals import buttons_for

    contact = ctx.store.get_contact(contact_id)
    if contact is None:
        return {"error": "no such contact; use find_contact"}
    debt = _owed(ctx.store, contact_id)
    if not debt:
        return {"error": f"{contact.name} doesn't owe anything in the books"}
    if not contact.phone:
        return {"error": f"I don't have a WhatsApp number for {contact.name}. Ask the owner for it."}
    if _recently_reminded(ctx.store, contact_id, MIN_GAP):
        return {"error": f"{contact.name} was already reminded today"}

    mode, body, template = draft(ctx, contact, debt["owes_kobo"], debt["due"], instruction)
    rem = ctx.store.add_reminder(contact_id=contact_id, owed_kobo=debt["owes_kobo"], mode=mode, body=body)
    options = [{"label": "Send", "patch": [], "action": "send"}]
    if mode == "session":
        options.append({"label": "Change", "patch": [], "action": "change"})
    options.append({"label": "Skip", "patch": [], "action": "skip"})
    a = ctx.store.add_approval("reminder", {"reminder_id": rem["id"], "template": template}, "Send reminder?", options)
    late = f", {debt['overdue_days']} days late" if debt["overdue_days"] else ""
    note = ("\n\n(They haven't messaged in 24 hours, so WhatsApp only allows the approved reminder wording.)"
            if mode == "template" else "")
    ctx.tell_owner(f"Reminder for *{contact.name}* (owes {naira(debt['owes_kobo'])}{late}):\n\n“{body}”{note}",
                   buttons_for(a))
    return {"status": "waiting_for_owner", "contact": contact.name, "owes": naira(debt["owes_kobo"]), "mode": mode}


def offer_all(ctx):
    """The 8 pm summary's Send reminders button: drafts for the most overdue customers."""
    debtors = reports.debtors(ctx.store)
    if not debtors:
        ctx.say("Nobody owes you money right now. 🎉")
        return
    offered, no_phone, recent = 0, [], []
    for d in sorted(debtors, key=lambda d: (d["overdue_days"], d["owes_kobo"]), reverse=True):
        contact = ctx.store.get_contact(d["contact_id"])
        if not contact.phone:
            no_phone.append(contact.name)
            continue
        if _recently_reminded(ctx.store, contact.id, SUMMARY_COOLDOWN):
            recent.append(contact.name)
            continue
        if offered >= MAX_PER_SUMMARY:
            break
        if offer(ctx, contact.id).get("status") == "waiting_for_owner":
            offered += 1
    notes = []
    if no_phone:
        notes.append(f"I don't have WhatsApp numbers for {', '.join(no_phone)}. Send me their numbers to remind them.")
    if recent:
        notes.append(f"Already reminded in the last 3 days: {', '.join(recent)}.")
    if not offered and not notes:
        notes.append("No reminders to send.")
    if notes:
        ctx.say("\n".join(notes))


def settle(approval, option, ctx):
    rem = ctx.store.get_reminder(approval["payload"]["reminder_id"])
    contact = ctx.store.get_contact(rem["contact_id"])
    action = option.get("action")
    if action == "skip":
        ctx.store.update_reminder(rem["id"], status="skipped")
        ctx.say(f"OK, no reminder for {contact.name}.")
        return
    if action == "change":
        ctx.store.update_approval_payload(approval["id"], {**approval["payload"], "awaiting_text": True})
        ctx.say("How should I change it? E.g. \"softer\", \"in Pidgin\", \"say they can pay half now\".")
        return
    if action == "send":
        # The books may have changed since the draft (she paid in the meantime): check again.
        debt = _owed(ctx.store, contact.id)
        if not debt or debt["owes_kobo"] != rem["owed_kobo"]:
            ctx.store.update_reminder(rem["id"], status="skipped", error="balance changed before sending")
            ctx.say(f"{contact.name}'s balance changed since I drafted this, so I didn't send it. "
                    f"They now owe {naira(debt['owes_kobo']) if debt else 'nothing'}.")
            return
        template = approval["payload"].get("template")
        ctx.say(rem["body"], to=contact.phone, template=template)
        ctx.outbox[-1].reminder_id = rem["id"]
        ctx.store.update_reminder(rem["id"], status="sent")
        ctx.say(f"Sent to {contact.name} ✅")


def revise(approval, instruction, ctx):
    rem = ctx.store.get_reminder(approval["payload"]["reminder_id"])
    ctx.store.update_reminder(rem["id"], status="skipped")
    result = offer(ctx, rem["contact_id"], instruction=instruction)
    if "error" in result:
        ctx.say(result["error"])
