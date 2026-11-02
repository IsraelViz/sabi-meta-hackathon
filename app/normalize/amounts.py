"""Find money amounts in Nigerian trader speech.

Only unambiguous forms are rewritten ("5k" -> 5,000). Anything that could mean
more than one amount is reported with all its candidates, and the agent must
ask the owner which one she meant. Bare numbers ("3 carton", "4500 each") are
left for the model: they may be quantities or prices, and the price-range check
in the validator catches a wrong reading.
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache

from app import money

UNITS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
DIGIT_WORDS = {w: n for w, n in UNITS.items() if n < 10}
NUMBER_WORDS = {**UNITS, **TENS, **SCALES, "a": 1, "and": 0}


@dataclass
class Mention:
    text: str
    start: int
    end: int
    candidates_kobo: list[int] = field(default_factory=list)

    @property
    def ambiguous(self):
        return len(self.candidates_kobo) != 1


@dataclass
class Normalized:
    original: str
    text: str
    mentions: list[Mention]

    @property
    def ambiguous(self):
        return [m for m in self.mentions if m.ambiguous]


def naira(kobo):
    """An amount in the shop's own money: ₦12,500 in Lagos, 12 500 F CFA in Dakar (see app/money.py)."""
    return money.fmt(kobo)


_NUM = r"\d+(?:,\d{3})*(?:\.\d+)?"

_WORD = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_DIGIT_WORD = "|".join(DIGIT_WORDS)

@lru_cache(maxsize=None)
def _patterns(code):
    """The amount patterns for one currency: its sign before a number, its words after one."""
    cur = money.CURRENCIES[code]
    sign = rf"(?:{money.symbol_pattern(cur)})"
    word = rf"\s?(?:{cur.words})\b"
    return [
        # 1k5 -> 1,500 ; 2k25 -> 2,250
        ("ksplit", re.compile(r"(?<![\w.])(\d+)k(\d{1,3})\b", re.I)),
        # ₦5k, N5k, 5k, 12.5k, 2.5m, 5k naira
        ("suffix", re.compile(rf"(?:(?<![\w.]){sign}\s?)?(?<![\w.])({_NUM})\s?(k|m)\b(?:{word})?", re.I)),
        # ₦12,500 or N12500 or 12,500 naira; 12,500 F CFA in a CFA shop
        ("currency", re.compile(rf"(?<![\w.]){sign}\s?({_NUM})\b|(?<![\w.])({_NUM}){word}", re.I)),
        # two-five (Pidgin shorthand: 2,500 or 25,000 depending on context)
        ("hyphen", re.compile(rf"\b({_DIGIT_WORD})-({_DIGIT_WORD})\b", re.I)),
        # five thousand, two thousand five hundred, fifty thousand naira (filtered in find_amounts)
        ("words", re.compile(rf"\b(?:{_WORD})(?:[\s-]+(?:{_WORD}))*\b(?:\s?(?:naira|{cur.words})\b)?", re.I)),
    ]


def _to_kobo(num_text):
    return round(float(num_text.replace(",", "")) * 100)


def words_to_int(text):
    total, current = 0, 0
    for w in re.findall(r"[a-z]+", text.lower()):
        if w == "and" or w not in NUMBER_WORDS:     # "naira", "cedis"
            continue
        if w == "a":
            current = current or 1
        elif w in UNITS or w in TENS:
            current += UNITS.get(w) or TENS[w]
        elif w == "hundred":
            current = (current or 1) * 100
        elif w in ("thousand", "million"):
            total += (current or 1) * SCALES[w]
            current = 0
    return total + current


def _candidates(kind, m):
    if kind == "ksplit":
        whole, tail = m.group(1), m.group(2)
        return [(int(whole) * 1000 + int(tail) * 10 ** (3 - len(tail))) * 100]
    if kind == "suffix":
        mult = 1000 if m.group(2).lower() == "k" else 1_000_000
        return [round(float(m.group(1).replace(",", "")) * mult * 100)]
    if kind == "currency":
        return [_to_kobo(m.group(1) or m.group(2))]
    if kind == "hyphen":
        a, b = DIGIT_WORDS[m.group(1).lower()], DIGIT_WORDS[m.group(2).lower()]
        return [(a * 1000 + b * 100) * 100, (a * 10_000 + b * 1000) * 100]
    if kind == "words":
        return [words_to_int(m.group(0)) * 100]
    raise ValueError(kind)


def _trim_words(text, m):
    """Keep a run of number words only if it is money-sized (has thousand/million or 'naira').

    Drops leading 'a'/'and' and trailing 'and' so "a bag and five thousand" -> "five thousand".
    """
    words = list(re.finditer(r"[a-z]+", m.group(0), re.I))
    while words and words[0].group(0).lower() in ("a", "and"):
        words.pop(0)
    while words and words[-1].group(0).lower() == "and":
        words.pop()
    if not words:
        return None
    lowered = [w.group(0).lower() for w in words]
    money_word = lowered[-1] not in NUMBER_WORDS     # "naira", "cedis", "francs"
    if not ({"thousand", "million"} & set(lowered) or money_word):
        return None
    if len(lowered) == 1 and money_word:
        return None
    start, end = m.start() + words[0].start(), m.start() + words[-1].end()
    return re.compile(re.escape(text[start:end])).search(text, start)


def find_amounts(text):
    taken, mentions = [], []
    for kind, pattern in _patterns(money.current().code):
        for m in pattern.finditer(text):
            s, e = m.span()
            if kind == "words":
                m = _trim_words(text, m)
                if m is None:
                    continue
                s, e = m.span()
            if any(s < te and ts < e for ts, te in taken):
                continue
            taken.append((s, e))
            mentions.append(Mention(m.group(0), s, e, _candidates(kind, m)))
    return sorted(mentions, key=lambda x: x.start)


# How French, Portuguese and Spanish speakers write the same amounts. Rewritten to the English
# form first, and only where there is one reading:
# 78.000 / 1.250.000: dots between groups of three digits are thousands, never decimals.
_DOT_THOUSANDS = re.compile(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+(?![\d.,])")
# 12 500 F, 15 000 francs, ₦ 12 500: spaces between groups of three, with money next to them.
# Without money next to it "3 500" could be 3 items at 500, so it is left for the model.
_SPACE_THOUSANDS = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ \u00a0\u202f]\d{3})+)(?=[ \u00a0]?(?:f\b|fcfa|cfa|francs?\b|naira|ngn|xof|xaf"
    r"|cedis?|shillings?|shilingi|rand\b))"
    r"|(?:(?<=₦)|(?<=₵)|(?<=sh)|(?<=sh\.))[ \u00a0]?(\d{1,3}(?:[ \u00a0\u202f]\d{3})+)(?![\d])", re.I)
# 1,5k / 2,5 mille: a comma before one or two digits and a multiplier is a decimal point.
_DECIMAL_COMMA = re.compile(r"(?<![\d,])(\d+),(\d{1,2})(?=\s?(?:k|m|mille|mil|million|milhões|millones)\b)", re.I)
# 2 mille / 15 mil (French, Portuguese, Spanish for thousand); elfu 2 (Swahili).
_THOUSAND_WORD = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?(?:mille|mil)\b", re.I)
_ELFU = re.compile(r"\belfu\s(\d+(?:\.\d+)?)\b", re.I)


def _plain_digits(text):
    text = _DOT_THOUSANDS.sub(lambda m: m.group(0).replace(".", ","), text)
    text = _SPACE_THOUSANDS.sub(lambda m: re.sub(r"[ \u00a0\u202f]", ",", m.group(1) or m.group(2)), text)
    text = _DECIMAL_COMMA.sub(r"\1.\2", text)
    text = _THOUSAND_WORD.sub(r"\1k", text)
    return _ELFU.sub(r"\1k", text)


def read_amounts(text):
    """Every amount in a text however it is written, e.g. Sabi's own "12 500 F CFA" in a caption."""
    return find_amounts(_plain_digits(text))


def normalize(text):
    """Rewrite unambiguous amounts in the shop's money (₦12,500); mark ambiguous ones for the model.
    `original` keeps exactly what she wrote; `text` is what the model reads."""
    original, text = text, _plain_digits(text)
    mentions = find_amounts(text)
    out, pos = [], 0
    for m in mentions:
        out.append(text[pos:m.start])
        if m.ambiguous:
            options = " or ".join(naira(c) for c in m.candidates_kobo)
            out.append(f"{m.text} [UNCLEAR AMOUNT: {options}]")
        else:
            out.append(naira(m.candidates_kobo[0]))
        pos = m.end
    out.append(text[pos:])
    return Normalized(original, "".join(out), mentions)
