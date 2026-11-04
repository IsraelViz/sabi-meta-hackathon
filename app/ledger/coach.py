"""Sabi as a business coach: a few plain tips from her own numbers, and whether she acted on them.

Every tip is computed here from the books (margins, costs, what sold and what didn't); the model
never invents one. A tip about a price is remembered with the price at the time, so a later
summary can say what happened after she changed it.
"""
from collections import Counter
from datetime import date, timedelta

from app.ledger.reports import last_purchases, tracks_stock, whole_naira as naira

TARGET_MARGIN = 20        # % a price suggestion aims for
THIN_MARGIN = 10          # below this a margin is called thin
COST_JUMP = 10            # % a new purchase cost must rise above the average to mention it
SLOW_DAYS = 14
MIN_TIED_UP = 1_000_000   # ₦10,000 of slow stock before it is worth a tip
PRIORITY = {"loss": 0, "cost_up": 1, "thin": 2, "no_cost": 3, "slow": 4, "best": 5}
PRICE_TIPS = ("loss", "cost_up", "thin")


def _round_price(kobo):
    """Prices people say out loud: to ₦50 under ₦2,000, else to ₦100."""
    step = 5_000 if kobo < 200_000 else 10_000
    return -(-kobo // step) * step


def _sold(store, start, end):
    """{product_id: (qty, sales_kobo, cogs_kobo)} for live sales in the range."""
    out = {}
    for e in store.list_entries(start=start, end=end):
        if e.kind != "sale" or e.status == "reversed":
            continue
        for l in e.lines:
            if not l.product_id:
                continue
            qty, sales, cogs = out.get(l.product_id, (0, 0, 0))
            if l.account == "sales":
                out[l.product_id] = (qty + (l.qty or 0), sales + l.credit_kobo, cogs)
            elif l.account == "cogs":
                out[l.product_id] = (qty, sales, cogs + l.debit_kobo)
    return out


def tips(store, today=None, limit=3):
    """The most useful tips first: [{kind, product_id, price_kobo, text}]."""
    today = today or date.today()
    start = today - timedelta(days=SLOW_DAYS - 1)
    sold = _sold(store, start, today)
    out = []
    best = None
    lasts = last_purchases(store)
    for p in store.list_products():
        qty, sales, cogs = sold.get(p.id, (0, 0, 0))
        price, cost = p.sell_price_kobo, p.avg_cost_kobo
        tracked = tracks_stock(store, p.id)
        last = lasts.get(p.id)
        if cost and price and tracked:
            margin = round(100 * (price - cost) / price)
            fair = _round_price(cost * 100 // (100 - TARGET_MARGIN))
            if price < cost:
                out.append({"kind": "loss", "product_id": p.id, "price_kobo": price,
                            "text": f"You sell {p.name} at {naira(price)} but it costs you {naira(cost)}: "
                                    f"you lose {naira(cost - price)} on each one. {naira(fair)} would give you "
                                    f"a {TARGET_MARGIN}% margin."})
            elif last and last["unit_cost_kobo"] > cost * (100 + COST_JUMP) // 100 and price < _round_price(
                    last["unit_cost_kobo"] * 100 // (100 - TARGET_MARGIN)):
                new_cost = last["unit_cost_kobo"]
                new_margin = round(100 * (price - new_cost) / price)
                out.append({"kind": "cost_up", "product_id": p.id, "price_kobo": price,
                            "text": f"Your last {p.name} cost {naira(new_cost)}, up from {naira(cost)} on average. "
                                    f"At {naira(price)} your margin falls to {new_margin}%. "
                                    f"Think about {naira(_round_price(new_cost * 100 // (100 - TARGET_MARGIN)))}."})
            elif margin < THIN_MARGIN and qty:
                out.append({"kind": "thin", "product_id": p.id, "price_kobo": price,
                            "text": f"You make only {naira(price - cost)} ({margin}%) on each {p.name}. "
                                    f"At {naira(fair)} you'd make {naira(fair - cost)} each."})
        if qty and not tracked:
            out.append({"kind": "no_cost", "product_id": p.id, "price_kobo": price,
                        "text": f"I don't know what {p.name} costs you, so its profit is counted as the full "
                                f"price. Tell me once, e.g. \"I have 10 {p.name} left, I bought them at ₦…\"."})
        stock = store.stock_on_hand(p.id)
        tied = stock * cost
        if tracked and stock > 0 and not qty and tied >= MIN_TIED_UP:
            out.append({"kind": "slow", "product_id": p.id, "price_kobo": price,
                        "text": f"{p.name}: {stock} {p.base_unit} in stock and none sold in {SLOW_DAYS} days ({naira(tied)} tied up). "
                                f"A post or a small discount could move them."})
        profit = sales - cogs
        if qty and (best is None or profit > best[0]):
            best = (profit, p, qty)
    if best and best[0] > 0:
        profit, p, qty = best
        out.append({"kind": "best", "product_id": p.id, "price_kobo": p.sell_price_kobo,
                    "text": f"{p.name} made you the most profit in the last {SLOW_DAYS} days: "
                            f"{naira(profit)} from {qty} sold. Don't run out of it."})
    out.sort(key=lambda t: PRIORITY[t["kind"]])
    return out[:limit]


def remember(store, given, today=None):
    """Keep the price tips she was given, so a later summary can tell her what changed."""
    from app import prefs
    today = today or date.today()
    kept = prefs.get(store).get("_tips") or []
    have = {(t["kind"], t["product_id"]) for t in kept}
    kept += [{"kind": t["kind"], "product_id": t["product_id"], "price_kobo": t["price_kobo"], "at": today.isoformat()}
             for t in given if t["kind"] in PRICE_TIPS and (t["kind"], t["product_id"]) not in have]
    prefs.save(store, {"_tips": kept[-20:]})


def follow_ups(store, today=None):
    """Price tips she acted on: what happened since. Each is reported once."""
    from app import prefs
    today = today or date.today()
    kept = prefs.get(store).get("_tips") or []
    out, left = [], []
    for t in kept:
        p = store.get_product(t["product_id"])
        if p is None:
            continue
        if p.sell_price_kobo == t["price_kobo"]:
            if (today - date.fromisoformat(t["at"])).days <= 30:
                left.append(t)
            continue
        since = date.fromisoformat(t["at"])
        qty, sales, cogs = _sold(store, since, today).get(p.id, (0, 0, 0))
        line = f"You changed {p.name} from {naira(t['price_kobo'])} to {naira(p.sell_price_kobo)}"
        if qty and sales:
            line += f". Since then: {qty} sold, {naira(sales - cogs)} profit ({round(100 * (sales - cogs) / sales)}% margin)."
        else:
            line += ". No sales at the new price yet."
        out.append(line)
    if out or len(left) != len(kept):
        prefs.save(store, {"_tips": left})
    return out


def best_sellers(store, start, end, n=3):
    sold = _sold(store, start, end)
    names = {p.id: p.name for p in store.list_products()}
    top = Counter({pid: v[1] for pid, v in sold.items()}).most_common(n)
    return [{"name": names.get(pid, "?"), "qty": sold[pid][0], "sales_kobo": kobo} for pid, kobo in top]
