"""The public demo: per-visitor shops, cost caps, two phones, nothing real touched."""
import base64
import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app.demo import DemoRegistry, make_router
from app.promo.flow import PromoServices
from app.promo.imagegen import GradientBackground
from tests.test_agent import ScriptedLLM
from tests.test_promo import FakeCutter


def client_for(registry):
    app = FastAPI()
    app.include_router(make_router(lambda: registry))
    return TestClient(app)


def registry(llm=None, **kw):
    return DemoRegistry(llm, promo_factory=lambda media_dir, sid: PromoServices(FakeCutter(), GradientBackground(), None, None),
                        **kw)


def photo():
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), (200, 30, 30)).save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def test_each_visitor_gets_their_own_shop():
    llm = ScriptedLLM([
        [("find_product", {"query": "indomie"}), ("find_contact", {"query": "Chioma"})],
        [("record_entry", {"kind": "sale", "items": [{"product_id": "p-indomie", "qty": 2, "unit_price_naira": None}],
                           "contact_id": "c-chioma", "paid_naira": 0, "memo": "2 ctn Indomie to Chioma"})],
        "Done. 2 cartons Indomie to Chioma, ₦25,000.",
    ])
    c = client_for(registry(llm))
    a, b = c.post("/demo/api/session").json(), c.post("/demo/api/session").json()
    assert a["id"] != b["id"] and a["books"] == b["books"]
    r = c.post(f"/demo/api/{a['id']}/message", json={"who": "owner", "text": "I sell 2 carton Indomie give Chioma"}).json()
    assert r["messages"][-1] == {"to": "owner", "body": "Done. 2 cartons Indomie to Chioma, ₦25,000.", "buttons": [],
                                 "image": None, "template": False}
    assert r["tools"] == ["find_product", "find_contact", "record_entry"]
    assert r["books"]["recent"][0]["memo"] == "2 ctn Indomie to Chioma"
    assert c.get(f"/demo/api/{b['id']}/books").json() == b["books"]          # the other visitor is untouched


def test_customer_order_screenshot_and_owner_confirmation_cross_phones():
    llm = ScriptedLLM([
        [("find_product", {"query": "peak milk"})],
        [("create_order", {"items": [{"product_id": "p-milk", "qty": 6}]})],
        "That's ₦4,200. Transfer to DEMO BANK...",
        [("my_open_orders", {})],
        [("record_payment_claim", {"order_id": "PLACEHOLDER", "amount_naira": 4200})],
        "Thank you! The shop will confirm.",
    ])
    c = client_for(registry(llm))
    sid = c.post("/demo/api/session").json()["id"]
    c.post(f"/demo/api/{sid}/message", json={"who": "customer", "text": "How much for 6 Peak milk?"})
    order_id = [m for m in llm.seen[-1][0] if m.get("role") == "tool"][-1]["content"].split('"order_id": "')[1].split('"')[0]
    llm.turns[1] = [("record_payment_claim", {"order_id": order_id, "amount_naira": 4200})]
    r = c.post(f"/demo/api/{sid}/message", json={"who": "customer", "text": "I don pay", "image": photo()}).json()
    owner = next(m for m in r["messages"] if m["to"] == "owner")
    assert "Mama Tunde says they paid ₦4,200" in owner["body"] and owner["buttons"][0]["title"] == "Yes, I saw it"
    yes = c.post(f"/demo/api/{sid}/message", json={"who": "owner", "button_id": owner["buttons"][0]["id"]}).json()
    assert any(m["to"] == "customer" and "Payment confirmed" in m["body"] for m in yes["messages"])
    assert yes["books"]["cash"]["bank_kobo"] == 2_360_000 + 420_000


def test_reminder_from_the_8pm_summary_reaches_the_customer_phone_as_a_template():
    c = client_for(registry())
    sid = c.post("/demo/api/session").json()["id"]
    summary = c.post(f"/demo/api/{sid}/summary").json()["messages"][0]
    assert summary["body"].startswith("Today's books") and [b["id"] for b in summary["buttons"]] == \
        ["summary:reminders", "summary:post"]
    r = c.post(f"/demo/api/{sid}/message", json={"who": "owner", "button_id": "summary:reminders"}).json()
    preview = next(m for m in r["messages"] if m["buttons"])
    assert "Reminder for *Mama Tunde*" in preview["body"]
    sent = c.post(f"/demo/api/{sid}/message", json={"who": "owner", "button_id": preview["buttons"][0]["id"]}).json()
    to_customer = next(m for m in sent["messages"] if m["to"] == "customer")
    assert to_customer["template"] is True and "₦54,000" in to_customer["body"]


def test_demo_never_posts_to_a_real_account():
    llm = ScriptedLLM([[("make_promo", {"product_id": "p-milk"})], "Peak milk ₦700 per tin! WhatsApp us. #Lagos", ""])
    c = client_for(registry(llm))
    sid = c.post("/demo/api/session").json()["id"]
    r = c.post(f"/demo/api/{sid}/message", json={"who": "owner", "text": "post peak milk", "image": photo()}).json()
    preview = next(m for m in r["messages"] if m["buttons"])
    assert preview["image"].startswith(f"/demo/api/{sid}/media/") and "not really posted" in preview["body"]
    assert c.get(preview["image"]).headers["content-type"] == "image/jpeg"
    done = c.post(f"/demo/api/{sid}/message", json={"who": "owner", "button_id": preview["buttons"][0]["id"]}).json()
    assert done["messages"][0]["body"].startswith("Posted ✅ instagram: demo-")


def test_message_caps_and_expiry():
    reg = registry(max_messages=2, daily_messages=3)
    c = client_for(reg)
    s1 = c.post("/demo/api/session").json()["id"]
    for _ in range(2):
        assert c.post(f"/demo/api/{s1}/message", json={"who": "owner", "text": "hi"}).status_code == 200
    assert c.post(f"/demo/api/{s1}/message", json={"who": "owner", "text": "hi"}).status_code == 429
    s2 = c.post("/demo/api/session").json()["id"]
    assert c.post(f"/demo/api/{s2}/message", json={"who": "owner", "text": "hi"}).status_code == 200
    r = c.post(f"/demo/api/{s2}/message", json={"who": "owner", "text": "hi"})
    assert r.status_code == 429 and "today's limit" in r.json()["detail"]
    reg.ttl = -1
    assert c.get(f"/demo/api/{s1}/books").status_code == 404


def test_session_cap_drops_the_oldest():
    reg = registry(max_sessions=2)
    c = client_for(reg)
    first = c.post("/demo/api/session").json()["id"]
    c.post("/demo/api/session"), c.post("/demo/api/session")
    assert len(reg.sessions) == 2 and c.get(f"/demo/api/{first}/books").status_code == 404


def test_without_a_model_it_says_so_and_buttons_still_work():
    c = client_for(registry(None))
    sid = c.post("/demo/api/session").json()["id"]
    r = c.post(f"/demo/api/{sid}/message", json={"who": "owner", "text": "I sell rice"}).json()
    assert "isn't connected yet" in r["messages"][0]["body"]
    r = c.post(f"/demo/api/{sid}/message", json={"who": "owner", "button_id": "summary:reminders"}).json()
    assert any("Reminder for" in m["body"] for m in r["messages"])


def test_bad_input_is_refused():
    c = client_for(registry())
    sid = c.post("/demo/api/session").json()["id"]
    assert c.post(f"/demo/api/{sid}/message", json={"who": "admin", "text": "hi"}).status_code == 422
    assert c.post(f"/demo/api/{sid}/message", json={"who": "owner"}).status_code == 422
    assert c.post(f"/demo/api/{sid}/message", json={"who": "owner", "image": "data:text/html,<b>"}).status_code == 422
    assert c.get(f"/demo/api/{sid}/media/..%2F..%2Fsecret").status_code == 404
    assert c.get("/demo/api/nope/books").status_code == 404
