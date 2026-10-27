"""Customer orders. A customer can never mark an order paid: a transfer
screenshot only makes it `claimed_paid`, and the owner confirms she saw the
bank alert. Fake screenshots are a common scam."""
from app.agent.approvals import CANCEL, CONFIRM, buttons_for, submit
from app.ledger.proposals import Item, Proposal
from app.normalize.amounts import naira


def create_order(ctx, items):
    """items: [{"product_id", "qty"}]. Prices always come from the books, never from the chat."""
    lines, total = [], 0
    for it in items:
        prod = ctx.store.get_product(it.get("product_id", ""))
        qty = it.get("qty")
        if prod is None:
            return {"error": f"unknown product {it.get('product_id')}"}
        if not isinstance(qty, int) or qty <= 0 or qty > 100:
            return {"error": "qty must be a whole number from 1 to 100"}
        available = ctx.store.stock_on_hand(prod.id) - ctx.store.reserved(prod.id)
        if qty > available:
            return {"error": f"only {max(available, 0)} {prod.base_unit} of {prod.name} available"}
        lines.append({"product_id": prod.id, "qty": qty, "unit_price_kobo": prod.sell_price_kobo})
        total += qty * prod.sell_price_kobo
    order = ctx.store.add_order(ctx.contact_id, lines, total)
    pay = getattr(ctx.store, "business", {}).get("payment_details", "Ask the shop for payment details.")
    return {"order_id": order["id"], "total": naira(total), "pay_to": pay,
            "items": [f"{l['qty']} x {ctx.store.get_product(l['product_id']).name}" for l in lines],
            "note": "Reserved for 24 hours."}


def record_payment_claim(ctx, order_id, amount_naira=None, reference=None):
    order = ctx.store.get_order(order_id)
    if order is None or order["contact_id"] != ctx.contact_id:
        return {"error": "no such order for this customer"}
    if order["status"] != "open":
        return {"error": f"order is {order['status']}"}
    ctx.store.update_order(order_id, status="claimed_paid", claim_evidence_id=ctx.evidence_id)
    who = ctx.store.get_contact(ctx.contact_id).name
    claimed = naira(round(float(amount_naira) * 100)) if amount_naira is not None else "an amount"
    mismatch = ""
    if amount_naira is not None and round(float(amount_naira) * 100) != order["total_kobo"]:
        mismatch = f" ⚠ The order total is {naira(order['total_kobo'])}."
    a = ctx.store.add_approval("order_paid", {"order_id": order_id},
                               f"{who} says they paid {claimed}" + (f", ref {reference}" if reference else "")
                               + f" for order {order_id[:6]}.{mismatch} Did you see the alert?",
                               [{**CONFIRM, "label": "Yes, I saw it"}, {**CANCEL, "label": "No alert"}])
    ctx.tell_owner(a["question"], buttons_for(a))
    return {"status": "waiting_for_owner",
            "tell_customer": "Thanks! The shop will confirm once the transfer shows up."}


def settle_order(order_id, confirmed, ctx):
    order = ctx.store.get_order(order_id)
    customer = ctx.store.get_contact(order["contact_id"])
    if not confirmed:
        ctx.store.update_order(order_id, status="open")
        ctx.say(f"OK, I won't mark it paid. I'll leave {customer.name}'s order open.")
        return
    # Release the reservation first so the sale's stock check sees the stock.
    ctx.store.update_order(order_id, status="paid")
    p = Proposal(kind="sale", contact_id=customer.id, paid_kobo=order["total_kobo"], payment_account="bank",
                 items=[Item(i["product_id"], i["qty"], i["unit_price_kobo"]) for i in order["items"]],
                 memo=f"Order {order_id[:6]}", evidence_id=order["claim_evidence_id"])
    result = submit(p, ctx, approved=True)
    if result["status"] == "committed":
        ctx.store.update_order(order_id, entry_id=result["entry_id"])
        ctx.say(f"Recorded: {result['summary']}.")
        if customer.phone:
            ctx.say("Payment confirmed. Thank you! Your order is being prepared.", to=customer.phone)
    else:
        ctx.store.update_order(order_id, status="claimed_paid")
        if result["status"] == "rejected":
            ctx.say("I couldn't record it: " + "; ".join(result["errors"]))
