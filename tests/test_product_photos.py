"""Shopping by picture: the owner gives a product its photo; a customer's photo is matched to stock,
answered with the shop's own picture or a similar product in stock, and what we couldn't offer is
noted for the owner."""
import json

from app.agent.orchestrator import Agent
from app.ledger.posting import commit_proposal
from app.ledger.proposals import Item, Proposal
from app.ledger.reports import sales_pipeline
from app.promo.flow import PromoServices
from app.promo.imagegen import GradientBackground
from app.promo.publish import LocalMedia
from tests.test_agent import CUSTOMER, LIMIT, OWNER, ScriptedLLM, tool_results

PHOTO = "data:image/jpeg;base64,/9j/AAAA"


class Seeing(ScriptedLLM):
    """The scripted model, plus what the photo check sees."""

    def __init__(self, turns, seen):
        super().__init__(turns)
        self.photo_answer = seen

    def chat(self, messages, tools=None, **kw):
        content = messages[-1]["content"]
        if isinstance(content, list) and content[0]["text"].startswith("A customer of a small shop sent this photo"):
            self.catalogue = content[0]["text"]
            from app.models.llm import Reply
            return Reply(json.dumps(self.photo_answer), [], 0, self.model, {})
        return super().chat(messages, tools, **kw)


def agent(store, llm, tmp_path):
    promo = PromoServices(None, GradientBackground(), LocalMedia(tmp_path, "/shop"), None)
    return Agent(store, llm, OWNER, LIMIT, promo=promo)


def test_owner_gives_a_product_its_picture(store, tmp_path):
    llm = ScriptedLLM([[("find_product", {"query": "peak milk"})],
                       [("save_product_photo", {"product_id": "p-milk"})], "Saved."])
    agent(store, llm, tmp_path).handle(sender=OWNER, text="This is my Peak milk, show customers this picture",
                                       image_data_url=PHOTO)
    assert tool_results(llm)[-1]["status"] == "saved"
    url = store.get_product("p-milk").photo
    assert url.startswith("/shop/media/") and (tmp_path / url.rsplit("/", 1)[1]).is_file()


def test_customer_photo_of_something_in_stock_gets_the_shops_picture(store, tmp_path):
    store.set_product_photo("p-milk", "/shop/media/milk.jpg")
    llm = Seeing([[("match_photo", {})], [("send_product_photo", {"product_id": "p-milk"})], "We have it! How many?"],
                 {"seen": "Peak evaporated milk tin", "product_id": "p-milk", "sure": 0.93, "similar": []})
    ctx = agent(store, llm, tmp_path).handle(sender=CUSTOMER, text="(photo, no caption)", image_data_url=PHOTO)
    match = tool_results(llm)[0]
    assert match["match"]["product_id"] == "p-milk" and match["match"]["in_stock"] and match["match"]["has_photo"]
    assert "p-milk: Peak milk tin" in llm.catalogue           # the model chose from the shop's own list
    picture = [o for o in ctx.outbox if o.image_url]
    assert picture[0].image_url == "/shop/media/milk.jpg" and picture[0].to == CUSTOMER
    assert picture[0].body.startswith("Peak milk tin: ₦")       # the price comes from the books
    assert "p-milk" in store.get_lead(ctx.contact_id)["interests"]


def test_something_we_dont_sell_is_noted_and_a_similar_product_offered(store, tmp_path):
    llm = Seeing([[("match_photo", {})], "We don't have that one, but we have Spaghetti pack."],
                 {"seen": "Golden Penny spaghetti 500g", "product_id": None, "sure": 0, "similar": ["p-spag", "p-fake"]})
    ctx = agent(store, llm, tmp_path).handle(sender=CUSTOMER, text="you get this?", image_data_url=PHOTO)
    result = tool_results(llm)[0]
    assert result["match"] is None
    assert [r["product_id"] for r in result["similar_in_stock"]] == ["p-spag"]     # an invented id is dropped
    assert "golden penny spaghetti 500g (photo)" in store.get_lead(ctx.contact_id)["unmet"]
    assert {"item": "golden penny spaghetti 500g (photo)", "times": 1} in sales_pipeline(store)["missed"]


def test_a_guess_is_not_a_match_and_out_of_stock_is_noted(store, tmp_path):
    llm = Seeing([[("match_photo", {})], ""],
                 {"seen": "a carton of noodles", "product_id": "p-indomie", "sure": 0.4, "similar": ["p-indomie"]})
    a = agent(store, llm, tmp_path)
    ctx = a.handle(sender=CUSTOMER, text="this one", image_data_url=PHOTO)
    assert tool_results(llm)[0]["status"] == "asked_customer"      # half sure: ask, never guess
    q = ctx.outbox[0]
    assert "Indomie carton" in q.body and [b["title"] for b in q.buttons] == ["Indomie carton", "Something else"]
    assert not (store.get_lead(ctx.contact_id) or {}).get("unmet")   # nothing is missing until they answer
    llm.turns += [[("find_product", {"query": "Indomie carton"})], "Yes, we have it."]
    tap = a.handle(sender=CUSTOMER, button_id=q.buttons[0]["id"], button_title=q.buttons[0]["title"])
    assert tool_results(llm)[-1]["matches"][0]["product_id"] == "p-indomie" and tap.outbox

    entry, _ = commit_proposal(Proposal(kind="stock_loss", items=[Item("p-eggs", store.stock_on_hand("p-eggs"))]),
                               store, LIMIT, approved=True)      # she confirmed the eggs broke
    assert entry is not None and store.stock_on_hand("p-eggs") == 0
    llm = Seeing([[("match_photo", {})], "Sorry, eggs are finished."],
                 {"seen": "a crate of eggs", "product_id": "p-eggs", "sure": 0.9, "similar": []})
    ctx = agent(store, llm, tmp_path).handle(sender=CUSTOMER, text="I want this", image_data_url=PHOTO)
    assert tool_results(llm)[0]["match"]["in_stock"] is False
    assert "Egg crate (out of stock)" in store.get_lead(ctx.contact_id)["unmet"]


def test_no_photo_means_no_guess(store, tmp_path):
    llm = Seeing([[("match_photo", {})], "Please send the picture again."], {})
    agent(store, llm, tmp_path).handle(sender=CUSTOMER, text="do you have the thing in my picture")
    assert "error" in tool_results(llm)[0]


def test_an_unreadable_photo_gets_a_question_not_a_guess(store, tmp_path):
    llm = Seeing([[("match_photo", {})], ""],
                 {"seen": "a dark blurry packet", "unclear": "label not readable", "candidates": [], "product_id": None})
    ctx = agent(store, llm, tmp_path).handle(sender=CUSTOMER, text="(photo, no caption)", image_data_url=PHOTO)
    assert tool_results(llm)[0]["why_unclear"] == "label not readable"
    assert "clearer picture" in ctx.outbox[0].body and ctx.outbox[0].buttons == []
