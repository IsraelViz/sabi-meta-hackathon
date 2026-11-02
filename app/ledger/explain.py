"""Why is this entry in my books? The trail behind one entry, in plain words.

What she said (or sent), what Sabi understood (the model's proposal), the checks code ran, and who
let it in. Built from what is already stored: the evidence, the agent run and the entry's own lines,
so it shows the rule "the model proposes, code commits" on every entry she taps.
"""
import json
import mimetypes
import re

from app.config import settings
from app.ledger.reports import headline_kobo, tracks_stock
from app.normalize.amounts import naira, read_amounts


def _proposals(store, evidence_id):
    """The record_entry / record_batch arguments the model sent for this message."""
    out = []
    for run in store.runs_for_evidence(evidence_id):
        calls = run.get("tool_calls") or []
        if isinstance(calls, str):
            calls = json.loads(calls)
        for c in calls:
            try:
                args = json.loads(c.get("args") or "{}") if isinstance(c.get("args"), str) else (c.get("args") or {})
            except json.JSONDecodeError:
                continue
            if c.get("tool") == "record_entry":
                out.append(args)
            elif c.get("tool") == "record_batch":
                out += args.get("entries") or []
    return out


def _understood(store, a):
    names = {p.id: p for p in store.list_products()}
    contact = store.get_contact(a["contact_id"]) if a.get("contact_id") else None
    who = contact.name if contact else a.get("new_contact_name")
    parts = [a.get("kind", "?").replace("_", " ")]
    for it in a.get("items") or []:
        p = names.get(it.get("product_id"))
        price = it.get("unit_price_naira")
        parts.append(f"{it.get('qty')} × {p.name if p else '?'} "
                     + (f"at {naira(round(price * 100))}" if isinstance(price, (int, float)) else "at your list price"))
    if a.get("amount_naira") is not None:
        parts.append(naira(round(a["amount_naira"] * 100)))
    if who:
        parts.append(f"with {who}")
    if a.get("paid_naira") is not None:
        parts.append("on credit" if a["paid_naira"] == 0 else f"paid {naira(round(a['paid_naira'] * 100))}"
                     + (" by transfer" if a.get("payment_account") == "bank" else " in cash" if a.get("payment_account") == "cash" else ""))
    return ", ".join(parts)


def explain(store, entry):
    steps = []
    ev = store.get_evidence(entry.evidence_id) if entry.evidence_id else None
    if ev:
        extra = ev.get("extracted_json") or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        media = mimetypes.guess_type((ev.get("media_url") or "").removeprefix("private:"))[0] or ""
        how = "Voice note" if extra.get("transcript_of") else "Photo" if media.startswith("image") else "Message"
        if extra.get("document"):
            how = "Photo of a receipt or notebook"
        steps.append({"icon": "💬", "title": f"What you sent ({how.lower()})", "detail": ev.get("original_text") or "(no text)"})
        read = [f"“{m.text}” → {naira(m.candidates_kobo[0])}" for m in read_amounts(ev.get("original_text") or "")
                if not m.ambiguous and re.sub(r"\D", "", m.text) != str(m.candidates_kobo[0] // 100)]
        if read:
            steps.append({"icon": "🔢", "title": "Amounts read by code, not the AI", "detail": "; ".join(read)})
        if extra.get("arithmetic_issues"):
            steps.append({"icon": "🧮", "title": "Photo's sums checked",
                          "detail": "The numbers didn't add up, so Sabi asked you first: " + "; ".join(extra["arithmetic_issues"])})
        elif extra.get("document"):
            steps.append({"icon": "🧮", "title": "Photo's sums checked", "detail": "Line totals and the grand total agree."})
    proposals = _proposals(store, entry.evidence_id) if entry.evidence_id else []
    if proposals:
        steps.append({"icon": "🤖", "title": "What Sabi understood",
                      "detail": " | ".join(_understood(store, a) for a in proposals[:3])
                      + ". The AI only suggested this; it cannot write to your books."})

    checks = []
    debits = sum(l.debit_kobo for l in entry.lines)
    checks.append(f"Balanced: {naira(debits)} in = {naira(sum(l.credit_kobo for l in entry.lines))} out")
    products = {p.id: p for p in store.list_products()}
    for l in entry.lines:
        p = products.get(l.product_id)
        if not p or not l.qty:
            continue
        if l.account == "sales":
            unit = l.credit_kobo // l.qty
            usual = p.price_floor <= unit <= p.price_ceiling
            checks.append(f"{p.name} at {naira(unit)}: " + (f"within your usual {naira(p.price_floor)}–{naira(p.price_ceiling)}"
                                                             if usual else "outside your usual range, so you were asked"))
            cost = next((c for c in entry.lines if c.account == "cogs" and c.product_id == p.id), None)
            if cost:
                checks.append(f"Profit uses your average cost of {naira(cost.debit_kobo // l.qty)} each")
            elif not tracks_stock(store, p.id):
                checks.append(f"No stock or cost recorded for {p.name} yet, so its cost counts as ₦0")
        if l.account == "inventory" and l.debit_kobo and entry.kind == "purchase":
            checks.append(f"{l.qty} {p.base_unit} {p.name} added to stock at {naira(l.debit_kobo // l.qty)} each")
    for m in entry.stock_moves:
        p = products.get(m.product_id)
        if p and m.qty < 0 and entry.kind == "sale":
            checks.append(f"Stock of {p.name} went down by {-m.qty}")
    for l in entry.lines:
        if l.account in ("ar", "ap") and l.contact_id:
            c = store.get_contact(l.contact_id)
            side = "owes you" if l.account == "ar" else "you owe"
            amount = l.debit_kobo if l.account == "ar" else l.credit_kobo
            if amount:
                checks.append(f"{naira(amount)} added to what {c.name if c else '?'} {side}"
                              + (f", due {entry.due_date:%d %b}" if entry.due_date else ""))
    steps.append({"icon": "✔️", "title": "Checked by code", "detail": "\n".join(checks)})

    who = {"agent:auto": f"Every check passed and it is under your {naira(settings.auto_post_limit_kobo)} limit, "
                         f"so Sabi recorded it straight away.",
           "agent:approved": "Sabi asked you and you tapped Confirm.",
           "app": "You entered it in the app.", "seed": "Demo data."}.get(entry.created_by, entry.created_by)
    if entry.kind == "reversal":
        who = "You asked Sabi to cancel an entry; the original stays in your history."
    steps.append({"icon": "👤", "title": "Who let it in", "detail": who})
    if ev and ev.get("model"):
        steps.append({"icon": "🗂", "title": "On record", "detail": f"Read by {ev['model']} (instructions {ev.get('prompt_version')}). "
                      f"Entry {entry.seq}, {naira(headline_kobo(entry))}. Entries are never edited: a mistake is "
                      f"cancelled by a new entry."})
    return steps
