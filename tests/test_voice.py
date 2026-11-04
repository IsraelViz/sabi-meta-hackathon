"""Sabi's voice: numbers read the way people say them, and voice-note replies when asked."""
import pytest

from app.agent.orchestrator import Agent
from app.media import speech
from tests.test_agent import CUSTOMER, OWNER, ScriptedLLM


def test_amounts_dates_classes_and_refs_are_read_as_words():
    assert speech.to_words("*Total ₦42,000*") == "total forty two thousand naira"
    assert speech.to_words("₦12.5k, 3.5k") == "twelve thousand five hundred naira, three thousand five hundred"
    assert speech.to_words("Resumes Monday 11 January 2027") == "resumes monday eleven january twenty twenty seven"
    assert speech.to_words("Civic Education JSS1 📦") == "civic education j s s one"
    assert speech.to_words("Ref: 936220") == "ref. nine three six two two zero"
    assert speech.to_words("due since 1st Nov, 22nd, 30th") == "due since first nov, twenty second, thirtieth"
    assert speech.number_words(1_250_305) == "one million two hundred and fifty thousand three hundred and five"


def test_long_or_number_heavy_answers_also_go_as_text():
    assert speech.wants_text_too("Transfer to DEMO BANK 0000000000")
    assert not speech.wants_text_too("Yes, we have it. How many copies?")


def test_she_asks_once_for_a_voice_reply(store):
    llm = ScriptedLLM([[("voice_replies", {"mode": "this_once"})], "Your profit this week is ₦45,000."])
    out = Agent(store, llm, OWNER, 10**9).handle(sender=OWNER, text="tell me my profit, reply with voice note").outbox
    assert out[-1].voice and out[-1].body.startswith("Your profit")
    llm = ScriptedLLM(["Recorded."])
    assert not Agent(store, llm, OWNER, 10**9).handle(sender=OWNER, text="sold 2 milk cash").outbox[-1].voice


def test_a_customer_can_ask_for_every_answer_by_voice(store):
    store.set_contact_phone("c-tunde", CUSTOMER)
    llm = ScriptedLLM([[("voice_replies", {"mode": "always"})], "OK, I go dey talk."])
    agent = Agent(store, llm, OWNER, 10**9)
    agent.handle(sender=CUSTOMER, text="abeg talk am, I no fit read well")
    assert store.get_contact("c-tunde").voice_replies is True
    llm.turns.append("Peak milk na ₦700.")
    assert agent.handle(sender=CUSTOMER, text="how much peak milk").outbox[-1].voice


def test_the_nigerian_voice_gets_amounts_and_refs_it_can_say():
    assert speech.for_speaking("*Total ₦42,000* 📦\nRef: 936220") == "Total 42,000 naira. Ref: 9 3 6 2 2 0"
    assert speech.for_speaking("₦12.5k and 3.5k") == "12,500 naira and 3,500"


@pytest.mark.skipif(not speech.mms.available(), reason="voice files not downloaded (scripts/get_voice.py)")
def test_the_mms_voice_makes_a_whatsapp_voice_note():
    ogg = speech.mms.voice_note("Good morning. Your list is ready: ₦42,000.")
    assert ogg[:4] == b"OggS" and len(ogg) > 5000


# --- tone: matched to each message, or fixed by the owner ---------------------------

def test_tone_follows_her_setting_for_her_and_for_customers(store):
    from app import prefs
    from app.agent.orchestrator import _system_prompt
    assert "read each message and match it" in _system_prompt("owner", store)
    prefs.save(store, {"style": "professional", "customer_style": "playful"})
    assert "always professional" in _system_prompt("owner", store)
    assert "always the lively personality" in _system_prompt("customer", store)
    assert prefs.save(store, {"style": "rude"})[1]          # not a style: refused, nothing saved
    assert prefs.get(store)["style"] == "professional"


def test_she_changes_the_tone_by_asking(store):
    llm = ScriptedLLM([[("set_preferences", {"style": "professional"})], "Understood."])
    Agent(store, llm, OWNER, 10**9).handle(sender=OWNER, text="Please always be professional with me")
    from app import prefs
    assert prefs.get(store)["style"] == "professional"


def test_no_dashes_in_what_sabi_sends(store):
    from app.agent.orchestrator import no_dashes
    assert no_dashes("*Mama Tunde — ₦32,500* — was due 01 Nov") == "*Mama Tunde, ₦32,500*, was due 01 Nov"
    assert no_dashes("Open Mon–Sat, 9am–6pm") == "Open Mon-Sat, 9am-6pm"
    llm = ScriptedLLM(["Good evening — Things Fall Apart is ₦3,500 — in stock."])
    out = Agent(store, llm, OWNER, 10**9).handle(sender=OWNER, text="price of things fall apart?").outbox
    assert "—" not in out[-1].body and out[-1].body == "Good evening, Things Fall Apart is ₦3,500, in stock."
