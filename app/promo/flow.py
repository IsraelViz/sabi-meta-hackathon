"""Sell → record → learn from the books → promote.

1. Code ranks products from the books and picks one (or the owner names one).
2. The owner's shelf photo → SAM 3.1 cuts out the real product.
3. Muse Image makes an empty background scene.
4. Pillow composes the flyer; every word and price comes from the books.
5. Muse Spark writes the caption; code rejects any price but the list price.
6. The owner sees the preview with Post / Change / Skip. Nothing is public until she taps Post.
"""
import base64
import io
import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageColor, ImageEnhance

from app.config import settings
from app.normalize.amounts import naira
from app.promo import ranking
from app.agent.language import for_owner
from app.promo.caption import write_caption
from app.promo.compose import compose, to_jpeg
from app.media.counting import sam_available
from app.promo.cutout import CenterCutter, SamCutter
from app.promo.imagegen import GradientBackground, MuseImage
from app.promo.publish import LocalMedia, MetaPublisher
from app.ledger.store import now

log = logging.getLogger("sabi.promo")
FRESH_DAYS = 7      # a product posted this week isn't picked again: her followers would see the same flyer


@dataclass
class PromoServices:
    cutter: object
    backgrounds: object
    media: object
    publisher: object


def default_services():
    return PromoServices(
        cutter=SamCutter() if sam_available() else CenterCutter(),
        backgrounds=MuseImage() if settings.model_api_key else GradientBackground(),
        media=LocalMedia(),
        publisher=MetaPublisher(),
    )


def _png(img):
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def ideas(ctx, limit=3):
    return {"picks": [{"product_id": p.product_id, "name": p.name, "score": p.score, "why": p.reason}
                      for p in ranking.rank(ctx.store, limit=limit)]}


def _posted_lately(store):
    since = now() - timedelta(days=FRESH_DAYS)
    return {p["product_id"] for p in store.list_posts() if p["created_at"] >= since}


def _saved_photo(services, product):
    """The picture of this product she saved before, so a post needs no new photo."""
    if not product.photo or getattr(services.media, "dir", None) is None:
        return None
    path = Path(services.media.dir) / product.photo.rsplit("/", 1)[-1]
    if not path.is_file():
        return None
    mime = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix, "image/jpeg")
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def make_promo(ctx, services, product_id=None, scene="default"):
    picks = ranking.rank(ctx.store, limit=None)
    reason_extra = ""
    if product_id:
        pick = next((p for p in picks if p.product_id == product_id), None)
        if pick is None:
            prod = ctx.store.get_product(product_id)
            if prod is None:
                return {"error": "unknown product; use find_product"}
            return {"error": f"{prod.name} is at or below its reorder level; promoting it would sell stock "
                             f"you can't supply. Suggest restocking first, or pick another product."}
    elif picks:
        lately = _posted_lately(ctx.store)
        fresh = [p for p in picks if p.product_id not in lately] or picks
        if fresh[0] is not picks[0]:
            reason_extra = f"; {picks[0].name} already had a post this week"
        # No photo in her message: the best of the top three she saved a picture of, so one tap makes a flyer.
        has_photo = [] if ctx.image_data_url else [
            p for p in fresh[:3] if _saved_photo(services, ctx.store.get_product(p.product_id))]
        pick = (has_photo or fresh)[0]
    else:
        return {"error": "nothing in stock above its reorder level to promote"}
    product = ctx.store.get_product(pick.product_id)
    reason = pick.reason + reason_extra

    photo = ctx.image_data_url or _saved_photo(services, product)
    if not photo:
        ctx.tell_owner(f"My pick to promote: *{product.name}*. {reason[:1].upper() + reason[1:]}.\n"
                       f"Send me a clear photo of it on your shelf and I'll make the post.")
        return {"status": "need_photo", "product_id": product.id, "name": product.name, "why": reason,
                "note": "She has been told the pick and asked for a photo. Don't repeat it: say nothing more."}

    tools = {}
    tools = {}
    from app.agent.product_photos import remember_from_flyer
    remember_from_flyer(ctx, product)
    cutout_path = _cut(services, photo, product, tools)
    background_path = _background(services, scene, tools)
    post = _render_and_ask(ctx, services, product, reason, cutout_path, background_path, tools)
    return {"status": "waiting_for_owner", "post_id": post["id"], "product": product.name, "why": reason,
            "caption_source": post["caption_source"]}


def _cut(services, photo, product, tools):
    """Keep her photo (a later edit may want all of it) and cut the product out of it. Returns the cut-out's path."""
    header, b64 = photo.split(",", 1)
    tools["photo_path"] = services.media.save(base64.b64decode(b64), "png" if "png" in header else "jpg")["path"]
    try:
        cutout = services.cutter.cutout(photo, product.name)
        tools["cutter"] = services.cutter.name
    except Exception as e:   # SAM missed it: still use her real photo, just not cut out
        log.warning("cutout failed, using centre crop: %s", e)
        cutout = CenterCutter().cutout(photo, product.name)
        tools["cutter"] = f"center-crop (SAM failed: {e})"
    return services.media.save(_png(cutout), "png")["path"]


def _background(services, scene, tools):
    plain = scene.lower().removeprefix("plain").strip() if scene.lower().startswith("plain") else None
    try:
        if plain is not None:       # "plain white", "plain #F5E6C8": a flat colour, no model needed
            background = Image.new("RGB", (1080, 1350), ImageColor.getrgb(plain or "white"))
            tools["background"] = f"plain {plain}"
        else:
            background = services.backgrounds.generate(scene)
            tools["background"] = services.backgrounds.name
    except Exception as e:
        log.warning("background failed, using gradient: %s", e)
        background = GradientBackground().generate()
        tools["background"] = f"gradient ({tools.get('background') or services.backgrounds.name} failed: {e})"
    return services.media.save(_png(background), "png")["path"]


def _styled(cutout, style):
    """Her picture edits that code can make exactly: turn it, lighten or darken it."""
    if style.get("rotate"):
        cutout = cutout.rotate(-style["rotate"], expand=True)
    if style.get("brightness", 1) != 1:
        rgba = cutout.convert("RGBA")
        lit = ImageEnhance.Brightness(rgba.convert("RGB")).enhance(style["brightness"]).convert("RGBA")
        lit.putalpha(rgba.getchannel("A"))
        cutout = lit
    return cutout


def _render_and_ask(ctx, services, product, reason, cutout_path, background_path, tools, instruction=None,
                    caption=None):
    shop = getattr(ctx.store, "business", {}).get("name", "Our shop")
    style = tools.get("style") or {}
    flyer = compose(Image.open(background_path), _styled(Image.open(cutout_path), style), shop_name=shop,
                    product_name=product.name, price_kobo=product.sell_price_kobo, unit=product.base_unit,
                    whatsapp=settings.shop_whatsapp_display or None, scale=style.get("size", 1),
                    show_price=style.get("price", True))
    saved = services.media.save(to_jpeg(flyer), "jpg")
    if caption:     # she only changed the picture: the caption she saw stays
        source = "kept"
    else:
        caption, source = write_caption(ctx.llm, shop=shop, product=product.name, price_kobo=product.sell_price_kobo,
                                        unit=product.base_unit, reason=reason, instruction=instruction,
                                        language=for_owner(ctx.store))
    post = ctx.store.add_post(product_id=product.id, reason=reason, caption=caption, caption_source=source,
                              asset_url=saved["url"], asset_path=saved["path"], cutout_path=cutout_path,
                              background_path=background_path, tools=tools)
    a = ctx.store.add_approval("post", {"post_id": post["id"]}, "Post this?", [
        {"label": "Post", "patch": [], "action": "post"},
        {"label": "Change", "patch": [], "action": "change"},
        {"label": "Skip", "patch": [], "action": "skip"}])
    from app.agent.approvals import buttons_for
    where = " and ".join(p.capitalize() for p in services.publisher.platforms) or "WhatsApp (Instagram and Facebook aren't connected yet)"
    body = (f"Draft post for *{product.name}* ({naira(product.sell_price_kobo)} per {product.base_unit}).\n"
            f"Why: {reason}.\n\n{caption}\n\nPost to {where}?")
    if not saved["url"]:
        body += "\n(Set PUBLIC_BASE_URL so I can show you the picture.)"
    ctx.tell_owner(body[:1024], buttons_for(a))
    if saved["url"]:
        ctx.outbox[-1].image_url = saved["url"]
    return post


def settle_post(approval, option, ctx, services):
    post = ctx.store.get_post(approval["payload"]["post_id"])
    action = option.get("action")
    if action == "skip":
        ctx.store.update_post(post["id"], status="skipped")
        ctx.say("OK, skipped. Nothing was posted.")
        return
    if action == "change":
        ctx.store.update_approval_payload(approval["id"], {**approval["payload"], "awaiting_text": True})
        ctx.say("What should I change? Send me the right picture of it, or tell me: \"plain white background\", "
                "\"bigger\", \"brighter\", \"use my whole photo\", \"no price\", \"shorter caption\", \"more Pidgin\".")
        return
    if action == "post":
        from app.promo.share import offer_whatsapp
        ids, errors = {}, {}
        if services.publisher.platforms:
            try:
                ids, errors = services.publisher.publish(post["asset_url"], post["caption"])
            except Exception as e:
                errors = {"instagram/facebook": str(e)[:300]}
        if ids:
            ctx.store.update_post(post["id"], status="published", platform_ids=ids)
            ctx.say("Posted ✅ on " + " and ".join(k.capitalize() for k in ids) + ".")
        else:
            ctx.store.update_post(post["id"], status="failed" if errors else "approved",
                                  error="; ".join(f"{k}: {v}" for k, v in errors.items())[:500] or None)
        for k, v in errors.items():
            ctx.say(f"I couldn't post it on {k.capitalize()}: {v}")
        offer_whatsapp(ctx, ctx.store.get_post(post["id"]))


EDIT_PROMPT = """The shop owner wants to change her flyer. Turn her words into JSON with only these keys, null for
anything she didn't ask for:
"caption": what to change in the caption, in her words, or null
"background": a new background scene to draw (e.g. "shelves of books in a cosy shop"), "plain <colour>" for a flat
  colour (e.g. "plain white", "plain #FCE7D2"), or null
"photo": "full" to show her whole photo instead of the cut-out product, "cutout" to cut the product out again, or null
"size": how big the product should be compared with now, 0.5 to 1.5 ("bigger" 1.2, "much smaller" 0.7), or null
"brightness": compared with now, 0.6 to 1.5 ("brighter" 1.25, "darker" 0.8), or null
"rotate": 90, 180 or 270 (degrees clockwise) if the product is sideways or upside down, or null
"price": false to take the price off, true to put it back, or null
Prices, the product name and the shop name come from her books: they can't be changed here.
Reply with the JSON only."""
BACKGROUND_WORDS = ("background", "colour", "color", "scene", "backdrop")


def _read_edit(llm, instruction):
    """Her words → the edits code can make. Without a model, background words redraw it and the rest is the caption."""
    if llm and instruction.strip():
        try:
            raw = llm.chat([{"role": "system", "content": EDIT_PROMPT}, {"role": "user", "content": instruction}]).content
            edit = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
            if isinstance(edit, dict):
                return edit
        except Exception as e:
            log.warning("couldn't read the flyer edit %r: %s", instruction, e)
    low = instruction.lower()
    return {"caption": instruction or None,
            "background": instruction if any(w in low for w in BACKGROUND_WORDS) else None}


def _number(value, low, high):
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return None


def revise_post(approval, instruction, ctx, services, photo=None):
    """She asked for a change, or sent the right picture: redraw what she asked for, keep the rest."""
    old = ctx.store.get_post(approval["payload"]["post_id"])
    ctx.store.update_post(old["id"], status="skipped")
    product = ctx.store.get_product(old["product_id"])
    tools = dict(old.get("tools") or {})
    style = dict(tools.get("style") or {})
    cutout_path, background_path = old["cutout_path"], old["background_path"]
    edit = _read_edit(ctx.llm, instruction)

    if photo:           # her own picture of the product replaces the old one
        cutout_path = _cut(services, photo, product, tools)
        style.pop("rotate", None)
    elif edit.get("photo") == "full" and tools.get("photo_path"):
        cutout_path = tools["photo_path"]
    elif edit.get("photo") == "cutout" and tools.get("photo_path"):
        with open(tools["photo_path"], "rb") as f:
            cutout_path = _cut(services, f"data:image/jpeg;base64,{base64.b64encode(f.read()).decode()}", product, tools)
    if edit.get("background"):
        background_path = _background(services, str(edit["background"]), tools)
    if (size := _number(edit.get("size"), 0.5, 1.5)) is not None:
        style["size"] = max(0.5, min(1.3, style.get("size", 1) * size))
    if (light := _number(edit.get("brightness"), 0.6, 1.5)) is not None:
        style["brightness"] = max(0.5, min(1.8, style.get("brightness", 1) * light))
    if edit.get("rotate") in (90, 180, 270, "90", "180", "270"):
        style["rotate"] = (style.get("rotate", 0) + int(edit["rotate"])) % 360
    if isinstance(edit.get("price"), bool):
        style["price"] = edit["price"]
    tools["style"] = style
    picture_only = not edit.get("caption") and (photo or any(edit.get(k) is not None for k in
                                                            ("photo", "background", "size", "brightness", "rotate", "price")))
    _render_and_ask(ctx, services, product, old["reason"], cutout_path, background_path, tools,
                    instruction=edit.get("caption") or instruction or None,
                    caption=old["caption"] if picture_only else None)
