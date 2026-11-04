"""The bookshop: classes, editions, booklists and the school year."""
from datetime import date

import pytest

from app.agent import books
from app.agent.context import RunContext
from app.agent.orchestrator import Agent
from app.agent.tools import find_product
from app.ledger.seed import DEMO_SHOPS, add_demo_history, demo_store
from tests.test_agent import CUSTOMER, OWNER, ScriptedLLM, tool_results

TODAY = date(2026, 11, 3)


@pytest.fixture
def shop():
    store = demo_store(DEMO_SHOPS["bookshop"])
    from app.ledger import bookshop_week
    bookshop_week.add_history(store, today=TODAY)
    return store


def ctx_for(store, persona="owner", contact_id=None):
    return RunContext(store=store, persona=persona, sender=OWNER if persona == "owner" else CUSTOMER,
                      owner_phone=OWNER, auto_post_limit_kobo=10**9, contact_id=contact_id)


def test_classes_are_read_every_way_parents_write_them():
    assert books.level_of("JSS 1") == books.level_of("js1") == books.level_of("Basic 7") == "JSS1"
    assert books.level_of("Pry 4") == books.level_of("basic four") == "Primary 4"
    assert books.level_of("SSS 2") == "SS2" and books.level_of("Things Fall Apart") is None


def test_bookshop_loads_with_its_history_in_postgres(pg_url):
    from app.ledger.pg_store import PostgresStore

    pg = PostgresStore(pg_url)
    pg.conn.execute("drop schema if exists public cascade; create schema public;")
    pg.apply_schema()
    demo_store(DEMO_SHOPS["bookshop"], store=pg)
    add_demo_history(pg, DEMO_SHOPS["bookshop"])
    assert pg.get_product("b-ngm-jss1-old").details["replaced_by"] == "b-ngm-jss1"
    assert pg.stock_on_hand("b-civic-jss1") == 0
    assert [b["school"] for b in pg.business["booklists"]] == ["Grace Model College, Ogba",
                                                              "Bright Future Nursery and Primary School"]
    pg.conn.close()


def test_a_kept_school_list_is_priced_from_the_school_and_class_alone(shop):
    r = books.price_booklist(ctx_for(shop), school="grace model college", level="JS 1")
    status = {l["asked"]: l["status"] for l in r["lines"]}
    assert status["Civic Education"] == "out_of_stock"
    assert status["Junior Secondary Agricultural Science JSS1"] == "not_stocked"
    assert status["Exercise books 40 leaves (dozen)"] == "available"
    # 6,800 + 5,500 + 5,200 + 2,000 + 15,000 + 2 x 3,000: what we have, at the shop's prices
    assert r["total_for_what_we_have"] == "₦40,500"
    assert {"product_id": "s-exercise", "qty": 2} in r["order_items"]
    assert not any(i["product_id"] == "b-civic-jss1" for i in r["order_items"])


def test_a_booklist_read_from_a_photo_matches_short_forms_at_the_right_class(shop):
    r = books.price_booklist(ctx_for(shop), level="JSS1", items=[
        {"title": "Basic Sci & Tech"}, {"title": "Civic Edu."}, {"title": "NGM", "level": "SS1"},
        {"title": "New General Mathematics", "level": "JSS2"}, {"title": "Things Fall Apart by Chinua Achebe"}])
    got = {l["asked"]: (l.get("product_id"), l["status"]) for l in r["lines"]}
    assert got["Basic Sci & Tech"] == ("b-bst-jss1", "available")
    assert got["Civic Edu."] == ("b-civic-jss1", "out_of_stock")
    assert got["NGM"] == (None, "not_stocked")          # we have it for JSS1-3, never pass it off as SS1
    assert got["New General Mathematics"] == ("b-ngm-jss2", "available")
    assert got["Things Fall Apart by Chinua Achebe"] == ("b-tfa", "available")


def test_editions_the_list_asks_for_and_the_newer_one(shop):
    r = books.price_booklist(ctx_for(shop), level="JSS1", items=[
        {"title": "New General Mathematics", "edition": "2014"}, {"title": "New General Mathematics"}])
    old, new = r["lines"]
    assert old["product_id"] == "b-ngm-jss1-old" and old["newer_edition"]["product_id"] == "b-ngm-jss1"
    assert new["product_id"] == "b-ngm-jss1" and new["old_edition_also"]["copies"] == 6
    looked = find_product(ctx_for(shop), "new general mathematics jss1 old edition")["matches"][0]
    assert looked["old_edition"] and looked["newer_edition"]["product_id"] == "b-ngm-jss1"


def test_what_a_parent_couldnt_get_is_noted_for_the_owner(shop):
    books.price_booklist(ctx_for(shop, "customer", "c-tunde"), level="JSS1",
                         items=[{"title": "Civic Education"}, {"title": "Agricultural Science"}])
    unmet = shop.get_lead("c-tunde")["unmet"]
    assert any("civic education" in u for u in unmet) and any("agricultural science" in u for u in unmet)


def test_the_owner_keeps_a_school_list_then_parents_just_name_the_school(shop):
    saved = books.save_booklist(ctx_for(shop), "Hope Academy", "Basic 8", [
        {"title": "New General Mathematics"}, {"title": "French for Beginners"}])
    assert saved["level"] == "JSS2" and saved["not_stocked"] == ["French for Beginners"]
    r = books.price_booklist(ctx_for(shop, "customer", "c-chioma"), school="hope academy", level="JSS 2")
    assert [l.get("product_id") for l in r["lines"]] == ["b-ngm-jss2", None]


def test_term_restock_plans_for_resumption_from_last_rush_and_unmet_requests(shop):
    r = books.term_restock(ctx_for(shop), today=TODAY)
    assert r["resumes"] == "Monday 11 January 2027" and r["next_term"] == "second term"
    first = r["order"][0]
    # 16 sold in the September rush x 40% for second term, + 2 parents who couldn't get it, + reorder level 4
    assert first["product_id"] == "b-civic-jss1" and first["order_qty"] == 13
    assert r["clear_old_editions"][0]["copies"] == 6
    assert r["asked_for_not_stocked"][0] == {"title": "junior secondary agricultural science jss1", "times": 3}


def test_a_new_class_of_the_same_title_is_a_new_book_not_a_price_change(shop):
    from app.agent.onboarding import add_products

    r = add_products(ctx_for(shop), [{"name": "New General Mathematics SS1", "price_naira": 7500,
                                      "book": {"title": "New General Mathematics", "level": "SS 1",
                                               "category": "textbook"}}])
    assert r["status"] == "waiting_for_owner" or "new" in str(r).lower()
    assert shop.get_product("b-ngm-jss1").sell_price_kobo == 680_000


def test_customer_sends_a_booklist_photo_and_gets_one_priced_answer(shop):
    shop.set_contact_phone("c-tunde", CUSTOMER)
    llm = ScriptedLLM([
        [("price_booklist", {"school": "Grace Model College", "level": "JSS1",
                             "items": [{"title": "New General Mathematics"}, {"title": "Civic Education"}]})],
        "Your list: 1 of 2 ready, ₦6,800. Civic Education JSS1 is sold out. Shall I reserve the maths book?",
    ])
    agent = Agent(shop, llm, OWNER, 10**9)
    ctx = agent.handle(sender=CUSTOMER, text="this is my son's list", image_data_url="data:image/jpeg;base64,AAAA")
    result = tool_results(llm)[0]
    assert result["ready"] == 1 and result["total_for_what_we_have"] == "₦6,800"
    assert ctx.outbox[-1].body.startswith("Your list")


def test_first_start_on_postgres_loads_the_whole_shop_or_nothing(pg_url, monkeypatch):
    from app import main
    from app.ledger import bookshop_week
    from app.ledger.pg_store import PostgresStore

    pg = PostgresStore(pg_url)
    pg.conn.execute("drop schema if exists public cascade; create schema public;")
    pg.apply_schema()
    monkeypatch.setattr(main, "settings", type("S", (), {"database_url": pg_url, "demo_history": True})())

    def broken(store, today=None, media=None):
        store.commit  # noqa: B018
        raise RuntimeError("restarted halfway")
    monkeypatch.setattr(bookshop_week, "add_history", broken)
    main.load_shop_once(DEMO_SHOPS["bookshop"])
    assert pg.is_empty()                                  # nothing half-loaded

    monkeypatch.undo()
    monkeypatch.setattr(main, "settings", type("S", (), {"database_url": pg_url, "demo_history": True})())
    main.load_shop_once(DEMO_SHOPS["bookshop"])
    assert pg.stock_on_hand("b-civic-jss1") == 0 and len(pg.list_entries()) == 22
    pg.conn.close()


def test_a_new_bookshop_set_up_from_plain_names_still_keeps_classes_apart():
    from app.ledger.model import Product
    from app.ledger.seed import chart_of_accounts
    from app.ledger.store import MemoryStore

    store = MemoryStore(chart_of_accounts())
    for pid, name in (("m1", "Mathematics JSS1"), ("m2", "Mathematics JSS2"), ("e1", "English Studies JSS1")):
        store.add_product(Product(id=pid, name=name, base_unit="copy", sell_price_kobo=500_000))
    r = books.price_booklist(ctx_for(store), level="JSS2", items=[{"title": "Mathematics"}, {"title": "English Studies"}])
    got = {l["asked"]: (l.get("product_id"), l["status"]) for l in r["lines"]}
    assert got["Mathematics"][0] == "m2"
    assert got["English Studies"][0] is None and "only for JSS1" in r["lines"][1]["note"]
    assert [b["product_id"] for b in books.class_books(ctx_for(store), "JSS 1")["books"]] == ["m1", "e1"]


def test_two_editions_added_from_a_price_list_are_linked_newest_first():
    from app.ledger.model import Product
    from app.ledger.seed import chart_of_accounts
    from app.ledger.store import MemoryStore

    store = MemoryStore(chart_of_accounts())
    for pid, ed in (("old", "2014 edition"), ("new", "2023 edition")):
        store.add_product(Product(id=pid, name=f"New General Mathematics JSS2 ({ed})", base_unit="copy",
                                  sell_price_kobo=500_000, details={"title": "New General Mathematics", "edition": ed,
                                                                    "level": "JSS2", "category": "textbook"}))
    assert books.link_editions(store) == 1
    assert store.get_product("old").details["replaced_by"] == "new"
    # no stock recorded yet: every tool says the same thing about it
    assert books.class_books(ctx_for(store), "JSS2")["books"][0]["in_stock"] is True
    assert find_product(ctx_for(store, "customer"), "New General Mathematics JSS2", customer=True)["matches"][0]["in_stock"] is True


def test_insights_say_what_the_books_say(shop):
    from app.ledger.insights import insights
    i = insights(shop, today=TODAY)
    assert len(i["weekly_sales"]) == 8 and max(w["sales_kobo"] for w in i["weekly_sales"][:3]) > 0   # the September rush
    assert i["kpis"]["sales_change_pct"] < 0 and "resumption rush" in i["takeaways"][0]
    assert i["slow_stock"][0]["name"] == "New General Mathematics JSS3"
    assert i["customer_demand"][0] == {"item": "Junior Secondary Agricultural Science JSS1", "times": 3}
    assert i["forecast"]["kind"] == "school_term" and i["forecast"]["items"][0]["name"] == "Civic Education JSS1"
    assert any("Mama Tunde owes ₦32,500" in t for t in i["takeaways"])
    assert all(any(ch.isdigit() for ch in t) for t in i["takeaways"])   # every sentence carries its numbers
