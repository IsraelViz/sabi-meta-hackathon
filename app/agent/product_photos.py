"""Pictures of what she sells, and customers who shop by picture.

Owner: she sends a photo of one product and says what it is ("this is my Peak milk");
`save_product_photo` keeps it with the product. A flyer made from her photo also gives the
product its picture if it has none.

Customer: they send a picture of what they want. `match_photo` shows it to Muse Spark with the
shop's product list and asks which one it is. Code then checks the answer against the books: only
a real product id counts, and stock and price come from the books, never from the model.
- Matched and in stock: the shop's own picture goes back with the price (`send_product_photo`).
- Not sold here, or out of stock: noted on the customer's lead, so it shows in her summary as
  "Asked for but not available", and look-alikes that are in stock are offered instead.
"""
import base64

from app.agent import sales
from app.ledger import reports
from app.normalize.amounts import naira
from app.normalize.json_reply import parse_json

SURE = 0.6       # below this the model's guess is a look-alike, not the product they sent
ASK = 0.3        # between ASK and SURE with a likely product: ask the customer which it is
SIMILAR = 3

IDENTIFY = """A customer of a small shop sent this photo of something they want to buy.
The shop sells (id: name; other names):
{catalogue}

Which of these products is in the photo? Only the same product counts: a different brand, flavour or
size is not a match, but it can be similar. If the photo shows no product (a payment screenshot, a
person, a place), say what it shows and give no product.
If you can't tell what it is (blurry, dark, cut off, label hidden, several different products), say so
in "unclear" instead of guessing.
Reply with JSON only:
{{"seen": "what the photo shows: brand, product and size if visible, a few words in English",
  "unclear": "<null if you can tell what it is; else why not, a few words: 'label not readable', 'several products'>",
  "candidates": ["<ids from the list it could be, best first, when unclear or not sure>"],
  "product_id": "<id from the list, or null>",
  "sure": <0 to 1, how sure you are it is that product>,
  "list_names_brand": <true if the list entry you chose names the brand or size, so you could check it; false if it is
                       generic, like "Spaghetti pack" for a photo of Golden Penny spaghetti>,
  "similar": ["<ids of up to 3 products from the list a customer wanting this could take instead>"]}}"""


def _row(store, p):
    available = store.stock_on_hand(p.id) - store.reserved(p.id)
    return {"product_id": p.id, "name": p.name, "unit": p.base_unit, "price": naira(p.sell_price_kobo),
            "in_stock": available > 0 or not reports.tracks_stock(store, p.id), "has_photo": bool(p.photo)}


def _book_line(p):
    d = p.details or {}
    bits = [f"by {d['author']}" if d.get("author") else "", d.get("publisher") or "",
            f"{d['edition']} edition" if d.get("edition") else "", d.get("level") or ""]
    bits = [b for b in bits if b]
    return f" (book: {', '.join(bits)})" if d.get("title") and bits else ""


def identify(llm, image_data_url, products):
    catalogue = "\n".join(f"- {p.id}: {p.name}" + (f"; {', '.join(p.aliases)}" if p.aliases else "") + _book_line(p)
                          for p in products)
    reply = llm.chat([{"role": "user", "content": [
        {"type": "text", "text": IDENTIFY.format(catalogue=catalogue or "(nothing yet)")},
        {"type": "image_url", "image_url": {"url": image_data_url}}]}])
    data = parse_json(reply.content or "{}")
    return data if isinstance(data, dict) else {}


def match_photo(ctx):
    if not ctx.image_data_url:
        return {"error": "there is no photo in this message; ask them to send it again or describe it"}
    if ctx.llm is None:
        return {"error": "I can't look at photos right now; ask them to type what it is"}
    products = {p.id: p for p in ctx.store.list_products()}
    seen = identify(ctx.llm, ctx.image_data_url, list(products.values()))
    what = str(seen.get("seen") or "the item in their photo")[:80]
    try:
        sure = float(seen.get("sure") or 0)
    except (TypeError, ValueError):
        sure = 0.0
    match = products.get(seen.get("product_id")) if sure >= SURE else None
    candidates = [products[i] for i in dict.fromkeys([*(seen.get("candidates") or []), seen.get("product_id")])
                  if i in products][:2]
    if not match and (seen.get("unclear") or (candidates and sure >= ASK)):
        return _ask(ctx, what, seen.get("unclear"), candidates)
    row = _row(ctx.store, match) if match else None
    similar = [_row(ctx.store, products[i]) for i in dict.fromkeys(seen.get("similar") or [])
               if i in products and (not match or i != match.id)]
    similar = [r for r in similar if r["in_stock"]][:SIMILAR]
    if ctx.contact_id:
        sales.note_photo(ctx, what, row)
    out = {"photo_shows": what, "match": row, "similar_in_stock": similar}
    if row and seen.get("list_names_brand") is False:
        # "Spaghetti pack" in her books says nothing about the brand in their photo: never promise it.
        row["same_brand"] = "unknown"
        out["next"] = (f"We sell {match.name}, but our list doesn't say if it is the same brand or size as "
                       f"'{what}'. Send our photo if has_photo, call it only '{match.name}', and let them decide.")
        return out
    if row and row["in_stock"]:
        out["next"] = "It is in stock: send its photo with send_product_photo if has_photo, give the price, ask how many."
    elif row:
        out["next"] = "We sell it but it is out of stock. Say so; offer similar_in_stock if any."
    else:
        out["next"] = "We don't sell that. Say so plainly; offer similar_in_stock if any, with its photo."
    return out


def _ask(ctx, what, why, candidates):
    """Not sure what the photo shows: ask the customer, with the likely products as buttons. Nothing is
    noted as missing until they answer."""
    options = [{"id": f"photo:{p.id}", "title": p.name[:20]} for p in candidates]
    options.append({"id": "photo:other", "title": "Something else"})
    if candidates:
        question = (f"I want to be sure I get you the right thing. Is it "
                    + " or ".join(p.name for p in candidates) + "?")
    else:
        question = "I can't see the photo clearly. Please tell me the name of the product, or send a clearer picture."
        options = []
    if ctx.contact_id:
        ctx.say(question, options)
    return {"status": "asked_customer", "photo_shows": what, "why_unclear": why or "not sure which product",
            "next": "The verifying question is sent. Don't ask it again or guess; wait for their answer. "
                    "When they answer, use find_product with what they say."}


def identify_for_owner(ctx):
    """Owner: which of her products is in this photo (a book cover, a pack), with stock and price."""
    if not ctx.image_data_url:
        return {"error": "there is no photo in this message"}
    if ctx.llm is None:
        return {"error": "I can't look at photos right now; ask her what it is"}
    products = {p.id: p for p in ctx.store.list_products()}
    seen = identify(ctx.llm, ctx.image_data_url, list(products.values()))
    try:
        sure = float(seen.get("sure") or 0)
    except (TypeError, ValueError):
        sure = 0.0
    p = products.get(seen.get("product_id"))
    if p is None or sure < SURE:
        options = [products[i].name for i in seen.get("candidates") or [] if i in products][:2]
        return {"photo_shows": seen.get("seen"), "match": None, "could_be": options,
                "next": "Not sure which product: ask her, naming could_be if any. If it is new, add_products."}
    from app.agent.books import facts
    row = {"product_id": p.id, "name": p.name, "stock": ctx.store.stock_on_hand(p.id),
           "price": naira(p.sell_price_kobo), **facts(ctx.store, p)}
    return {"photo_shows": seen.get("seen"), "match": row,
            "next": "Use this product_id for what she asked (record_entry, stock, price)."}


def send_product_photo(ctx, product_id):
    p = ctx.store.get_product(product_id)
    if p is None:
        return {"error": "no such product; use the product_id from match_photo or find_product"}
    if not p.photo:
        return {"status": "no_photo", "note": "This product has no picture yet; describe it in words."}
    ctx.say(f"{p.name}: {naira(p.sell_price_kobo)} per {p.base_unit}", image_url=p.photo)
    return {"status": "sent", "note": "The picture and price are sent; don't repeat the price unless asked."}


def _save(ctx, image_data_url):
    header, b64 = image_data_url.split(",", 1)
    ext = "png" if "png" in header else "webp" if "webp" in header else "jpg"
    return ctx.media.save(base64.b64decode(b64), ext)


def save_product_photo(ctx, product_id):
    p = ctx.store.get_product(product_id)
    if p is None:
        return {"error": "no such product; look it up with find_product first"}
    if not ctx.image_data_url:
        return {"error": "there is no photo in this message; ask her to send the picture"}
    if ctx.media is None:
        return {"error": "pictures can't be saved here"}
    saved = _save(ctx, ctx.image_data_url)
    if not saved.get("url"):
        return {"error": "the picture was saved but has no web address yet (PUBLIC_BASE_URL is not set)"}
    ctx.store.set_product_photo(p.id, saved["url"])
    return {"status": "saved", "product": p.name,
            "note": "Tell her in one line: customers who send a picture of it, or ask to see it, get this photo."}


def remember_from_flyer(ctx, product):
    """Her photo for a post is a good picture of the product: keep it if the product has none."""
    if product.photo or not ctx.image_data_url or ctx.media is None:
        return
    saved = _save(ctx, ctx.image_data_url)
    if saved.get("url"):
        ctx.store.set_product_photo(product.id, saved["url"])
