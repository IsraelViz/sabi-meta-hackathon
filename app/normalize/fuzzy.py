"""Fuzzy matching of what the trader said to products and contacts in the books."""
import re
from difflib import SequenceMatcher

STOPWORDS = {"the", "of", "a", "an", "my", "for", "to", "give", "mama", "baba", "oga", "iya", "aunty", "uncle"}


def _norm(s):
    return " ".join(re.findall(r"[a-z0-9]+", s.lower()))


def similarity(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    # Whole-word containment ("indomie" in "indomie super pack") is a strong signal.
    if f" {a} " in f" {b} " or f" {b} " in f" {a} ":
        ratio = max(ratio, 0.9)
    return ratio


def best_match(query, candidates, names):
    """Return [(score, candidate)] best first. `names(c)` yields the strings to compare."""
    scored = [(max(similarity(query, n) for n in names(c)), c) for c in candidates]
    return sorted(scored, key=lambda x: x[0], reverse=True)


def name_similarity(a, b):
    """For people: titles like 'Mama' count, but a matching core name matters most."""
    full = similarity(a, b)
    core_a = " ".join(w for w in _norm(a).split() if w not in STOPWORDS)
    core_b = " ".join(w for w in _norm(b).split() if w not in STOPWORDS)
    if not core_a or not core_b:
        return full
    # "Mama Ade" vs "Mama Tunde" share a title, not a person: score on the core name.
    return min(full, similarity(core_a, core_b)) if full < 1.0 else 1.0
