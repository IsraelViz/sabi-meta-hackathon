"""Turn a validated proposal into balanced journal lines, and commit it.

All the accounting arithmetic lives here, in code: the model never computes
cost of goods sold, balances or average cost.
"""
from app.ledger.model import Contact, Entry, LedgerError, Line, StockMove
from app.ledger.proposals import Proposal
from app.ledger.store import new_id
from app.ledger.validator import validate


def _ensure_contact(p: Proposal, store, kind):
    if p.contact_id:
        return p.contact_id
    if p.new_contact_name:
        c = store.add_contact(Contact(id=new_id(), name=p.new_contact_name, kind=kind))
        return c.id
    return None


def _settlement_lines(paid, total, payment_account, balance_account, contact_id, side):
    """Paid part to cash/bank, the rest to AR/AP. `side` is 'debit' for sales, 'credit' for purchases."""
    lines = []
    if paid:
        lines.append(Line(payment_account, **{f"{side}_kobo": paid}))
    if total - paid:
        lines.append(Line(balance_account, contact_id=contact_id, **{f"{side}_kobo": total - paid}))
    return lines


def build_entry(p: Proposal, store, created_by) -> Entry:
    kind = p.kind
    lines, moves = [], []
    contact_id = None

    if kind == "sale":
        contact_id = _ensure_contact(p, store, "customer")
        total = p.items_total_kobo
        lines += _settlement_lines(p.paid_kobo, total, p.payment_account, "ar", contact_id, "debit")
        for it in p.items:
            prod = store.get_product(it.product_id)
            lines.append(Line("sales", credit_kobo=it.qty * it.unit_price_kobo,
                              product_id=prod.id, contact_id=contact_id, qty=it.qty))
            cost = it.qty * prod.avg_cost_kobo
            if cost:
                lines.append(Line("cogs", debit_kobo=cost, product_id=prod.id, qty=it.qty))
                lines.append(Line("inventory", credit_kobo=cost, product_id=prod.id, qty=it.qty))
            moves.append(StockMove(prod.id, -it.qty, prod.avg_cost_kobo))

    elif kind == "purchase":
        contact_id = _ensure_contact(p, store, "supplier")
        total = p.items_total_kobo
        for it in p.items:
            lines.append(Line("inventory", debit_kobo=it.qty * it.unit_price_kobo,
                              product_id=it.product_id, qty=it.qty))
            moves.append(StockMove(it.product_id, it.qty, it.unit_price_kobo))
        lines += _settlement_lines(p.paid_kobo, total, p.payment_account, "ap", contact_id, "credit")

    elif kind == "payment_in":
        contact_id = p.contact_id
        lines += [Line(p.payment_account, debit_kobo=p.amount_kobo),
                  Line("ar", credit_kobo=p.amount_kobo, contact_id=contact_id)]

    elif kind == "payment_out":
        contact_id = p.contact_id
        lines += [Line("ap", debit_kobo=p.amount_kobo, contact_id=contact_id),
                  Line(p.payment_account, credit_kobo=p.amount_kobo)]

    elif kind == "expense":
        lines += [Line(p.expense_account, debit_kobo=p.amount_kobo),
                  Line(p.payment_account, credit_kobo=p.amount_kobo)]

    elif kind == "drawing":
        lines += [Line("drawings", debit_kobo=p.amount_kobo),
                  Line(p.payment_account, credit_kobo=p.amount_kobo)]

    elif kind == "stock_loss":
        for it in p.items:
            prod = store.get_product(it.product_id)
            cost = it.qty * prod.avg_cost_kobo
            if cost:
                lines.append(Line("stock_loss", debit_kobo=cost, product_id=prod.id, qty=it.qty))
                lines.append(Line("inventory", credit_kobo=cost, product_id=prod.id, qty=it.qty))
            moves.append(StockMove(prod.id, -it.qty, prod.avg_cost_kobo))
        if not lines:
            raise LedgerError("stock loss has no cost to post; record the purchase cost first")

    else:
        raise LedgerError(f"cannot post kind {kind!r}")

    return Entry(kind=kind, lines=lines, stock_moves=moves, memo=p.memo, contact_id=contact_id,
                 due_date=p.due_date, evidence_id=p.evidence_id, created_by=created_by)


def commit_proposal(p: Proposal, store, auto_post_limit_kobo, approved=False):
    """Validate and commit. Returns (entry | None, verdict).

    `approved=True` means the owner tapped Confirm: approvals are satisfied,
    but errors and open questions still block.
    """
    verdict = validate(p, store, auto_post_limit_kobo)
    blocked = verdict.errors or verdict.questions or (verdict.approvals and not approved)
    if blocked:
        return None, verdict
    entry = build_entry(p, store, "agent:approved" if approved else "agent:auto")
    return store.commit(entry), verdict


def reverse(entry_id, store, reason, created_by="agent:approved"):
    """Cancel an entry with its exact mirror image. The original stays in the books."""
    original = store.get_entry(entry_id)
    if original is None:
        raise LedgerError("no such entry")
    lines = [Line(l.account, debit_kobo=l.credit_kobo, credit_kobo=l.debit_kobo,
                  product_id=l.product_id, contact_id=l.contact_id, qty=l.qty) for l in original.lines]
    moves = [StockMove(m.product_id, -m.qty, m.unit_cost_kobo) for m in original.stock_moves]
    for m in moves:
        if m.qty < 0 and store.stock_on_hand(m.product_id) + m.qty < 0:
            prod = store.get_product(m.product_id)
            raise LedgerError(f"cannot reverse: {prod.name} stock would go below zero")
    return store.commit(Entry(kind="reversal", lines=lines, stock_moves=moves,
                              memo=f"Reverses: {original.memo} ({reason})".strip(),
                              contact_id=original.contact_id, reverses_id=original.id,
                              evidence_id=original.evidence_id, created_by=created_by))
