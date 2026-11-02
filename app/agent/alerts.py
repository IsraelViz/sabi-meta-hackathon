"""Sabi speaks first: after a sale that leaves a product about to run out, it
says so, with the pace she is selling at, who she last bought from and at what
price, and how many to order for about a week. One tap drafts the order
message for the supplier. Never more than one alert per product a day.
"""
from datetime import timedelta

from app.agent.approvals import buttons_for
from app.ledger.reports import restock_advice
from app.ledger.store import now
from app.normalize.amounts import naira

QUIET_FOR = timedelta(hours=24)


def after_sale(ctx, entry):
    recent = {a["payload"].get("product_id") for a in ctx.store.recent_approvals("restock", now() - QUIET_FOR)}
    for pid in {l.product_id for l in entry.lines if l.account == "sales" and l.product_id}:
        if pid in recent:
            continue
        r = restock_advice(ctx.store, pid)
        if not r["due"]:
            continue
        pace = f" At your pace ({r['per_day']:g} a day) that's about {r['days_left']:g} days." if r["days_left"] else ""
        last = r["last_purchase"]
        source = (f" Last time you bought from {last['supplier']} at {naira(last['unit_cost_kobo'])}."
                  if last and last["supplier"] else
                  f" Last time you paid {naira(last['unit_cost_kobo'])}." if last else "")
        text = (f"📦 {r['name']}: only {r['stock']} {r['unit']}{'s' if r['stock'] != 1 else ''} left.{pace}{source}"
                f" Order {r['order_qty']}?")
        a = ctx.store.add_approval("restock", {"product_id": pid, "qty": r["order_qty"],
                                               "supplier_id": last["supplier_id"] if last else None},
                                   text, [{"label": f"Draft order ({r['order_qty']})", "action": "draft"},
                                          {"label": "Not now", "action": "skip"}])
        ctx.tell_owner(text, buttons_for(a))


def settle_restock(a, option, ctx):
    if option.get("action") != "draft":
        ctx.say("OK. I'll flag it again tomorrow if it's still low.")
        return
    p = ctx.store.get_product(a["payload"]["product_id"])
    qty = a["payload"]["qty"]
    supplier = ctx.store.get_contact(a["payload"]["supplier_id"]) if a["payload"].get("supplier_id") else None
    last = restock_advice(ctx.store, p.id)["last_purchase"]
    price = f" Same price as last time, {naira(last['unit_cost_kobo'])}?" if last else " How much per one?"
    message = (f"Good day{' ' + supplier.name if supplier else ''}. Abeg I need {qty} {p.name}.{price}"
               " When you fit deliver?")
    ctx.say(f"Here's the order message{' for ' + supplier.name if supplier else ''}. Forward it:\n\n{message}\n\n"
            "When the goods arrive, tell me what you paid and I'll record the purchase.")
