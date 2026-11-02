"""Sabi speaking first, and the statement a lender reads: credit guard, restock alerts, the business
statement and its links, setting up a shop from a photo, and the impact line."""
import json
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.orchestrator import Agent
from app.ledger import reports
from app.ledger.posting import commit_proposal
from app.ledger.proposals import Item, Proposal
from app.ledger.statement import check_token, make_token, render
from tests.test_agent import CUSTOMER, LIMIT, OWNER, ScriptedLLM, tool_results

TODAY = date.today()


def credit_sale(store, contact, qty, days_ago, due_in, product="p-spag", price=110_000):
    p = Proposal(kind="sale", items=[Item(product, qty, price)], contact_id=contact, paid_kobo=0,
                 entry_date=TODAY - timedelta(days=days_ago), due_date=TODAY - timedelta(days=days_ago - due_in),
                 credit_ok=True)
    entry, v = commit_proposal(p, store, LIMIT, approved=True)
    assert entry, v.to_json()
    return entry


def payment(store, contact, kobo, days_ago):
    p = Proposal(kind="payment_in", contact_id=contact, amount_kobo=kobo, payment_account="cash",
                 entry_date=TODAY - timedelta(days=days_ago))
    entry, v = commit_proposal(p, store, LIMIT, approved=True)
    assert entry, v.to_json()


# --- credit guard ---------------------------------------------------------------------

def test_credit_profile_pays_oldest_debt_first(store):
    credit_sale(store, "c-sule", 10, days_ago=30, due_in=7)     # ₦11,000 due 23 days ago
    credit_sale(store, "c-sule", 10, days_ago=20, due_in=7)     # ₦11,000 due 13 days ago
    payment(store, "c-sule", 1_100_000, days_ago=18)            # first debt: 5 days late
    payment(store, "c-sule", 1_100_000, days_ago=6)             # second: 7 days late
    credit_sale(store, "c-sule", 5, days_ago=4, due_in=2)       # ₦5,500 due 2 days ago, unpaid
    prof = reports.credit_profile(store, "c-sule")
    assert prof == {"owes_kobo": 550_000, "overdue_days": 2, "debts_paid": 2, "paid_late": 2, "avg_days_late": 6.0}


def guarded_sale(store, llm_turns, text):
    llm = ScriptedLLM(llm_turns)
    agent = Agent(store, llm, OWNER, LIMIT)
    return agent, llm, agent.handle(sender=OWNER, text=text)


SALE_TO_SULE = [[("find_product", {"query": "spaghetti"}), ("find_contact", {"query": "Baba Sule"})],
                [("record_entry", {"kind": "sale", "items": [{"product_id": "p-spag", "qty": 4, "unit_price_naira": None}],
                                   "contact_id": "c-sule", "paid_naira": 0, "memo": "4 spag to Baba Sule"})],
                "I don ask you."]


def test_credit_to_a_late_payer_asks_first(store):
    credit_sale(store, "c-sule", 10, days_ago=30, due_in=7)
    payment(store, "c-sule", 1_100_000, days_ago=10)            # 13 days late
    credit_sale(store, "c-sule", 10, days_ago=20, due_in=7)
    payment(store, "c-sule", 1_100_000, days_ago=5)             # 8 days late
    credit_sale(store, "c-sule", 2, days_ago=6, due_in=1)       # ₦2,200, 5 days late now
    agent, llm, ctx = guarded_sale(store, SALE_TO_SULE, "I give Baba Sule 4 spaghetti, he go pay later")
    ask = next(o for o in ctx.outbox if o.buttons)
    assert ask.body.startswith("⚠️ Before you give credit: Baba Sule already owes ₦2,200, 5 days late")
    assert "has paid 2 of the last 2 debts late (about 10.5 days)" in ask.body and "Still give ₦4,400 on credit?" in ask.body
    assert [b["title"] for b in ask.buttons] == ["Yes, give credit", "Paid cash now", "Cancel"]
    assert not ctx.committed
    done = agent.handle(sender=OWNER, button_id=ask.buttons[1]["id"])        # Paid cash now
    assert [e.kind for e in done.committed] == ["sale"]
    assert reports.credit_profile(store, "c-sule")["owes_kobo"] == 220_000   # no new debt


def test_she_can_still_give_credit(store):
    credit_sale(store, "c-sule", 2, days_ago=6, due_in=1)
    agent, llm, ctx = guarded_sale(store, SALE_TO_SULE, "I give Baba Sule 4 spaghetti, he go pay later")
    ask = next(o for o in ctx.outbox if o.buttons)
    agent.handle(sender=OWNER, button_id=ask.buttons[0]["id"])                # Yes, give credit
    assert reports.credit_profile(store, "c-sule")["owes_kobo"] == 220_000 + 440_000


def test_good_payer_gets_credit_without_questions(store):
    credit_sale(store, "c-sule", 2, days_ago=10, due_in=3)
    payment(store, "c-sule", 220_000, days_ago=8)                              # paid early
    agent, llm, ctx = guarded_sale(store, SALE_TO_SULE, "I give Baba Sule 4 spaghetti, he go pay later")
    assert [e.kind for e in ctx.committed] == ["sale"] and not any(o.buttons for o in ctx.outbox)


def test_a_big_total_debt_asks_even_with_nothing_late(store):
    agent, llm, ctx = guarded_sale(store, [
        [("find_product", {"query": "rice"}), ("find_contact", {"query": "Baba Sule"})],
        [("record_entry", {"kind": "sale", "items": [{"product_id": "p-rice", "qty": 2, "unit_price_naira": None}],
                           "contact_id": "c-sule", "paid_naira": 0})], "Asked."], "2 bag rice to Baba Sule on credit")
    asks = [o.body for o in ctx.outbox if o.buttons]
    assert any("would owe ₦156,000 in total" in b for b in asks)


# --- restock --------------------------------------------------------------------------------

def test_selling_down_to_the_reorder_level_sends_one_restock_alert(store):
    buy = Proposal(kind="purchase", items=[Item("p-eggs", 4, 390_000)], contact_id="c-depot", paid_kobo=0,
                   entry_date=TODAY - timedelta(days=5))
    assert commit_proposal(buy, store, LIMIT, approved=True)[0]                # eggs now 14, last cost ₦3,900
    sale = [[("find_product", {"query": "egg"})],
            [("record_entry", {"kind": "sale", "items": [{"product_id": "p-eggs", "qty": 10, "unit_price_naira": None}],
                               "paid_naira": 45000, "payment_account": "cash"})], "Done."]
    llm = ScriptedLLM(sale)
    agent = Agent(store, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, text="sold 10 crate egg cash")
    alert = next(o for o in ctx.outbox if o.buttons)
    assert alert.body.startswith("📦 Egg crate: only 4 crates left.")
    assert "Last time you bought from Alaba Depot at ₦3,900." in alert.body
    order_qty = reports.restock_advice(store, "p-eggs")["order_qty"]
    assert alert.buttons[0]["title"] == f"Draft order ({order_qty})"

    llm.turns = sale[:]                                                        # selling more the same day: no repeat
    again = agent.handle(sender=OWNER, text="another 1 crate egg cash")
    assert not any(o.body.startswith("📦") for o in again.outbox)

    draft = agent.handle(sender=OWNER, button_id=alert.buttons[0]["id"])
    body = draft.outbox[0].body
    assert f"Good day Alaba Depot. Abeg I need {order_qty} Egg crate. Same price as last time, ₦3,900?" in body
    assert "Forward it" in body


def test_restock_report_and_summary_lines(store):
    commit_proposal(Proposal(kind="sale", items=[Item("p-sugar", 4, None)], paid_kobo=6_600_000,
                             payment_account="cash"), store, LIMIT, approved=True)
    due = reports.report(store, "restock")
    assert [r["name"] for r in due] == ["Sugar bag"] and due[0]["stock"] == 1
    text = reports.daily_summary_text(store)
    assert "Restock soon: Sugar bag (1 left, ~3.5 days)." in text
    assert "Sabi today: 0 entries recorded" not in text                     # seeded by hand, not by Sabi


def test_summary_suggests_what_customers_keep_asking_for(store):
    from app.ledger.model import Contact
    for i in range(3):
        cid = f"c-x{i}"
        store.add_contact(Contact(id=cid, name=f"Buyer {i}", phone=f"2348100000{i}"))
        store.save_lead(cid, unmet=["golden morn"])
    assert "3 customers asked for Golden Morn this week. Worth stocking?" in reports.daily_summary_text(store)


def test_impact_line_counts_sabis_work(store):
    llm = ScriptedLLM([[("find_product", {"query": "spag"})],
                       [("record_entry", {"kind": "sale", "items": [{"product_id": "p-spag", "qty": 1, "unit_price_naira": None}],
                                          "paid_naira": 1100, "payment_account": "cash"})], "Done.",
                       "Spaghetti na ₦1,100."])
    agent = Agent(store, llm, OWNER, LIMIT)
    agent.handle(sender=OWNER, text="sold 1 spaghetti cash")
    agent.handle(sender=CUSTOMER, text="how much spaghetti")
    assert reports.impact_today(store) == {"entries": 1, "customer_replies": 1, "minutes_saved": 4}
    assert "While you worked, Sabi answered 1 customer messages and recorded 1 entries (about 4 minutes of your time)." \
        in reports.daily_summary_text(store)


# --- business statement ------------------------------------------------------------------------

def test_statement_figures_come_from_the_books(store):
    credit_sale(store, "c-sule", 10, days_ago=40, due_in=7)
    payment(store, "c-sule", 1_100_000, days_ago=30)
    commit_proposal(Proposal(kind="sale", items=[Item("p-milk", 6, None)], paid_kobo=420_000, payment_account="bank",
                             entry_date=TODAY - timedelta(days=2)), store, LIMIT, approved=True)
    s = reports.business_statement(store)
    assert s["from"] == TODAY - timedelta(days=40) and s["days"] == 41
    assert s["total"]["sales_kobo"] == 1_100_000 + 420_000
    assert s["credit_sold_kobo"] == 1_100_000 and s["collected_kobo"] == 1_100_000 and s["collection_pct"] == 100
    assert sum(m["sales_kobo"] for m in s["months"]) == s["total"]["sales_kobo"]
    assert s["top_products"][0]["name"] == "Spaghetti pack" and s["trading_days"] == 2
    assert s["bank_kobo"] == 420_000 and s["address"] == "Shop 14, Oke-Arin Market, Lagos Island."
    html = render(s)
    assert "Mama Nkechi Provisions" in html and "₦15,200" in html and "100% collected" in html
    assert "not audited accounts" in html and "Only 41 days of records" in html
    assert "<script" not in html


def test_statement_links_are_signed_and_expire():
    t = make_token()
    assert check_token(t)
    expires, sig = t.split(".")
    assert not check_token(f"{int(expires) + 86400}.{sig}")                 # can't extend it
    assert not check_token("garbage") and not check_token(f"{expires}.{'0' * 32}")
    assert not check_token(make_token(days=1), now=__import__("time").time() + 2 * 86400)


def test_owner_asks_for_her_loan_statement(store):
    llm = ScriptedLLM([[("business_statement", {})], "Here's your statement."])
    agent = Agent(store, llm, OWNER, LIMIT, statement_link=lambda: "https://sabi.example/s/abc.def")
    agent.handle(sender=OWNER, text="I wan apply for loan for LAPO, wetin I go show them?")
    result = tool_results(llm)[0]
    assert result["link"] == "https://sabi.example/s/abc.def" and "7 days" in result["link_note"]
    assert "total" in result and "note" in result                          # short history: told to say so
    assert "business_statement" in llm.seen[0][1]


def test_owner_app_statement_and_share_link(store):
    from app.api import router
    app = FastAPI()
    app.state.store, app.state.owner_token, app.state.private_media_dir = store, "k", "unused"
    app.include_router(router)
    c = TestClient(app, headers={"Authorization": "Bearer k"})
    assert TestClient(app).get("/api/statement").status_code == 401
    page = c.get("/api/statement")
    assert page.status_code == 200 and "Business statement" in page.text and page.headers["cache-control"] == "private, no-store"
    link = c.post("/api/statement/link").json()
    assert "/s/" in link["url"] and link["days"] == 7 and check_token(link["url"].rsplit("/", 1)[1])


def test_demo_statement_page():
    from tests.test_demo import client_for, registry
    c = client_for(registry(ScriptedLLM([])))
    sid = c.post("/demo/api/session").json()["id"]
    page = c.get(f"/demo/api/{sid}/statement")
    assert page.status_code == 200 and "Mama Nkechi Provisions" in page.text and "Month by month" in page.text


# --- a shop from a photo ------------------------------------------------------------------------

PRICE_LIST = json.dumps({"products": [
    {"name": "Golden Morn 900g", "unit": "pack", "price_naira": 3200, "qty": 12, "cost_naira": 2800},
    {"name": "Milo 400g", "unit": "tin", "price_naira": 2500, "qty": 6, "cost_naira": None},
    {"name": "Peak milk tin", "unit": "tin", "price_naira": 750, "qty": None, "cost_naira": None},
    {"name": "Indomie carton", "unit": "carton", "price_naira": 12500, "qty": None, "cost_naira": None},
    {"name": "Smudge", "unit": "", "price_naira": None}], "unreadable": ["last line, torn"]})


def test_price_list_photo_becomes_products_prices_and_stock(store):
    llm = ScriptedLLM([[("setup_from_photo", {})], PRICE_LIST, "Check the list I sent."])
    agent = Agent(store, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, text="these are my prices", image_data_url="data:image/jpeg;base64,AAAA")
    ask = next(o for o in ctx.outbox if o.buttons)
    assert ask.body.startswith("From your photo: 2 new products, 1 price change.")
    assert "➕ Golden Morn 900g: ₦3,200 per pack, 12 in stock at ₦2,800" in ask.body
    assert "➕ Milo 400g: ₦2,500 per tin, 6 in stock (cost not shown)" in ask.body
    assert "✏️ Peak milk tin: ₦700 → ₦750" in ask.body and "Indomie" not in ask.body
    assert "not the stock of Milo 400g" in ask.body and "Couldn't read: last line, torn" in ask.body
    assert store.get_product("p-golden-morn-900g") is None                    # nothing until she taps

    done = agent.handle(sender=OWNER, button_id=ask.buttons[0]["id"])
    assert "Added 2 products (1 with opening stock) and updated 1 price" in done.outbox[0].body
    gm = store.get_product("p-golden-morn-900g")
    assert gm.sell_price_kobo == 320_000 and store.stock_on_hand(gm.id) == 12 and gm.avg_cost_kobo == 280_000
    assert store.stock_on_hand("p-milo-400g") == 0 and store.get_product("p-milk").sell_price_kobo == 75_000
    [opening] = [e for e in done.committed if e.kind == "opening"]
    assert opening.total_debits == opening.total_credits == 12 * 280_000

    llm.turns = [[("find_product", {"query": "golden morn"})], "Yes, ₦3,200."]
    agent.handle(sender=CUSTOMER, text="una get golden morn?")
    assert tool_results(llm)[0]["matches"][0]["price"] == "₦3,200"


def test_cancelling_the_photo_list_changes_nothing(store):
    llm = ScriptedLLM([[("setup_from_photo", {})], PRICE_LIST, "Sent."])
    agent = Agent(store, llm, OWNER, LIMIT)
    ask = next(o for o in agent.handle(sender=OWNER, text="my prices", image_data_url="data:image/jpeg;base64,AAAA").outbox
               if o.buttons)
    agent.handle(sender=OWNER, button_id=ask.buttons[1]["id"])
    assert store.get_product("p-golden-morn-900g") is None and store.get_product("p-milk").sell_price_kobo == 70_000


def test_prompts_speak_nigerian_languages(store):
    from app.agent.orchestrator import _system_prompt
    for persona in ("owner", "customer"):
        assert "Yoruba, Igbo" in _system_prompt(persona, store)


def test_new_products_are_not_restock_alerts_and_summaries_use_whole_naira(store):
    from app.ledger.model import Product
    store.add_product(Product(id="p-new", name="Milo 400g", base_unit="tin", sell_price_kobo=250_000, reorder_level=2))
    assert not reports.restock_advice(store, "p-new")["due"]                  # never stocked: nothing to restock
    assert reports.whole_naira(586_546) == "₦5,865" and reports.whole_naira(586_550) == "₦5,866"
