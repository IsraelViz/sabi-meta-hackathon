"""The owner app's API, on both stores, with a week of demo history."""
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import router
from app.ledger.demo_week import add_history
from app.media.evidence_store import PrivateMedia
from app.promo.publish import LocalMedia

TOKEN = "test-owner-key"
TODAY = date.today()


@pytest.fixture
def setup(store, tmp_path):
    add_history(store, today=TODAY, media=LocalMedia(tmp_path / "public", ""))
    app = FastAPI()
    app.state.store = store
    app.state.owner_token = TOKEN
    app.state.private_media_dir = str(tmp_path / "private")
    app.include_router(router)
    return store, app, TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"}), tmp_path


def test_every_route_needs_the_owner_key(setup):
    _, app, _, _ = setup
    anon = TestClient(app)
    for path in ("/api/summary", "/api/entries", "/api/debtors", "/api/posts", "/api/products", "/api/report/pnl"):
        assert anon.get(path).status_code == 401, path
        assert anon.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401, path


def test_no_key_configured_allows_only_this_machine(setup):
    _, app, _, _ = setup
    app.state.owner_token = ""
    assert TestClient(app).get("/api/me").status_code == 200   # TestClient's host is local
    remote = TestClient(app, client=("203.0.113.9", 5000))
    assert remote.get("/api/me").status_code == 401


def test_summary(setup):
    _, _, c, _ = setup
    s = c.get("/api/summary").json()
    assert s["shop"] == "Mama Nkechi Provisions"
    assert s["today"]["sales_kobo"] == 660_000 + 420_000 + 900_000
    # Mama Tunde: ₦37,500 Indomie + ₦16,500 sugar; Chioma: ₦22,000 - ₦11,000
    assert s["owed_total_kobo"] == 3_750_000 + 1_650_000 + 1_100_000
    assert s["debtors"][0]["name"] == "Mama Tunde" and s["debtors"][0]["overdue_days"] == 2
    assert s["pending_questions"] == 1
    assert [r["kind"] for r in s["recent"]][:3] == ["sale", "sale", "sale"]


def test_entries_newest_first_with_paging_and_filters(setup):
    _, _, c, _ = setup
    first = c.get("/api/entries?limit=5").json()
    assert len(first["entries"]) == 5
    seqs = [e["seq"] for e in first["entries"]]
    assert seqs == sorted(seqs, reverse=True)
    rest = c.get(f"/api/entries?limit=100&before_seq={first['next_before_seq']}").json()
    assert rest["next_before_seq"] is None
    assert not {e["id"] for e in first["entries"]} & {e["id"] for e in rest["entries"]}
    assert all(e["kind"] != "opening" for e in first["entries"] + rest["entries"])

    sales = c.get("/api/entries?kind=sale&limit=100").json()["entries"]
    assert sales and all(e["kind"] == "sale" for e in sales)
    indomie = next(e for e in sales if e["contact"] == "Mama Tunde" and "Indomie" in e["items"][0])
    assert indomie["amount_kobo"] == 3_750_000 and indomie["items"] == ["3 × Indomie carton"]


def test_reversal_links_both_ways_and_shows_original_amount(setup):
    _, _, c, _ = setup
    entries = c.get("/api/entries?limit=100").json()["entries"]
    cancelled = next(e for e in entries if e["status"] == "reversed")
    rev = next(e for e in entries if e["kind"] == "reversal")
    assert cancelled["reversed_by"] == rev["id"] and rev["reverses_id"] == cancelled["id"]
    assert rev["amount_kobo"] == cancelled["amount_kobo"] == 1_650_000
    assert rev["items"] == ["1 × Sugar bag"]


def test_entry_detail_has_lines_and_evidence(setup):
    _, _, c, _ = setup
    entries = c.get("/api/entries?kind=sale&limit=100").json()["entries"]
    e = next(x for x in entries if x["contact"] == "Mama Tunde" and "Indomie" in x["items"][0])
    d = c.get(f"/api/entries/{e['id']}").json()
    assert sum(l["debit_kobo"] for l in d["lines"]) == sum(l["credit_kobo"] for l in d["lines"])
    assert {l["account_name"] for l in d["lines"]} == {"Accounts Receivable", "Sales", "Cost of Goods Sold", "Inventory"}
    assert d["evidence"]["original_text"].startswith("I sell 3 carton Indomie give Mama Tunde")
    assert d["evidence"]["model"] == "muse-spark-1.3"
    assert c.get("/api/entries/nope").status_code == 404


def test_voice_note_is_served_only_through_the_api(setup):
    store, app, c, tmp = setup
    media_url = PrivateMedia(tmp / "private").save(b"OggS-fake-audio", "audio/ogg; codecs=opus")
    ev = store.add_evidence(channel="whatsapp", original_text="sold 2 rice", media_url=media_url)
    detail = {"media": None}
    from app.api import _evidence
    detail = _evidence(store.get_evidence(ev))
    assert detail["media"] == {"kind": "audio", "url": f"/api/evidence/{ev}/media"}
    r = c.get(detail["media"]["url"])
    assert r.status_code == 200 and r.content == b"OggS-fake-audio" and r.headers["content-type"] == "audio/ogg"
    assert "private" in r.headers["cache-control"]
    assert TestClient(app).get(detail["media"]["url"]).status_code == 401


def test_media_path_traversal_is_refused(setup):
    store, _, c, tmp = setup
    (tmp / "secret.txt").write_text("nope")
    ev = store.add_evidence(channel="whatsapp", media_url="private:../secret.txt")
    assert c.get(f"/api/evidence/{ev}/media").status_code == 404


def test_debtor_statement_has_running_balance(setup):
    _, _, c, _ = setup
    s = c.get("/api/debtors/c-chioma").json()
    assert [r["change_kobo"] for r in s["rows"]] == [2_200_000, -1_100_000]
    assert [r["balance_kobo"] for r in s["rows"]] == [2_200_000, 1_100_000]
    assert s["owes_kobo"] == 1_100_000
    ade = c.get("/api/debtors/c-ade").json()
    assert ade["owes_kobo"] == 0 and len(ade["rows"]) == 2   # the wrong sale and its cancellation


def test_profit_week_and_products(setup):
    _, _, c, _ = setup
    pnl = c.get("/api/report/pnl?period=all").json()
    assert pnl["sales_kobo"] > pnl["gross_profit_kobo"] > pnl["net_profit_kobo"]
    assert pnl["expenses_kobo"]["transport"] == 350_000
    assert pnl["expenses_kobo"]["stock_loss"] == 380_000
    products = {p["id"]: p for p in c.get("/api/products").json()}
    assert products["p-indomie"]["stock"] == 12 + 10 - 3 - 4
    assert products["p-eggs"]["stock"] == 10 - 1 - 2


def test_posts_with_private_image(setup):
    _, _, c, _ = setup
    [p] = c.get("/api/posts").json()
    assert p["status"] == "published" and p["product"] == "Peak milk tin"
    assert p["platform_ids"] == {"instagram": "demo-17890000000"}
    img = c.get(p["image"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"


def test_web_app_files_exist_and_reference_each_other():
    from pathlib import Path
    web = Path(__file__).resolve().parents[1] / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    for f in ("app.css", "app.js", "manifest.webmanifest", "icon.svg"):
        assert f in html or f == "icon.svg" and "icon.svg" in html
        assert (web / f).exists()
    assert (web / "sw.js").exists()
