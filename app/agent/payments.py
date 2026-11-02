"""Checking a payment alert before she hands over goods.

Fake transfer screenshots and fake credit SMS are a common scam on Nigerian traders. A forwarded
alert is read (the model reads a screenshot; code reads an SMS), then code, not the model, checks it:
is it complete, is it today, is it the right amount, is it from the right person, is it to her account,
and does it match an order or a debt. Whatever it finds, the advice ends the same way: only her own
bank app or balance proves the money arrived. Nothing is recorded until she confirms.
"""
import json
import re
from datetime import date, datetime, timedelta

from app.normalize.amounts import naira
from app.normalize.fuzzy import name_similarity

NOT_DONE = re.compile(r"\b(pending|processing|in progress|queued|scheduled|initiated|awaiting|on hold|reversed|failed)\b", re.I)
AMOUNT = re.compile(r"(?:NGN|N|₦)\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
AMT_LABEL = re.compile(r"\b(?:amt|amount)\s*[:\-]?\s*(?:NGN|N|₦)?\s?([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.I)
BALANCE = re.compile(r"\b(?:avail(?:able)?\.?\s*bal(?:ance)?|bal(?:ance)?)\s*[:\-]?", re.I)
CREDIT = re.compile(r"\b(cr|credit(?:ed)?|received|inflow)\b", re.I)
DEBIT = re.compile(r"\b(dr|debit(?:ed)?|sent|transfer to|you sent|outflow)\b", re.I)
FROM = re.compile(r"(?:\bfrom\b|\bfrm\b|sender\s*[:\-]?)\s*:?\s*([A-Za-z][A-Za-z .'-]{2,40}?)(?=\s*(?:/|\bto\b|\bref\b|\bon\b|\bdesc\b|\bamt\b|\bbal\b|\bavail|\bdate\b|\bsession\b|[,.;:\n]|$))", re.I)
DATE = re.compile(r"\b(\d{1,2})[-/ ](\d{1,2}|[A-Za-z]{3})[-/ ](\d{2,4})\b")
ACCOUNT = re.compile(r"\b(?:acct|account|a/c)\b[^0-9]{0,10}([0-9X*]{4,12})", re.I)
REF = re.compile(r"\b(?:ref(?:erence)?|session id|trans(?:action)? id)\s*[:#]?\s*([A-Za-z0-9/-]{6,})", re.I)
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}

READ_PROMPT = """This is a screenshot of a bank transfer receipt or bank alert, sent to a Nigerian shop owner as proof of
payment. Read it exactly. Reply with JSON only:
{"amount_naira": number or null, "status": "the status words shown, e.g. Successful / Pending", "sender_name": "",
 "receiver_name": "", "receiver_account": "the account number shown, as shown", "bank": "", "reference": "",
 "date": "YYYY-MM-DD or null", "time": "HH:MM or null",
 "kind": "receipt from the sender's app" or "alert to the receiver" or "unclear",
 "oddities": ["anything that looks edited, cut off, mismatched fonts or misaligned text"]}"""


def _amount(text):
    m = AMT_LABEL.search(text) or AMOUNT.search(text)
    return round(float(m.group(1).replace(",", "")) * 100) if m else None


def _date(text, today):
    m = DATE.search(text)
    if not m:
        return None
    d, mo, y = m.groups()
    try:
        month = int(mo) if mo.isdigit() else MONTHS[mo[:3].lower()]
        year = int(y) + (2000 if len(y) == 2 else 0)
        return date(year, month, int(d))
    except (KeyError, ValueError):
        return None


def read_sms(text, today=None):
    """What a forwarded bank SMS or typed alert says. Fields it can't find are None."""
    today = today or date.today()
    sender = FROM.search(text)
    account = ACCOUNT.search(text)
    ref = REF.search(text)
    credit, debit = bool(CREDIT.search(text)), bool(DEBIT.search(text))
    return {"amount_kobo": _amount(text), "status": (NOT_DONE.search(text) or [None])[0],
            "sender_name": sender.group(1).strip() if sender else None,
            "receiver_account": account.group(1) if account else None,
            "reference": ref.group(1) if ref else None, "date": _date(text, today),
            "has_balance": bool(BALANCE.search(text)),
            "kind": "alert to the receiver" if credit and not debit else "receipt from the sender's app" if debit else "unclear",
            "oddities": [], "source": "sms"}


def read_screenshot(llm, image_data_url):
    reply = llm.chat([{"role": "user", "content": [{"type": "text", "text": READ_PROMPT},
                                                   {"type": "image_url", "image_url": {"url": image_data_url}}]}])
    text = (reply.content or "").strip()
    text = text[text.find("{"): text.rfind("}") + 1]
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return None
    try:
        when = date.fromisoformat(d["date"]) if d.get("date") else None
    except ValueError:
        when = None
    status = d.get("status") or ""
    return {"amount_kobo": round(float(d["amount_naira"]) * 100) if d.get("amount_naira") else None,
            "status": (NOT_DONE.search(status) or [None])[0], "status_text": status,
            "sender_name": d.get("sender_name") or None, "receiver_name": d.get("receiver_name") or None,
            "receiver_account": d.get("receiver_account") or None, "reference": d.get("reference") or None,
            "date": when, "has_balance": None, "kind": d.get("kind") or "unclear",
            "oddities": [o for o in d.get("oddities") or [] if isinstance(o, str)][:3], "source": "screenshot"}


def _digits(s):
    return re.sub(r"\D", "", s or "")


def check(store, alert, today=None, expect_kobo=None, expect_name=None):
    """Code's verdict on an alert: {'verdict': ok|check|warning, 'flags': [...], 'matches': [...]}."""
    today = today or date.today()
    flags = []
    amount = alert.get("amount_kobo")
    if alert.get("status"):
        flags.append(f"It says “{alert['status']}”: the money has not arrived yet.")
    if amount is None:
        flags.append("I couldn't find an amount on it.")
    when = alert.get("date")
    if when and when > today:
        flags.append(f"It is dated {when:%d %b}, which is in the future.")
    elif when and when < today - timedelta(days=1):
        flags.append(f"It is dated {when:%d %b}, not today. An old receipt can be sent again.")
    if alert.get("kind") == "receipt from the sender's app":
        flags.append("This is the sender's receipt, not your bank's alert. Receipts are easy to fake or edit.")
    if alert.get("source") == "sms" and alert.get("kind") == "alert to the receiver" and alert.get("has_balance") is False:
        flags.append("Real bank credit SMS usually show your available balance; this one doesn't.")
    for o in alert.get("oddities") or []:
        flags.append(f"Looks odd: {o}.")
    mine = _digits(getattr(store, "business", {}).get("payment_details"))
    theirs = _digits(alert.get("receiver_account"))
    if len(mine) >= 10 and len(theirs) >= 4 and not mine.endswith(theirs[-4:]) and "X" not in (alert.get("receiver_account") or "").upper():
        flags.append(f"It went to account …{theirs[-4:]}, which isn't the account in your payment details.")
    if expect_kobo is not None and amount is not None and amount != expect_kobo:
        flags.append(f"It shows {naira(amount)}, but {naira(expect_kobo)} is due.")
    sender = alert.get("sender_name")
    if expect_name and sender and name_similarity(expect_name, sender) < 0.6 and not _same_person(expect_name, sender):
        flags.append(f"It is from {sender}, not {expect_name}. It may be a relative paying, so ask.")

    matches = []
    if amount is not None:
        for o in store.open_orders():
            if o["total_kobo"] == amount:
                c = store.get_contact(o["contact_id"])
                matches.append({"type": "order", "order_id": o["id"], "ref": o["id"][:6].upper(), "name": c.name if c else "?",
                                "amount_kobo": amount, "name_match": _score(c.name if c else "", sender)})
        from app.ledger.reports import debtors
        for d in debtors(store, today):
            if d["owes_kobo"] == amount or (sender and _score(d["name"], sender) >= 0.6 and amount <= d["owes_kobo"]):
                matches.append({"type": "debt", "contact_id": d["contact_id"], "name": d["name"],
                                "owes_kobo": d["owes_kobo"], "amount_kobo": amount, "name_match": _score(d["name"], sender)})
    matches.sort(key=lambda m: m["name_match"], reverse=True)
    serious = any(f.startswith(("It says", "This is the sender", "It went to", "It is dated", "Looks odd", "Real bank"))
                  for f in flags)
    verdict = "warning" if serious else "check" if flags or not matches else "ok"
    return {"verdict": verdict, "flags": flags, "matches": matches[:3],
            "advice": "Only your own bank app or balance proves the money arrived. Check it before you give the goods."}


def _same_person(a, b):
    """'Ada Obi' and 'OBI ADAEZE CHIOMA': banks print names in any order and in full."""
    wa, wb = set(re.findall(r"[a-z]+", a.lower())), set(re.findall(r"[a-z]+", b.lower()))
    return any(x == y or (len(x) >= 3 and (y.startswith(x) or x.startswith(y))) for x in wa for y in wb)


def _score(name, sender):
    if not sender:
        return 0.0
    return 1.0 if _same_person(name, sender) else round(name_similarity(name, sender), 2)


def summary(alert, result):
    """The owner's message: what the alert says, what looks wrong, what it matches."""
    a = alert
    head = f"{naira(a['amount_kobo']) if a.get('amount_kobo') else 'No amount'}"
    if a.get("sender_name"):
        head += f" from {a['sender_name']}"
    if a.get("date"):
        head += f", {a['date']:%d %b}"
    icon = {"ok": "✅", "check": "🟡", "warning": "⚠️"}[result["verdict"]]
    lines = [f"{icon} Alert check: {head}."]
    lines += [f"• {f}" for f in result["flags"]]
    if not result["matches"] and a.get("amount_kobo"):
        lines.append("It doesn't match any open order or anyone who owes you.")
    for m in result["matches"][:2]:
        if m["type"] == "order":
            lines.append(f"Matches {m['name']}'s order {m['ref']} ({naira(m['amount_kobo'])}).")
        else:
            lines.append(f"{m['name']} owes you {naira(m['owes_kobo'])}.")
    lines.append(result["advice"])
    return "\n".join(lines)


def check_payment(ctx, text=None):
    """Owner tool: she forwarded an alert (SMS text or a screenshot)."""
    from app.agent.approvals import CANCEL, CONFIRM, buttons_for
    if ctx.image_data_url:
        if ctx.llm is None:
            return {"error": "the vision model isn't connected"}
        alert = read_screenshot(ctx.llm, ctx.image_data_url)
        if alert is None:
            return {"error": "couldn't read the screenshot; ask her to send it again or type the alert"}
    else:
        source = text or (ctx.message.original if ctx.message else "")
        if not source.strip():
            return {"error": "no alert to check: ask her to forward the SMS or send the screenshot"}
        alert = read_sms(source)
    result = check(ctx.store, alert)
    ctx.store.set_evidence_extracted(ctx.evidence_id, {"payment_alert": json.loads(json.dumps(alert, default=str)),
                                                       "alert_check": result})
    order = next((m for m in result["matches"] if m["type"] == "order"), None)
    msg = summary(alert, result)
    if order and result["verdict"] != "warning":
        o = ctx.store.get_order(order["order_id"])
        if o and o["status"] == "open":
            ctx.store.update_order(o["id"], status="claimed_paid", claim_evidence_id=ctx.evidence_id)
        a = ctx.store.add_approval("order_paid", {"order_id": order["order_id"]},
                                   msg + "\n\nIs the money in your account?",
                                   [{**CONFIRM, "label": "Yes, it's there"}, {**CANCEL, "label": "Not yet"}])
        ctx.tell_owner(a["question"], buttons_for(a))
        return {"status": "waiting_for_owner", "verdict": result["verdict"], "note": "She was sent the check with buttons."}
    ctx.tell_owner(msg)
    debt = next((m for m in result["matches"] if m["type"] == "debt"), None)
    out = {"status": "told_owner", "verdict": result["verdict"]}
    if debt and result["verdict"] != "warning":
        out["next"] = (f"If she says the money is in her account, record payment_in from {debt['name']} "
                       f"(contact_id {debt['contact_id']}) of {naira(alert['amount_kobo'])}. Don't record it before she says so.")
    return out


def claim_check(ctx, order):
    """Customer side: a screenshot sent as proof for an order gets the same check, for the owner's eyes."""
    if not ctx.image_data_url or ctx.llm is None:
        return None
    alert = read_screenshot(ctx.llm, ctx.image_data_url)
    if alert is None:
        return None
    customer = ctx.store.get_contact(order["contact_id"])
    result = check(ctx.store, alert, expect_kobo=order["total_kobo"], expect_name=customer.name if customer else None)
    ctx.store.set_evidence_extracted(ctx.evidence_id, {"payment_alert": json.loads(json.dumps(alert, default=str)),
                                                       "alert_check": result})
    return summary(alert, result)
