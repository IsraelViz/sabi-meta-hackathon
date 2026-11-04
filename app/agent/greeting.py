"""When she just says hello: a quick look at her shop and three one-tap next steps, instead of small talk.

Built by code from the books, so it is instant, costs no model call, and every number is real. The
buttons send their words to Sabi as if she typed them ("How's business?" gets the insights).
"""
import re
from datetime import timedelta

from app.ledger.reports import LAGOS, debtors, profit_and_loss, restock_advice, whole_naira as naira
from app.ledger.store import now

GREETING = re.compile(
    r"^\W*(hi+|hello+|hey+|hiya|yo|good\s*(morning|afternoon|evening|day)|morning|evening|how\s*far|howdy|"
    r"how\s*(are|r)\s*(you|u)|how\s*is\s*it|wetin\s*dey|bawo|e\s*kaaro|e\s*kaasan|kedu|sannu|salam|"
    r"bonjour|salut|ola|jambo|habari)(\s+(sabi|there|o+|oo+|ma|sir))*\W*$", re.I)
BUTTONS = [{"id": "hello:record", "title": "Record a sale"}, {"id": "hello:insights", "title": "How's business?"},
           {"id": "hello:order", "title": "What to order?"}]
SAYS = {"hello:record": "I want to record a sale", "hello:insights": "How is my business doing?",
        "hello:order": "What should I order?"}


def is_greeting(text):
    return bool(text) and len(text) <= 40 and bool(GREETING.match(text.strip()))


def _sales_line(store, today):
    p = profit_and_loss(store, "today", today)
    count = sum(1 for e in store.list_entries(start=today, end=today) if e.kind == "sale" and e.status != "reversed")
    if count:
        return f"💰 Today so far: *{naira(p['sales_kobo'])}* from {count} sale{'s' if count != 1 else ''}"
    y = today - timedelta(days=1)
    py = profit_and_loss(store, "today", y)
    ycount = sum(1 for e in store.list_entries(start=y, end=y) if e.kind == "sale" and e.status != "reversed")
    if ycount:
        return f"💰 Yesterday: *{naira(py['sales_kobo'])}* from {ycount} sale{'s' if ycount != 1 else ''}. Nothing recorded today yet"
    return "💰 No sales recorded today yet"


def snapshot(store, at=None):
    at = (at or now()).astimezone(LAGOS)
    today = at.date()
    part = "morning" if at.hour < 12 else "afternoon" if at.hour < 17 else "evening"
    shop = (getattr(store, "business", {}) or {}).get("name") or "your shop"
    lines = [_sales_line(store, today)]

    late = sorted((d for d in debtors(store, today) if d["overdue_days"] > 0), key=lambda d: d["owes_kobo"], reverse=True)
    if late:
        d = late[0]
        lines.append(f"⏰ {d['name']} owes {naira(d['owes_kobo'])}, {d['overdue_days']} day{'s' if d['overdue_days'] != 1 else ''} late"
                     + (f" (and {len(late) - 1} more late)" if len(late) > 1 else ""))
    out = [p for p in store.list_products() if restock_advice(store, p.id, today)["due"]]
    sold_out = [p for p in out if store.stock_on_hand(p.id) <= 0]
    if sold_out:
        lines.append(f"📦 {sold_out[0].name} is sold out" + (f", {len(out) - 1} more running low" if len(out) > 1 else ""))
    elif out:
        lines.append(f"📦 {len(out)} product{'s are' if len(out) != 1 else ' is'} running low, starting with {out[0].name}")
    waiting = len(store.pending_approvals())
    if waiting:
        lines.append(f"✋ {waiting} thing{'s' if waiting != 1 else ''} waiting for your answer above")

    body = f"Good {part}! 👋 Here's {shop} today:\n\n" + "\n".join(lines) + "\n\nWhat would you like to do?"
    return body, BUTTONS
