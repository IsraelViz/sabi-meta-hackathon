from datetime import date

import pytest

from app.ledger.model import Entry, LedgerError, Line
from app.ledger.posting import commit_proposal, reverse
from app.ledger.proposals import Item, Proposal, apply_patch
from app.ledger.reports import debtors, profit_and_loss
from app.ledger.store import next_avg_cost
from app.ledger.validator import validate

LIMIT = 50_000_00


def sale(**kw):
    base = dict(kind="sale", items=[Item("p-indomie", 3, 1_250_000)], contact_id="c-tunde",
                paid_kobo=0, due_date=date(2026, 10, 30))
    base.update(kw)
    return Proposal(**base)


# --- the rules that must never break ------------------------------------

def test_unbalanced_entry_is_rejected(store):
    e = Entry(kind="sale", lines=[Line("cash", debit_kobo=100), Line("sales", credit_kobo=90)])
    with pytest.raises(LedgerError, match="unbalanced"):
        store.commit(e)


def test_line_with_both_sides_is_rejected(store):
    e = Entry(kind="sale", lines=[Line("cash", debit_kobo=100, credit_kobo=100)])
    with pytest.raises(LedgerError):
        store.commit(e)


def test_empty_entry_is_rejected(store):
    with pytest.raises(LedgerError, match="no lines"):
        store.commit(Entry(kind="sale", lines=[]))


def test_unknown_account_is_rejected(store):
    e = Entry(kind="sale", lines=[Line("magic", debit_kobo=1), Line("sales", credit_kobo=1)])
    with pytest.raises(LedgerError, match="unknown account"):
        store.commit(e)


def test_every_committed_entry_balances(store):
    commit_proposal(sale(), store, LIMIT)
    commit_proposal(Proposal(kind="payment_in", contact_id="c-tunde", amount_kobo=2_000_000,
                             payment_account="bank"), store, LIMIT)
    for e in store.list_entries():
        assert e.total_debits == e.total_credits > 0


def test_database_rejects_tampering_that_bypasses_python(store):
    """Raw SQL from any client (a bug, a script, the Supabase console) still can't break the books."""
    if not hasattr(store, "conn"):
        pytest.skip("Postgres triggers only")
    import psycopg

    entry, _ = commit_proposal(sale(), store, LIMIT)
    conn = store.conn
    with pytest.raises(psycopg.errors.RaiseException, match="unbalanced"):
        with conn.transaction():
            conn.execute("insert into journal_entries (id, kind, created_by) values ('x1', 'sale', 'sql')")
            conn.execute("insert into journal_lines (entry_id, account, debit_kobo) values ('x1', 'cash', 100)")
            conn.execute("insert into journal_lines (entry_id, account, credit_kobo) values ('x1', 'sales', 90)")
    with pytest.raises(psycopg.errors.RaiseException, match="no lines"):
        with conn.transaction():
            conn.execute("insert into journal_entries (id, kind, created_by) values ('x2', 'sale', 'sql')")
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("update journal_lines set debit_kobo = 1 where entry_id = %s and debit_kobo > 0", (entry.id,))
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        conn.execute("delete from journal_entries where id = %s", (entry.id,))
    with pytest.raises(psycopg.errors.RaiseException, match="committed to reversed"):
        conn.execute("update journal_entries set memo = 'edited' where id = %s", (entry.id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("insert into journal_lines (entry_id, account, debit_kobo, credit_kobo) values (%s, 'cash', 5, 5)",
                     (entry.id,))
    assert store.get_entry(entry.id).memo == entry.memo


# --- the worked example from the plan -----------------------------------

def test_credit_sale_posts_receivable_and_cogs(store):
    entry, verdict = commit_proposal(sale(), store, LIMIT)
    assert verdict.auto_ok and entry is not None
    by_acct = {(l.account, "D" if l.debit_kobo else "C"): l.debit_kobo or l.credit_kobo for l in entry.lines}
    assert by_acct == {("ar", "D"): 3_750_000, ("sales", "C"): 3_750_000,
                       ("cogs", "D"): 3_300_000, ("inventory", "C"): 3_300_000}
    assert store.stock_on_hand("p-indomie") == 9
    assert entry.due_date == date(2026, 10, 30)
    [d] = debtors(store, today=date(2026, 11, 2))
    assert d["name"] == "Mama Tunde" and d["owes_kobo"] == 3_750_000 and d["overdue_days"] == 3


def test_profit_and_loss(store):
    commit_proposal(sale(paid_kobo=3_750_000, payment_account="cash"), store, LIMIT)
    pnl = profit_and_loss(store, "today")
    assert pnl["sales_kobo"] == 3_750_000
    assert pnl["gross_profit_kobo"] == 450_000


# --- corrections reverse, never edit ------------------------------------

def test_reversal_mirrors_and_restores_stock(store):
    entry, _ = commit_proposal(sale(), store, LIMIT)
    rev = reverse(entry.id, store, "wrong customer")
    assert store.get_entry(entry.id).status == "reversed"
    assert store.stock_on_hand("p-indomie") == 12
    assert store.account_balance("ar", "c-tunde") == 0
    assert rev.reverses_id == entry.id
    with pytest.raises(LedgerError):
        reverse(entry.id, store, "again")
    with pytest.raises(LedgerError):
        reverse(rev.id, store, "reverse the reversal")


def test_reversed_sale_drops_out_of_debtors(store):
    entry, _ = commit_proposal(sale(), store, LIMIT)
    reverse(entry.id, store, "typo")
    assert debtors(store) == []


# --- average cost --------------------------------------------------------

def test_moving_average_cost(store):
    # 12 cartons at 11,000 + 12 at 12,000 -> 11,500
    p = Proposal(kind="purchase", items=[Item("p-indomie", 12, 1_200_000)], contact_id="c-depot",
                 paid_kobo=14_400_000, payment_account="bank")
    entry, verdict = commit_proposal(p, store, LIMIT, approved=True)
    assert entry is not None, verdict.to_json()
    assert store.get_product("p-indomie").avg_cost_kobo == 1_150_000
    assert next_avg_cost(10, 500, -3, 500) == 500


# --- money that isn't clear never posts ----------------------------------

def test_unclear_amount_asks_with_candidates(store):
    p = sale(items=[Item("p-eggs", 1, 250_000)],
             unclear_amounts=[{"path": "items.0.unit_price_kobo", "text": "two-five",
                               "candidates_kobo": [250_000, 2_500_000]}])
    entry, verdict = commit_proposal(p, store, LIMIT)
    assert entry is None
    q = verdict.questions[0]
    assert [o["label"] for o in q.options] == ["₦2,500", "₦25,000", "Other amount"]
    # Owner taps ₦2,500 -> still outside the egg price range (₦3,600-₦5,400), so it asks again
    p2 = apply_patch(p, q.options[0]["patch"])
    assert p2.unclear_amounts == []
    _, v2 = commit_proposal(p2, store, LIMIT)
    assert "usual price" in v2.questions[0].text


def test_price_out_of_range_asks(store):
    _, v = commit_proposal(sale(items=[Item("p-indomie", 1, 125_000)]), store, LIMIT)
    assert v.questions and "usual price" in v.questions[0].text


def test_unknown_payment_status_asks(store):
    _, v = commit_proposal(sale(paid_kobo=None), store, LIMIT)
    assert [o["label"] for o in v.questions[0].options] == ["Paid cash", "Paid transfer", "Not yet (owes)"]


def test_not_enough_stock_asks(store):
    _, v = commit_proposal(sale(items=[Item("p-sugar", 9, 1_650_000)]), store, LIMIT)
    assert "only 5" in v.questions[0].text


def test_credit_needs_a_customer(store):
    _, v = commit_proposal(sale(contact_id=None), store, LIMIT)
    assert "Who owes" in v.questions[0].text


# --- trust is earned per action ------------------------------------------

def test_big_sale_needs_confirmation(store):
    p = sale(items=[Item("p-rice", 1, 7_800_000)])
    entry, v = commit_proposal(p, store, LIMIT)
    assert entry is None and v.approvals and not v.questions
    entry, _ = commit_proposal(p, store, LIMIT, approved=True)
    assert entry.created_by == "agent:approved"


def test_new_customer_needs_confirmation_then_is_created(store):
    p = sale(contact_id=None, new_contact_name="Iya Bisi")
    entry, v = commit_proposal(p, store, LIMIT)
    assert entry is None and any("new contact" in a for a in v.approvals)
    entry, _ = commit_proposal(p, store, LIMIT, approved=True)
    assert store.get_contact(entry.contact_id).name == "Iya Bisi"


def test_overpayment_asks(store):
    commit_proposal(sale(), store, LIMIT)
    _, v = commit_proposal(Proposal(kind="payment_in", contact_id="c-tunde", amount_kobo=5_000_000,
                                    payment_account="cash"), store, LIMIT)
    assert "owes only" in v.questions[0].text


def test_stock_loss_always_needs_confirmation(store):
    p = Proposal(kind="stock_loss", items=[Item("p-eggs", 2)], memo="broken")
    entry, v = commit_proposal(p, store, LIMIT)
    assert entry is None
    entry, _ = commit_proposal(p, store, LIMIT, approved=True)
    assert store.stock_on_hand("p-eggs") == 8
    assert store.account_balance("stock_loss") == 760_000


def test_cancel(store):
    _, v = commit_proposal(sale(cancel=True), store, LIMIT, approved=True)
    assert v.errors == ["cancelled by the owner"]


def test_validator_errors_go_back_to_the_model(store):
    v = validate(Proposal(kind="sale", items=[Item("p-nope", 1)]), store, LIMIT)
    assert v.errors and "unknown product" in v.errors[0]
