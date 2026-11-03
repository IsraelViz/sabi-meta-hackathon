"""Sharing a posted flyer on WhatsApp, three ways, each chosen by the owner.

WhatsApp's API cannot post to a Status, and it only lets a business message people on its terms:
- My Status: Sabi sends her the flyer with its caption, and she forwards it to her own Status.
- Recent customers: people who messaged the shop in the last 24 hours. WhatsApp allows free
  messages inside that window. Anyone who said "stop" is left out.
- Customers who want offers: only those who said yes to offers (`Contact.offers_ok`). Inside
  the window they get the flyer as a message; outside it, WhatsApp only delivers an approved
  marketing template (scripts/create_promo_template.py), which Meta charges per message.
She sees who will get it and taps Send before anything goes to a customer.
"""
from datetime import timedelta

from app.agent.approvals import buttons_for
from app.config import settings
from app.ledger.store import now
from app.normalize.amounts import naira

WINDOW = timedelta(hours=23)     # WhatsApp's 24-hour window, with an hour to spare
MAX_SEND = 200                   # one tap never messages more people than this
STOP_WORDS = {"stop", "stop promotions", "unsubscribe", "no more offers", "stop offers"}

# Must match the template submitted to Meta word for word ({{1}} shop, {{2}} product, {{3}} price).
TEMPLATE_TEXT = ("New at {{1}}: {{2}}, {{3}}. Reply to this message to order, or reply STOP to stop offers.")


def render_template(shop, product, price):
    return TEMPLATE_TEXT.replace("{{1}}", shop).replace("{{2}}", product).replace("{{3}}", price)


def offer_whatsapp(ctx, post):
    """After a post: ask where on WhatsApp it should go."""
    a = ctx.store.add_approval("share", {"post_id": post["id"]}, "Share it on WhatsApp too?", [
        {"label": "My Status", "action": "status"},
        {"label": "Recent customers", "action": "recent"},
        {"label": "Offer list", "action": "offers"}])
    ctx.tell_owner("Share it on WhatsApp too? *My Status*: I send you the flyer to forward to your Status. "
                   "*Recent customers*: people who messaged you in the last day. *Offer list*: customers who "
                   "said yes to your offers.", buttons_for(a))


def _in_window(c, at):
    return c.last_inbound_at is not None and at - c.last_inbound_at <= WINDOW


def recipients(store, who, at=None):
    """[(contact, 'session' | 'template')] for 'recent' or 'offers'."""
    at = at or now()
    out = []
    for c in store.list_contacts():
        if c.kind != "customer" or not c.phone or c.phone == settings.owner_phone or c.offers_ok is False:
            continue
        if who == "recent" and _in_window(c, at):
            out.append((c, "session"))
        elif who == "offers" and c.offers_ok:
            out.append((c, "session" if _in_window(c, at) else "template"))
    return out[:MAX_SEND]


def settle_share(approval, option, ctx):
    post = ctx.store.get_post(approval["payload"]["post_id"])
    action = option.get("action")
    if not post or not post.get("asset_url"):
        ctx.say("That flyer has no picture address, so I can't share it. Set PUBLIC_BASE_URL and make it again.")
        return
    if action == "status":
        ctx.say(post["caption"], image_url=post["asset_url"])
        ctx.say("To put it on your Status: tap and hold the picture above → Forward → My status → Send.")
        return
    people = recipients(ctx.store, action)
    if not people:
        ctx.say("Nobody has messaged you in the last day, so there is no one to send it to yet." if action == "recent"
                else "No customer has said yes to your offers yet. When a customer says \"send me your offers\", "
                     "I add them to the list.")
        return
    names = ", ".join(c.name for c, _ in people[:8]) + (f" and {len(people) - 8} more" if len(people) > 8 else "")
    by_template = sum(1 for _, mode in people if mode == "template")
    text = f"Send the flyer to {len(people)} customer{'s' if len(people) != 1 else ''}: {names}?"
    if by_template:
        text += (f" {by_template} haven't messaged you in the last day, so they get the approved offer message "
                 "(Meta charges a small fee for each).")
    a = ctx.store.add_approval("broadcast", {"post_id": post["id"], "who": action,
                                             "contact_ids": [c.id for c, _ in people]}, text,
                               [{"label": "Send", "action": "send"}, {"label": "Cancel", "action": "cancel"}])
    ctx.say(text, buttons_for(a))


def settle_broadcast(approval, option, ctx):
    if option.get("action") != "send":
        ctx.say("OK, not sent to anyone.")
        return
    post = ctx.store.get_post(approval["payload"]["post_id"])
    product = ctx.store.get_product(post["product_id"])
    shop = ctx.store.business.get("name", "our shop")
    price = naira(product.sell_price_kobo) + f" per {product.base_unit}"
    wanted = set(approval["payload"]["contact_ids"])
    sent = 0
    # Checked again at send time: someone may have said stop since she was asked.
    for c, mode in recipients(ctx.store, approval["payload"]["who"]):
        if c.id not in wanted:
            continue
        if mode == "session":
            ctx.say(post["caption"], to=c.phone, image_url=post["asset_url"])
        else:
            ctx.say(render_template(shop, product.name, price), to=c.phone, template={
                "name": settings.promo_template, "language": settings.promo_template_lang,
                "params": [shop, product.name, price], "header_image": post["asset_url"]})
        sent += 1
    ids = dict(post.get("platform_ids") or {})
    ids["whatsapp"] = ids.get("whatsapp", 0) + sent
    ctx.store.update_post(post["id"], platform_ids=ids)
    ctx.say(f"Sending to {sent} customer{'s' if sent != 1 else ''} ✅" if sent else "Nobody left to send it to.")


def set_offers(ctx, opt_in):
    """Customer: yes or no to the shop's offers on WhatsApp."""
    ctx.store.set_contact_offers(ctx.contact_id, bool(opt_in))
    return {"status": "saved", "offers": bool(opt_in),
            "note": "Tell them in one line: they'll get the shop's offers here, and can reply STOP any time."
                    if opt_in else "Tell them in one line: no more offers; they can still message to buy."}


def is_stop(text):
    return (text or "").strip().lower().strip(".! ") in STOP_WORDS
