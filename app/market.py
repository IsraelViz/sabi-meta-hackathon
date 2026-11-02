"""What other Sabi shops pay suppliers, and who is restocking the same thing: anonymous, opt-in.

A shop's purchase prices count only if she switched on "share my supplier prices" (prefs.share_prices),
and nothing is shown unless at least MIN_SHOPS other shops bought something with the same name and
unit, so no single shop's price can be picked out. No shop names, suppliers or quantities leave a shop.
"""
from datetime import date, timedelta

from app.normalize.fuzzy import name_similarity

MIN_SHOPS = 2          # other shops needed before any number is shown
SAME_NAME = 0.85
DAYS = 60


def _bought(store, product, since):
    """Unit costs this shop paid for one product since a date."""
    out = []
    for e in store.list_entries(start=since):
        if e.kind != "purchase" or e.status == "reversed":
            continue
        for l in e.lines:
            if l.account == "inventory" and l.product_id == product.id and l.qty:
                out.append(l.debit_kobo // l.qty)
    return out


def _same(a, b):
    return a.base_unit.lower().rstrip("s") == b.base_unit.lower().rstrip("s") and name_similarity(a.name, b.name) >= SAME_NAME


def _others(shops, shop):
    from app import prefs
    return [s for s in shops.all() if s.id != shop.id and prefs.get(s.store)["share_prices"]]


def market_view(shops, shop, product_id, today=None):
    """{'shops': n, 'low', 'median', 'high', 'mine', 'restocking'} for one of her products, or a note why not."""
    from app.ledger.reports import restock_advice
    today = today or date.today()
    p = shop.store.get_product(product_id)
    if p is None:
        return {"error": "no such product; use find_product"}
    prices, restocking, n = [], 0, 0
    for other in _others(shops, shop):
        matches = [q for q in other.store.list_products() if _same(p, q)]
        found = False
        for q in matches:
            paid = _bought(other.store, q, today - timedelta(days=DAYS))
            if paid:
                prices += paid
                found = True
            if restock_advice(other.store, q.id, today)["due"]:
                restocking += 1
        n += found
    mine = _bought(shop.store, p, today - timedelta(days=DAYS))
    if n < MIN_SHOPS:
        return {"product": p.name, "shops": n,
                "note": f"Not enough Sabi shops have shared prices for {p.name} yet (need {MIN_SHOPS})."}
    prices.sort()
    return {"product": p.name, "unit": p.base_unit, "shops": n, "low_kobo": prices[0], "high_kobo": prices[-1],
            "median_kobo": prices[len(prices) // 2], "mine_kobo": mine[-1] if mine else None,
            "restocking": restocking, "days": DAYS,
            "note": "Anonymous: from other Sabi shops that chose to share. No names or suppliers."}
