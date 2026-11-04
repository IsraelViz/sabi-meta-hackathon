"""A shop owner signs up on /join herself, then signs in with her phone and PIN on any device."""
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import accounts, pilot
from app.api import require_owner, router as api_router
from app.ledger.seed import demo_store
from tests.test_pilot import shops  # noqa: F401  (fixture)

IFE = {"shop_name": "Ife's Book Corner", "kind": "books", "owner_name": "Ife", "phone": "0803 123 4567",
       "secret": "4826", "remember": True, "consent": True, "about": "Novels and children's books",
       "address": "Shop 5, Tejuosho Market, Yaba", "hours": "Monday to Saturday, 9am to 6pm",
       "delivery": "Yaba and Surulere ₦1,500", "payment": "Transfer to DEMO BANK 0000000000, Ife Books",
       "returns": "", "instagram": "@ifebooks"}


def app_for(shops):  # noqa: F811
    app = FastAPI()
    app.state.store, app.state.owner_token, app.state.shops = demo_store(), "main-key", shops
    app.state.private_media_dir = "private_media"
    app.include_router(api_router)
    app.include_router(pilot.owner_router(require_owner))
    app.include_router(pilot.join_router())
    return TestClient(app)


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_she_signs_up_and_lands_in_her_own_shop(shops):  # noqa: F811
    c = app_for(shops)
    r = c.post("/join/api/register", json=IFE)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    me = c.get("/api/me", headers=bearer(token)).json()
    assert me["shop"] == "Ife's Book Corner" and me["chat"]
    business = c.get("/api/business", headers=bearer(token)).json()
    assert business["shop_info"] == ("Novels and children's books. We are at Shop 5, Tejuosho Market, Yaba. "
                                     "Open Monday to Saturday, 9am to 6pm. Delivery: Yaba and Surulere ₦1,500. "
                                     "Payment: Transfer to DEMO BANK 0000000000, Ife Books.")
    assert business["payment_details"].startswith("Transfer to DEMO BANK")
    row = shops.db.execute("select owner_phone, pin_hash, consent_at from pilot.shops").fetchone()
    assert row["owner_phone"] == "2348031234567" and row["consent_at"] is not None
    assert row["pin_hash"].startswith("scrypt$") and row["pin_hash"].split("$")[2] != "4826"   # only the hash is kept
    assert accounts.verify_secret("4826", row["pin_hash"]) and not accounts.verify_secret("4827", row["pin_hash"])
    # The same number can't make a second shop
    assert c.post("/join/api/register", json=IFE).status_code == 409
    assert c.get("/join/api/phone", params={"phone": "08031234567"}).json() == {"valid": True, "taken": True}


def test_weak_pins_bad_numbers_and_no_consent_are_refused(shops):  # noqa: F811
    c = app_for(shops)
    for change, words in (({"secret": "1234"}, "too easy"), ({"secret": "12"}, "4 to 8 digits"),
                          ({"phone": "12345"}, "doesn't look right"), ({"consent": False}, "agree")):
        r = c.post("/join/api/register", json={**IFE, **change})
        assert r.status_code == 422 and words in r.text, (change, r.text)


def test_sign_in_on_another_phone_and_sign_out_only_there(shops):  # noqa: F811
    c = app_for(shops)
    first = c.post("/join/api/register", json=IFE).json()["token"]
    second = c.post("/join/api/login", json={"phone": "+234 803 123 4567", "secret": "4826", "remember": False}).json()
    assert c.get("/api/me", headers=bearer(second["token"])).status_code == 200
    assert c.post("/api/logout", headers=bearer(second["token"])).status_code == 200
    assert c.get("/api/me", headers=bearer(second["token"])).status_code == 401
    assert c.get("/api/me", headers=bearer(first)).status_code == 200          # her other phone stays in


def test_five_wrong_pins_lock_sign_in(shops):  # noqa: F811
    c = app_for(shops)
    c.post("/join/api/register", json=IFE)
    for left in (4, 3, 2, 1):
        r = c.post("/join/api/login", json={"phone": "08031234567", "secret": "0001"})
        assert r.status_code == 401 and f"{left} tr" in r.text
    r = c.post("/join/api/login", json={"phone": "08031234567", "secret": "0001"})
    assert "locked" in r.text
    r = c.post("/join/api/login", json={"phone": "08031234567", "secret": "4826"})   # even the right PIN waits
    assert r.status_code == 401 and "Try again in" in r.text


def test_a_session_ends_when_it_expires(shops):  # noqa: F811
    c = app_for(shops)
    token = c.post("/join/api/register", json={**IFE, "remember": False}).json()["token"]
    shops.db.execute("update pilot.sessions set expires_at = now() - interval '1 minute'")
    assert c.get("/api/me", headers=bearer(token)).status_code == 401
    assert accounts.SESSION == timedelta(hours=12) and accounts.REMEMBER == timedelta(days=180)


def test_deleting_her_shop_ends_every_session(shops):  # noqa: F811
    c = app_for(shops)
    token = c.post("/join/api/register", json=IFE).json()["token"]
    assert c.request("DELETE", "/api/shop", headers=bearer(token), json={}).status_code == 200
    assert c.get("/api/me", headers=bearer(token)).status_code == 401
    assert c.post("/join/api/register", json=IFE).status_code == 200            # the number is free again


# --- the judges' demo shop --------------------------------------------------------------

def test_judges_open_the_demo_shop_in_one_tap_and_never_see_the_full_number(shops, monkeypatch):  # noqa: F811
    import dataclasses

    from app import judge
    from app.config import settings
    monkeypatch.setattr(pilot, "settings", dataclasses.replace(settings, demo_login_phone="2347015065065",
                                                               demo_login_pin="2026"))
    c = app_for(shops)
    assert c.get("/join/api/demo").json()["available"] is False          # not made yet
    judge._create(shops, "2347015065065", "2026")                          # what start-up does, in the background
    info = c.get("/join/api/demo").json()
    assert info == {"available": True, "phone": "0701 XXXX 065", "pin": "2026"}
    assert "7015065065" not in c.get("/join/api/demo").text
    token = c.post("/join/api/demo").json()["token"]
    me = c.get("/api/me", headers=bearer(token)).json()
    assert me["shop"] == "Broadway Books" and me["chat"]
    products = {p["name"]: p for p in c.get("/api/products", headers=bearer(token)).json()}
    assert products["Emotional Intelligence"]["stock"] == 3 and products["Emotional Intelligence"]["photo"]
    assert "Agbowo, Ibadan" in shops.get(judge.find(shops, "2347015065065")).store.business["shop_info"]
    # Nobody can register a shop with the owner's number
    assert c.post("/join/api/register", json={**IFE, "phone": "07015065065"}).status_code == 409
    # Start-up again: no second shop, PIN kept to the setting
    judge.ensure(shops, "2347015065065", "2026")
    assert shops.db.execute("select count(*) as n from pilot.shops where owner_phone = '2347015065065'").fetchone()["n"] == 1


def test_masked_numbers():
    from app.judge import masked
    assert masked("2347015065065") == "0701 XXXX 065" and masked("447911123456") == "4479 XXXX 456"


def test_the_earlier_demo_shop_is_deleted_to_free_its_number(shops, monkeypatch):  # noqa: F811
    from app import judge
    row, _ = shops.create("Nkechi Books & Stationery", "books", "Nkechi")
    shops.accounts.set_login(row["id"], "2347015065065", "2026")
    monkeypatch.setattr(judge, "_create", lambda *a: None)     # the new one is built in the test above
    judge.ensure(shops, "2347015065065", "2026")
    assert shops.row(row["id"]) is None and judge.find(shops, "2347015065065") is None
