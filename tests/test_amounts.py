import pytest

from app.normalize.amounts import find_amounts, naira, normalize


def kobo(text):
    ms = find_amounts(text)
    assert len(ms) == 1, ms
    return ms[0].candidates_kobo


@pytest.mark.parametrize("text, naira_value", [
    ("she pay 5k", 5_000),
    ("na 1k5 each", 1_500),
    ("2k25 for the oil", 2_250),
    ("12.5k per carton", 12_500),
    ("₦12,500 per carton", 12_500),
    ("N12500", 12_500),
    ("NGN 3,000", 3_000),
    ("2.5m for the shop rent", 2_500_000),
    ("fifty thousand total", 50_000),
    ("two thousand five hundred naira", 2_500),
    ("twelve thousand five hundred", 12_500),
    ("one hundred and fifty thousand", 150_000),
    ("₦5k", 5_000),
    ("4500 naira", 4_500),
])
def test_unambiguous(text, naira_value):
    assert kobo(text) == [naira_value * 100]


def test_pidgin_shorthand_is_ambiguous():
    assert kobo("she pay two-five") == [250_000, 2_500_000]


@pytest.mark.parametrize("text", [
    "sold 3 carton indomie give Mama Tunde",
    "a bag of rice and one crate of eggs",
    "six bags of rice",
    "she go pay Friday",
])
def test_quantities_are_not_money(text):
    assert find_amounts(text) == []


def test_normalize_rewrites_and_marks():
    n = normalize("I sell 3 carton indomie 12.5k each, Chioma pay two-five")
    assert "₦12,500" in n.text
    assert "[UNCLEAR AMOUNT: ₦2,500 or ₦25,000]" in n.text
    assert len(n.ambiguous) == 1
    assert n.original.startswith("I sell 3 carton")


def test_naira_format():
    assert naira(1_250_000) == "₦12,500"
    assert naira(150) == "₦1.50"
