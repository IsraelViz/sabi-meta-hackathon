"""Tool schemas and handlers.

Owner mode gets the bookkeeping tools. Customer mode gets a smaller set that
cannot read the ledger or anyone else's data, whatever the DM says.
"""
import json
from datetime import date

from app.agent import orders
from app.agent.approvals import CANCEL, CONFIRM, buttons_for, describe, submit
from app.ledger import reports
from app.ledger.proposals import KINDS, Item, Proposal
from app.normalize.amounts import naira
from app.normalize.fuzzy import best_match, name_similarity


def _fn(name, description, properties, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required)}}}


_ITEMS = {"type": "array", "items": {"type": "object", "properties": {
    "product_id": {"type": "string"},
    "qty": {"type": "integer"},
    "unit_price_naira": {"type": ["number", "null"], "description": "Price per unit if said, else null"},
}, "required": ["product_id", "qty"]}}

FIND_PRODUCT = _fn("find_product", "Look up products by what the person called them. Returns ids, prices and stock.",
                   {"query": {"type": "string"}}, ["query"])
FIND_CONTACT = _fn("find_contact", "Look up a customer or supplier by name. Returns ids and match scores.",
                   {"query": {"type": "string"}}, ["query"])
RECORD = _fn(
    "record_entry",
    "Propose one business event for the books. Code checks it and either records it, or asks the owner. "
    "Never compute totals, cost or balances yourself.",
    {
        "kind": {"type": "string", "enum": list(KINDS)},
        "items": _ITEMS,
        "contact_id": {"type": ["string", "null"]},
        "new_contact_name": {"type": ["string", "null"], "description": "Only if find_contact found nobody"},
        "amount_naira": {"type": ["number", "null"], "description": "payment_in/payment_out/expense/drawing"},
        "paid_naira": {"type": ["number", "null"],
                       "description": "sale/purchase: amount paid now. 0 if on credit. null if not said."},
        "payment_account": {"type": ["string", "null"], "enum": ["cash", "bank", None],
                            "description": "bank = transfer/POS"},
        "expense_account": {"type": ["string", "null"], "enum": ["transport", "power", "other_exp", None]},
        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD when credit is due, if said"},
        "memo": {"type": "string"},
        "unclear_amounts": {"type": "array", "description":
            "Every amount marked [UNCLEAR AMOUNT] in the message. path is the field it belongs to, "
            "e.g. 'items.0.unit_price_kobo', 'paid_kobo' or 'amount_kobo'.",
            "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "text": {"type": "string"},
                "candidates_naira": {"type": "array", "items": {"type": "number"}}},
                "required": ["path", "text", "candidates_naira"]}},
    },
    ["kind", "memo"],
)
RECENT = _fn("recent_entries", "The latest ledger entries, newest first, to find one to correct.",
             {"limit": {"type": "integer"}})
REVERSE = _fn("cancel_entry", "Cancel a wrong entry with a reversing entry. The owner confirms first.",
              {"entry_id": {"type": "string"}, "reason": {"type": "string"}}, ["entry_id", "reason"])
REPORT = _fn("report", "Books report.", {
    "kind": {"type": "string", "enum": ["pnl", "cash", "debtors", "low_stock"]},
    "period": {"type": "string", "enum": ["today", "week", "month", "all"]}}, ["kind"])
ASK = _fn("ask_owner", "Ask the owner a question with up to 3 short button options.", {
    "question": {"type": "string"},
    "options": {"type": "array", "items": {"type": "string"}, "maxItems": 3}}, ["question"])

CUST_PRODUCT = _fn("find_product", "Look up products the shop sells. Returns price and whether it is in stock.",
                   {"query": {"type": "string"}}, ["query"])
CREATE_ORDER = _fn("create_order", "Create an order for this customer at the shop's prices. Reserves stock for 24h.", {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "product_id": {"type": "string"}, "qty": {"type": "integer"}}, "required": ["product_id", "qty"]}}},
    ["items"])
PAYMENT_CLAIM = _fn("record_payment_claim",
                    "The customer says they paid (e.g. sent a transfer screenshot). The owner must confirm.", {
                        "order_id": {"type": "string"}, "amount_naira": {"type": ["number", "null"]},
                        "reference": {"type": ["string", "null"]}}, ["order_id"])
MY_ORDERS = _fn("my_open_orders", "This customer's open orders.", {})

OWNER_TOOLS = [FIND_PRODUCT, FIND_CONTACT, RECORD, RECENT, REVERSE, REPORT, ASK]
CUSTOMER_TOOLS = [CUST_PRODUCT, CREATE_ORDER, PAYMENT_CLAIM, MY_ORDERS]


def _kobo(naira_value):
    return None if naira_value is None else round(float(naira_value) * 100)


# --- owner handlers --------------------------------------------------------

def find_product(ctx, query, customer=False):
    scored = best_match(query, ctx.store.list_products(), lambda p: [p.name, *p.aliases])[:3]
    out = []
    for score, p in scored:
        if score < 0.5:
            continue
        ctx.match_scores[p.id] = max(score, ctx.match_scores.get(p.id, 0))
        available = ctx.store.stock_on_hand(p.id) - ctx.store.reserved(p.id)
        row = {"product_id": p.id, "name": p.name, "unit": p.base_unit, "price": naira(p.sell_price_kobo)}
        if customer:
            row["in_stock"] = available > 0
        else:
            row.update(match=round(score, 2), stock=available, usual_price_range=
                       f"{naira(p.price_floor)}-{naira(p.price_ceiling)}")
        out.append(row)
    return {"matches": out} if out else {"matches": [], "note": "no product matches; ask what they meant"}


def find_contact(ctx, query):
    scored = sorted(((max(name_similarity(query, n) for n in [c.name, *c.aliases]), c)
                     for c in ctx.store.list_contacts()), key=lambda x: x[0], reverse=True)[:3]
    out = []
    for score, c in scored:
        if score < 0.5:
            continue
        ctx.match_scores[c.id] = max(score, ctx.match_scores.get(c.id, 0))
        out.append({"contact_id": c.id, "name": c.name, "kind": c.kind, "match": round(score, 2),
                    "owes": naira(max(ctx.store.account_balance("ar", c.id), 0))})
    return {"matches": out} if out else {"matches": [], "note": "nobody by that name; use new_contact_name"}


def record_entry(ctx, **a):
    try:
        p = Proposal(
            kind=a["kind"],
            items=[Item(i["product_id"], i["qty"], _kobo(i.get("unit_price_naira"))) for i in a.get("items") or []],
            contact_id=a.get("contact_id"),
            new_contact_name=a.get("new_contact_name"),
            amount_kobo=_kobo(a.get("amount_naira")),
            paid_kobo=_kobo(a.get("paid_naira")),
            payment_account=a.get("payment_account"),
            expense_account=a.get("expense_account"),
            due_date=date.fromisoformat(a["due_date"]) if a.get("due_date") else None,
            memo=a.get("memo", ""),
            evidence_id=ctx.evidence_id,
            unclear_amounts=[{"path": u["path"], "text": u["text"],
                              "candidates_kobo": [_kobo(c) for c in u["candidates_naira"]]}
                             for u in a.get("unclear_amounts") or []],
        )
    except (KeyError, TypeError, ValueError) as e:
        return {"status": "rejected", "errors": [f"bad arguments: {e}"]}

    # Code, not the model, decides whether money was unclear: every ambiguous
    # amount the normaliser found must be handed to the owner as a question.
    if ctx.message:
        listed = " ".join(u["text"].lower() for u in p.unclear_amounts)
        missing = [m.text for m in ctx.message.ambiguous if m.text.lower() not in listed]
        if missing:
            return {"status": "rejected", "errors": [
                f"the message has unclear amounts {missing}; list each in unclear_amounts with its candidates"]}

    ids = [i.product_id for i in p.items] + ([p.contact_id] if p.contact_id else [])
    p.match_scores = {i: ctx.match_scores[i] for i in ids if i in ctx.match_scores}
    unseen = [i for i in ids if i not in ctx.match_scores]
    if unseen:
        return {"status": "rejected", "errors": [f"look these up with find_product/find_contact first: {unseen}"]}
    return submit(p, ctx)


def recent_entries(ctx, limit=5):
    entries = ctx.store.list_entries()[-max(1, min(limit or 5, 20)):]
    out = []
    for e in reversed(entries):
        if e.kind in ("opening",):
            continue
        amount = max(e.total_debits, 0)
        who = ctx.store.get_contact(e.contact_id).name if e.contact_id else None
        out.append({"entry_id": e.id, "kind": e.kind, "date": str(e.entry_date), "memo": e.memo,
                    "who": who, "amount": naira(amount), "status": e.status})
    return {"entries": out}


def cancel_entry(ctx, entry_id, reason):
    e = ctx.store.get_entry(entry_id)
    if e is None:
        return {"error": "no such entry; use recent_entries"}
    if e.status != "committed" or e.kind == "reversal":
        return {"error": "that entry is already cancelled or is itself a cancellation"}
    a = ctx.store.add_approval("reversal", {"entry_id": entry_id, "reason": reason},
                               f"Cancel this entry? {e.memo or e.kind} ({naira(e.total_debits)}), "
                               f"{e.entry_date}. Reason: {reason}", [CONFIRM, {**CANCEL, "label": "Keep it"}])
    ctx.tell_owner(a["question"], buttons_for(a))
    return {"status": "waiting_for_owner"}


def report(ctx, kind, period="today"):
    data = reports.report(ctx.store, kind, period)
    return json.loads(json.dumps(data, default=str))


def ask_owner(ctx, question, options=None):
    options = (options or [])[:3]
    buttons = [{"id": f"q:{i}:{o[:20]}", "title": o[:20]} for i, o in enumerate(options)]
    ctx.tell_owner(question, buttons)
    return {"status": "asked"}


# --- customer handlers -----------------------------------------------------

def my_open_orders(ctx):
    return {"orders": [{"order_id": o["id"], "total": naira(o["total_kobo"]), "status": o["status"],
                        "items": [f"{i['qty']} x {ctx.store.get_product(i['product_id']).name}" for i in o["items"]]}
                       for o in ctx.store.open_orders(ctx.contact_id)]}


OWNER_HANDLERS = {
    "find_product": find_product, "find_contact": find_contact, "record_entry": record_entry,
    "recent_entries": recent_entries, "cancel_entry": cancel_entry, "report": report, "ask_owner": ask_owner,
}
CUSTOMER_HANDLERS = {
    "find_product": lambda ctx, query: find_product(ctx, query, customer=True),
    "create_order": lambda ctx, items: orders.create_order(ctx, items),
    "record_payment_claim": orders.record_payment_claim,
    "my_open_orders": my_open_orders,
}


def toolset(persona):
    return (OWNER_TOOLS, OWNER_HANDLERS) if persona == "owner" else (CUSTOMER_TOOLS, CUSTOMER_HANDLERS)
