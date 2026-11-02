"""Any language she writes in.

The model already replies in her language. What code writes (confirmations, questions with buttons,
summaries, tips) is English, so before it goes out it is translated, in one model call per message
batch. Code then checks every number in the translation against the English: if one is missing or
changed, that message goes out in English instead. A translation can make a sentence clumsy; it can
never change an amount.

Which language: her setting (prefs.language), or, on "auto", the language of her own messages.
In auto, English, Pidgin and the Nigerian languages keep English for code-written text, because
traders who write Yoruba or Pidgin read English every day; she can still choose them in Settings.
"""
import json
import logging
import re
from collections import Counter, OrderedDict

log = logging.getLogger("sabi.language")

# Shown in Settings; she can also type any other language to Sabi.
CHOICES = ("auto", "English", "Pidgin", "French", "Portuguese", "Spanish", "Swahili", "Arabic",
           "Yoruba", "Igbo", "Hausa", "Amharic", "Wolof", "Zulu", "Hindi", "Chinese")
NO_TRANSLATION = {"English"}
ENGLISH_OK = {"English", "Pidgin", "Yoruba", "Igbo", "Hausa"}   # in auto: keep code-written text in English

# A few very common words per language. Enough to tell her language from a short trading message.
_WORDS = {
    "English": "the and i sold bought paid she he owes for to of is today my with cash bag carton how much",
    "Pidgin": "don dey wey abeg na una wetin oga sabi go pay collect sef no be dem e am",
    "French": "le la les un une des j'ai jai vendu acheté achete payé paye elle il doit pour et à de du "
              "aujourd'hui avec mon ma mes combien sac carton espèces especes est sont pas oui merci bonjour "
              "client cliente crédit credit francs mille je tu vous votre ton nous reste noté enregistré achat vente "
              "cartons sacs boîtes",
    "Portuguese": "o a os as um uma vendi comprei pagou ela ele deve para e de do da hoje com meu minha "
                  "quanto saco dinheiro não sim obrigado obrigada bom dia você seu sua registrei anotei venda "
                  "compra sacos ao em por está",
    "Spanish": "el la los las un una vendí vendi compré compre pagó pago ella él debe para y de del hoy con "
               "mi cuánto cuanto saco efectivo no sí gracias hola",
    "Swahili": "nimeuza nimenunua amelipa analipa deni leo na kwa ya wa mimi pesa taslimu gunia katoni "
               "habari asante sawa ndiyo hapana bei ngapi mteja",
    "Wolof": "jaay jënd fey dafa am ci ak ngir tey xaalis jërëjëf waaw déedéet",
}
_WORDS = {lang: set(words.split()) for lang, words in _WORDS.items()}
_TOKEN = re.compile(r"[a-zà-ÿœ']+", re.I)

# Letters that only some languages use.
_SCRIPTS = [
    ("Arabic", re.compile(r"[؀-ۿ]")),
    ("Amharic", re.compile(r"[ሀ-፿]")),
    ("Hindi", re.compile(r"[ऀ-ॿ]")),
    ("Chinese", re.compile(r"[一-鿿]")),
    ("Russian", re.compile(r"[Ѐ-ӿ]")),
    ("Hausa", re.compile(r"[ƙɗɓƘƊƁ]")),
    ("Igbo", re.compile(r"[ịụṅỊỤṄ]")),
    ("Yoruba", re.compile("[ẹọṣẸỌṢ\u0323]")),
]


def detect(text):
    """Her language from one message, or None when the message is too short or too mixed to say."""
    if not text:
        return None
    for lang, pattern in _SCRIPTS:
        if len(pattern.findall(text)) >= (1 if lang in ENGLISH_OK else 2):   # one ẹ or ƙ is enough
            return lang
    words = [w.lower().strip("'") for w in _TOKEN.findall(text)]
    if len(words) < 3:
        return None
    hits = Counter({lang: sum(w in vocab for w in words) for lang, vocab in _WORDS.items()})
    (best, n), *rest = hits.most_common() + [("", 0)]
    second = rest[0][1] if rest else 0
    if n >= 2 and n > second:
        return best
    return None


def for_owner(store, text=None):
    """The language code-written messages to the owner should be in, remembering what she writes in."""
    from app import prefs
    p = prefs.get(store)
    seen = detect(text)
    if seen and seen != p.get("_language"):
        prefs.save(store, {"_language": seen})
        p = prefs.get(store)
    if p["language"] != "auto":
        return p["language"]
    lang = p.get("_language") or "English"
    return "English" if lang in ENGLISH_OK else lang


def for_customer(text):
    lang = detect(text)
    return None if not lang or lang in ENGLISH_OK else lang


# ---------------------------------------------------------------- translation

_NUMBER = re.compile(r"\d+(?:[,.]\d+|[ \u00a0\u202f]\d{3}(?!\d))*")   # 12,500 / 12.500 / 12 500
_CACHE: "OrderedDict[tuple, str]" = OrderedDict()
CACHE_SIZE = 2000
BUTTON_MAX = 20     # WhatsApp's limit on a button title

PROMPT = """Translate each text below from English into {language}, for a small trader reading WhatsApp.
Rules:
- Short, warm and plain, the way a shopkeeper talks. Keep emojis and line breaks.
- Copy every number exactly as written: amounts, quantities, dates, times, percentages. Keep money signs (₦, F CFA, GH₵, KSh) and the digits as they are (₦12,500 stays ₦12,500). Do not convert 8 pm to 20 h.
- Keep people's names, product names, shop names and links exactly as written. Keep *bold* stars.
- Texts marked "button" must be 20 characters or fewer.
Reply with JSON only: {{"t": ["translation 1", "translation 2", ...]}} in the same order.

Texts:
{texts}"""


def _numbers(text):
    return Counter(re.sub(r"\D", "", n) for n in _NUMBER.findall(text))


def safe(original, translated, button=False):
    """Code's check on a translation: same numbers, not empty, buttons short enough."""
    if not isinstance(translated, str) or not translated.strip():
        return False
    if button and len(translated) > BUTTON_MAX:
        return False
    return _numbers(original) == _numbers(translated)


def translate(llm, texts, language, buttons=()):
    """English texts -> {language}. Any text whose translation fails the check stays English.
    `buttons` holds the indexes of texts that are button titles."""
    if not texts or language in NO_TRANSLATION or llm is None:
        return list(texts)
    out = [_CACHE.get((language, t)) for t in texts]
    todo = [i for i, t in enumerate(texts) if out[i] is None and t.strip()]
    if todo:
        listing = "\n".join(f'{n + 1}.{" (button)" if i in buttons else ""} {json.dumps(texts[i], ensure_ascii=False)}'
                            for n, i in enumerate(todo))
        try:
            reply = llm.chat([{"role": "user", "content": PROMPT.format(language=language, texts=listing)}])
            raw = reply.content or ""
            got = json.loads(raw[raw.find("{"): raw.rfind("}") + 1]).get("t") or []
        except Exception:
            log.exception("translation to %s failed", language)
            got = []
        for n, i in enumerate(todo):
            t = got[n] if n < len(got) else None
            if safe(texts[i], t, button=i in buttons):
                out[i] = t.strip()
                _CACHE[(language, texts[i])] = out[i]
                if len(_CACHE) > CACHE_SIZE:
                    _CACHE.popitem(last=False)
            else:
                log.info("kept English (translation to %s failed the check): %r", language, texts[i][:80])
    return [o if o is not None else t for o, t in zip(out, texts)]


def localize(llm, messages, language):
    """Translate Outgoing-like messages in place: .body and each button's title (ids never change)."""
    if not messages or not language or language in NO_TRANSLATION:
        return messages
    texts, buttons = [], set()
    for m in messages:
        texts.append(m.body)
        for b in m.buttons:
            buttons.add(len(texts))
            texts.append(b["title"])
    done = iter(translate(llm, texts, language, buttons))
    for m in messages:
        m.body = next(done)
        m.buttons = [{**b, "title": next(done)} for b in m.buttons]
    return messages
