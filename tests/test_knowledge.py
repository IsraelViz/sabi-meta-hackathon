"""Learning the shop from its own pages: safe fetching, the Meta readers, owner approval, and customer lookups."""
import dataclasses
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.agent.knowledge as kn
from app.agent.orchestrator import Agent
from app.config import settings
from tests.test_agent import CUSTOMER, LIMIT, OWNER, ScriptedLLM

HOME = """<html><head><title>Mama Nkechi Provisions</title>
<meta name="description" content="Provisions wholesale and retail in Oke-Arin Market">
<script type="application/ld+json">{"@type": "GroceryStore", "openingHours": "Mo-Sa 07:00-19:00"}</script>
<script>var tracking = "ignore me";</script><style>.x{}</style></head>
<body><h1>Welcome</h1><p>Best prices on Indomie, rice and oil.</p>
<a href="/delivery">Delivery</a> <a href="/blog/new-year">Happy new year</a>
<a href="https://other-site.com/about">Partner</a> <a href="/about-us">About us</a></body></html>"""
DELIVERY = "<html><title>Delivery</title><body><p>Lagos Island ₦1,500 same day.</p><p>Ikeja ₦2,500 next day.</p></body></html>"
ABOUT = "<html><title>About</title><body><p>Family shop since 1998. Bulk buyers get 5% off 20 cartons.</p></body></html>"


def fake_web(pages):
    def handler(request):
        html = pages.get(str(request.url))
        return httpx.Response(200, text=html, headers={"content-type": "text/html"}) if html else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def no_dns(monkeypatch):
    monkeypatch.setattr(kn, "_public_host", lambda host: None)


def test_page_text_keeps_content_and_facts_not_scripts():
    text = kn.parse_page(HOME).text()
    assert "Best prices on Indomie" in text and "Oke-Arin Market" in text and "Mo-Sa 07:00-19:00" in text
    assert "ignore me" not in text and ".x{}" not in text


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/api/summary", "http://localhost/", "http://10.0.0.5/",
                                 "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "ftp://x.com"])
def test_internal_addresses_are_refused(url):
    with pytest.raises(kn.ReadError):
        kn.safe_get(url)


def test_a_redirect_to_an_internal_address_is_refused(monkeypatch):
    monkeypatch.setattr(kn, "_public_host", lambda host: (_ for _ in ()).throw(kn.ReadError("private"))
                        if host == "127.0.0.1" else None)
    client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})))
    with pytest.raises(kn.ReadError, match="private"):
        kn.safe_get("https://shop.example/", client)


def test_website_reads_the_useful_pages_on_the_same_site(no_dns):
    client = fake_web({"https://shop.example/": HOME, "https://shop.example/delivery": DELIVERY,
                       "https://shop.example/about-us": ABOUT, "https://shop.example/blog/new-year": "<p>party</p>"})
    docs = kn.read_website("shop.example", client)
    assert [d.url for d in docs] == ["https://shop.example/", "https://shop.example/delivery", "https://shop.example/about-us"]
    assert "Ikeja ₦2,500" in docs[1].text


def test_instagram_through_the_graph_api(monkeypatch):
    monkeypatch.setattr(kn, "settings", dataclasses.replace(settings, page_access_token="tok", ig_user_id="17841"))
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"business_discovery": {
            "username": "mamankechi", "name": "Mama Nkechi", "biography": "Provisions. Delivery all over Lagos 🚚",
            "media": {"data": [{"caption": "Peak milk restocked!"}, {"caption": "We close 4pm on Saturday this week"}]}}})
    [doc] = kn.read_instagram("https://instagram.com/mamankechi/", httpx.Client(transport=httpx.MockTransport(handler)))
    assert "business_discovery.username%28mamankechi%29" in seen["url"] or "business_discovery.username(mamankechi)" in seen["url"]
    assert "Delivery all over Lagos" in doc.text and "close 4pm on Saturday" in doc.text


def test_instagram_without_a_token_says_why_it_cant_read(monkeypatch, no_dns):
    monkeypatch.setattr(kn, "settings", dataclasses.replace(settings, page_access_token="", ig_user_id=""))
    client = fake_web({"https://www.instagram.com/mamankechi/": "<html><title>Instagram</title><body>Log in</body></html>"})
    with pytest.raises(kn.ReadError, match="signed-in"):
        kn.read_instagram("@mamankechi", client)


def test_whatsapp_profile_needs_whatsapp_connected(monkeypatch):
    monkeypatch.setattr(kn, "settings", dataclasses.replace(settings, wa_access_token="", wa_phone_number_id=""))
    docs, status = kn.read_all({"whatsapp": "connected"})
    assert docs == [] and "isn't connected" in status["whatsapp"]


DRAFT = json.dumps({"shop_info": "Shop 14, Oke-Arin Market. Open Mon-Sat 7am-7pm. Delivery: Lagos Island ₦1,500 same day; "
                                 "Ikeja ₦2,500 next day. Bulk: 5% off 20 cartons.",
                    "products_seen": [{"name": "Indomie carton", "price_naira": 12500}, {"name": "Golden Morn 900g", "price_naira": None}],
                    "missing": ["return policy"]})


def pages(monkeypatch):
    docs = [kn.Doc("website", "https://shop.example/delivery", "Delivery", "Lagos Island ₦1,500 same day.\nIkeja ₦2,500 next day."),
            kn.Doc("website", "https://shop.example/about-us", "About", "Family shop since 1998. Bulk buyers get 5% off 20 cartons.")]
    monkeypatch.setattr(kn, "read_all", lambda links, client=None: (docs, {"website": "ok", "instagram": "needs a token"}))


def test_owner_gives_her_website_and_approves_what_customers_hear(store, monkeypatch):
    pages(monkeypatch)
    llm = ScriptedLLM([[("learn_business", {"website": "shop.example"})], DRAFT, "Check the message I sent."])
    agent = Agent(store, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, text="my website na shop.example, learn am")
    ask = next(o for o in ctx.outbox if o.buttons)
    assert "Here's what I'll tell customers" in ask.body and "Ikeja ₦2,500" in ask.body
    assert "website ✓" in ask.body and "instagram ✗ (needs a token)" in ask.body
    assert "Golden Morn 900g" in ask.body and "Indomie carton" not in ask.body.split("not in your books:")[1]
    assert "return policy" in ask.body
    assert "Ikeja" not in store.business.get("shop_info", "")       # nothing used before she approves

    agent.handle(sender=OWNER, button_id=ask.buttons[0]["id"])       # Use this
    assert "Ikeja ₦2,500" in store.business["shop_info"]
    assert store.business["links"]["website"] == "shop.example"
    assert len(store.business["knowledge"]) == 2

    llm.turns = [[("shop_details", {"question": "Do bulk buyers get discount?"})], "Yes, 5% off 20 cartons."]
    agent.handle(sender=CUSTOMER, text="Do bulk buyers get discount?")
    system = llm.seen[-1][0][0]["content"]
    assert "Ikeja ₦2,500 next day" in system
    found = json.loads(next(m for m in llm.seen[-1][0] if m.get("role") == "tool")["content"])
    assert found["found"][0]["text"].startswith("Family shop since 1998. Bulk buyers")


def test_owner_changes_the_draft_before_using_it(store, monkeypatch):
    pages(monkeypatch)
    llm = ScriptedLLM([[("learn_business", {"website": "shop.example"})], DRAFT, "Sent."])
    agent = Agent(store, llm, OWNER, LIMIT)
    ask = next(o for o in agent.handle(sender=OWNER, text="learn shop.example").outbox if o.buttons)
    tap = agent.handle(sender=OWNER, button_id=ask.buttons[1]["id"])          # Change
    assert "What should I change?" in tap.outbox[0].body
    llm.turns = ["Shop 14, Oke-Arin Market. Open Mon-Sat 7am-7pm. Delivery: Lagos Island ₦1,500; Ikeja ₦3,000."]
    again = agent.handle(sender=OWNER, text="Ikeja delivery is 3000 now")
    new_ask = next(o for o in again.outbox if o.buttons)
    assert "Ikeja ₦3,000" in new_ask.body
    agent.handle(sender=OWNER, button_id=new_ask.buttons[0]["id"])
    assert "Ikeja ₦3,000" in store.business["shop_info"] and len(store.business["knowledge"]) == 2


def test_owner_states_a_fact_and_approves_it(store):
    llm = ScriptedLLM([[("update_shop_info", {"change": "we now close 8pm on Saturday"})],
                       "Shop 14, Oke-Arin Market. Open Monday to Friday 7am-7pm, Saturday 7am-8pm.",
                       "Sent for you to check."])
    agent = Agent(store, llm, OWNER, LIMIT)
    ask = next(o for o in agent.handle(sender=OWNER, text="Saturday we dey close 8pm now").outbox if o.buttons)
    agent.handle(sender=OWNER, button_id=ask.buttons[2]["id"])                 # Skip
    assert "Saturday 7am-8pm" not in store.business["shop_info"]
    assert store.get_approval(ask.buttons[2]["id"].split(":")[1])["status"] == "rejected"


def test_customers_cannot_make_sabi_read_links(store):
    names = [t["function"]["name"] for t in __import__("app.agent.tools", fromlist=["CUSTOMER_TOOLS"]).CUSTOMER_TOOLS]
    assert "learn_business" not in names and "update_shop_info" not in names and "shop_details" in names


def test_shop_details_with_nothing_saved_says_to_hand_over(store):
    ctx = type("C", (), {"store": store})()
    assert kn.shop_details(ctx, "do you deliver to Abuja")["found"] == []


def test_owner_app_reads_drafts_and_saves_the_profile(store, monkeypatch):
    from app.api import router
    pages(monkeypatch)
    monkeypatch.setattr(kn, "settings", dataclasses.replace(settings, wa_access_token="", wa_phone_number_id=""))
    app = FastAPI()
    app.state.store, app.state.owner_token, app.state.private_media_dir = store, "k", "unused"
    app.state.llm = ScriptedLLM([DRAFT])
    app.include_router(router)
    c = TestClient(app, headers={"Authorization": "Bearer k"})
    assert TestClient(app).post("/api/business/learn", json={}).status_code == 401

    d = c.post("/api/business/learn", json={"website": "shop.example"}).json()
    assert "Ikeja ₦2,500" in d["shop_info"] and d["status"]["website"] == "ok" and len(d["docs"]) == 2
    assert "Ikeja" not in c.get("/api/business").json()["shop_info"]        # a draft until she saves

    saved = c.put("/api/business", json={"shop_info": d["shop_info"] + "\nNo returns on opened items.",
                                         "payment_details": "Transfer to GTB 0123456789", "links": d["links"],
                                         "docs": d["docs"]}).json()
    assert saved["shop_info"].endswith("No returns on opened items.") and saved["links"] == {"website": "shop.example"}
    assert [s["title"] for s in saved["sources"]] == ["Delivery", "About"]
    assert store.business["payment_details"] == "Transfer to GTB 0123456789"


def test_a_shop_can_be_loaded_from_a_file():
    from pathlib import Path

    from app.ledger.seed import demo_store
    store = demo_store(path=Path(__file__).resolve().parents[1] / "seed" / "my_shop.example.json")
    assert store.business["name"] == "Your Shop Name" and store.business["links"]["instagram"]
    assert store.business["shop_info"].startswith("Address:")
    assert {p.id for p in store.list_products()} >= {"p-indomie"} and store.stock_on_hand("p-indomie") == 10
