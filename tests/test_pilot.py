"""Pilot shops: each tester's own books, the owner's chat, her customers' chat link, and the admin page."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import pilot
from app.api import require_owner, router as api_router
from app.jobs import pilot as pilot_jobs
from app.ledger.seed import demo_store
from app.ledger.statement import make_token, token_shop
from app.promo.flow import PromoServices
from app.promo.imagegen import GradientBackground
from app.shops import Shops
from tests.test_agent import ScriptedLLM
from tests.test_promo import FakeCutter

CID = "cust-browser-0000001"


@pytest.fixture
def shops(pg_url, tmp_path):
    import psycopg
    with psycopg.connect(pg_url, autocommit=True) as conn:
        for (schema,) in conn.execute("select nspname from pg_namespace where nspname = 'pilot' or nspname like 'shop\\_%'"):
            conn.execute(f"drop schema {schema} cascade")
    s = Shops(pg_url, None, media_root=tmp_path / "shops",
              promo_factory=lambda media_dir, sid: PromoServices(FakeCutter(), GradientBackground(), None, None))
    yield s
    for shop in s._open.values():
        shop.store.conn.close()
    s.db.close()


def client(shops, owner_token="main-key"):
    app = FastAPI()
    app.state.store = demo_store()
    app.state.owner_token = owner_token
    app.state.private_media_dir = "private_media"
    app.state.shops = shops
    app.include_router(api_router)
    app.include_router(pilot.owner_router(require_owner))
    app.include_router(pilot.customer_router())
    app.include_router(pilot.admin_router())
    return TestClient(app)


def as_owner(token):
    return {"Authorization": f"Bearer {token}"}


def use(shops, shop_id, turns):
    llm = ScriptedLLM(turns)
    shops.get(shop_id).agent.llm = llm
    return llm


def test_each_tester_gets_empty_books_of_her_own(shops):
    c = client(shops)
    a, key_a = shops.create("Ada's Books", "books", "Ada")
    b, key_b = shops.create("Ada's Books", "online")
    assert a["slug"] == "ada-s-books" and b["slug"] == "ada-s-books-2"
    me = c.get("/api/me", headers=as_owner(key_a)).json()
    assert me["shop"] == "Ada's Books" and me["chat"] and me["kind"] == "books"
    assert c.get("/api/products", headers=as_owner(key_a)).json() == []
    assert c.get("/api/summary", headers=as_owner(key_b)).json()["recent"] == []
    # The main shop's key still opens the main shop, and has no chat.
    assert len(c.get("/api/products", headers=as_owner("main-key")).json()) > 3
    assert c.get("/api/chat", headers=as_owner("main-key")).status_code == 404
    assert c.get("/api/products", headers=as_owner("not-a-key")).status_code == 401


def test_owner_adds_products_by_chat_then_records_a_sale(shops):
    c = client(shops)
    row, key = shops.create("Mama Ada Provisions")
    use(shops, row["id"], [
        [("find_product", {"query": "Things Fall Apart"})],
        [("add_products", {"products": [{"name": "Things Fall Apart", "unit": "copy", "price_naira": 5000,
                                         "qty_in_stock": 10, "cost_naira": 3500}]})],
        "Check the list I sent and tap Add all.",
    ])
    r = c.post("/api/chat", headers=as_owner(key), json={"text": "I sell Things Fall Apart 5k, I get 10 for shop, I buy am 3500"}).json()
    mine, *replies = r["messages"]
    assert mine["role"] == "in" and mine["body"].startswith("I sell Things Fall Apart")
    ask = next(m for m in replies if m["buttons"])
    assert ask["body"].startswith("To add: 1 new product") and "10 in stock at ₦3,500" in ask["body"]
    done = c.post("/api/chat", headers=as_owner(key), json={"button_id": ask["buttons"][0]["id"],
                                                            "button_title": "Add all"}).json()["messages"]
    assert done[0]["body"] == "Add all" and done[1]["body"].startswith("Done. Added 1 product (1 with opening stock)")
    assert c.get("/api/products", headers=as_owner(key)).json()[0]["stock"] == 10

    use(shops, row["id"], [
        [("find_product", {"query": "things fall apart"})],
        [("record_entry", {"kind": "sale", "items": [{"product_id": "p-things-fall-apart", "qty": 2, "unit_price_naira": None}],
                           "paid_naira": 10000, "payment_account": "cash", "memo": "2 Things Fall Apart, cash"})],
        "Recorded: 2 Things Fall Apart, ₦10,000 cash. 8 left.",
    ])
    c.post("/api/chat", headers=as_owner(key), json={"text": "sold 2 things fall apart cash"})
    s = c.get("/api/summary", headers=as_owner(key)).json()
    assert s["today"]["sales_kobo"] == 10_000_00 and s["recent"][0]["items"] == ["2 × Things Fall Apart"]
    history = c.get("/api/chat", headers=as_owner(key)).json()["messages"]
    assert history[-1]["body"] == "Recorded: 2 Things Fall Apart, ₦10,000 cash. 8 left."
    assert c.get(f"/api/chat?after={history[-2]['id']}", headers=as_owner(key)).json()["messages"] == history[-1:]


def _stocked_shop(shops, c):
    row, key = shops.create("Chi Online")
    use(shops, row["id"], [[("add_products", {"products": [{"name": "Ankara gown", "unit": "piece", "price_naira": 15000}]})], "ok"])
    ask = c.post("/api/chat", headers=as_owner(key), json={"text": "I sell Ankara gown 15k"}).json()["messages"][1]
    c.post("/api/chat", headers=as_owner(key), json={"button_id": ask["buttons"][0]["id"]})
    return row, key


def test_customer_link_handoff_and_owner_reply_reach_each_other(shops):
    c = client(shops)
    row, key = _stocked_shop(shops, c)
    assert c.get(f"/c/api/{row['slug']}").json()["name"] == "Chi Online"
    use(shops, row["id"], [
        [("hand_to_owner", {"reason": "wants a discount on 3 gowns", "summary": "3 Ankara gowns at 12k each?"})],
        "I've passed this to the owner, who will reply here soon.",
    ])
    r = c.post(f"/c/api/{row['slug']}/message", json={"cid": CID, "name": "Bisi", "text": "3 gowns for 12k each?"}).json()
    assert [m["role"] for m in r["messages"]] == ["in", "out"]
    owner = c.get("/api/chat", headers=as_owner(key)).json()["messages"][-1]
    assert owner["body"].startswith("🙋 Bisi needs you: wants a discount") and owner["buttons"][0]["title"] == "Reply"
    c.post("/api/chat", headers=as_owner(key), json={"button_id": owner["buttons"][0]["id"], "button_title": "Reply"})
    c.post("/api/chat", headers=as_owner(key), json={"text": "I fit do 13,500 each for 3"})
    after = r["messages"][-1]["id"]
    got = c.get(f"/c/api/{row['slug']}/messages", params={"cid": CID, "after": after}).json()["messages"]
    assert [m["body"] for m in got] == ["I fit do 13,500 each for 3"]
    # Another customer's chat is separate.
    assert c.get(f"/c/api/{row['slug']}/messages", params={"cid": "someone-else-000000"}).json()["messages"] == []
    assert c.get("/c/api/nobody-here").status_code == 404


def test_customer_order_and_payment_claim_go_to_owner_chat(shops):
    c = client(shops)
    row, key = _stocked_shop(shops, c)
    llm = use(shops, row["id"], [
        [("find_product", {"query": "ankara"})],
        [("create_order", {"items": [{"product_id": "p-ankara-gown", "qty": 1}]})],
        "That's ₦15,000.",
    ])
    c.post(f"/c/api/{row['slug']}/message", json={"cid": CID, "name": "Bisi", "text": "I want 1 Ankara gown"})
    order_id = [m for m in llm.seen[-1][0] if m.get("role") == "tool"][-1]["content"].split('"order_id": "')[1].split('"')[0]
    use(shops, row["id"], [[("record_payment_claim", {"order_id": order_id, "amount_naira": 15000})], "Thanks! The shop will confirm."])
    c.post(f"/c/api/{row['slug']}/message", json={"cid": CID, "name": "Bisi", "text": "I don pay"})
    owner = c.get("/api/chat", headers=as_owner(key)).json()["messages"][-1]
    assert "Bisi says they paid ₦15,000" in owner["body"]
    c.post("/api/chat", headers=as_owner(key), json={"button_id": owner["buttons"][0]["id"]})
    got = c.get(f"/c/api/{row['slug']}/messages", params={"cid": CID}).json()["messages"]
    assert "Payment confirmed" in got[-1]["body"]
    assert c.get("/api/summary", headers=as_owner(key)).json()["today"]["sales_kobo"] == 15_000_00


def test_admin_creates_shops_reads_chats_and_replaces_keys(shops):
    c = client(shops, owner_token="admin-and-main")
    assert c.get("/admin/api/shops").status_code == 401
    admin = as_owner("admin-and-main")
    made = c.post("/admin/api/shops", headers=admin, json={"name": "Tolu Stores", "kind": "shop", "owner_name": "Tolu"}).json()
    key = made["join_url"].split("#key=")[1]
    assert made["chat_url"].endswith("/c/tolu-stores")
    use(shops, made["shop"]["id"], ["Hello Tolu!"])
    c.post("/api/chat", headers=as_owner(key), json={"text": "hello, any news?"})
    c.post("/api/feedback", headers=as_owner(key), json={"note": "it was slow", "rating": -1})
    listed = c.get("/admin/api/shops", headers=admin).json()
    assert listed[0]["owner_in"] == 1 and listed[0]["feedback"] == 1 and listed[0]["products"] == 0
    assert [m["body"] for m in c.get(f"/admin/api/shops/{made['shop']['id']}/transcript", headers=admin).json()] == ["hello, any news?", "Hello Tolu!"]
    assert c.get("/admin/api/feedback", headers=admin).json()[0]["note"] == "it was slow"
    fresh = c.post(f"/admin/api/shops/{made['shop']['id']}/key", headers=admin).json()["join_url"].split("#key=")[1]
    assert c.get("/api/me", headers=as_owner(key)).status_code == 401
    assert c.get("/api/me", headers=as_owner(fresh)).json()["shop"] == "Tolu Stores"


def test_deleting_her_shop_erases_books_and_chats(shops):
    c = client(shops)
    row, key = shops.create("Delete Me")
    use(shops, row["id"], ["hi"])
    c.post("/api/chat", headers=as_owner(key), json={"text": "hello, any news?"})
    assert c.delete("/api/shop", headers=as_owner(key)).json() == {"ok": True}
    assert c.get("/api/me", headers=as_owner(key)).status_code == 401
    assert shops.transcript(row["id"]) == []
    assert not shops.db.execute("select 1 from pg_namespace where nspname = %s", (f"shop_{row['id']}",)).fetchone()
    assert c.get(f"/c/api/{row['slug']}").status_code == 404


def test_daily_limit_and_summary_in_chat(shops):
    c = client(shops)
    row, key = shops.create("Busy Shop")
    shops.daily_messages = 1
    use(shops, row["id"], ["hi"])
    assert c.post("/api/chat", headers=as_owner(key), json={"text": "one"}).status_code == 200
    assert c.post("/api/chat", headers=as_owner(key), json={"text": "two"}).status_code == 429
    pilot_jobs.send_summaries(shops, force="daily")
    last = c.get("/api/chat", headers=as_owner(key)).json()["messages"][-1]
    assert last["body"].startswith("Today's books") and last["buttons"][0]["id"] == "summary:reminders"


def test_statement_links_name_the_shop_they_are_for(shops):
    c = client(shops)
    row, key = shops.create("Loan Ready")
    url = c.post("/api/statement/link", headers=as_owner(key)).json()["url"]
    assert token_shop(url.rsplit("/", 1)[1]) == row["id"]
    assert token_shop(make_token()) == ""
    forged = url.rsplit("/", 1)[1].replace(row["id"], "deadbeef")
    assert token_shop(forged) is None


def test_products_without_stock_records_still_sell(shops):
    """A new shop often gives products and prices only: sales and orders go through without stock questions."""
    c = client(shops)
    row, key = _stocked_shop(shops, c)
    use(shops, row["id"], [
        [("find_product", {"query": "ankara gown"})],
        [("record_entry", {"kind": "sale", "items": [{"product_id": "p-ankara-gown", "qty": 2, "unit_price_naira": None}],
                           "paid_naira": 30000, "payment_account": "bank", "memo": "2 gowns, transfer"})],
        "Recorded.",
    ])
    c.post("/api/chat", headers=as_owner(key), json={"text": "sold 2 ankara gowns, transfer"})
    s = c.get("/api/summary", headers=as_owner(key)).json()
    assert s["today"]["sales_kobo"] == 30_000_00 and s["low_stock"] == []
    assert s["pending_questions"] == 0


def test_settings_summary_time_and_why_from_the_app(shops):
    from datetime import datetime

    from app.jobs.daily_summary import LAGOS
    c = client(shops)
    row, key = shops.create("Early Bird")
    p = c.get("/api/prefs", headers=as_owner(key)).json()
    assert p["summary"]["frequency"] == "daily" and p["summary_text"] == "Summary: every day at 8 pm"
    r = c.put("/api/prefs", headers=as_owner(key), json={"summary": {"frequency": "weekly", "hour": 7, "weekday": 5},
                                                          "_summary_last": "x"})
    assert r.json()["summary_text"] == "Summary: every Saturday at 7 am"
    assert "_summary_last" not in shops.get(row["id"]).store.business["prefs"]
    assert c.put("/api/prefs", headers=as_owner(key), json={"summary": {"hour": 2}}).status_code == 422
    # Saturday 7 Nov 2026, 7:30 am in Lagos: her summary goes out once.
    sat = datetime(2026, 11, 7, 7, 30, tzinfo=LAGOS)
    pilot_jobs.send_summaries(shops, now=datetime(2026, 11, 6, 7, 30, tzinfo=LAGOS))
    assert not c.get("/api/chat", headers=as_owner(key)).json()["messages"]
    pilot_jobs.send_summaries(shops, now=sat)
    pilot_jobs.send_summaries(shops, now=sat)
    msgs = c.get("/api/chat", headers=as_owner(key)).json()["messages"]
    assert len(msgs) == 1 and msgs[0]["body"].startswith("This week's books")


def test_push_results_and_shared_prices(shops, monkeypatch):
    from datetime import date

    from app import prefs
    from app.ledger.model import Product
    from app.ledger.posting import commit_proposal
    from app.ledger.proposals import Item, Proposal
    c = client(shops)
    a, key = shops.create("Rice Ade", "shop")
    # Phone notifications: the key is a P-256 public key; a subscription is stored and used.
    k = c.get("/api/push/key", headers=as_owner(key)).json()["key"]
    assert len(k) == 87
    sub = {"endpoint": "https://push.example.com/abc", "keys": {"p256dh": "x", "auth": "y"}}
    assert c.post("/api/push", headers=as_owner(key), json=sub).json() == {"ok": True}
    sent = []
    monkeypatch.setattr(shops, "notify", lambda sid, title, body, url=None: sent.append((sid, title)))
    use(shops, a["id"], [[("hand_to_owner", {"reason": "wants a discount", "summary": "2 bags, wants less"})],
                         "I've asked the owner.", "Hello!"])
    c.post(f"/c/api/{a['slug']}/message", json={"cid": CID, "name": "Bola", "text": "abeg reduce am"})
    assert sent and sent[0][1] == "Sabi needs you"          # a customer message reached her: phone told
    sent.clear()
    c.post("/api/chat", headers=as_owner(key), json={"text": "hi, any news?"})
    assert not sent                                         # her own messages don't buzz her phone

    # Shared prices: anonymous, opt-in, and only once two other shops share.
    def shop_with_rice(name, cost):
        row, _ = shops.create(name)
        store = shops.get(row["id"]).store
        store.add_product(Product(id="rice", name="Rice 50kg bag", base_unit="bag", sell_price_kobo=8_000_000))
        p = Proposal(kind="purchase", items=[Item("rice", 2, cost, price_confirmed=True)], paid_kobo=2 * cost,
                     payment_account="cash", overdraw_ok=True)
        assert commit_proposal(p, store, 10**12, approved=True)[0]
        return store
    mine = shops.get(a["id"]).store
    mine.add_product(Product(id="r1", name="Rice 50kg", base_unit="bags", sell_price_kobo=8_200_000))
    b, d = shop_with_rice("B", 7_000_000), shop_with_rice("D", 7_400_000)
    from app.market import market_view
    assert "Not enough" in market_view(shops, shops.get(a["id"]), "r1")["note"]   # nobody shared yet
    prefs.save(b, {"share_prices": True}); prefs.save(d, {"share_prices": True})
    m = market_view(shops, shops.get(a["id"]), "r1")
    assert m["shops"] == 2 and m["low_kobo"] == 7_000_000 and m["high_kobo"] == 7_400_000

    # Results: totals only; a quote shows only after the admin ticks it.
    c.post("/api/feedback", headers=as_owner(key), json={"note": "Sabi saves me time"})
    r = c.get("/admin/api/results", headers=as_owner("main-key")).json()
    assert r["shops"] == 3 and r["messages"] >= 2 and r["quotes"] == []
    fid = c.get("/admin/api/feedback", headers=as_owner("main-key")).json()[0]["id"]
    c.post(f"/admin/api/feedback/{fid}/public", headers=as_owner("main-key"), json={"public": True})
    assert c.get("/admin/api/results", headers=as_owner("main-key")).json()["quotes"] == [
        {"text": "Sabi saves me time", "who": "Shop"}]
