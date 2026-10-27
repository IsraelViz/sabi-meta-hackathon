"""Where the books live.

`MemoryStore` enforces the same rules as the Postgres triggers in schema.sql
(balanced, append-only, reverse-once), so tests and local development behave
like production. `PostgresStore` lives in pg_store.py.
"""
import copy
import itertools
import uuid
from datetime import date, datetime, timedelta, timezone

from app.ledger.model import Contact, Entry, LedgerError, Product, check_balanced


def new_id():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc)


def next_avg_cost(on_hand, avg_cost, qty, unit_cost):
    """Moving average after a stock move. Sales out at average cost leave it unchanged."""
    new_on_hand = on_hand + qty
    if new_on_hand <= 0:
        return avg_cost if new_on_hand == 0 and qty < 0 else unit_cost
    return (on_hand * avg_cost + qty * unit_cost) // new_on_hand


class MemoryStore:
    def __init__(self, accounts):
        self.accounts = {a["code"]: a for a in accounts}
        self.business: dict = {}
        self.products: dict[str, Product] = {}
        self.contacts: dict[str, Contact] = {}
        self.entries: dict[str, Entry] = {}
        self.evidence: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.approvals: dict[str, dict] = {}
        self.runs: list[dict] = []
        self.posts: dict[str, dict] = {}
        self._seq = itertools.count(1)

    # --- catalogue -------------------------------------------------------
    def add_product(self, p: Product):
        self.products[p.id] = p
        return p

    def get_product(self, pid) -> Product | None:
        return self.products.get(pid)

    def list_products(self):
        return list(self.products.values())

    def update_product_price(self, pid, sell_price_kobo):
        self.products[pid].sell_price_kobo = sell_price_kobo

    def add_contact(self, c: Contact):
        self.contacts[c.id] = c
        return c

    def get_contact(self, cid) -> Contact | None:
        return self.contacts.get(cid)

    def list_contacts(self):
        return list(self.contacts.values())

    def contact_by_phone(self, phone):
        return next((c for c in self.contacts.values() if c.phone == phone), None)

    # --- ledger ----------------------------------------------------------
    def commit(self, entry: Entry) -> Entry:
        """Atomically save an entry, its stock moves, and the average-cost cache."""
        check_balanced(entry)
        for l in entry.lines:
            if l.account not in self.accounts:
                raise LedgerError(f"unknown account {l.account!r}")
        if entry.reverses_id:
            original = self.entries.get(entry.reverses_id)
            if original is None:
                raise LedgerError("reversing an entry that does not exist")
            if original.status != "committed" or original.kind == "reversal":
                raise LedgerError("entry is already reversed, or is itself a reversal")

        # Compute new average costs first, so nothing changes if a product is missing.
        on_hand = {}
        new_avg = {}
        for m in entry.stock_moves:
            p = self.products.get(m.product_id)
            if p is None:
                raise LedgerError(f"unknown product {m.product_id}")
            qty_before = on_hand.get(m.product_id, self.stock_on_hand(m.product_id))
            avg_before = new_avg.get(m.product_id, p.avg_cost_kobo)
            new_avg[m.product_id] = next_avg_cost(qty_before, avg_before, m.qty, m.unit_cost_kobo)
            on_hand[m.product_id] = qty_before + m.qty

        saved = copy.deepcopy(entry)
        saved.id = new_id()
        saved.seq = next(self._seq)
        saved.status = "committed"
        saved.entry_date = saved.entry_date or date.today()
        self.entries[saved.id] = saved
        for pid, avg in new_avg.items():
            self.products[pid].avg_cost_kobo = avg
        if entry.reverses_id:
            self.entries[entry.reverses_id].status = "reversed"
        return copy.deepcopy(saved)

    def get_entry(self, eid) -> Entry | None:
        e = self.entries.get(eid)
        return copy.deepcopy(e) if e else None

    def list_entries(self, since_seq=0, start=None, end=None):
        out = [e for e in self.entries.values() if e.seq > since_seq]
        if start:
            out = [e for e in out if e.entry_date >= start]
        if end:
            out = [e for e in out if e.entry_date <= end]
        return [copy.deepcopy(e) for e in sorted(out, key=lambda e: e.seq)]

    def _lines(self, start=None, end=None):
        for e in self.entries.values():
            if (start and e.entry_date < start) or (end and e.entry_date > end):
                continue
            for l in e.lines:
                yield e, l

    def stock_on_hand(self, pid):
        return sum(m.qty for e in self.entries.values() for m in e.stock_moves if m.product_id == pid)

    def account_balance(self, code, contact_id=None, start=None, end=None):
        """Debit-positive balance for asset/expense; callers flip sign for the others."""
        return sum(l.debit_kobo - l.credit_kobo for _, l in self._lines(start, end)
                   if l.account == code and (contact_id is None or l.contact_id == contact_id))

    def sales_qty(self, pid, start=None, end=None):
        """Units sold in a date range, ignoring sales that were cancelled."""
        return sum(l.qty or 0 for e, l in self._lines(start, end)
                   if l.account == "sales" and l.product_id == pid
                   and e.status != "reversed" and e.kind != "reversal")

    def receivables(self):
        """[(contact_id, balance_kobo, oldest_open_due_date)] for customers who owe."""
        by_contact = {}
        for e, l in self._lines():
            # A reversed entry and its reversal cancel out; leave both out of the ageing.
            if e.status == "reversed" or e.kind == "reversal":
                continue
            if l.account == "ar" and l.contact_id:
                by_contact.setdefault(l.contact_id, []).append((e, l))
        out = []
        for cid, rows in by_contact.items():
            balance = sum(l.debit_kobo - l.credit_kobo for _, l in rows)
            if balance <= 0:
                continue
            out.append((cid, balance, _oldest_open_due(rows)))
        return sorted(out, key=lambda r: r[1], reverse=True)

    # --- evidence, orders, approvals, runs --------------------------------
    def add_evidence(self, **fields):
        eid = new_id()
        self.evidence[eid] = {"id": eid, "created_at": now(), **fields}
        return eid

    def get_evidence(self, eid):
        return self.evidence.get(eid)

    def set_evidence_extracted(self, eid, extracted):
        self.evidence[eid]["extracted_json"] = {**(self.evidence[eid].get("extracted_json") or {}), **extracted}

    def add_order(self, contact_id, items, total_kobo, ttl_hours=24):
        oid = new_id()
        self.orders[oid] = {"id": oid, "contact_id": contact_id, "items": items, "total_kobo": total_kobo,
                            "status": "open", "expires_at": now() + timedelta(hours=ttl_hours),
                            "claim_evidence_id": None, "entry_id": None, "created_at": now()}
        return self.orders[oid]

    def get_order(self, oid):
        return self.orders.get(oid)

    def update_order(self, oid, **fields):
        self.orders[oid].update(fields)
        return self.orders[oid]

    def open_orders(self, contact_id=None):
        t = now()
        return [o for o in self.orders.values()
                if o["status"] in ("open", "claimed_paid") and o["expires_at"] > t
                and (contact_id is None or o["contact_id"] == contact_id)]

    def reserved(self, pid):
        return sum(i["qty"] for o in self.open_orders() for i in o["items"] if i["product_id"] == pid)

    def add_approval(self, kind, payload, question, options):
        aid = new_id()[:8]
        self.approvals[aid] = {"id": aid, "kind": kind, "payload": payload, "question": question,
                               "options": options, "status": "pending", "answer": None,
                               "answered_at": None, "created_at": now()}
        return self.approvals[aid]

    def get_approval(self, aid):
        return self.approvals.get(aid)

    def update_approval_payload(self, aid, payload):
        self.approvals[aid]["payload"] = payload

    def answer_approval(self, aid, status, answer):
        self.approvals[aid].update(status=status, answer=answer, answered_at=now())
        return self.approvals[aid]

    def pending_approvals(self):
        return [a for a in self.approvals.values() if a["status"] == "pending"]

    def add_post(self, **fields):
        pid = new_id()
        self.posts[pid] = {"id": pid, "created_at": now(), "status": "draft", "platform_ids": None, "error": None,
                           "tools": {}, **fields}
        return dict(self.posts[pid])

    def get_post(self, pid):
        p = self.posts.get(pid)
        return dict(p) if p else None

    def update_post(self, pid, **fields):
        self.posts[pid].update(fields)
        return dict(self.posts[pid])

    def list_posts(self):
        return sorted((dict(p) for p in self.posts.values()), key=lambda p: p["created_at"], reverse=True)

    def log_run(self, **fields):
        self.runs.append({"created_at": now(), **fields})

    def recent_runs(self, limit=20):
        return self.runs[-limit:][::-1]


def _oldest_open_due(rows):
    """Apply payments to the oldest debts first; return the due date of the oldest unpaid one."""
    debts = sorted(((e.due_date or e.entry_date, l.debit_kobo) for e, l in rows if l.debit_kobo),
                   key=lambda d: d[0])
    paid = sum(l.credit_kobo for _, l in rows)
    for due, amount in debts:
        if paid >= amount:
            paid -= amount
            continue
        return due
    return None
