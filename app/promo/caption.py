"""Captions in the owner's voice, written by Muse Spark and checked by code.

The only amount a caption may mention is the list price from the books. A
caption with any other figure is rewritten once, then replaced by a safe
template; it never reaches the owner for approval with a wrong price.
"""
import re

from app.normalize.amounts import find_amounts, naira

CAPTION_PROMPT = """Write an Instagram and Facebook caption for {shop}, a Nigerian provisions shop, promoting {product}.
Price: {price} per {unit} (write it exactly like that, or leave the price out).
Why this product now (from the shop's books, for your inspiration only; do not quote the numbers): {reason}
{extra}
Style: warm, short (under 60 words), the way a Lagos shop owner talks to her customers. A little Pidgin is fine.
End with how to order: send a WhatsApp message to the shop. Add 3 to 5 relevant hashtags.
Do not mention any other price, discount, quantity in stock, or promise. Reply with the caption only."""


def template_caption(shop, product, price_kobo, unit):
    return (f"{product} dey available for {naira(price_kobo)} per {unit} at {shop}! 🛒\n"
            f"Send us a WhatsApp message to order today.\n#Lagos #NaijaShop #Provisions")


def caption_problems(text, price_kobo):
    problems = []
    for m in find_amounts(text):
        if m.candidates_kobo != [price_kobo]:
            problems.append(f"mentions {m.text!r}; the only price allowed is {naira(price_kobo)}")
    # Bare numbers with 3+ digits are likely prices or stock counts in disguise.
    stripped = re.sub(r"#\w+", "", text)
    price_digits = str(price_kobo // 100)
    for n in re.findall(r"\b\d[\d,]{2,}\b", stripped):
        if n.replace(",", "") != price_digits:
            problems.append(f"mentions the number {n}")
    if len(text) > 2000:
        problems.append("too long for Instagram")
    return problems


def write_caption(llm, *, shop, product, price_kobo, unit, reason, instruction=None):
    """Returns (caption, source) where source is 'model' or 'template'."""
    if llm is None:
        return template_caption(shop, product, price_kobo, unit), "template"
    extra = f"The owner asked for this change: {instruction}" if instruction else ""
    prompt = CAPTION_PROMPT.format(shop=shop, product=product, price=naira(price_kobo), unit=unit,
                                   reason=reason, extra=extra)
    messages = [{"role": "user", "content": prompt}]
    for _ in range(2):
        text = (llm.chat(messages).content or "").strip().strip('"')
        problems = caption_problems(text, price_kobo)
        if text and not problems:
            return text, "model"
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Fix this: " + "; ".join(problems or ["empty caption"])}]
    return template_caption(shop, product, price_kobo, unit), "template"
