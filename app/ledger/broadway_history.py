"""Broadway Books' trading, for the judges' demo shop: Ife's real sales sheet set in October, then
November so far. Every entry goes through the normal validator and posting code, like demo_week.py.

Her shelf counts in seed/broadway.json are what is left after all of this: the opening stock there is
her count plus everything sold below (SOLD), so stock never goes below zero.
"""
from collections import Counter
from datetime import date
from pathlib import Path

from app.ledger.demo_week import _post
from app.ledger.proposals import Item

COVERS = Path(__file__).resolve().parents[2] / "seed" / "broadway_covers"
OCT, NOV = date(2026, 10, 1), date(2026, 11, 1)

# (day, what she said, [(product, qty, price in naira; None = her shelf price)], how she was paid)
SALES = [
    (OCT.replace(day=8), "8 October sales, all cash", [
        ("101 Essays That Will Change the Way You Think", 1, 6800), ("The Power of a Praying Husband", 1, 5000),
        ("The Power of a Praying Husband", 1, 4500), ("Gifted Hands", 1, 3500), ("Think Big", 1, 3500),
        ("The Chronicles of Narnia", 1, 3500), ("Make Today Count", 3, 3300), ("How Successful People Lead", 2, 3500),
        ("How Successful People Grow", 4, 3500)], "cash"),
    (OCT.replace(day=17), "17 October sales, all cash", [
        ("You Have a Brain", 1, 3700), ("Why You Act the Way You Do", 1, 4800),
        ("How to Stop Worrying and Start Living", 1, 4800), ("A5 Notebook", 2, 2500), ("Highlighter", 2, 500),
        ("Highlighter", 1, 700), ("Sticky Notes", 2, 1300), ("Eraser with Case", 2, 1300), ("Gel Pen", 1, 700)], "cash"),
    (OCT.replace(day=28), "28 October sales, all cash", [
        ("Deep Work", 1, 6000), ("There Was a Country", 1, 5500), ("The Millionaire Fastlane", 1, 5500),
        ("Nemesis", 1, 3200), ("Developing the Leader Within You", 1, 4500), ("The Almanack of Naval Ravikant", 1, 6000),
        ("Things I Wish I Had Known", 1, 5000), ("Scavengers of the Soul", 1, 3200), ("The Self Aware Leader", 1, 4000),
        ("Boundaries", 1, 4800)], "cash"),
    (NOV.replace(day=2), "sold 7 Habits, Psychology of Money and Tiny Habits, cash", [
        ("7 Habits of Highly Effective People", 1, None), ("The Psychology of Money", 1, None),
        ("Tiny Habits", 1, None)], "cash"),
    (NOV.replace(day=2), "Rich Dad Poor Dad and Emotional Intelligence, she paid by transfer", [
        ("Rich Dad Poor Dad", 1, None), ("Emotional Intelligence", 1, None)], "bank"),
    (NOV.replace(day=3), "The Mountain Is You and Can't Hurt Me, transfer", [
        ("The Mountain Is You", 1, None), ("Can't Hurt Me", 1, None)], "bank"),
    (NOV.replace(day=4), "Think Like a Monk and Ikigai, cash", [
        ("Think Like a Monk", 1, None), ("Ikigai", 1, None)], "cash"),
]
CREDIT = (NOV.replace(day=3), "Mrs Adebayo took The Five Love Languages and The Power of a Praying Wife, "
          "she go pay Friday", [("The Five Love Languages", 1, None), ("The Power of a Praying Wife", 1, None)],
          "c-adebayo", NOV.replace(day=6))
EXPENSES = [("Rent", 81_600, "other_exp"), ("Salary", 28_000, "other_exp"), ("Data", 15_000, "other_exp"),
            ("Content creation", 10_000, "other_exp"), ("Fan repair", 10_000, "other_exp"),
            ("Bank charges", 2_000, "other_exp"), ("Freebies", 1_160, "other_exp"), ("Utility bill", 1_100, "power"),
            ("Airtime", 400, "other_exp")]
SOLD = Counter()
for _, _, lines, *_ in [*SALES, CREDIT]:
    for name, qty, _ in lines:
        SOLD[name] += qty


def add_history(store, today=None, media=None):
    today = today or date.today()
    ids = {p.name: p for p in store.list_products()}

    def items(lines):
        return [Item(ids[n].id, q, price * 100 if price else ids[n].sell_price_kobo, price_confirmed=True)
                for n, q, price in lines]

    events = []                # (day, what she said, proposal fields), posted in date order so "Latest" is today's

    def post(day, said, **kw):
        if day <= today:       # nothing dated after the day the shop is made
            events.append((day, said, kw))

    post(OCT, "I put ₦150,000 of my own money into the shop's account", kind="capital", amount_kobo=150_000_00,
         payment_account="bank")
    for day, said, lines, account in SALES:
        its = items(lines)
        post(day, said, kind="sale", items=its, paid_kobo=sum(i.qty * i.unit_price_kobo for i in its),
             payment_account=account)
    day, said, lines, contact, due = CREDIT
    post(day, said, kind="sale", items=items(lines), contact_id=contact, paid_kobo=0, due_date=due)
    for name, naira, account in EXPENSES:
        post(OCT.replace(day=31), name, kind="expense", expense_account=account, amount_kobo=naira * 100,
             payment_account="bank")      # what she said is the label: "Expense (Rent)"
    post(NOV.replace(day=3), "Transport to Bodija market", kind="expense", expense_account="transport",
         amount_kobo=150_000, payment_account="cash")
    for day, said, kw in sorted(events, key=lambda e: e[0]):
        _post(store, said, (today - day).days, today, **kw)

    if media is not None:      # her cover photos: customers who send a cover get matched, posts need no new photo
        for f in sorted(COVERS.glob("*.jpg")):
            if store.get_product(f.stem):
                store.set_product_photo(f.stem, media.save(f.read_bytes(), "jpg")["url"])
    return store
