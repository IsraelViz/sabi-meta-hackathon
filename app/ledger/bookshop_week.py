"""The demo bookshop's history: the September resumption rush, then a normal week.

The rush is what `term_restock` learns from ("last September you sold 24 JSS1 maths"); the
week gives the owner app its debts, a correction, a loss and an open question. Every entry
goes through the normal validator and posting code, like demo_week.py.
"""
from datetime import date, timedelta

from app.agent.books import school_calendar
from app.ledger.demo_week import _post
from app.ledger.posting import reverse
from app.ledger.proposals import Item, Proposal

PRICE = {}   # product id -> selling price, filled from the store


def _sale(store, said, when, today, items, **kw):
    kw.setdefault("paid_kobo", sum(q * PRICE[p] for p, q in items))
    kw.setdefault("payment_account", "cash")
    return _post(store, said, (today - when).days, today, kind="sale",
                 items=[Item(p, q, PRICE[p]) for p, q in items], **kw)


def add_history(store, today=None, media=None):
    today = today or date.today()
    PRICE.update({p.id: p.sell_price_kobo for p in store.list_products()})
    d = lambda n: today - timedelta(days=n)   # noqa: E731
    rush = school_calendar(today)["last_new_session"]
    r = lambda n: rush + timedelta(days=n)    # noqa: E731

    # Before the new session: the depot delivery on credit, paid off from the rush takings.
    delivery = _post(
        store, "Ikeja depot bring JSS1 and Primary 4 books for resumption, I go pay after resumption",
        (today - r(-7)).days, today, kind="purchase", contact_id="c-depot", paid_kobo=0, due_date=r(16),
        items=[Item("b-ngm-jss1", 20, 520_000), Item("b-eng-jss1", 15, 410_000), Item("b-bst-jss1", 15, 390_000),
               Item("b-civic-jss1", 10, 280_000), Item("b-ngm-jss2", 10, 520_000), Item("b-ngm-jss3", 6, 540_000),
               Item("b-math-p4", 10, 310_000), Item("b-eng-p4", 10, 300_000), Item("b-sci-p4", 8, 280_000),
               Item("b-chem-ss2", 6, 650_000), Item("b-bio-ss2", 6, 630_000), Item("b-eze", 10, 140_000),
               Item("b-oald", 4, 1_200_000), Item("s-exercise", 60, 220_000), Item("s-mathset", 20, 100_000)])
    owed = delivery.total_debits

    # The rush: parents buying whole booklists.
    _sale(store, "resumption day: 8 JSS1 maths, 6 English JSS1, 6 basic science JSS1, 5 civic, 12 exercise book dozen",
          r(0), today, [("b-ngm-jss1", 8), ("b-eng-jss1", 6), ("b-bst-jss1", 6), ("b-civic-jss1", 5), ("s-exercise", 12)])
    _sale(store, "today 6 JSS1 maths, 5 English JSS1, 4 basic science, 4 civic, 5 primary 4 maths, 4 primary 4 english, transfer",
          r(1), today, [("b-ngm-jss1", 6), ("b-eng-jss1", 5), ("b-bst-jss1", 4), ("b-civic-jss1", 4),
                        ("b-math-p4", 5), ("b-eng-p4", 4)], payment_account="bank")
    _sale(store, "5 JSS2 maths, 4 JSS3 maths, 4 chemistry SS2, 3 biology SS2, 6 Eze goes to school, 15 exercise book, cash",
          r(2), today, [("b-ngm-jss2", 5), ("b-ngm-jss3", 4), ("b-chem-ss2", 4), ("b-bio-ss2", 3), ("b-eze", 6),
                        ("s-exercise", 15)])
    _sale(store, "Saturday rush: 6 JSS1 maths, 4 English, 4 basic science, 3 civic, 5 primary 4 maths, 5 primary 4 english, "
                 "5 basic science primary 4, 4 QR, 4 VR, 10 maths set", r(5), today,
          [("b-ngm-jss1", 6), ("b-eng-jss1", 4), ("b-bst-jss1", 4), ("b-civic-jss1", 3), ("b-math-p4", 5),
           ("b-eng-p4", 5), ("b-sci-p4", 5), ("b-qr-p4", 4), ("b-vr-p4", 4), ("s-mathset", 10)], payment_account="bank")
    _sale(store, "4 JSS1 maths, 3 English JSS1, 3 basic science, 2 civic, 5 JSS2 maths, 4 primary 4 maths, 3 dictionary, "
                 "20 exercise book", r(8), today,
          [("b-ngm-jss1", 4), ("b-eng-jss1", 3), ("b-bst-jss1", 3), ("b-civic-jss1", 2), ("b-ngm-jss2", 5),
           ("b-math-p4", 4), ("b-oald", 3), ("s-exercise", 20)])
    _sale(store, "late buyers: 2 civic, 4 Things Fall Apart, 4 Eze, 3 chemistry, 3 biology, 4 primary 4 science, 4 QR, 3 VR",
          r(15), today, [("b-civic-jss1", 2), ("b-tfa", 4), ("b-eze", 4), ("b-chem-ss2", 3), ("b-bio-ss2", 3),
                         ("b-sci-p4", 4), ("b-qr-p4", 4), ("b-vr-p4", 3)], payment_account="bank")

    _post(store, "I pay Ikeja depot 300k cash", (today - r(10)).days, today,
          kind="payment_out", contact_id="c-depot", amount_kobo=30_000_000, payment_account="cash")
    _post(store, "I don clear Ikeja depot balance by transfer", (today - r(16)).days, today,
          kind="payment_out", contact_id="c-depot", amount_kobo=owed - 30_000_000, payment_account="bank")

    # A normal week.
    _post(store, f"Grace Model College take 4 Eze Goes to School for their library, dem go pay {d(-3):%A}", 6, today,
          kind="sale", items=[Item("b-eze", 4, PRICE["b-eze"])], contact_id="c-grace", paid_kobo=0, due_date=d(-3))
    _post(store, f"Mama Tunde buy JSS1 maths, English and basic science for Tunde, she go pay {d(2):%A}", 5, today,
          kind="sale", contact_id="c-tunde", paid_kobo=0, due_date=d(2),
          items=[Item("b-ngm-jss1", 1, PRICE["b-ngm-jss1"]), Item("b-eng-jss1", 1, PRICE["b-eng-jss1"]),
                 Item("b-bst-jss1", 1, PRICE["b-bst-jss1"])])
    _sale(store, "2 Things Fall Apart, cash", d(5), today, [("b-tfa", 2)])
    _sale(store, "Chioma buy New School Chemistry and Modern Biology for her daughter, transfer", d(4), today,
          [("b-chem-ss2", 1), ("b-bio-ss2", 1)], contact_id="c-chioma", payment_account="bank")
    _post(store, "transport to Ikeja depot 2.5k", 3, today,
          kind="expense", expense_account="transport", amount_kobo=250_000, payment_account="cash")
    _sale(store, "Baba Sule buy one Bible, cash", d(3), today, [("b-bible", 1)], contact_id="c-sule")
    wrong = _post(store, "one Oxford dictionary to Mama Ade, credit", 2, today, kind="sale",
                  items=[Item("b-oald", 1, PRICE["b-oald"])], contact_id="c-ade", paid_kobo=0)
    reverse(wrong.id, store, "it was Mama Tunde, not Mama Ade")
    _post(store, "no be Mama Ade, na Mama Tunde take the dictionary", 2, today, kind="sale",
          items=[Item("b-oald", 1, PRICE["b-oald"])], contact_id="c-tunde", paid_kobo=0, due_date=d(-2))
    _post(store, "rain enter shop, 2 civic education JSS1 spoil", 1, today,
          kind="stock_loss", items=[Item("b-civic-jss1", 2)])
    _sale(store, "3 dozen exercise book and 2 maths set, cash", d(1), today, [("s-exercise", 3), ("s-mathset", 2)])
    _sale(store, "1 JSS2 maths and 1 English JSS1, POS", d(0), today, [("b-ngm-jss2", 1), ("b-eng-jss1", 1)],
          payment_account="bank")

    # Parents asked for what she didn't have: term_restock and the summary use this.
    for cid, unmet in (("c-chioma", ["junior secondary agricultural science jss1 (booklist)"]),
                       ("c-sule", ["junior secondary agricultural science jss1 (booklist)",
                                   "civic education jss1 (out of stock)"]),
                       ("c-ade", ["civic education jss1 (out of stock)",
                                  "junior secondary agricultural science jss1 (booklist)"])):
        store.save_lead(cid, unmet=unmet)
    store.add_approval("proposal", {"proposal": Proposal(kind="payment_in", contact_id="c-tunde").to_json(),
                                    "text_path": "amount_kobo"}, "How much did Mama Tunde pay? Type the amount.", [])
    if media is not None:
        _demo_post(store, media)
    return store


def _demo_post(store, media):
    from PIL import Image, ImageDraw

    from app.promo.compose import compose, to_jpeg
    from app.promo.imagegen import GradientBackground

    book = Image.new("RGBA", (300, 420), (0, 0, 0, 0))
    draw = ImageDraw.Draw(book)
    draw.rectangle((0, 0, 299, 419), fill=(190, 120, 40, 255))
    draw.rectangle((0, 0, 24, 419), fill=(120, 70, 20, 255))
    draw.text((50, 150), "THINGS\nFALL\nAPART", fill=(20, 20, 20, 255), font_size=44)
    flyer = compose(GradientBackground().generate(), book, shop_name=store.business.get("name", "Shop"),
                    product_name="Things Fall Apart", price_kobo=350_000, unit="copy")
    saved = media.save(to_jpeg(flyer), "jpg")
    store.add_post(product_id="b-tfa", reason="on the SS literature list, 4 copies left, sold 6 since resumption",
                   caption="Things Fall Apart by Chinua Achebe, ₦3,500 at Nkechi Books & Stationery, Ogba. "
                           "Send us your child's booklist on WhatsApp and we pack everything. #Lagos #Books #BackToSchool",
                   caption_source="model", asset_url=saved["url"], asset_path=saved["path"], status="published",
                   platform_ids={"instagram": "demo-17890000001"}, tools={"cutter": "demo", "background": "gradient"})
