"""Receipts, notebook pages and shelf stock-takes, end to end with a scripted model."""
import json

import pytest

from app.agent import orchestrator
from app.agent.orchestrator import Agent
from app.agent.stocktake import compare, tolerance
from app.media.counting import SamCounter, _detections
from app.media.documents import check_arithmetic
from tests.test_agent import OWNER, ScriptedLLM, tool_results

LIMIT = 50_000_00
PHOTO = "data:image/jpeg;base64,AAAA"


@pytest.fixture(autouse=True)
def fresh_state():
    orchestrator._history.clear()
    orchestrator._pending_documents.clear()


def receipt(lines, total, doc_type="supplier_receipt"):
    return json.dumps({"doc_type": doc_type, "supplier": "Alaba Depot", "date": "2026-10-27",
                       "lines": lines, "total_naira": total, "unreadable": []})


# --- arithmetic check --------------------------------------------------------

def test_arithmetic_ok():
    doc = json.loads(receipt([{"description": "Indomie", "qty": 10, "unit_price_naira": 11000, "line_total_naira": 110000},
                              {"description": "Sugar", "qty": 2, "unit_price_naira": 14500, "line_total_naira": None}],
                             139000))
    assert check_arithmetic(doc) == []


def test_arithmetic_catches_bad_line_and_bad_total():
    doc = json.loads(receipt([{"description": "Indomie", "qty": 10, "unit_price_naira": 11000, "line_total_naira": 100000},
                              {"description": "Sugar", "qty": 2, "unit_price_naira": 14500, "line_total_naira": 29000}],
                             139000))
    issues = check_arithmetic(doc)
    assert "Indomie: 10 × ₦11,000 = ₦110,000, but the line says ₦100,000" in issues
    assert "the lines add up to ₦129,000, but the total says ₦139,000" in issues


def test_unreadable_parts_are_reported():
    doc = {"lines": [], "total_naira": None, "unreadable": ["the date"]}
    assert check_arithmetic(doc) == ["I couldn't read: the date"]


# --- receipt flow --------------------------------------------------------------

def test_supplier_receipt_becomes_one_purchase_after_one_tap(store):
    llm = ScriptedLLM([
        [("read_document", {})],
        receipt([{"description": "Indomie super pack", "qty": 10, "unit_price_naira": 11500, "line_total_naira": 115000}],
                115000),
        [("find_product", {"query": "Indomie super pack"}), ("find_contact", {"query": "Alaba Depot"})],
        [("record_batch", {"title": "Alaba Depot receipt", "entries": [
            {"kind": "purchase", "items": [{"product_id": "p-indomie", "qty": 10, "unit_price_naira": 11500}],
             "contact_id": "c-depot", "paid_naira": 0, "memo": "Alaba Depot receipt"}]})],
        "",
    ])
    agent = Agent(store, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, text="(photo, no caption)", image_data_url=PHOTO)
    ask = ctx.outbox[0]
    assert ask.body.startswith("Alaba Depot receipt: 1 entry, ₦115,000")
    assert ctx.committed == []
    agent.handle(sender=OWNER, button_id=ask.buttons[0]["id"])
    assert store.stock_on_hand("p-indomie") == 22
    assert store.account_balance("ap", "c-depot") == -11_500_000
    # (12 × 11,000 + 10 × 11,500) / 22 = 11,227.27 → 1,122,727 kobo
    assert store.get_product("p-indomie").avg_cost_kobo == 1_122_727
    ev = store.get_evidence(store.list_entries()[-1].evidence_id)
    assert ev["extracted_json"]["document"]["supplier"] == "Alaba Depot"


def test_receipt_that_doesnt_add_up_is_asked_about_not_recorded(store):
    llm = ScriptedLLM([
        [("read_document", {})],
        receipt([{"description": "Sugar", "qty": 2, "unit_price_naira": 14500, "line_total_naira": 30000}], 30000),
        # The model tries to record anyway
        [("record_batch", {"entries": [{"kind": "purchase", "items": [
            {"product_id": "p-sugar", "qty": 2, "unit_price_naira": 15000}], "paid_naira": 30000,
            "payment_account": "cash", "memo": "sugar"}]})],
        "",
    ])
    ctx = Agent(store, llm, OWNER, LIMIT).handle(sender=OWNER, text="receipt", image_data_url=PHOTO)
    assert "numbers don't agree" in ctx.outbox[0].body
    assert "Sugar: 2 × ₦14,500 = ₦29,000, but the line says ₦30,000" in ctx.outbox[0].body
    assert tool_results(llm)[-1]["status"] == "rejected"
    assert store.stock_on_hand("p-sugar") == 5
    # The next message gets the earlier document in its prompt
    assert OWNER in orchestrator._pending_documents


def test_notebook_page_batches_clean_lines_and_asks_about_the_odd_one(store):
    llm = ScriptedLLM([
        [("read_document", {})],
        receipt([{"description": "milk", "qty": 6, "unit_price_naira": 700, "line_total_naira": 4200, "customer": None, "paid": True},
                 {"description": "spag", "qty": 10, "unit_price_naira": 1100, "line_total_naira": 11000, "customer": "Kemi", "paid": True},
                 {"description": "eggs", "qty": 1, "unit_price_naira": 450, "line_total_naira": 450, "customer": None, "paid": True}],
                15650, doc_type="notebook_sales"),
        [("find_product", {"query": "milk"}), ("find_product", {"query": "spaghetti"}), ("find_product", {"query": "eggs"})],
        [("record_batch", {"title": "Notebook page", "entries": [
            {"kind": "sale", "items": [{"product_id": "p-milk", "qty": 6, "unit_price_naira": 700}],
             "paid_naira": 4200, "payment_account": "cash", "memo": "milk"},
            {"kind": "sale", "items": [{"product_id": "p-spag", "qty": 10, "unit_price_naira": 1100}],
             "paid_naira": 11000, "payment_account": "cash", "memo": "spag Kemi"},
            {"kind": "sale", "items": [{"product_id": "p-eggs", "qty": 1, "unit_price_naira": 450}],
             "paid_naira": 450, "payment_account": "cash", "memo": "eggs"}]})],
        "",
    ])
    agent = Agent(store, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, text="today's page", image_data_url=PHOTO)
    bodies = [o.body for o in ctx.outbox]
    # ₦450 for a ₦4,500 crate of eggs is asked on its own...
    assert any("usual price is ₦4,500" in b for b in bodies)
    # ...the two clean lines go together for one Confirm
    batch = next(o for o in ctx.outbox if o.body.startswith("Notebook page: 2 entries, ₦15,200"))
    agent.handle(sender=OWNER, button_id=batch.buttons[0]["id"])
    assert store.account_balance("cash") == 1_520_000
    assert store.stock_on_hand("p-milk") == 42 and store.stock_on_hand("p-spag") == 50


# --- stock-take ------------------------------------------------------------------

class FakeCounter:
    name = "fake"

    def __init__(self, counts):
        self.counts = counts
        self.prompts = None

    def count(self, image, prompts):
        self.prompts = prompts
        return {k: {"count": self.counts.get(k, 0), "detections": []} for k in prompts}


def test_tolerance_and_compare():
    assert tolerance(5) == 1 and tolerance(48) == 5
    assert compare(12, 9, False) == "missing"
    assert compare(12, 11, False) == "ok"
    assert compare(12, 0, False) == "not_seen"
    assert compare(12, 0, True) == "missing"
    assert compare(6, 9, False) == "extra"


def stock_take_agent(store, counts):
    llm = ScriptedLLM([[("stock_take", {})], "Rice and milk match. Indomie is short; I've asked you about it."])
    return Agent(store, llm, OWNER, LIMIT, counter=FakeCounter(counts)), llm


def test_stock_take_flags_missing_cartons_and_records_unrecorded_sale(store):
    agent, llm = stock_take_agent(store, {"p-indomie": 9, "p-rice": 6, "p-milk": 47})
    ctx = agent.handle(sender=OWNER, text="check my stock", image_data_url=PHOTO)
    result = tool_results(llm)[0]
    by_name = {r["name"]: r["status"] for r in result["results"]}
    assert by_name["Indomie carton"] == "missing" and by_name["Rice 50kg bag"] == "ok"
    assert by_name["Peak milk tin"] == "ok" and by_name["Groundnut oil 5L"] == "not_seen"
    gap = ctx.outbox[0]
    assert gap.body.startswith("Books say 12 carton of Indomie carton; I count 9")
    assert [b["title"] for b in gap.buttons] == ["Sold, not recorded", "Damaged or lost", "Recount"]

    paid_q = agent.handle(sender=OWNER, button_id=gap.buttons[0]["id"]).outbox[0]
    assert "Has this ₦37,500 been paid?" in paid_q.body
    done = agent.handle(sender=OWNER, button_id=paid_q.buttons[0]["id"])   # Paid cash
    assert [e.kind for e in done.committed] == ["sale"]
    assert store.stock_on_hand("p-indomie") == 9
    assert store.account_balance("cash") == 3_750_000
    # The shelf photo is the evidence behind the sale
    assert store.get_evidence(done.committed[0].evidence_id)["extracted_json"]["stocktake"]


def test_stock_take_damaged_posts_stock_loss(store):
    agent, _ = stock_take_agent(store, {"p-indomie": 9})
    gap = agent.handle(sender=OWNER, text="count", image_data_url=PHOTO).outbox[0]
    done = agent.handle(sender=OWNER, button_id=gap.buttons[1]["id"])
    assert [e.kind for e in done.committed] == ["stock_loss"]
    assert store.account_balance("stock_loss") == 3_300_000


def test_stock_take_recount_changes_nothing(store):
    agent, _ = stock_take_agent(store, {"p-indomie": 9})
    gap = agent.handle(sender=OWNER, text="count", image_data_url=PHOTO).outbox[0]
    reply = agent.handle(sender=OWNER, button_id=gap.buttons[2]["id"]).outbox[0]
    assert "nothing changed" in reply.body
    assert store.stock_on_hand("p-indomie") == 12


def test_stock_take_needs_a_photo(store):
    llm = ScriptedLLM([[("stock_take", {})], "Please send a photo of the shelf."])
    Agent(store, llm, OWNER, LIMIT, counter=FakeCounter({})).handle(sender=OWNER, text="check my stock")
    assert "no photo" in tool_results(llm)[0]["error"]


def test_sam_response_parsing_and_score_filter(monkeypatch):
    assert len(_detections({"masks": [{}, {}, {}]})) == 3
    assert len(_detections({"scores": [0.9, 0.2]})) == 2
    with pytest.raises(ValueError):
        _detections({"weird": 1})
    sam = SamCounter(endpoint="https://example.invalid/sam")
    monkeypatch.setattr(sam, "segment", lambda img, prompt: {"detections": [{"score": 0.9}, {"score": 0.3}, {"score": 0.7}]})
    assert sam.count(PHOTO, {"p-indomie": "Indomie carton"})["p-indomie"]["count"] == 2
