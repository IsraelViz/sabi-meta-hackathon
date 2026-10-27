"""What the agent is allowed to propose.

The model never writes journal lines. It proposes a business event in these
shapes; validator.py checks it and posting.py turns it into balanced lines.
"""
from dataclasses import asdict, dataclass, field
from datetime import date

KINDS = ("sale", "purchase", "payment_in", "payment_out", "expense", "drawing", "stock_loss")


@dataclass
class Item:
    product_id: str
    qty: int
    unit_price_kobo: int | None = None   # sale price, or purchase cost per unit
    price_confirmed: bool = False        # owner said yes to a price outside her usual range


@dataclass
class Proposal:
    kind: str
    items: list[Item] = field(default_factory=list)
    contact_id: str | None = None
    new_contact_name: str | None = None      # a person not yet in the books
    amount_kobo: int | None = None           # payment / expense / drawing amount
    paid_kobo: int | None = None             # sale or purchase: how much was paid now (None = not said)
    payment_account: str | None = None       # 'cash' | 'bank'
    expense_account: str | None = None
    due_date: date | None = None
    memo: str = ""
    evidence_id: str | None = None
    # Amounts the normaliser could not pin down, with their candidates in kobo
    unclear_amounts: list[dict] = field(default_factory=list)
    # How sure the matcher was about the product and contact names (filled by tools)
    match_scores: dict = field(default_factory=dict)
    entry_date: date | None = None           # None = today; set for back-dated history (demo seed, imports)
    overpayment_ok: bool = False             # owner confirmed a payment above what is owed
    cancel: bool = False                     # owner tapped Cancel

    @property
    def items_total_kobo(self):
        return sum(i.qty * (i.unit_price_kobo or 0) for i in self.items)

    def to_json(self):
        d = asdict(self)
        d["due_date"] = self.due_date.isoformat() if self.due_date else None
        d["entry_date"] = self.entry_date.isoformat() if self.entry_date else None
        return d

    @classmethod
    def from_json(cls, d):
        d = dict(d)
        d["items"] = [Item(**i) for i in d.get("items", [])]
        for key in ("due_date", "entry_date"):
            if d.get(key):
                d[key] = date.fromisoformat(d[key])
        return cls(**d)


def apply_patch(proposal: Proposal, patch: list[dict]) -> Proposal:
    """Apply an owner's answer, e.g. [{"path": "items.0.unit_price_kobo", "value": 250000}].

    A value of None on a list element removes it (used to clear a settled unclear amount).
    """
    d = proposal.to_json()
    for step in patch:
        *parents, last = step["path"].split(".")
        target = d
        for key in parents:
            target = target[int(key)] if isinstance(target, list) else target[key]
        if isinstance(target, list):
            target[int(last)] = step["value"]
        else:
            target[last] = step["value"]
    d["unclear_amounts"] = [u for u in d["unclear_amounts"] if u is not None]
    return Proposal.from_json(d)
