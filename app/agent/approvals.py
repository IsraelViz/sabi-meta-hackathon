"""Owner confirmations. Answers are handled by code, not the model: a button
tap applies a fixed patch to the stored proposal, which is validated again
before anything is committed.

Button ids look like "ap:<approval_id>:<option_index>".
"""
from app.ledger.posting import commit_proposal, reverse
from app.ledger.model import LedgerError
from app.ledger.proposals import Proposal, apply_patch
from app.normalize.amounts import find_amounts, naira
from app.normalize.fuzzy import best_match, name_similarity

CONFIRM = {"label": "Confirm", "patch": [], "confirm": True}
CANCEL = {"label": "Cancel", "patch": [{"path": "cancel", "value": True}]}


def buttons_for(approval):
    return [{"id": f"ap:{approval['id']}:{i}", "title": o["label"][:20]}
            for i, o in enumerate(approval["options"][:3])]


def describe(p: Proposal, store):
    """One line a trader can check at a glance."""
    who = store.get_contact(p.contact_id).name if p.contact_id else p.new_contact_name
    if p.items:
        parts = []
        for it in p.items:
            prod = store.get_product(it.product_id)
            name = prod.name if prod else it.product_id
            unit = prod.base_unit if prod else ""
            price = f" @ {naira(it.unit_price_kobo)}" if it.unit_price_kobo is not None else ""
            parts.append(f"{it.qty} {unit} {name}{price}")
        text = ", ".join(parts)
        total = f", total {naira(p.items_total_kobo)}" if all(i.unit_price_kobo is not None for i in p.items) else ""
    else:
        text = naira(p.amount_kobo) if p.amount_kobo is not None else "amount not set"
        total = ""
    label = {"sale": "Sale", "purchase": "Purchase", "payment_in": "Payment from", "payment_out": "Payment to",
             "expense": f"Expense ({p.expense_account})", "drawing": "You took", "stock_loss": "Stock loss"}[p.kind]
    if p.kind in ("payment_in", "payment_out"):
        return f"{label} {who}: {text}"
    s = f"{label}: {text}{total}"
    if who:
        s += f" — {who}"
    if p.kind in ("sale", "purchase") and p.paid_kobo is not None:
        s += ", paid" if p.paid_kobo == p.items_total_kobo else (", on credit" if p.paid_kobo == 0 else f", paid {naira(p.paid_kobo)}")
        if p.due_date and p.paid_kobo < p.items_total_kobo:
            s += f", due {p.due_date:%a %d %b}"
    return s


def submit(p: Proposal, ctx, approved=False):
    """Validate and commit, or park it and ask the owner. Returns a dict for the model."""
    try:
        entry, verdict = commit_proposal(p, ctx.store, ctx.auto_post_limit_kobo, approved=approved)
    except LedgerError as e:
        return {"status": "rejected", "errors": [str(e)]}
    if entry:
        ctx.committed.append(entry)
        return {"status": "committed", "entry_id": entry.id, "summary": describe(p, ctx.store),
                "notes": verdict.notes, **_stock_after(p, ctx.store)}
    if verdict.errors:
        return {"status": "rejected", "errors": verdict.errors}
    if verdict.questions:
        q = verdict.questions[0]
        a = ctx.store.add_approval("proposal", {"proposal": p.to_json(), "text_path": q.text_path,
                                                "approved": approved}, q.text, q.options)
        ctx.tell_owner(q.text, buttons_for(a))
        return {"status": "waiting_for_owner", "question": q.text}
    a = ctx.store.add_approval("proposal", {"proposal": p.to_json(), "approved": False},
                               f"{describe(p, ctx.store)}. Record it?", [CONFIRM, CANCEL])
    ctx.tell_owner(a["question"] + "\n(" + "; ".join(verdict.approvals) + ")", buttons_for(a))
    return {"status": "waiting_for_owner", "reason": verdict.approvals}


def _stock_after(p, store):
    if p.kind not in ("sale", "purchase", "stock_loss"):
        return {}
    return {"stock_now": {store.get_product(i.product_id).name: store.stock_on_hand(i.product_id) for i in p.items}}


def handle_button(button_id, ctx):
    """Owner tapped a reply button. Returns True if it was ours."""
    try:
        _, aid, idx = button_id.split(":")
        idx = int(idx)
    except ValueError:
        return False
    a = ctx.store.get_approval(aid)
    if a is None or a["status"] != "pending":
        ctx.say("That question has already been answered.")
        return True
    option = a["options"][idx]

    if a["kind"] == "proposal":
        if option["label"] == "Other amount":
            ctx.store.update_approval_payload(aid, {**a["payload"], "awaiting_text": True})
            ctx.say("Type the amount, e.g. 12500 or 12.5k.")
            return True
        ctx.store.answer_approval(aid, "approved", option["label"])
        p = apply_patch(Proposal.from_json(a["payload"]["proposal"]), option["patch"])
        if p.cancel:
            ctx.say("Cancelled. Nothing was recorded.")
            return True
        approved = option.get("confirm", False) or a["payload"].get("approved", False)
        _report(submit(p, ctx, approved=approved), ctx)
        return True

    if a["kind"] == "reversal":
        ctx.store.answer_approval(aid, "approved" if option.get("confirm") else "rejected", option["label"])
        if option.get("confirm"):
            try:
                reverse(a["payload"]["entry_id"], ctx.store, a["payload"].get("reason", ""))
                ctx.say("Done. That entry is cancelled; the original stays in the history.")
            except LedgerError as e:
                ctx.say(f"I couldn't cancel it: {e}")
        else:
            ctx.say("OK, left as it was.")
        return True

    if a["kind"] == "order_paid":
        from app.agent.orders import settle_order
        ctx.store.answer_approval(aid, "approved" if option.get("confirm") else "rejected", option["label"])
        settle_order(a["payload"]["order_id"], option.get("confirm", False), ctx)
        return True

    return False


def handle_typed_answer(text, ctx):
    """If the owner owes us a typed answer (an amount or a name), apply it. Returns True if handled."""
    waiting = [a for a in ctx.store.pending_approvals()
               if a["kind"] == "proposal" and (a["payload"].get("awaiting_text") or
                                               (a["payload"].get("text_path") and not a["options"]))]
    if not waiting:
        return False
    a = max(waiting, key=lambda x: x["created_at"])
    path = a["payload"]["text_path"]
    p = Proposal.from_json(a["payload"]["proposal"])
    if path == "contact":
        scored = best_match(text, ctx.store.list_contacts(), lambda c: [c.name, *c.aliases])
        if scored and scored[0][0] >= 0.85:
            patch = [{"path": "contact_id", "value": scored[0][1].id}]
        else:
            patch = [{"path": "new_contact_name", "value": text.strip().title()}]
    else:
        amounts = [m for m in find_amounts(text) if not m.ambiguous]
        if not amounts:
            digits = text.replace(",", "").strip().lstrip("₦Nn")
            if not digits.replace(".", "", 1).isdigit():
                ctx.say("I need just the amount, e.g. 12500 or 12.5k.")
                return True
            value = round(float(digits) * 100)
        else:
            value = amounts[0].candidates_kobo[0]
        patch = [{"path": path, "value": value}]
        if path.startswith("items.") and path.endswith("unit_price_kobo"):
            # She typed it herself, so a price outside the usual range is deliberate.
            patch.append({"path": path.replace("unit_price_kobo", "price_confirmed"), "value": True})
        patch += [{"path": f"unclear_amounts.{i}", "value": None} for i, u in enumerate(p.unclear_amounts)
                  if u.get("path") == path]
    ctx.store.answer_approval(a["id"], "approved", text)
    _report(submit(apply_patch(p, patch), ctx, approved=a["payload"].get("approved", False)), ctx)
    return True


def _report(result, ctx):
    if result["status"] == "committed":
        stock = result.get("stock_now") or {}
        left = "; ".join(f"{n}: {q} left" for n, q in stock.items())
        ctx.say(f"Done. {result['summary']}." + (f" {left}." if left else ""))
    elif result["status"] == "rejected":
        ctx.say("I couldn't record that: " + "; ".join(result["errors"]))
    # waiting_for_owner: the question was already sent by submit()
