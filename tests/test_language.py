"""Any language: telling which one she writes in, French and other ways of writing amounts, and
translating code-written messages without ever changing a number."""
import json
import re

from app import prefs
from app.agent import language
from app.agent.orchestrator import Agent
from app.jobs.daily_summary import summary_message
from app.ledger.posting import commit_proposal
from app.ledger.proposals import Item, Proposal
from app.models.llm import Reply
from app.normalize.amounts import normalize
from tests.test_agent import LIMIT, OWNER, CUSTOMER, ScriptedLLM


class Translating(ScriptedLLM):
    """The scripted model, plus a translator that marks each text "FR: ..." (or breaks the numbers)."""

    def __init__(self, turns, break_numbers=False):
        super().__init__(turns)
        language._CACHE.clear()
        self.break_numbers = break_numbers
        self.translations = []

    def chat(self, messages, tools=None, **kw):
        prompt = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        if prompt.startswith("Translate each text"):
            self.translations.append(prompt)
            texts = [json.loads(line.split(" ", 1)[1].removeprefix("(button) "))
                     for line in prompt.split("Texts:\n", 1)[1].splitlines() if line.strip()]
            out = [("FR: " + t) if not self.break_numbers else re.sub(r"\d", "9", "FR: " + t) for t in texts]
            out = [o if "Other" not in o else "Autre montant" for o in out]
            return Reply(json.dumps({"t": out}), [], 0, self.model, {})
        return super().chat(messages, tools, **kw)


def test_detects_the_language_she_writes_in():
    assert language.detect("J'ai vendu 3 cartons d'Indomie à Chioma, elle a payé en espèces") == "French"
    assert language.detect("vendi 2 sacos de arroz, pagou em dinheiro") == "Portuguese"
    assert language.detect("Nimeuza katoni 3 za Indomie kwa Chioma, amelipa taslimu") == "Swahili"
    assert language.detect("بعت ثلاثة كراتين") == "Arabic"
    assert language.detect("sold 3 carton indomie to mama tunde for cash") == "English"
    assert language.detect("Mo ta àpò ìrẹsì méjì") == "Yoruba"
    assert language.detect("ok") is None and language.detect("") is None


def test_french_and_other_ways_of_writing_amounts():
    assert normalize("vendu 3 cartons à 12 500 F chacun").text == "vendu 3 cartons à ₦12,500 chacun"
    assert normalize("Mama Ade doit 15 000 francs").text == "Mama Ade doit ₦15,000"
    assert normalize("2 sacs de riz 78.000 cash").text == "2 sacs de riz 78,000 cash"
    assert normalize("transport 2 mille espèces").text == "transport ₦2,000 espèces"
    assert normalize("1,5k").text == "₦1,500"
    assert normalize("vendi por 15 mil").text == "vendi por ₦15,000"
    assert normalize("nimelipa elfu 5").text == "nimelipa ₦5,000"
    # Without money next to it, "3 500" may be 3 items at 500: left for the model, never guessed.
    assert normalize("sold 3 500 each").text == "sold 3 500 each"
    assert normalize("vendu 12 500 F").original == "vendu 12 500 F"   # her exact words are kept


def test_a_translation_that_changes_a_number_is_not_sent():
    assert language.safe("Sold 3 for ₦12,500. 9 left.", "Vendu 3 pour ₦12,500. Il en reste 9.")
    assert not language.safe("Sold 3 for ₦12,500.", "Vendu 3 pour ₦1,250.")
    assert not language.safe("Sold 3 for ₦12,500.", "Vendu trois pour ₦12,500.")      # a number dropped
    assert not language.safe("Send reminders", "Envoyer les rappels maintenant", button=True)   # too long
    assert not language.safe("Hi", "")


def owes_chioma(store):
    entry, _ = commit_proposal(Proposal(kind="sale", items=[Item("p-oil", 3, 1_100_000)],
                                        contact_id="c-chioma", paid_kobo=0), store, LIMIT)
    assert entry is not None


UNCLEAR = [
    [("find_contact", {"query": "Chioma"})],
    [("record_entry", {"kind": "payment_in", "contact_id": "c-chioma", "amount_naira": 25000,
                       "payment_account": "bank", "memo": "Chioma paid",
                       "unclear_amounts": [{"path": "amount_kobo", "text": "deux-cinq",
                                            "candidates_naira": [2500, 25000]}]})],
    "",
]


def test_code_written_question_reaches_her_in_french_with_the_same_numbers(store):
    owes_chioma(store)
    llm = Translating(UNCLEAR)
    agent = Agent(store, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=OWNER, text="Chioma a payé deux-cinq par virement aujourd'hui")
    q = ctx.outbox[0]
    assert q.body.startswith("FR: ")
    assert [b["title"] for b in q.buttons] == ["FR: ₦2,500", "FR: ₦25,000", "Autre montant"]
    assert prefs.get(store)["_language"] == "French"
    # Button ids are untouched, so her tap still settles it.
    tap = agent.handle(sender=OWNER, button_id=q.buttons[1]["id"], button_title=q.buttons[1]["title"])
    assert [e.kind for e in tap.committed] == ["payment_in"]
    assert store.account_balance("bank") == 2_500_000
    assert tap.outbox[0].body.startswith("FR: Done.")


def test_broken_translation_falls_back_to_english(store):
    owes_chioma(store)
    llm = Translating(UNCLEAR, break_numbers=True)
    ctx = Agent(store, llm, OWNER, LIMIT).handle(sender=OWNER, text="Chioma a payé deux-cinq par virement aujourd'hui")
    q = ctx.outbox[0]     # the question has no numbers, so its translation passes; the amounts don't
    assert [b["title"] for b in q.buttons][:2] == ["₦2,500", "₦25,000"]


def test_english_and_pidgin_are_never_sent_to_the_translator(store):
    owes_chioma(store)
    llm = Translating(UNCLEAR)
    Agent(store, llm, OWNER, LIMIT).handle(sender=OWNER, text="Chioma don pay two-five by transfer abeg")
    assert llm.translations == []


def test_her_chosen_language_and_the_models_own_reply(store):
    _, errors = prefs.save(store, {"language": "Swahili"})
    assert not errors and prefs.get(store)["language"] == "Swahili"
    assert prefs.save(store, {"language": "12; drop"})[1]          # not a language name
    llm = Translating(["Nimerekodi."])
    ctx = Agent(store, llm, OWNER, LIMIT).handle(sender=OWNER, text="habari ya biashara?")
    assert ctx.outbox[0].body == "Nimerekodi." and llm.translations == []   # the model's reply is already hers
    msg = summary_message(store, "daily", llm=llm)
    assert msg["body"].startswith("FR: Today's books") and "into Swahili" in llm.translations[0]
    assert [b["id"] for b in msg["buttons"]] == ["summary:reminders", "summary:post"]


def test_a_french_customer_gets_code_written_replies_in_french(store):
    llm = Translating([])     # the model fails: the apology code writes is translated for her
    ctx = Agent(store, llm, OWNER, LIMIT).handle(sender=CUSTOMER, text="Bonjour, je veux une remise sur le riz s'il vous plait")
    assert [o.body[:4] for o in ctx.outbox if o.to == CUSTOMER] == ["FR: "]
    # A customer's French says nothing about the owner's language.
    assert prefs.get(store).get("_language") is None


def test_owner_hears_about_a_customer_in_her_own_language(store):
    prefs.save(store, {"language": "French"})
    llm = Translating([[("hand_to_owner", {"reason": "discount", "summary": "wants a discount on rice"})], ""])
    ctx = Agent(store, llm, OWNER, LIMIT).handle(sender=CUSTOMER, text="I want a discount on the rice please")
    to_owner = [o for o in ctx.outbox if o.to == OWNER]
    assert to_owner and all(o.body.startswith("FR: ") for o in to_owner)
