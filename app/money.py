"""The money a shop keeps its books in.

The books store integers in hundredths of the shop's currency ("kobo" in the code, because Sabi
started in Nigeria): 1,250,000 is ₦12,500 in a Lagos shop and 12 500 F CFA in a Dakar shop. Nothing
is ever converted between currencies; the setting only decides how amounts are written and read.

Which shop is being served is set once per request or job with `use(store)`; `fmt()` and the amount
reader then follow that shop. Outside any shop it is naira.
"""
import re
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Currency:
    code: str
    name: str            # "naira", in a sentence: "twelve thousand naira"
    label: str           # shown in Settings
    symbol: str
    after: bool = False  # 12 500 F CFA rather than ₦12,500
    group: str = ","     # thousands separator
    cents: bool = True   # show .50 when there is a fraction
    prefixes: str = ""   # regex: ways traders write the sign before a number (besides the symbol)
    words: str = ""      # regex: ways traders write it after a number

    def fmt(self, kobo, exact=True):
        n = round(kobo) if exact and self.cents else round(kobo / 100) * 100
        sign, n = ("-" if n < 0 else ""), abs(n)
        whole = f"{n // 100:,}".replace(",", self.group)
        frac = f".{n % 100:02d}" if n % 100 else ""
        return f"{sign}{whole}{frac} {self.symbol}" if self.after else f"{sign}{self.symbol}{whole}{frac}"


_NBSP = " "   # thin no-break space: 12 500 never breaks across lines
CURRENCIES = {c.code: c for c in [
    Currency("NGN", "naira", "Naira (₦), Nigeria", "₦", prefixes=r"ngn\s?|n(?=\d)",
             words=r"naira|f|fcfa|cfa|francs?"),     # F CFA words here too: French-writing traders in Nigeria
    Currency("XOF", "CFA francs", "F CFA, West Africa (Senegal, Côte d'Ivoire, Mali, Benin…)", "F CFA",
             after=True, group=_NBSP, cents=False, prefixes=r"xof\s?|fcfa\s?|cfa\s?",
             words=r"f\s?cfa|fcfa|cfa|f|francs?|xof"),
    Currency("XAF", "CFA francs", "F CFA, Central Africa (Cameroon, Gabon, Chad…)", "F CFA",
             after=True, group=_NBSP, cents=False, prefixes=r"xaf\s?|fcfa\s?|cfa\s?",
             words=r"f\s?cfa|fcfa|cfa|f|francs?|xaf"),
    Currency("GHS", "cedis", "Cedi (GH₵), Ghana", "GH₵", prefixes=r"gh₵\s?|gh¢\s?|ghs\s?|₵\s?|¢\s?",
             words=r"cedis?|ghs"),
    Currency("KES", "Kenyan shillings", "Shilling (KSh), Kenya", "KSh ", cents=False,
             prefixes=r"ksh\.?\s?|kes\s?|sh\.?\s?", words=r"bob|shillings?|kes|ksh"),
    Currency("TZS", "Tanzanian shillings", "Shilling (TSh), Tanzania", "TSh ", cents=False,
             prefixes=r"tsh\.?\s?|tzs\s?|sh\.?\s?", words=r"shilingi|shillings?|tzs|tsh"),
    Currency("UGX", "Ugandan shillings", "Shilling (USh), Uganda", "USh ", cents=False,
             prefixes=r"ush\.?\s?|ugx\s?|sh\.?\s?", words=r"shillings?|ugx|ush"),
    Currency("ZAR", "rand", "Rand (R), South Africa", "R", prefixes=r"zar\s?|r(?=\d)", words=r"rand|zar"),
]}
DEFAULT = CURRENCIES["NGN"]

_current: ContextVar[Currency] = ContextVar("currency", default=DEFAULT)


def of(store):
    from app import prefs
    return CURRENCIES.get(prefs.get(store).get("currency"), DEFAULT)


def use(store):
    """Serve this shop's currency for the rest of the request or job. Returns it."""
    cur = of(store) if store is not None else DEFAULT
    _current.set(cur)
    return cur


def current():
    return _current.get()


def fmt(kobo, exact=True):
    """₦12,500 / 12 500 F CFA / GH₵12,500.50 for the shop being served. exact=False rounds to whole."""
    return _current.get().fmt(kobo, exact)


def describe(cur=None):
    """One line for the model's instructions."""
    cur = cur or _current.get()
    return (f"This shop's books are in {cur.name} ({cur.symbol.strip()}). Write amounts the way the tools "
            f"give them, e.g. {cur.fmt(1_250_000)}. Every field named *_naira holds an amount in {cur.name}.")


def symbol_pattern(cur=None):
    """Regex for the sign written before a number, for the amount reader."""
    cur = cur or _current.get()
    sym = re.escape(cur.symbol.strip()) if not cur.after else ""
    return "|".join(p for p in (sym, cur.prefixes) if p)
