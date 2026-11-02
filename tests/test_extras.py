"""Her settings and summaries, the coach, the Sabi Score, the payment alert check, the "Why?" trail,
savings, and shared supplier prices."""
from datetime import date, datetime, timedelta

from app import prefs
from app.agent.orchestrator import Agent
from app.agent import payments
from app.jobs.daily_summary import LAGOS, due
from app.ledger import coach, reports
from app.ledger.explain import explain
from app.ledger.posting import commit_proposal
from app.ledger.proposals import Item, Proposal
from app.ledger.score import sabi_score
from app.ledger.statement import page
from tests.test_agent import CUSTOMER, LIMIT, OWNER, ScriptedLLM, tool_results

TODAY = date.today()


def sale(store, product, qty, price, days_ago=0, contact=None, paid=True):
    p = Proposal(kind="sale", items=[Item(product, qty, price)], contact_id=contact,
                 paid_kobo=qty * price if paid else 0, payment_account="cash" if paid else None,
                 entry_date=TODAY - timedelta(days=days_ago), credit_ok=True)
    entry, v = commit_proposal(p, store, LIMIT, approved=True)
    assert entry, v.to_json()
    return entry


def purchase(store, product, qty, cost, days_ago=0):
    p = Proposal(kind="purchase", items=[Item(product, qty, cost, price_confirmed=True)], paid_kobo=qty * cost,
                 payment_account="cash", entry_date=TODAY - timedelta(days=days_ago), overdraw_ok=True)
    entry, v = commit_proposal(p, store, LIMIT, approved=True)
    assert entry, v.to_json()


# --- her settings ----------------------------------------------------------------------

def test_summary_time_and_frequency_are_hers(store):
    p = prefs.get(store)
    assert p["summary"] == {"frequency": "daily", "hour": 20, "weekday": 6}
    at = lambda d, h: datetime(2026, 11, d, h, 5, tzinfo=LAGOS)     # 2 Nov 2026 is a Monday
    assert prefs.summary_due(p, at(2, 19)) is None and prefs.summary_due(p, at(2, 20)) == "daily"
    assert prefs.summary_due(p, at(2, 23)) is None          # missed by hours: skipped, not sent at night

    saved, errors = prefs.save(store, {"summary": {"frequency": "weekly", "hour": 7, "weekday": 5}})
    assert not errors and prefs.get(store)["summary"]["hour"] == 7
    assert prefs.describe_summary(saved) == "Summary: every Saturday at 7 am"
    assert prefs.summary_due(saved, at(2, 7)) is None and prefs.summary_due(saved, at(7, 7)) == "weekly"
    # It goes once: the job marks the day sent.
    assert due(store, at(7, 7)) == "weekly" and due(store, at(7, 8)) is None

    _, errors = prefs.save(store, {"summary": {"hour": 3}, "reminder_tone": "angry"})
    assert len(errors) == 2 and prefs.get(store)["summary"]["hour"] == 7     # nothing saved
    _, errors = prefs.save(store, {"summary": {"frequency": "off"}})
    assert prefs.summary_due(prefs.get(store), at(7, 7)) is None


def test_she_sets_her_summary_by_chat(store):
    llm = ScriptedLLM([[("set_preferences", {"summary_frequency": "weekly", "summary_hour": 9,
                                              "summary_weekday": "Sunday", "reminder_tone": "pidgin"})],
                       "Done: your summary now comes every Sunday at 9 am."])
    Agent(store, llm, OWNER, LIMIT).handle(sender=OWNER, text="send my summary sunday morning 9am, weekly")
    result = tool_results(llm)[0]
    assert result["status"] == "saved" and result["summary"] == "Summary: every Sunday at 9 am"
    assert prefs.get(store)["reminder_tone"] == "pidgin"


def test_weekly_summary_has_the_week_and_who_owes(store):
    sale(store, "p-spag", 5, 110_000, days_ago=1)
    sale(store, "p-indomie", 1, 1_250_000, days_ago=9)
    sale(store, "p-milk", 2, 70_000, days_ago=2, contact="c-sule", paid=False)
    text = reports.weekly_summary_text(store, TODAY)
    assert "Last 7 days: sales ₦6,900 (down 45% on the week before)" in text
    assert "Best sellers: Spaghetti pack (5)" in text
    assert "• Baba Sule ₦1,400" in text


# --- coach -----------------------------------------------------------------------------

def test_coach_flags_losses_rising_costs_and_unknown_costs(store):
    store.update_product_price("p-spag", 85_000)              # costs ₦900, sells at ₦850
    sale(store, "p-spag", 2, 85_000)
    purchase(store, "p-sugar", 2, 1_700_000)                  # cost up from ₦14,500 to ₦17,000
    tips = coach.tips(store, TODAY, limit=5)
    kinds = [t["kind"] for t in tips]
    assert kinds[0] == "loss" and "you lose ₦50 on each one" in tips[0]["text"]
    assert "cost_up" in kinds
    cost_up = next(t for t in tips if t["kind"] == "cost_up")
    assert "Your last Sugar bag cost ₦17,000" in cost_up["text"]

    # She acts on the tip: the next summary says what happened.
    coach.remember(store, tips, TODAY)
    store.update_product_price("p-spag", 115_000)
    sale(store, "p-spag", 3, 115_000)
    follow = coach.follow_ups(store, TODAY)
    assert follow and follow[0].startswith("You changed Spaghetti pack from ₦850 to ₦1,150. Since then: 5 sold")
    assert coach.follow_ups(store, TODAY) == []               # reported once


def test_daily_summary_has_one_tip_and_savings(store):
    prefs.save(store, {"savings": {"percent": 10, "goal_kobo": 5_000_000, "name": "Ajo"}})
    sale(store, "p-indomie", 2, 1_250_000)                    # profit ₦3,000
    text = reports.daily_summary_text(store, TODAY)
    assert "Put aside ₦300 for Ajo today (10% of profit). Saved so far: ₦0 of ₦50,000 (0%)." in text
    assert text.count("💡") == 1


def test_saving_is_her_money_moved_aside(store):
    sale(store, "p-indomie", 1, 1_250_000)
    p = Proposal(kind="saving", amount_kobo=200_000, payment_account="cash", memo="ajo")
    store_cash = store.account_balance("cash")
    entry, v = commit_proposal(p, store, LIMIT, approved=True)
    assert entry, v.to_json()
    assert store.account_balance("savings") == 200_000 and store.account_balance("cash") == store_cash - 200_000
    assert reports.savings(store)["saved_kobo"] == 200_000


# --- Sabi Score and the statement -------------------------------------------------------

def test_sabi_score_needs_records_then_explains_itself(store):
    early = sabi_score(store, TODAY)
    assert early["score"] is None and "at least 14 days" in early["note"]
    purchase(store, "p-spag", 100, 90_000, days_ago=40)
    for d in range(30):
        sale(store, "p-spag", 2 + d % 3, 110_000, days_ago=d)
    s = sabi_score(store, TODAY)
    assert 0 < s["score"] <= 100 and s["band"] in ("Strong", "Good", "Building", "Early")
    assert {p["key"] for p in s["parts"]} == {"records", "steady", "profit", "collect", "proof", "growth"}
    assert sum(p["weight"] for p in s["parts"]) == 100
    assert s["next_step"]
    html = page(store)
    assert "Sabi Score" in html and "It is not a credit bureau score" in html
    prefs.save(store, {"share_score": False})
    assert "Sabi Score" not in page(store)


# --- payment alerts ---------------------------------------------------------------------

SMS_OK = "Acct:000****000 Amt:NGN2,200.00 CR Desc:TRF FROM ADAEZE OBI/0012 Date:{d} Avail Bal:NGN45,100.00"


def test_credit_sms_is_read_and_checked():
    a = payments.read_sms(SMS_OK.format(d=TODAY.strftime("%d-%b-%Y")))
    assert a["amount_kobo"] == 220_000 and a["sender_name"].upper() == "ADAEZE OBI" and a["has_balance"]
    assert a["date"] == TODAY and a["kind"] == "alert to the receiver"
    b = payments.read_sms("Amt:NGN4,500.00 CR Desc:TRF FROM ADA OBI Avail Bal:NGN60,000.00")
    assert b["sender_name"] == "ADA OBI"


def test_cash_or_transfer_without_an_amount_means_paid_in_full(store):
    p = Proposal(kind="sale", items=[Item("p-rice", 2)], payment_account="cash")
    entry, v = commit_proposal(p, store, 10**12)
    assert entry and "paid in full by cash" in v.notes
    assert store.account_balance("cash") == 15_600_000


def test_fake_alert_signs_are_flagged(store):
    old = payments.read_sms("Transfer of NGN2,200 to Mama Nkechi is pending. Date: 01-01-2026")
    r = payments.check(store, old, TODAY)
    assert r["verdict"] == "warning"
    assert any("pending" in f for f in r["flags"]) and any("not today" in f for f in r["flags"])
    assert "Only your own bank app" in r["advice"]
    wrong = payments.check(store, {"amount_kobo": 220_000, "receiver_account": "1234567890", "kind": "unclear",
                                   "date": TODAY}, TODAY)
    assert any("isn't the account in your payment details" in f for f in wrong["flags"])


def test_alert_matches_an_order_and_she_confirms(store):
    customer = Agent(store, ScriptedLLM([
        [("find_product", {"query": "spaghetti"})],
        [("create_order", {"items": [{"product_id": "p-spag", "qty": 2}]})], "Order made."]), OWNER, LIMIT)
    customer.handle(sender=CUSTOMER, text="2 spaghetti", profile_name="Adaeze Obi")
    sms = SMS_OK.format(d=TODAY.strftime("%d-%b-%Y"))
    llm = ScriptedLLM([[("check_payment", {"text": sms})], "I've sent you the check."])
    ctx = Agent(store, llm, OWNER, LIMIT).handle(sender=OWNER, text=sms)
    msg = next(o for o in ctx.outbox if o.buttons)
    assert msg.body.startswith("✅ Alert check: ₦2,200 from ADAEZE OBI") and "Matches Adaeze Obi's order" in msg.body
    tap = Agent(store, ScriptedLLM([]), OWNER, LIMIT).handle(sender=OWNER, button_id=msg.buttons[0]["id"],
                                                              button_title=msg.buttons[0]["title"])
    assert any("Recorded" in o.body for o in tap.outbox)
    assert store.list_entries()[-1].kind == "sale"


def test_customer_screenshot_claim_is_checked_for_the_owner(store):
    agent = Agent(store, ScriptedLLM([
        [("find_product", {"query": "spaghetti"})],
        [("create_order", {"items": [{"product_id": "p-spag", "qty": 2}]})], "Order made.",
    ]), OWNER, LIMIT)
    agent.handle(sender=CUSTOMER, text="2 spaghetti", profile_name="Adaeze Obi")
    order = store.open_orders()[0]
    agent.llm = ScriptedLLM([
        [("record_payment_claim", {"order_id": order["id"], "amount_naira": 2200})],
        '{"amount_naira": 2200, "status": "Pending", "sender_name": "Adaeze Obi", "receiver_account": "", '
        '"date": null, "kind": "receipt from the sender\'s app", "oddities": []}',
        "Thanks, the shop will confirm.",
    ])
    ctx = agent.handle(sender=CUSTOMER, text="I have paid", image_data_url="data:image/png;base64,AAAA")
    note = next(o for o in ctx.outbox if o.to == OWNER)
    assert "⚠️ Alert check" in note.body and "the money has not arrived yet" in note.body
    assert "sender's receipt" in note.body


# --- why is this entry in my books? --------------------------------------------------------

def test_why_shows_her_words_the_proposal_and_the_checks(store):
    llm = ScriptedLLM([
        [("find_product", {"query": "spaghetti"})],
        [("record_entry", {"kind": "sale", "items": [{"product_id": "p-spag", "qty": 2, "unit_price_naira": None}],
                           "paid_naira": 2200, "payment_account": "cash", "memo": "2 spaghetti"})],
        "Recorded.",
    ])
    Agent(store, llm, OWNER, LIMIT).handle(sender=OWNER, text="sold 2 spaghetti 2.2k cash")
    e = store.list_entries()[-1]
    steps = {s["title"]: s["detail"] for s in explain(store, e)}
    assert steps["What you sent (message)"] == "sold 2 spaghetti 2.2k cash"
    assert "“2.2k” → ₦2,200" in steps["Amounts read by code, not the AI"]
    assert "2 × Spaghetti pack at your list price" in steps["What Sabi understood"]
    checks = steps["Checked by code"]
    assert "Balanced: ₦4,000 in = ₦4,000 out" in checks and "within your usual" in checks
    assert "average cost of ₦900" in checks
    assert "recorded it straight away" in steps["Who let it in"]
