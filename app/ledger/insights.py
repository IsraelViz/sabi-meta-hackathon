"""Business intelligence from her own books: what is selling, what makes money, what is stuck, what
customers want that she lacks, and what is coming. Every number and every sentence is computed here
from the ledger; the model only retells them.

The window is the last 30 days, compared with the 30 before. Weekly sales go back 8 weeks so a
season (a school resumption rush, festive sales) shows in the shape.
"""
import re
from collections import Counter
from datetime import date, timedelta

from app.ledger import coach
from app.ledger.reports import debtors, cash_position, restock_advice, tracks_stock, whole_naira as naira

DAYS = 30
WEEKS = 8


SMALL = {"and", "of", "the", "for", "in", "to", "a"}


def _title(words):
    """Requests are noted in lowercase: "junior secondary agricultural science jss1" -> "Junior Secondary
    Agricultural Science JSS1"."""
    out = []
    for i, w in enumerate(words.split()):
        if re.fullmatch(r"(jss|sss|ss|p)\d", w):
            out.append(w.upper())
        else:
            out.append(w if i and w in SMALL else w[:1].upper() + w[1:])
    return " ".join(out)


def _pct(part, whole):
    return round(100 * part / whole) if whole else None


def insights(store, today=None):
    today = today or date.today()
    start, prev_start = today - timedelta(days=DAYS - 1), today - timedelta(days=2 * DAYS - 1)
    now = coach._sold(store, start, today)
    before = coach._sold(store, prev_start, start - timedelta(days=1))
    products = {p.id: p for p in store.list_products()}
    sales = sum(s for _, s, _ in now.values())
    sales_before = sum(s for _, s, _ in before.values())
    costed = {pid: v for pid, v in now.items() if tracks_stock(store, pid)}
    gross = sum(s - c for _, s, c in costed.values())
    costed_sales = sum(s for _, s, _ in costed.values())

    stock_value = sum(max(store.stock_on_hand(p.id), 0) * p.avg_cost_kobo for p in products.values())
    owed = debtors(store, today)
    cash = cash_position(store)
    kpis = {"sales_kobo": sales, "sales_change_pct": _pct(sales - sales_before, sales_before),
            "gross_profit_kobo": gross, "margin_pct": _pct(gross, costed_sales),
            "owed_to_you_kobo": sum(d["owes_kobo"] for d in owed), "stock_value_kobo": stock_value,
            "cash_kobo": cash.get("cash_kobo", 0) + cash.get("bank_kobo", 0)}

    weekly = []
    for w in range(WEEKS - 1, -1, -1):
        end = today - timedelta(days=7 * w)
        week = coach._sold(store, end - timedelta(days=6), end)
        weekly.append({"week_ending": end.isoformat(), "label": end.strftime("%d %b"),
                       "sales_kobo": sum(s for _, s, _ in week.values())})

    def row(pid, v):
        qty, s, c = v
        p = products.get(pid)
        tracked = tracks_stock(store, pid)
        return {"product_id": pid, "name": p.name if p else pid, "qty": qty, "sales_kobo": s,
                "share_pct": _pct(s, sales), "profit_kobo": s - c if tracked else None,
                "margin_pct": _pct(s - c, s) if tracked else None}

    best = sorted((row(pid, v) for pid, v in now.items()), key=lambda r: r["sales_kobo"], reverse=True)
    earners = sorted((r for r in best if r["profit_kobo"] is not None), key=lambda r: r["profit_kobo"], reverse=True)

    slow = []
    for p in products.values():
        stock = store.stock_on_hand(p.id)
        if stock > 0 and p.id not in now and tracks_stock(store, p.id) and not (p.details or {}).get("replaced_by"):
            slow.append({"product_id": p.id, "name": p.name, "stock": stock, "tied_up_kobo": stock * p.avg_cost_kobo})
    slow.sort(key=lambda r: r["tied_up_kobo"], reverse=True)

    wanted = Counter()
    for lead in store.list_leads():
        for item in lead.get("unmet") or []:
            wanted[item.split(" (")[0].strip()] += 1
    demand = [{"item": _title(k), "times": n} for k, n in wanted.most_common(5)]

    price_watch = [t for t in coach.tips(store, today, limit=6) if t["kind"] in coach.PRICE_TIPS][:3]
    forecast = _forecast(store, today, products)

    out = {"period": {"start": start.isoformat(), "end": today.isoformat(), "days": DAYS}, "kpis": kpis,
           "weekly_sales": weekly, "best_sellers": best[:5], "top_earners": earners[:5], "slow_stock": slow[:5],
           "customer_demand": demand, "price_watch": [{"product_id": t["product_id"], "text": t["text"]}
                                                      for t in price_watch],
           "forecast": forecast, "owed": [{"name": d["name"], "owes_kobo": d["owes_kobo"],
                                           "days_late": d.get("overdue_days") or 0} for d in owed[:3]]}
    out["takeaways"] = takeaways(out, store, today)
    return out


def _forecast(store, today, products):
    from app.agent import books
    if any(books.is_book(p) for p in products.values()):
        ctx = type("Ctx", (), {"store": store})()
        r = books.term_restock(ctx, today=today)
        return {"kind": "school_term", "headline": f"{r['next_term'].capitalize()} resumes {r['resumes']}",
                "order_by": r["order_by"], "items": [{"name": o["name"], "order_qty": o["order_qty"]} for o in r["order"][:5]],
                "clear": [c["name"] for c in r["clear_old_editions"]]}
    due = [restock_advice(store, pid, today) for pid in products]
    due = sorted((d for d in due if d["due"]), key=lambda d: d["days_left"] if d["days_left"] is not None else 99)
    return {"kind": "restock", "headline": "Running low at your pace",
            "items": [{"name": d["name"], "order_qty": d["order_qty"], "days_left": d["days_left"]} for d in due[:5]]}


def takeaways(i, store, today):
    """The few sentences worth reading, most important first."""
    k, out = i["kpis"], []
    change = k["sales_change_pct"]
    if change is not None and abs(change) >= 5:
        line = f"Sales in the last {DAYS} days are {naira(k['sales_kobo'])}, {'up' if change > 0 else 'down'} {abs(change)}% on the {DAYS} days before"
        if change < 0 and i["forecast"].get("kind") == "school_term":
            line += ", which had the resumption rush: a normal mid-term dip"
        out.append(line + ".")
    elif k["sales_kobo"]:
        out.append(f"Sales in the last {DAYS} days are {naira(k['sales_kobo'])}, about the same as before.")
    if i["top_earners"]:
        e = i["top_earners"][0]
        out.append(f"{e['name']} makes you the most profit: {naira(e['profit_kobo'])} in {DAYS} days"
                   + (f" ({e['margin_pct']}% margin)." if e["margin_pct"] is not None else "."))
    top3 = sum(r["share_pct"] or 0 for r in i["best_sellers"][:3])
    if len(i["best_sellers"]) >= 4 and top3 >= 60:
        out.append(f"Your top 3 products bring {top3}% of sales: keep them in stock above everything.")
    if i["price_watch"]:
        out.append(i["price_watch"][0]["text"])
    if i["slow_stock"]:
        tied = sum(s["tied_up_kobo"] for s in i["slow_stock"])
        names = ", ".join(s["name"] for s in i["slow_stock"][:2])
        out.append(f"{naira(tied)} is sitting in stock that hasn't sold in {DAYS} days ({names}"
                   + (" and more" if len(i["slow_stock"]) > 2 else "") + "). A small discount or a post could free it.")
    if i["customer_demand"]:
        d = i["customer_demand"][0]
        out.append(f"Customers asked {d['times']} time{'s' if d['times'] != 1 else ''} for {d['item']}, which you "
                   "couldn't supply: worth stocking.")
    late = [o for o in i["owed"] if o["days_late"] > 0]
    if late:
        out.append(f"{late[0]['name']} owes {naira(late[0]['owes_kobo'])} and is {late[0]['days_late']} "
                   f"day{'s' if late[0]['days_late'] != 1 else ''} late.")
    f = i["forecast"]
    if f.get("kind") == "school_term" and f["items"]:
        out.append(f"{f['headline']}: order {f['items'][0]['order_qty']} {f['items'][0]['name']} and "
                   f"{len(f['items']) - 1} more by {f['order_by']}.")
    elif f.get("items"):
        out.append(f"{f['items'][0]['name']} will run out soon: order {f['items'][0]['order_qty']}.")
    return out[:7]
