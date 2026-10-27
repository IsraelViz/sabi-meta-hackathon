"""Payment reminders: 24-hour window, template fallback, owner approval, the 8 pm button."""
from datetime import timedelta

import pytest

from app.agent import orchestrator
from app.agent.orchestrator import Agent
from app.agent.reminders import TEMPLATE_TEXT, in_window, wording_problems
from app.agent.tools import normalize_phone
from app.channels.whatsapp import build_payload
from app.ledger.demo_week import add_history
from app.ledger.posting import commit_proposal
from app.ledger.proposals import Proposal
from app.ledger.store import now
from tests.test_agent import OWNER, ScriptedLLM, tool_results

LIMIT = 50_000_00
TUNDE, CHIOMA = "2348030000001", "2348030000002"


@pytest.fixture(autouse=True)
def fresh_state():
    orchestrator._history.clear()


@pytest.fixture
def shop(store):
    add_history(store)                      # Mama Tunde owes ₦54,000 (2 days late), Chioma ₦11,000
    store.set_contact_phone("c-tunde", TUNDE)
    store.set_contact_phone("c-chioma", CHIOMA)
    return store


def previews(ctx):
    return [o for o in ctx.outbox if o.to == OWNER and o.buttons]


def test_eight_pm_button_drafts_template_reminders_outside_the_window(shop):
    agent = Agent(shop, None, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, button_id="summary:reminders", button_title="Send reminders")
    first, second = previews(ctx)
    assert first.body.startswith("Reminder for *Mama Tunde* (owes ₦54,000, 2 days late)")
    assert "Hello Mama Tunde, this is a friendly reminder from Mama Nkechi Provisions that ₦54,000" in first.body
    assert "only allows the approved reminder wording" in first.body
    assert [b["title"] for b in first.buttons] == ["Send", "Skip"]      # template wording can't be changed
    assert "Chioma" in second.body

    sent = agent.handle(sender=OWNER, button_id=first.buttons[0]["id"])
    to_customer = next(o for o in sent.outbox if o.to == TUNDE)
    assert to_customer.template == {"name": "payment_reminder", "language": "en",
                                    "params": ["Mama Tunde", "Mama Nkechi Provisions", "₦54,000"]}
    assert to_customer.reminder_id
    assert sent.outbox[-1].body == "Sent to Mama Tunde ✅"
    assert shop.list_reminders("c-tunde")[0]["status"] == "sent"

    again = agent.handle(sender=OWNER, button_id="summary:reminders")
    assert any("Already reminded in the last 3 days: Mama Tunde" in o.body for o in again.outbox)
    assert not any("Mama Tunde" in o.body for o in previews(again))


def test_inside_the_window_the_model_drafts_and_she_can_change_it(shop):
    shop.touch_inbound("c-chioma")
    llm = ScriptedLLM([
        "Chioma dear, abeg remember the ₦15,000 balance.",                     # wrong amount: rejected
        "Chioma dear, good evening! Just a reminder say ₦11,000 still dey. Thank you!",
        "Chioma my sister, how far? Na ₦11,000 remain o. God bless!",          # after "more Pidgin"
    ])
    agent = Agent(shop, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, button_id="summary:reminders")
    chioma = next(o for o in previews(ctx) if "Chioma" in o.body)
    assert "₦11,000 still dey" in chioma.body and "₦15,000" not in chioma.body
    assert "Fix this" in llm.seen[1][0][-1]["content"]
    assert [b["title"] for b in chioma.buttons] == ["Send", "Change", "Skip"]

    agent.handle(sender=OWNER, button_id=chioma.buttons[1]["id"])          # Change
    revised = previews(agent.handle(sender=OWNER, text="more Pidgin"))[0]
    assert "How far" in revised.body or "how far" in revised.body
    assert "more Pidgin" in llm.seen[-1][0][0]["content"]

    sent = agent.handle(sender=OWNER, button_id=revised.buttons[0]["id"])
    msg = next(o for o in sent.outbox if o.to == CHIOMA)
    assert msg.template is None and msg.body.startswith("Chioma my sister")
    statuses = sorted(r["status"] for r in shop.list_reminders("c-chioma"))
    assert statuses == ["sent", "skipped"]


def test_reminder_is_not_sent_if_she_paid_in_the_meantime(shop):
    agent = Agent(shop, None, OWNER, LIMIT)
    preview = next(o for o in previews(agent.handle(sender=OWNER, button_id="summary:reminders")) if "Chioma" in o.body)
    commit_proposal(Proposal(kind="payment_in", contact_id="c-chioma", amount_kobo=500_000, payment_account="cash"),
                    shop, LIMIT)
    out = agent.handle(sender=OWNER, button_id=preview.buttons[0]["id"]).outbox
    assert not any(o.to == CHIOMA for o in out)
    assert "balance changed" in out[0].body and "₦6,000" in out[0].body


def test_customers_without_numbers_are_named(store):
    add_history(store)
    ctx = Agent(store, None, OWNER, LIMIT).handle(sender=OWNER, button_id="summary:reminders")
    assert previews(ctx) == []
    assert "I don't have WhatsApp numbers for Mama Tunde, Chioma" in ctx.outbox[-1].body


def test_skip_sends_nothing(shop):
    agent = Agent(shop, None, OWNER, LIMIT)
    first = previews(agent.handle(sender=OWNER, button_id="summary:reminders"))[0]
    out = agent.handle(sender=OWNER, button_id=first.buttons[-1]["id"]).outbox
    assert out[0].body == "OK, no reminder for Mama Tunde." and not any(o.to == TUNDE for o in out)


def test_send_reminder_tool_and_its_refusals(shop):
    llm = ScriptedLLM([
        [("find_contact", {"query": "Mama Ade"}), ("send_reminder", {"contact_id": "c-ade"})],
        "Mama Ade doesn't owe you anything.",
    ])
    Agent(shop, llm, OWNER, LIMIT).handle(sender=OWNER, text="remind Mama Ade")
    assert "doesn't owe anything" in tool_results(llm)[-1]["error"]

    llm = ScriptedLLM([[("send_reminder", {"contact_id": "c-tunde"})], ""])
    agent = Agent(shop, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, text="remind Mama Tunde")
    assert tool_results(llm)[-1]["status"] == "waiting_for_owner"
    agent.handle(sender=OWNER, button_id=previews(ctx)[0].buttons[0]["id"])   # Send
    llm.turns = [[("send_reminder", {"contact_id": "c-tunde"})], ""]
    agent.handle(sender=OWNER, text="remind her again")
    assert "already reminded today" in tool_results(llm)[-1]["error"]


def test_owner_can_save_a_phone_number(store):
    add_history(store)
    llm = ScriptedLLM([[("set_contact_phone", {"contact_id": "c-tunde", "phone": "0803 123 4567"})], "Saved."])
    Agent(store, llm, OWNER, LIMIT).handle(sender=OWNER, text="Mama Tunde number na 0803 123 4567")
    assert store.get_contact("c-tunde").phone == "2348031234567"


def test_customer_message_opens_the_window(shop):
    assert not in_window(shop.get_contact("c-tunde"))
    llm = ScriptedLLM(["Good evening! How can we help?"])
    Agent(shop, llm, OWNER, LIMIT).handle(sender=TUNDE, text="Good evening")
    assert in_window(shop.get_contact("c-tunde"))
    shop.touch_inbound("c-tunde", now() - timedelta(hours=25))
    assert not in_window(shop.get_contact("c-tunde"))


def test_make_post_button_asks_for_a_photo(shop, tmp_path):
    from app.promo.flow import PromoServices
    from app.promo.imagegen import GradientBackground
    from app.promo.publish import LocalMedia
    from tests.test_promo import FakeCutter, FakePublisher
    services = PromoServices(FakeCutter(), GradientBackground(), LocalMedia(tmp_path, ""), FakePublisher())
    ctx = Agent(shop, None, OWNER, LIMIT, promo=services).handle(sender=OWNER, button_id="summary:post")
    assert ctx.outbox[0].body.startswith("My pick to promote:")


def test_wording_guard():
    assert wording_problems("Hello, ₦11,000 still dey. Thanks!", 1_100_000) == []
    assert wording_problems("Hello, 11k still dey.", 1_100_000) == []
    assert wording_problems("Pay ₦11,000 or ₦2,000 fee", 1_100_000)
    assert wording_problems("Please remember your balance.", 1_100_000) == ["must mention ₦11,000"]


@pytest.mark.parametrize("raw, expected", [
    ("0803 123 4567", "2348031234567"), ("+234 803 123 4567", "2348031234567"), ("8031234567", "2348031234567"),
    ("2348031234567", "2348031234567"), ("12345", None), ("+23480312", None), ("+44 7911 123456", "447911123456"),
])
def test_phone_numbers(raw, expected):
    assert normalize_phone(raw) == expected


def test_template_payload_and_submission_body():
    p = build_payload("2348030000001", "ignored", template={"name": "payment_reminder", "language": "en",
                                                             "params": ["Mama Tunde", "Shop", "₦54,000"]})
    assert p["type"] == "template" and p["template"]["language"] == {"code": "en"}
    assert [x["text"] for x in p["template"]["components"][0]["parameters"]] == ["Mama Tunde", "Shop", "₦54,000"]
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "tmpl", Path(__file__).resolve().parents[1] / "scripts" / "create_reminder_template.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    body = mod.template_request()
    assert body["category"] == "UTILITY" and body["components"][0]["text"] == TEMPLATE_TEXT
    assert len(body["components"][0]["example"]["body_text"][0]) == 3
