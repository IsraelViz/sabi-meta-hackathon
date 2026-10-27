"""The books in Postgres (Supabase in production). Same interface as MemoryStore.

The rules are enforced twice: Python checks give readable errors, and the
triggers in schema.sql make them impossible to bypass from any client.
"""
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.ledger.model import Contact, Entry, LedgerError, Line, Product, StockMove, check_balanced
from app.ledger.store import _oldest_open_due, new_id, next_avg_cost, now

SCHEMA = Path(__file__).with_name("schema.sql")

_PRODUCT_COLS = ("id", "name", "aliases", "base_unit", "sell_price_kobo", "min_price_kobo", "max_price_kobo",
                 "avg_cost_kobo", "reorder_level", "countable")
_CONTACT_COLS = ("id", "name", "kind", "phone", "aliases", "ig_handle")


class PostgresStore:
    def __init__(self, dsn):
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)

    # --- setup -----------------------------------------------------------
    def apply_schema(self):
        self.conn.execute(SCHEMA.read_text(encoding="utf-8"))

    def seed_accounts(self, accounts):
        with self.conn.cursor() as cur:
            cur.executemany("insert into accounts (code, name, kind) values (%(code)s, %(name)s, %(kind)s) "
                            "on conflict (code) do nothing", accounts)

    @property
    def accounts(self):
        return {r["code"]: r for r in self.conn.execute("select * from accounts")}

    @property
    def business(self):
        row = self.conn.execute("select * from businesses limit 1").fetchone()
        return dict(row) if row else {}

    @business.setter
    def business(self, data):
        fields = {k: v for k, v in data.items() if k in ("name", "payment_details", "timezone", "currency", "locale")}
        if self.conn.execute("select 1 from businesses").fetchone():
            sets = ", ".join(f"{k} = %({k})s" for k in fields)
            if sets:
                self.conn.execute(f"update businesses set {sets}", fields)
        else:
            cols = ", ".join(fields)
            self.conn.execute(f"insert into businesses ({cols}) values ({', '.join('%(' + k + ')s' for k in fields)})",
                              fields)

    def is_empty(self):
        return self.conn.execute("select count(*) as n from products").fetchone()["n"] == 0

    # --- catalogue -------------------------------------------------------
    def add_product(self, p: Product):
        self.conn.execute(f"insert into products ({', '.join(_PRODUCT_COLS)}) values "
                          f"({', '.join('%(' + c + ')s' for c in _PRODUCT_COLS)})", p.__dict__)
        return p

    def get_product(self, pid):
        row = self.conn.execute("select * from products where id = %s", (pid,)).fetchone()
        return Product(**{c: row[c] for c in _PRODUCT_COLS}) if row else None

    def list_products(self):
        return [Product(**{c: r[c] for c in _PRODUCT_COLS}) for r in self.conn.execute("select * from products order by name")]

    def update_product_price(self, pid, sell_price_kobo):
        self.conn.execute("update products set sell_price_kobo = %s where id = %s", (sell_price_kobo, pid))

    def add_contact(self, c: Contact):
        self.conn.execute(f"insert into contacts ({', '.join(_CONTACT_COLS)}) values "
                          f"({', '.join('%(' + k + ')s' for k in _CONTACT_COLS)})", c.__dict__)
        return c

    def _contact(self, row):
        return Contact(**{c: row[c] for c in _CONTACT_COLS}) if row else None

    def get_contact(self, cid):
        return self._contact(self.conn.execute("select * from contacts where id = %s", (cid,)).fetchone())

    def list_contacts(self):
        return [self._contact(r) for r in self.conn.execute("select * from contacts order by name")]

    def contact_by_phone(self, phone):
        return self._contact(self.conn.execute("select * from contacts where phone = %s", (phone,)).fetchone())

    # --- ledger ----------------------------------------------------------
    def commit(self, entry: Entry) -> Entry:
        check_balanced(entry)
        known = set(self.accounts)
        for l in entry.lines:
            if l.account not in known:
                raise LedgerError(f"unknown account {l.account!r}")
        try:
            with self.conn.transaction(), self.conn.cursor() as cur:
                if entry.reverses_id:
                    orig = cur.execute("select status, kind from journal_entries where id = %s for update",
                                       (entry.reverses_id,)).fetchone()
                    if orig is None:
                        raise LedgerError("reversing an entry that does not exist")
                    if orig["status"] != "committed" or orig["kind"] == "reversal":
                        raise LedgerError("entry is already reversed, or is itself a reversal")

                # Lock the products this entry moves, then compute the new average costs.
                new_avg, on_hand = {}, {}
                for m in entry.stock_moves:
                    row = cur.execute("select avg_cost_kobo from products where id = %s for update",
                                      (m.product_id,)).fetchone()
                    if row is None:
                        raise LedgerError(f"unknown product {m.product_id}")
                    qty_before = on_hand.get(m.product_id, self._stock(cur, m.product_id))
                    avg_before = new_avg.get(m.product_id, row["avg_cost_kobo"])
                    new_avg[m.product_id] = next_avg_cost(qty_before, avg_before, m.qty, m.unit_cost_kobo)
                    on_hand[m.product_id] = qty_before + m.qty

                eid = new_id()
                saved = cur.execute(
                    "insert into journal_entries (id, entry_date, kind, memo, reverses_id, evidence_id, contact_id,"
                    " due_date, created_by) values (%s, coalesce(%s, current_date), %s, %s, %s, %s, %s, %s, %s)"
                    " returning seq, entry_date",
                    (eid, entry.entry_date, entry.kind, entry.memo, entry.reverses_id, entry.evidence_id,
                     entry.contact_id, entry.due_date, entry.created_by)).fetchone()
                cur.executemany(
                    "insert into journal_lines (entry_id, account, debit_kobo, credit_kobo, product_id, contact_id, qty)"
                    " values (%s, %s, %s, %s, %s, %s, %s)",
                    [(eid, l.account, l.debit_kobo, l.credit_kobo, l.product_id, l.contact_id, l.qty)
                     for l in entry.lines])
                if entry.stock_moves:
                    cur.executemany("insert into stock_moves (entry_id, product_id, qty, unit_cost_kobo)"
                                    " values (%s, %s, %s, %s)",
                                    [(eid, m.product_id, m.qty, m.unit_cost_kobo) for m in entry.stock_moves])
                for pid, avg in new_avg.items():
                    cur.execute("update products set avg_cost_kobo = %s where id = %s", (avg, pid))
                if entry.reverses_id:
                    cur.execute("update journal_entries set status = 'reversed' where id = %s", (entry.reverses_id,))
        except psycopg.errors.RaiseException as e:   # a trigger said no
            raise LedgerError(str(e).splitlines()[0]) from e
        except psycopg.errors.IntegrityError as e:
            raise LedgerError(str(e).splitlines()[0]) from e
        return self.get_entry(eid)

    @staticmethod
    def _stock(cur, pid):
        return cur.execute("select coalesce(sum(qty), 0) as q from stock_moves where product_id = %s",
                           (pid,)).fetchone()["q"]

    def _entries(self, where, params):
        rows = self.conn.execute(f"select * from journal_entries {where} order by seq", params).fetchall()
        if not rows:
            return []
        ids = [r["id"] for r in rows]
        lines, moves = {}, {}
        for l in self.conn.execute("select * from journal_lines where entry_id = any(%s) order by id", (ids,)):
            lines.setdefault(l["entry_id"], []).append(Line(l["account"], l["debit_kobo"], l["credit_kobo"],
                                                            l["product_id"], l["contact_id"], l["qty"]))
        for m in self.conn.execute("select * from stock_moves where entry_id = any(%s) order by id", (ids,)):
            moves.setdefault(m["entry_id"], []).append(StockMove(m["product_id"], m["qty"], m["unit_cost_kobo"]))
        return [Entry(kind=r["kind"], lines=lines.get(r["id"], []), memo=r["memo"], stock_moves=moves.get(r["id"], []),
                      contact_id=r["contact_id"], due_date=r["due_date"], evidence_id=r["evidence_id"],
                      reverses_id=r["reverses_id"], created_by=r["created_by"], entry_date=r["entry_date"],
                      id=r["id"], seq=r["seq"], status=r["status"]) for r in rows]

    def get_entry(self, eid):
        found = self._entries("where id = %s", (eid,))
        return found[0] if found else None

    def list_entries(self, since_seq=0, start=None, end=None):
        return self._entries("where seq > %s and (%s::date is null or entry_date >= %s::date)"
                             " and (%s::date is null or entry_date <= %s::date)",
                             (since_seq, start, start, end, end))

    def stock_on_hand(self, pid):
        with self.conn.cursor() as cur:
            return self._stock(cur, pid)

    def account_balance(self, code, contact_id=None, start=None, end=None):
        row = self.conn.execute(
            "select coalesce(sum(l.debit_kobo - l.credit_kobo), 0) as b from journal_lines l"
            " join journal_entries e on e.id = l.entry_id where l.account = %s"
            " and (%s::text is null or l.contact_id = %s::text)"
            " and (%s::date is null or e.entry_date >= %s::date) and (%s::date is null or e.entry_date <= %s::date)",
            (code, contact_id, contact_id, start, start, end, end)).fetchone()
        return int(row["b"])

    def receivables(self):
        rows = self.conn.execute(
            "select e.entry_date, e.due_date, l.contact_id, l.debit_kobo, l.credit_kobo from journal_lines l"
            " join journal_entries e on e.id = l.entry_id"
            " where l.account = 'ar' and l.contact_id is not null and e.status <> 'reversed' and e.kind <> 'reversal'"
            " order by e.seq").fetchall()
        by_contact = {}
        for r in rows:
            e = SimpleNamespace(entry_date=r["entry_date"], due_date=r["due_date"])
            l = SimpleNamespace(debit_kobo=r["debit_kobo"], credit_kobo=r["credit_kobo"])
            by_contact.setdefault(r["contact_id"], []).append((e, l))
        out = []
        for cid, pairs in by_contact.items():
            balance = sum(l.debit_kobo - l.credit_kobo for _, l in pairs)
            if balance > 0:
                out.append((cid, balance, _oldest_open_due(pairs)))
        return sorted(out, key=lambda r: r[1], reverse=True)

    # --- evidence, orders, approvals, runs --------------------------------
    def add_evidence(self, **f):
        eid = new_id()
        self.conn.execute(
            "insert into evidence (id, channel, sender, media_url, original_text, normalized_text, extracted_json,"
            " model, prompt_version) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (eid, f.get("channel", "app"), f.get("sender"), f.get("media_url"), f.get("original_text"),
             f.get("normalized_text"), Jsonb(f.get("extracted_json")) if f.get("extracted_json") is not None else None,
             f.get("model"), f.get("prompt_version")))
        return eid

    def get_evidence(self, eid):
        return self.conn.execute("select * from evidence where id = %s", (eid,)).fetchone()

    def add_order(self, contact_id, items, total_kobo, ttl_hours=24):
        return self.conn.execute(
            "insert into orders (id, contact_id, items, total_kobo, expires_at) values (%s, %s, %s, %s, %s) returning *",
            (new_id(), contact_id, Jsonb(items), total_kobo, now() + timedelta(hours=ttl_hours))).fetchone()

    def get_order(self, oid):
        return self.conn.execute("select * from orders where id = %s", (oid,)).fetchone()

    def update_order(self, oid, **fields):
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        return self.conn.execute(f"update orders set {sets} where id = %(oid)s returning *", {**fields, "oid": oid}).fetchone()

    def open_orders(self, contact_id=None):
        return self.conn.execute(
            "select * from orders where status in ('open','claimed_paid') and expires_at > now()"
            " and (%s::text is null or contact_id = %s::text) order by created_at", (contact_id, contact_id)).fetchall()

    def reserved(self, pid):
        return sum(i["qty"] for o in self.open_orders() for i in o["items"] if i["product_id"] == pid)

    def add_approval(self, kind, payload, question, options):
        return self.conn.execute(
            "insert into approvals (id, kind, payload, question, options) values (%s, %s, %s, %s, %s) returning *",
            (new_id()[:8], kind, Jsonb(payload), question, Jsonb(options))).fetchone()

    def get_approval(self, aid):
        return self.conn.execute("select * from approvals where id = %s", (aid,)).fetchone()

    def update_approval_payload(self, aid, payload):
        self.conn.execute("update approvals set payload = %s where id = %s", (Jsonb(payload), aid))

    def answer_approval(self, aid, status, answer):
        return self.conn.execute("update approvals set status = %s, answer = %s, answered_at = now() where id = %s"
                                 " returning *", (status, answer, aid)).fetchone()

    def pending_approvals(self):
        return self.conn.execute("select * from approvals where status = 'pending' order by created_at").fetchall()

    def log_run(self, **f):
        self.conn.execute(
            "insert into agent_runs (trigger, persona, evidence_id, tool_calls, reply, tokens, latency_ms, error)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s)",
            (f.get("trigger"), f.get("persona"), f.get("evidence_id"),
             Jsonb(json.loads(json.dumps(f.get("tool_calls", []), default=str))),
             Jsonb(f.get("reply")), f.get("tokens"), f.get("latency_ms"), f.get("error")))

    def recent_runs(self, limit=20):
        return self.conn.execute("select * from agent_runs order by id desc limit %s", (limit,)).fetchall()
