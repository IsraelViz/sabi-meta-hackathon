"""A realistic week of trading for the demo shop, so the owner app has something to show.

Every entry goes through the normal validator and posting code, with an
evidence row holding the words the owner "said".
"""
from datetime import date, datetime, time, timedelta, timezone

from app.ledger.posting import commit_proposal, reverse
from app.ledger.proposals import Item, Proposal
from app.normalize.amounts import normalize

LIMIT = 10**12   # history is pre-approved


def _say(store, text, when):
    n = normalize(text)
    eid = store.add_evidence(channel="whatsapp", sender="owner", original_text=text, normalized_text=n.text,
                             model="muse-spark-1.3", prompt_version="2026-10-27.1",
                             extracted_json={"transcript_of": "voice_note"})
    _backdate_evidence(store, eid, when)
    return eid


def _backdate_evidence(store, eid, when):
    """Demo only: make the message time match the day it was 'sent'."""
    stamp = datetime.combine(when, time(10, 30), tzinfo=timezone(timedelta(hours=1)))   # Lagos
    if hasattr(store, "conn"):
        store.conn.execute("update evidence set created_at = %s where id = %s", (stamp, eid))
    else:
        store.evidence[eid]["created_at"] = stamp


def _post(store, said, days_ago, today, **kw):
    when = today - timedelta(days=days_ago)
    p = Proposal(entry_date=when, evidence_id=_say(store, said, when), memo=said[:60], **kw)
    entry, verdict = commit_proposal(p, store, LIMIT, approved=True)
    if entry is None:
        raise RuntimeError(f"demo entry rejected: {said!r}: {verdict.to_json()}")
    return entry


def add_history(store, today=None, media=None):
    today = today or date.today()
    d = lambda n: today - timedelta(days=n)   # noqa: E731

    _post(store, "I collect 10 carton Indomie from Alaba depot, 11,500 each, I go pay next week", 6, today,
          kind="purchase", items=[Item("p-indomie", 10, 1_150_000)], contact_id="c-depot", paid_kobo=0,
          due_date=d(-1))
    _post(store, "sold 8 spaghetti, cash", 6, today,
          kind="sale", items=[Item("p-spag", 8, 110_000)], paid_kobo=880_000, payment_account="cash")
    _post(store, f"I sell 3 carton Indomie give Mama Tunde, she go pay {d(2):%A}", 5, today,
          kind="sale", items=[Item("p-indomie", 3, 1_250_000)], contact_id="c-tunde", paid_kobo=0, due_date=d(2))
    _post(store, "12 tins of Peak milk, 700 each, she pay by transfer", 5, today,
          kind="sale", items=[Item("p-milk", 12, 70_000)], paid_kobo=840_000, payment_account="bank")
    _post(store, "Baba Sule buy one bag rice 78k, cash", 4, today,
          kind="sale", items=[Item("p-rice", 1, 7_800_000)], contact_id="c-sule", paid_kobo=7_800_000,
          payment_account="cash")
    _post(store, f"two keg of oil for Chioma, she go pay {d(-3):%A}", 4, today,
          kind="sale", items=[Item("p-oil", 2, 1_100_000)], contact_id="c-chioma", paid_kobo=0, due_date=d(-3))
    _post(store, "Chioma don pay 11k by transfer", 3, today,
          kind="payment_in", contact_id="c-chioma", amount_kobo=1_100_000, payment_account="bank")
    _post(store, "transport to market 3.5k", 3, today,
          kind="expense", expense_account="transport", amount_kobo=350_000, payment_account="cash")
    wrong = _post(store, "one bag sugar to Mama Ade, credit", 2, today,
                  kind="sale", items=[Item("p-sugar", 1, 1_650_000)], contact_id="c-ade", paid_kobo=0)
    reverse(wrong.id, store, "it was Mama Tunde, not Mama Ade")
    _post(store, "no be Mama Ade, na Mama Tunde take the sugar", 2, today,
          kind="sale", items=[Item("p-sugar", 1, 1_650_000)], contact_id="c-tunde", paid_kobo=0, due_date=d(-2))
    _post(store, "one crate of egg fall, all broke", 1, today,
          kind="stock_loss", items=[Item("p-eggs", 1)])
    _post(store, "4 carton indomie, cash", 1, today,
          kind="sale", items=[Item("p-indomie", 4, 1_250_000)], paid_kobo=5_000_000, payment_account="cash")
    _post(store, "6 spaghetti cash", 0, today,
          kind="sale", items=[Item("p-spag", 6, 110_000)], paid_kobo=660_000, payment_account="cash")
    _post(store, "sold 6 peak milk, POS", 0, today,
          kind="sale", items=[Item("p-milk", 6, 70_000)], paid_kobo=420_000, payment_account="bank")
    _post(store, "2 crate egg, 4.5k each, cash", 0, today,
          kind="sale", items=[Item("p-eggs", 2, 450_000)], paid_kobo=900_000, payment_account="cash")
    # Leave one open question, as if Sabi were waiting on the owner
    store.add_approval("proposal", {"proposal": Proposal(kind="payment_in", contact_id="c-tunde").to_json(),
                                    "text_path": "amount_kobo"}, "How much did Mama Tunde pay? Type the amount.", [])
    if media is not None:
        _demo_post(store, media, today)
    return store


def _demo_post(store, media, today):
    from PIL import Image, ImageDraw

    from app.promo.compose import compose, to_jpeg
    from app.promo.imagegen import GradientBackground

    tin = Image.new("RGBA", (300, 380), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tin)
    draw.rounded_rectangle((0, 0, 299, 379), 40, fill=(20, 60, 160, 255))
    draw.rectangle((0, 120, 299, 260), fill=(255, 255, 255, 255))
    draw.text((40, 170), "PEAK (demo)", fill=(20, 60, 160, 255), font_size=40)
    flyer = compose(GradientBackground().generate(), tin, shop_name=store.business.get("name", "Shop"),
                    product_name="Peak milk tin", price_kobo=70_000, unit="tin")
    saved = media.save(to_jpeg(flyer), "jpg")
    store.add_post(product_id="p-milk", reason="30 tin in stock, about 7 weeks' worth, sold 6 this week vs 4.5 usual, "
                                               "17% margin (₦120 per tin)",
                   caption="Peak milk don land! ₦700 per tin at Mama Nkechi Provisions. Send us a WhatsApp message "
                           "to order. #Lagos #NaijaShop #Provisions",
                   caption_source="model", asset_url=saved["url"], asset_path=saved["path"], status="published",
                   platform_ids={"instagram": "demo-17890000000"}, tools={"cutter": "demo", "background": "gradient"})
