"""Shelf photo stock-take: the camera checks the books.

SAM 3.1 counts each product it can see; code compares that with stock in the
books (the sum of stock moves). A gap goes to the owner with buttons, and her
answer becomes an ordinary entry through the same validator. The books never
adjust themselves.
"""
from app.agent.approvals import buttons_for, submit
from app.ledger.proposals import Item, Proposal
from app.media.counting import make_counter


def tolerance(books_qty):
    """How far a count may be off before we ask: 1 unit, or 10% for big stacks."""
    return max(1, round(books_qty * 0.1))


def compare(books, counted, explicitly_asked):
    """Classify one product. Returns 'ok', 'missing', 'extra' or 'not_seen'."""
    if counted == 0 and books > 0 and not explicitly_asked:
        return "not_seen"   # probably just not in this photo
    diff = books - counted
    if abs(diff) <= tolerance(books):
        return "ok"
    return "missing" if diff > 0 else "extra"


def run(ctx, product_ids=None):
    if not ctx.image_data_url:
        return {"error": "no photo in this message; ask the owner to send a shelf photo"}
    products = [p for p in ctx.store.list_products() if p.countable]
    if product_ids:
        wanted = set(product_ids)
        products = [p for p in products if p.id in wanted]
        unknown = wanted - {p.id for p in products}
        if unknown:
            return {"error": f"unknown or uncountable products {sorted(unknown)}; use find_product"}
    if not products:
        return {"error": "no countable products"}

    counter = ctx.counter or make_counter(ctx.llm)
    counts = counter.count(ctx.image_data_url, {p.id: p.name for p in products})

    results = []
    for p in products:
        books = ctx.store.stock_on_hand(p.id)
        counted = counts.get(p.id, {}).get("count", 0)
        status = compare(books, counted, bool(product_ids))
        results.append({"product_id": p.id, "name": p.name, "unit": p.base_unit,
                        "books": books, "counted": counted, "status": status})
        if status == "missing":
            gap = books - counted
            a = ctx.store.add_approval(
                "stock_gap", {"product_id": p.id, "qty": gap, "books": books, "counted": counted, "evidence_id": ctx.evidence_id},
                f"Books say {books} {p.base_unit} of {p.name}; I count {counted} in the photo. "
                f"{gap} missing — what happened?",
                [{"label": "Sold, not recorded", "patch": [], "action": "sold"},
                 {"label": "Damaged or lost", "patch": [], "action": "lost"},
                 {"label": "Recount", "patch": [], "action": "recount"}])
            ctx.tell_owner(a["question"], buttons_for(a))
        elif status == "extra":
            extra = counted - books
            a = ctx.store.add_approval(
                "stock_gap", {"product_id": p.id, "qty": extra, "books": books, "counted": counted, "evidence_id": ctx.evidence_id},
                f"I count {counted} {p.base_unit} of {p.name}, but the books say {books}. "
                f"Did {extra} new come in that I haven't recorded?",
                [{"label": "Yes, record it", "patch": [], "action": "purchase"},
                 {"label": "Recount", "patch": [], "action": "recount"}])
            ctx.tell_owner(a["question"], buttons_for(a))

    ctx.store.set_evidence_extracted(ctx.evidence_id, {"stocktake": results, "counter": counter.name})
    flagged = [r for r in results if r["status"] in ("missing", "extra")]
    return {"counter": counter.name, "results": results,
            "status": "waiting_for_owner" if flagged else "all_match",
            "note": "Counts only what is visible in the photo."}


def settle_gap(payload, option, ctx):
    pid, qty = payload["product_id"], payload["qty"]
    action = option.get("action")
    if action == "recount":
        ctx.say("OK, nothing changed. Send another photo when you're ready.")
        return
    photo = payload.get("evidence_id")  # the shelf photo is the receipt for this entry
    memo = f"Stock-take: books {payload['books']}, counted {payload['counted']}"
    if action == "sold":
        p = Proposal(kind="sale", items=[Item(pid, qty)], memo=memo + " (unrecorded sale)", evidence_id=photo)
    elif action == "lost":
        p = Proposal(kind="stock_loss", items=[Item(pid, qty)], memo=memo + " (damaged or lost)",
                     evidence_id=photo)
    elif action == "purchase":
        p = Proposal(kind="purchase", items=[Item(pid, qty)], memo=memo + " (unrecorded delivery)",
                     evidence_id=photo)
    else:
        ctx.say("I didn't understand that answer.")
        return
    result = submit(p, ctx, approved=True)
    if result["status"] == "committed":
        ctx.say(f"Done. {result['summary']}.")
    elif result["status"] == "rejected":
        ctx.say("I couldn't record it: " + "; ".join(result["errors"]))
