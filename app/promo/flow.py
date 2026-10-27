"""Sell → record → learn from the books → promote.

1. Code ranks products from the books and picks one (or the owner names one).
2. The owner's shelf photo → SAM 3.1 cuts out the real product.
3. Muse Image makes an empty background scene.
4. Pillow composes the flyer; every word and price comes from the books.
5. Muse Spark writes the caption; code rejects any price but the list price.
6. The owner sees the preview with Post / Change / Skip. Nothing is public until she taps Post.
"""
import io
import logging
from dataclasses import dataclass

from PIL import Image

from app.config import settings
from app.normalize.amounts import naira
from app.promo import ranking
from app.promo.caption import write_caption
from app.promo.compose import compose, to_jpeg
from app.promo.cutout import CenterCutter, SamCutter
from app.promo.imagegen import GradientBackground, MuseImage
from app.promo.publish import LocalMedia, MetaPublisher

log = logging.getLogger("sabi.promo")


@dataclass
class PromoServices:
    cutter: object
    backgrounds: object
    media: object
    publisher: object


def default_services():
    return PromoServices(
        cutter=SamCutter() if settings.sam_endpoint else CenterCutter(),
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


def make_promo(ctx, services, product_id=None, scene="default"):
    picks = ranking.rank(ctx.store, limit=None)
    if product_id:
        pick = next((p for p in picks if p.product_id == product_id), None)
        if pick is None:
            prod = ctx.store.get_product(product_id)
            if prod is None:
                return {"error": "unknown product; use find_product"}
            return {"error": f"{prod.name} is at or below its reorder level; promoting it would sell stock "
                             f"you can't supply. Suggest restocking first, or pick another product."}
    elif picks:
        pick = picks[0]
    else:
        return {"error": "nothing in stock above its reorder level to promote"}
    product = ctx.store.get_product(pick.product_id)

    if not ctx.image_data_url:
        ctx.tell_owner(f"My pick to promote: *{product.name}* — {pick.reason}.\n"
                       f"Send me a clear photo of it on your shelf and I'll make the post.")
        return {"status": "need_photo", "product_id": product.id, "name": product.name, "why": pick.reason}

    tools = {}
    try:
        cutout = services.cutter.cutout(ctx.image_data_url, product.name)
        tools["cutter"] = services.cutter.name
    except Exception as e:   # SAM missed it: still use her real photo, just not cut out
        log.warning("cutout failed, using centre crop: %s", e)
        cutout = CenterCutter().cutout(ctx.image_data_url, product.name)
        tools["cutter"] = f"center-crop (SAM failed: {e})"
    try:
        background = services.backgrounds.generate(scene)
        tools["background"] = services.backgrounds.name
    except Exception as e:
        log.warning("background failed, using gradient: %s", e)
        background = GradientBackground().generate(scene)
        tools["background"] = f"gradient ({services.backgrounds.name} failed: {e})"

    cut_file = services.media.save(_png(cutout), "png")
    bg_file = services.media.save(_png(background), "png")
    post = _render_and_ask(ctx, services, product, pick.reason, cut_file["path"], bg_file["path"], tools)
    return {"status": "waiting_for_owner", "post_id": post["id"], "product": product.name, "why": pick.reason,
            "caption_source": post["caption_source"]}


def _render_and_ask(ctx, services, product, reason, cutout_path, background_path, tools, instruction=None):
    shop = getattr(ctx.store, "business", {}).get("name", "Our shop")
    flyer = compose(Image.open(background_path), Image.open(cutout_path), shop_name=shop, product_name=product.name,
                    price_kobo=product.sell_price_kobo, unit=product.base_unit,
                    whatsapp=settings.shop_whatsapp_display or None)
    saved = services.media.save(to_jpeg(flyer), "jpg")
    caption, source = write_caption(ctx.llm, shop=shop, product=product.name, price_kobo=product.sell_price_kobo,
                                    unit=product.base_unit, reason=reason, instruction=instruction)
    post = ctx.store.add_post(product_id=product.id, reason=reason, caption=caption, caption_source=source,
                              asset_url=saved["url"], asset_path=saved["path"], cutout_path=cutout_path,
                              background_path=background_path, tools=tools)
    a = ctx.store.add_approval("post", {"post_id": post["id"]}, "Post this?", [
        {"label": "Post", "patch": [], "action": "post"},
        {"label": "Change", "patch": [], "action": "change"},
        {"label": "Skip", "patch": [], "action": "skip"}])
    from app.agent.approvals import buttons_for
    where = ", ".join(services.publisher.platforms) or "Instagram (not connected yet)"
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
        ctx.say("What should I change? E.g. \"shorter caption\", \"more Pidgin\", \"different background\".")
        return
    if action == "post":
        try:
            ids = services.publisher.publish(post["asset_url"], post["caption"])
        except Exception as e:
            ctx.store.update_post(post["id"], status="failed", error=str(e)[:500])
            ctx.say(f"I couldn't post it: {e}")
            return
        ctx.store.update_post(post["id"], status="published", platform_ids=ids)
        ctx.say("Posted ✅ " + ", ".join(f"{k}: {v}" for k, v in ids.items()))


def revise_post(approval, instruction, ctx, services):
    """The owner typed what to change: re-caption, and redraw the background if she asked for it."""
    old = ctx.store.get_post(approval["payload"]["post_id"])
    ctx.store.update_post(old["id"], status="skipped")
    product = ctx.store.get_product(old["product_id"])
    background_path, tools = old["background_path"], dict(old.get("tools") or {})
    if any(w in instruction.lower() for w in ("background", "colour", "color", "scene", "picture")):
        try:
            bg = services.backgrounds.generate(instruction)
            tools["background"] = services.backgrounds.name
        except Exception as e:
            log.warning("background redo failed: %s", e)
            bg = GradientBackground().generate()
        background_path = services.media.save(_png(bg), "png")["path"]
    _render_and_ask(ctx, services, product, old["reason"], old["cutout_path"], background_path, tools,
                    instruction=instruction)
