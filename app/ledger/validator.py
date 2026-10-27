"""Checks a proposal against the books before anything is saved.

Three kinds of finding:
- errors: the proposal is malformed; the model gets them back and must fix it.
- questions: only the owner can settle it (an unclear amount, a price outside
  her range, not enough stock). Nothing posts until she answers.
- approvals: the proposal is fine but needs her tap (big amount, new customer,
  unsure name match).
Only a proposal with none of the three posts automatically.
"""
from dataclasses import dataclass, field

from app.ledger.model import EXPENSE_ACCOUNTS, PAYMENT_ACCOUNTS
from app.ledger.proposals import KINDS, Proposal
from app.normalize.amounts import naira

MATCH_THRESHOLD = 0.85


@dataclass
class Question:
    text: str
    options: list[dict] = field(default_factory=list)   # [{"label": str, "patch": [{"path", "value"}]}]


@dataclass
class Verdict:
    errors: list[str] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def auto_ok(self):
        return not (self.errors or self.questions or self.approvals)

    def to_json(self):
        return {
            "errors": self.errors,
            "questions": [{"text": q.text, "options": [o["label"] for o in q.options]} for q in self.questions],
            "needs_owner_confirmation": self.approvals,
            "notes": self.notes,
        }


def _opt(label, *patches):
    return {"label": label[:20], "patch": [{"path": p, "value": v} for p, v in patches]}


def validate(p: Proposal, store, auto_post_limit_kobo) -> Verdict:
    v = Verdict()
    if p.cancel:
        v.errors.append("cancelled by the owner")
        return v
    if p.kind not in KINDS:
        v.errors.append(f"kind must be one of {KINDS}")
        return v

    _check_unclear(p, v)
    _check_contact(p, store, v)
    {
        "sale": _check_sale, "purchase": _check_purchase, "payment_in": _check_payment,
        "payment_out": _check_payment, "expense": _check_expense, "drawing": _check_drawing,
        "stock_loss": _check_stock_loss,
    }[p.kind](p, store, v)

    for what, score in p.match_scores.items():
        if score < MATCH_THRESHOLD:
            v.approvals.append(f"not sure about {what} (match {score:.2f})")
    amount = p.items_total_kobo if p.items else (p.amount_kobo or 0)
    if amount > auto_post_limit_kobo:
        v.approvals.append(f"amount {naira(amount)} is above the auto-post limit")
    return v


def _check_unclear(p, v):
    for i, u in enumerate(p.unclear_amounts):
        cands = u.get("candidates_kobo") or []
        path = u.get("path")
        if not path or len(cands) < 2:
            v.errors.append(f"unclear_amounts[{i}] needs a 'path' and at least two candidates")
            continue
        v.questions.append(Question(
            f"I heard \"{u.get('text', '?')}\". Which amount did you mean?",
            [_opt(naira(c), (path, c), (f"unclear_amounts.{i}", None)) for c in cands[:2]] + [_opt("Other amount")],
        ))


def _check_contact(p, store, v):
    if p.contact_id and store.get_contact(p.contact_id) is None:
        v.errors.append(f"contact {p.contact_id} does not exist; use find_contact")
    if p.new_contact_name:
        v.approvals.append(f"new contact \"{p.new_contact_name}\"")


def _has_contact(p):
    return bool(p.contact_id or p.new_contact_name)


def _check_items(p, store, v, need_price):
    if not p.items:
        v.errors.append("items is empty")
        return []
    products = []
    for i, it in enumerate(p.items):
        prod = store.get_product(it.product_id)
        if prod is None:
            v.errors.append(f"items[{i}]: unknown product {it.product_id}; use get_stock to find it")
            continue
        if not isinstance(it.qty, int) or it.qty <= 0:
            v.errors.append(f"items[{i}]: qty must be a whole number above 0")
        if need_price and it.unit_price_kobo is None:
            v.questions.append(Question(f"How much per {prod.base_unit} for {prod.name}?", [_opt("Other amount")]))
        products.append((i, it, prod))
    return products


def _check_stock(store, v, i, it, prod):
    available = store.stock_on_hand(prod.id) - store.reserved(prod.id)
    if it.qty > available:
        v.questions.append(Question(
            f"The books show only {available} {prod.base_unit} of {prod.name}, but this is {it.qty}. "
            f"Did new stock come in that I haven't recorded?",
            ([_opt(f"Only {available}", (f"items.{i}.qty", available))] if available > 0 else [])
            + [_opt("Cancel", ("cancel", True))],
        ))


def _check_paid(p, v, total):
    if p.paid_kobo is None:
        v.questions.append(Question(
            f"Has this {naira(total)} been paid?",
            [_opt("Paid cash", ("paid_kobo", total), ("payment_account", "cash")),
             _opt("Paid transfer", ("paid_kobo", total), ("payment_account", "bank")),
             _opt("Not yet (owes)", ("paid_kobo", 0))],
        ))
        return
    if p.paid_kobo < 0 or p.paid_kobo > total:
        v.errors.append(f"paid_kobo must be between 0 and the total {total}")
    if p.paid_kobo > 0 and p.payment_account not in PAYMENT_ACCOUNTS:
        v.questions.append(Question(
            f"Was the {naira(p.paid_kobo)} cash or transfer?",
            [_opt("Cash", ("payment_account", "cash")), _opt("Transfer", ("payment_account", "bank"))],
        ))


def _check_sale(p, store, v):
    rows = _check_items(p, store, v, need_price=False)
    for i, it, prod in rows:
        if it.unit_price_kobo is None:
            it.unit_price_kobo = prod.sell_price_kobo
            v.notes.append(f"{prod.name}: no price said, used list price {naira(prod.sell_price_kobo)}")
        elif not it.price_confirmed and not prod.price_floor <= it.unit_price_kobo <= prod.price_ceiling:
            v.questions.append(Question(
                f"{prod.name} at {naira(it.unit_price_kobo)} per {prod.base_unit}? "
                f"Your usual price is {naira(prod.sell_price_kobo)}.",
                [_opt(f"Yes, {naira(it.unit_price_kobo)}", (f"items.{i}.price_confirmed", True)),
                 _opt(f"No, {naira(prod.sell_price_kobo)}", (f"items.{i}.unit_price_kobo", prod.sell_price_kobo)),
                 _opt("Other amount")],
            ))
        _check_stock(store, v, i, it, prod)
    if v.errors:
        return
    total = p.items_total_kobo
    _check_paid(p, v, total)
    if p.paid_kobo is not None and p.paid_kobo < total and not _has_contact(p):
        v.questions.append(Question(f"Who owes the {naira(total - p.paid_kobo)} balance?", []))


def _check_purchase(p, store, v):
    _check_items(p, store, v, need_price=True)
    if v.errors or any(it.unit_price_kobo is None for it in p.items):
        return
    total = p.items_total_kobo
    _check_paid(p, v, total)
    if p.paid_kobo is not None and p.paid_kobo < total and not _has_contact(p):
        v.questions.append(Question(f"Which supplier do you owe the {naira(total - p.paid_kobo)} balance?", []))


def _check_amount(p, v, what):
    if p.amount_kobo is None:
        v.questions.append(Question(f"How much was the {what}?", [_opt("Other amount")]))
        return False
    if p.amount_kobo <= 0:
        v.errors.append("amount_kobo must be above 0")
        return False
    return True


def _check_account(p, v, what):
    if p.payment_account not in PAYMENT_ACCOUNTS:
        v.questions.append(Question(
            f"Was the {what} cash or transfer?",
            [_opt("Cash", ("payment_account", "cash")), _opt("Transfer", ("payment_account", "bank"))],
        ))


def _check_payment(p, store, v):
    incoming = p.kind == "payment_in"
    what = "payment received" if incoming else "payment to the supplier"
    if not p.contact_id:
        v.errors.append(f"{p.kind} needs contact_id (who paid / who was paid); use find_contact")
        return
    if not _check_amount(p, v, what):
        return
    _check_account(p, v, what)
    owed = store.account_balance("ar", p.contact_id) if incoming else -store.account_balance("ap", p.contact_id)
    if p.amount_kobo > owed and not p.overpayment_ok:
        name = store.get_contact(p.contact_id).name
        v.questions.append(Question(
            f"{name} {'owes' if incoming else 'is owed'} only {naira(max(owed, 0))}, but this is {naira(p.amount_kobo)}. "
            f"Record it anyway?",
            [_opt("Record anyway", ("overpayment_ok", True)),
             _opt(f"Record {naira(max(owed, 0))}", ("amount_kobo", max(owed, 0))) if owed > 0 else _opt("Cancel", ("cancel", True))],
        ))


def _check_expense(p, store, v):
    if p.expense_account not in EXPENSE_ACCOUNTS:
        v.errors.append(f"expense_account must be one of {EXPENSE_ACCOUNTS}")
    if _check_amount(p, v, "expense"):
        _check_account(p, v, "expense")


def _check_drawing(p, store, v):
    if _check_amount(p, v, "money you took"):
        _check_account(p, v, "money you took")


def _check_stock_loss(p, store, v):
    for i, it, prod in _check_items(p, store, v, need_price=False):
        _check_stock(store, v, i, it, prod)
    v.approvals.append("stock losses always need your confirmation")
