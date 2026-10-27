"""The promotion loop: books pick → cutout → background → flyer → caption → owner approval → post."""
import base64
import io
import json

import httpx
import pytest
from PIL import Image, ImageDraw

from app.agent import orchestrator
from app.agent.orchestrator import Agent
from app.ledger.posting import commit_proposal
from app.ledger.proposals import Item, Proposal
from app.promo import ranking
from app.promo.caption import caption_problems, template_caption, write_caption
from app.promo.compose import SIZE, compose
from app.promo.cutout import apply_mask, mask_from_detection
from app.promo.flow import PromoServices
from app.promo.imagegen import GradientBackground
from app.promo.publish import LocalMedia, MetaPublisher
from tests.test_agent import OWNER, ScriptedLLM, tool_results

LIMIT = 50_000_00


@pytest.fixture(autouse=True)
def fresh_state():
    orchestrator._history.clear()
    orchestrator._pending_documents.clear()


def shelf_photo():
    img = Image.new("RGB", (800, 600), (240, 240, 240))
    ImageDraw.Draw(img).rectangle((250, 150, 550, 450), fill=(200, 30, 30))   # "the carton"
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def sell(store, pid, qty, price):
    entry, v = commit_proposal(Proposal(kind="sale", items=[Item(pid, qty, price)], paid_kobo=qty * price,
                                        payment_account="cash"), store, LIMIT, approved=True)
    assert entry, v.to_json()


# --- ranking -------------------------------------------------------------------

def test_ranking_prefers_slow_well_stocked_good_margin(store):
    sell(store, "p-spag", 30, 110_000)   # spaghetti is flying off the shelf
    sell(store, "p-eggs", 5, 450_000)    # eggs now at their reorder level
    picks = ranking.rank(store, limit=None)
    names = [p.name for p in picks]
    assert "Egg crate" not in names                       # never promote what's about to run out
    assert names.index("Spaghetti pack") > names.index("Peak milk tin")
    top = picks[0]
    assert "in stock" in top.reason and "margin" in top.reason


def test_ranking_reason_uses_book_numbers(store):
    sell(store, "p-milk", 4, 70_000)
    milk = next(p for p in ranking.rank(store, limit=None) if p.product_id == "p-milk")
    assert milk.this_week == 4 and milk.weekly_avg == 1.0
    assert "44 tin in stock" in milk.reason and "17% margin (₦120 per tin)" in milk.reason


# --- cutout, flyer, caption ---------------------------------------------------------

def test_mask_shapes():
    size = (100, 80)
    poly = mask_from_detection({"polygon": [[10, 10], [60, 10], [60, 50]]}, size)
    assert poly.getbbox() == (10, 10, 61, 51)
    box = mask_from_detection({"bbox": [20, 10, 30, 40]}, size)   # COCO x, y, w, h
    assert box.getbbox() == (20, 10, 51, 51)
    norm = mask_from_detection({"box": [0.5, 0.5, 1.0, 1.0]}, size)
    assert norm.getbbox() == (50, 40, 100, 80)
    png = Image.new("L", size, 0)
    ImageDraw.Draw(png).ellipse((5, 5, 40, 40), fill=255)
    buf = io.BytesIO()
    png.save(buf, "PNG")
    assert mask_from_detection({"mask": base64.b64encode(buf.getvalue()).decode()}, size).getbbox() == (5, 5, 41, 41)
    assert mask_from_detection({"score": 0.9}, size) is None
    cut = apply_mask(Image.new("RGB", size, "red"), poly)
    assert cut.mode == "RGBA" and cut.size == (51, 41)


def test_flyer_is_instagram_portrait_with_product_composited():
    cut = Image.new("RGBA", (300, 300), (200, 30, 30, 255))
    flyer = compose(GradientBackground().generate(), cut, shop_name="Mama Nkechi Provisions",
                    product_name="Indomie carton", price_kobo=1_250_000, unit="carton", whatsapp="0801 234 5678")
    assert flyer.size == SIZE and flyer.mode == "RGB"
    r, g, b = flyer.getpixel((SIZE[0] // 2, int(SIZE[1] * 0.45)))
    assert (r, g, b) == (200, 30, 30)   # the real product, untouched


def test_price_on_flyer_is_never_a_broken_glyph():
    from PIL import ImageFont

    from app.promo.compose import _font, has_glyph, price_label
    label = price_label(1_250_000)
    assert label in ("₦12,500", "N12,500")
    assert has_glyph(_font(64), label[0])
    # Pillow's built-in font has no ₦: the probe must notice, so the flyer falls back to "N"
    assert not has_glyph(ImageFont.load_default(size=64), "₦")


def test_caption_guard_allows_only_the_list_price():
    assert caption_problems("Indomie now ₦12,500 per carton! #Lagos", 1_250_000) == []
    assert caption_problems("Only 12.5k! #Lagos", 1_250_000) == []
    assert caption_problems("Was ₦14,000, now ₦12,500!", 1_250_000)
    assert caption_problems("We have 120 cartons left", 1_250_000)
    assert caption_problems(template_caption("Shop", "Indomie carton", 1_250_000, "carton"), 1_250_000) == []


def test_caption_with_wrong_price_is_rewritten_then_falls_back():
    good = ScriptedLLM(["Big promo: Indomie ₦10,000!", "Indomie carton dey for ₦12,500. Holla us on WhatsApp! #Lagos"])
    text, src = write_caption(good, shop="S", product="Indomie carton", price_kobo=1_250_000, unit="carton", reason="r")
    assert src == "model" and "₦12,500" in text
    assert "Fix this" in good.seen[1][0][-1]["content"]
    bad = ScriptedLLM(["₦9,000 only!", "Still ₦9,000"])
    text, src = write_caption(bad, shop="S", product="Indomie carton", price_kobo=1_250_000, unit="carton", reason="r")
    assert src == "template" and "₦12,500" in text


# --- publisher ---------------------------------------------------------------------

def test_instagram_then_facebook_publish_calls():
    calls = []

    def handler(request):
        calls.append((request.url.path, dict(httpx.QueryParams(request.content.decode()))))
        if request.url.path.endswith("/media"):
            return httpx.Response(200, json={"id": "container1"})
        if request.url.path.endswith("/media_publish"):
            return httpx.Response(200, json={"id": "ig-post-9"})
        return httpx.Response(200, json={"id": "fb-photo-3", "post_id": "p"})

    pub = MetaPublisher(ig_user_id="178", page_id="555", token="tok", version="v21.0",
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert pub.publish("https://demo.test/media/a.jpg", "caption") == {"instagram": "ig-post-9", "facebook": "fb-photo-3"}
    assert calls[0][0] == "/v21.0/178/media" and calls[0][1]["image_url"] == "https://demo.test/media/a.jpg"
    assert calls[1][1]["creation_id"] == "container1"
    assert calls[2][0] == "/v21.0/555/photos"


def test_publish_needs_a_public_url():
    with pytest.raises(RuntimeError, match="PUBLIC_BASE_URL"):
        MetaPublisher(ig_user_id="1", token="t").publish(None, "c")


# --- the whole loop through the agent ----------------------------------------------

class FakeCutter:
    name = "fake-sam"

    def __init__(self):
        self.prompts = []

    def cutout(self, image_data_url, prompt):
        self.prompts.append(prompt)
        return Image.new("RGBA", (300, 300), (200, 30, 30, 255))


class FakePublisher:
    platforms = ["instagram"]

    def __init__(self):
        self.published = []

    def publish(self, url, caption):
        self.published.append((url, caption))
        return {"instagram": "ig-123"}


@pytest.fixture
def services(tmp_path):
    return PromoServices(FakeCutter(), GradientBackground(), LocalMedia(tmp_path, "https://demo.test"), FakePublisher())


def test_make_post_asks_for_photo_then_drafts_and_posts_on_tap(store, services):
    llm = ScriptedLLM([
        [("make_promo", {"product_id": "PLACEHOLDER"})],
        "Indomie carton dey for ₦12,500 per carton! Send us a WhatsApp message. #Lagos #NaijaShop",
        "Here's the draft.",
    ])
    agent = Agent(store, llm, OWNER, LIMIT, promo=services)
    # The 8 pm summary's button is handled by code: the books pick, she is asked for a photo
    ask = agent.handle(sender=OWNER, button_id="summary:post", button_title="Make tomorrow's post")
    top = ranking.rank(store, limit=1)[0]
    pick = {"product_id": top.product_id, "name": top.name}
    assert ask.outbox[0].body.startswith(f"My pick to promote: *{pick['name']}*")
    assert llm.seen == []

    llm.turns[0] = [("make_promo", {"product_id": pick["product_id"]})]
    draft = agent.handle(sender=OWNER, text="here", image_data_url=shelf_photo())
    preview = next(o for o in draft.outbox if o.buttons)
    assert [b["title"] for b in preview.buttons] == ["Post", "Change", "Skip"]
    assert preview.image_url.startswith("https://demo.test/media/") and preview.image_url.endswith(".jpg")
    assert "Why:" in preview.body and "in stock" in preview.body
    assert services.cutter.prompts == [store.get_product(pick["product_id"]).name]
    [post] = store.list_posts()
    assert post["status"] == "draft" and post["caption_source"] == "model"
    assert services.publisher.published == []          # nothing public before the tap

    done = agent.handle(sender=OWNER, button_id=preview.buttons[0]["id"])
    assert services.publisher.published == [(post["asset_url"], post["caption"])]
    assert store.get_post(post["id"])["status"] == "published"
    assert done.outbox[0].body.startswith("Posted ✅")


def test_change_recaptions_and_skip_posts_nothing(store, services):
    llm = ScriptedLLM([
        [("make_promo", {"product_id": "p-milk"})],
        "Peak milk tin for ₦700 per tin. WhatsApp us! #Lagos",
        "",
        "Peak milk don land o! ₦700 per tin. Abeg WhatsApp us. #Lagos #Naija",
    ])
    agent = Agent(store, llm, OWNER, LIMIT, promo=services)
    preview = next(o for o in agent.handle(sender=OWNER, text="post peak milk", image_data_url=shelf_photo()).outbox
                   if o.buttons)
    agent.handle(sender=OWNER, button_id=preview.buttons[1]["id"])          # Change
    revised = agent.handle(sender=OWNER, text="more Pidgin")
    new_preview = next(o for o in revised.outbox if o.buttons)
    assert "don land" in new_preview.body
    assert "more Pidgin" in llm.seen[-1][0][0]["content"]
    agent.handle(sender=OWNER, button_id=new_preview.buttons[2]["id"])      # Skip
    assert services.publisher.published == []
    assert {p["status"] for p in store.list_posts()} == {"skipped"}


def test_low_stock_product_is_not_promoted(store, services):
    sell(store, "p-sugar", 3, 1_650_000)   # 2 left, reorder level 2
    llm = ScriptedLLM([[("make_promo", {"product_id": "p-sugar"})], "Sugar is running low."])
    Agent(store, llm, OWNER, LIMIT, promo=services).handle(sender=OWNER, text="post sugar", image_data_url=shelf_photo())
    assert "reorder level" in tool_results(llm)[0]["error"]
    assert store.list_posts() == []


def test_failed_publish_is_reported_and_recorded(store, services):
    class Broken(FakePublisher):
        def publish(self, url, caption):
            raise RuntimeError("Graph API 190: token expired")
    services.publisher = Broken()
    llm = ScriptedLLM([[("make_promo", {"product_id": "p-milk"})], "Peak milk ₦700 per tin #Lagos", ""])
    agent = Agent(store, llm, OWNER, LIMIT, promo=services)
    preview = next(o for o in agent.handle(sender=OWNER, text="post", image_data_url=shelf_photo()).outbox if o.buttons)
    out = agent.handle(sender=OWNER, button_id=preview.buttons[0]["id"]).outbox[0]
    assert "token expired" in out.body
    assert store.list_posts()[0]["status"] == "failed"


def test_whatsapp_preview_payload_has_image_header_and_buttons():
    from app.channels.whatsapp import build_payload
    p = build_payload("2348", "Post this?", [{"id": "ap:1:0", "title": "Post"}], image_url="https://demo.test/m.jpg")
    assert p["interactive"]["header"] == {"type": "image", "image": {"link": "https://demo.test/m.jpg"}}
    assert json.dumps(p)  # serialisable
