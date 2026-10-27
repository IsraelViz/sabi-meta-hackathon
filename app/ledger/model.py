"""Plain data types for the ledger. No I/O here."""
from dataclasses import dataclass, field
from datetime import date, datetime

PAYMENT_ACCOUNTS = ("cash", "bank")
EXPENSE_ACCOUNTS = ("transport", "power", "other_exp")


@dataclass
class Product:
    id: str
    name: str
    base_unit: str
    sell_price_kobo: int
    aliases: list[str] = field(default_factory=list)
    min_price_kobo: int | None = None
    max_price_kobo: int | None = None
    avg_cost_kobo: int = 0
    reorder_level: int = 0
    countable: bool = True

    @property
    def price_floor(self):
        return self.min_price_kobo if self.min_price_kobo is not None else self.sell_price_kobo * 8 // 10

    @property
    def price_ceiling(self):
        return self.max_price_kobo if self.max_price_kobo is not None else self.sell_price_kobo * 12 // 10


@dataclass
class Contact:
    id: str
    name: str
    kind: str = "customer"
    phone: str | None = None
    aliases: list[str] = field(default_factory=list)
    ig_handle: str | None = None
    last_inbound_at: datetime | None = None   # last message from them (WhatsApp 24-hour window)


@dataclass
class Line:
    account: str
    debit_kobo: int = 0
    credit_kobo: int = 0
    product_id: str | None = None
    contact_id: str | None = None
    qty: int | None = None


@dataclass
class StockMove:
    product_id: str
    qty: int
    unit_cost_kobo: int


@dataclass
class Entry:
    kind: str
    lines: list[Line]
    memo: str = ""
    stock_moves: list[StockMove] = field(default_factory=list)
    contact_id: str | None = None
    due_date: date | None = None
    evidence_id: str | None = None
    reverses_id: str | None = None
    created_by: str = "agent:auto"
    entry_date: date | None = None
    # Set by the store on commit
    id: str | None = None
    seq: int | None = None
    status: str = "committed"

    @property
    def total_debits(self):
        return sum(l.debit_kobo for l in self.lines)

    @property
    def total_credits(self):
        return sum(l.credit_kobo for l in self.lines)


class LedgerError(Exception):
    """An entry broke a rule that must never be broken (unbalanced, edited, ...)."""


def check_balanced(entry):
    if not entry.lines:
        raise LedgerError("entry has no lines")
    for l in entry.lines:
        one_side = (l.debit_kobo > 0 and l.credit_kobo == 0) or (l.credit_kobo > 0 and l.debit_kobo == 0)
        if not one_side:
            raise LedgerError(f"line on {l.account} must be a debit or a credit, not both or zero")
    if entry.total_debits != entry.total_credits:
        raise LedgerError(f"unbalanced: debits {entry.total_debits} <> credits {entry.total_credits}")
