"""A shop's own money: books outside Nigeria are written and read in F CFA, cedis, shillings or rand."""
import pytest

from app import money, prefs
from app.agent import language
from app.agent.orchestrator import _system_prompt
from app.jobs.daily_summary import summary_message
from app.normalize.amounts import naira, normalize
from app.promo.caption import caption_problems

T = " "     # the thin no-break space between thousands in 12 500 F CFA


@pytest.fixture
def cfa(store):
    prefs.save(store, {"currency": "XOF"})
    money.use(store)
    yield store
    money.use(None)


def test_amounts_are_written_in_the_shops_money(store):
    assert money.use(store).code == "NGN" and naira(1_250_000) == "₦12,500" and naira(1_250_050) == "₦12,500.50"
    for code, written in [("XOF", f"12{T}500 F CFA"), ("GHS", "GH₵12,500"),
                          ("KES", "KSh 12,500"), ("ZAR", "R12,500")]:
        assert money.CURRENCIES[code].fmt(1_250_000) == written
    assert money.CURRENCIES["XOF"].fmt(1_250_070) == f"12{T}501 F CFA"   # no centimes in F CFA


def test_only_known_currencies_are_saved(store):
    assert prefs.save(store, {"currency": "xof"})[1] == [] and prefs.get(store)["currency"] == "XOF"
    assert prefs.save(store, {"currency": "BTC"})[1]
    assert prefs.get(store)["currency"] == "XOF"


def test_a_cfa_shop_reads_and_writes_francs(cfa):
    assert normalize("vendu 3 cartons à 12 500 F chacun").text == f"vendu 3 cartons à 12{T}500 F CFA chacun"
    assert normalize("transport 2 mille").text == f"transport 2{T}000 F CFA"
    assert normalize("payé 15000 fcfa").text == f"payé 15{T}000 F CFA"
    # Sabi reads back what it wrote itself.
    again = normalize(naira(1_250_000))
    assert [m.candidates_kobo for m in again.mentions] == [[1_250_000]]


def test_a_ghana_shop_reads_cedis(store):
    prefs.save(store, {"currency": "GHS"})
    money.use(store)
    assert normalize("sold 2 bags for GH₵ 450").text == "sold 2 bags for GH₵450"
    assert normalize("she paid 300 cedis").text == "she paid GH₵300"
    money.use(None)


def test_a_naira_shop_never_reads_other_signs_as_naira(store):
    money.use(store)
    assert normalize("bought it for $20").text == "bought it for $20"


def test_the_model_is_told_the_shops_money(cfa):
    text = _system_prompt("owner", cfa)
    assert "CFA francs" in text and f"12{T}500 F CFA" in text and "₦12,500" not in text


def test_summary_and_captions_use_the_shops_money(cfa):
    msg = summary_message(cfa, "daily")
    assert "₦" not in msg["body"] and "F CFA" in msg["body"]
    price = 1_250_000
    assert caption_problems(f"Riz dispo à 12{T}500 F CFA le sac! #Dakar", price) == []
    assert caption_problems("Riz dispo à 9 000 F CFA le sac!", price)


def test_a_translation_may_write_the_thin_space_as_a_plain_one():
    assert language.safe(f"Sold for 12{T}500 F CFA", "Vendu pour 12 500 F CFA")
    assert not language.safe(f"Sold for 12{T}500 F CFA", "Vendu pour 12 600 F CFA")
