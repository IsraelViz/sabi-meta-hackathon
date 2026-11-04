"""Reports computed from journal lines and the sales leads. Nothing here is stored."""
import math
import re
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.ledger.store import now
from app.normalize.amounts import naira

LAGOS = ZoneInfo("Africa/Lagos")


def whole_naira(kobo):
    """Profit uses average cost, so it can land on kobo; nobody wants ₦5,865.46 in a summary."""
    return naira(round(kobo / 100) * 100)


HEADLINE_ACCOUNTS = {          # the one number a trader cares about, per kind of entry
    "sale": ("sales", "credit"), "purchase": ("inventory", "debit"), "payment_in": ("ar", "credit"),
    "payment_out": ("ap", "debit"), "drawing": ("drawings", "debit"), "stock_loss": ("stock_loss", "debit"),
    "saving": ("savings", "debit"), "capital": ("equity", "credit"),
    "opening": ("inventory", "debit"),
}


def headline_kobo(entry):
    if entry.kind == "expense":
        return sum(l.debit_kobo for l in entry.lines if l.account not in ("cash", "bank"))
    account, side = HEADLINE_ACCOUNTS.get(entry.kind, (None, None))
    if account:
        return sum(getattr(l, f"{side}_kobo") for l in entry.lines if l.account == account)
    return entry.total_debits


def _period(kind, today=None):
    today = today or date.today()
    if kind == "today":
        return today, today
    if kind == "week":
        return today - timedelta(days=today.weekday()), today
    if kind == "month":
        return today.replace(day=1), today
    return None, today


def profit_and_loss(store, period="today", today=None):
    start, end = _period(period, today)
    income = -store.account_balance("sales", start=start, end=end)
    cogs = store.account_balance("cogs", start=start, end=end)
    expenses = {code: store.account_balance(code, start=start, end=end)
                for code in ("stock_loss", "transport", "power", "other_exp")}
    expenses = {k: v for k, v in expenses.items() if v}
    gross = income - cogs
    return {"period": period, "start": start, "end": end, "sales_kobo": income, "cogs_kobo": cogs,
            "gross_profit_kobo": gross, "expenses_kobo": expenses,
            "net_profit_kobo": gross - sum(expenses.values())}


def cash_position(store):
    return {"cash_kobo": store.account_balance("cash"), "bank_kobo": store.account_balance("bank")}


def debtors(store, today=None):
    today = today or date.today()
    out = []
    for cid, balance, due in store.receivables():
        c = store.get_contact(cid)
        out.append({"contact_id": cid, "name": c.name if c else "?", "owes_kobo": balance,
                    "due": due, "overdue_days": max((today - due).days, 0) if due else 0})
    return out


def tracks_stock(store, product_id):
    """False until stock of it has ever come in (opening stock or a purchase). A new shop often starts with
    products and prices only: their sales are recorded without stock questions, and nothing nags to restock them."""
    return any(m.product_id == product_id and m.qty > 0 for e in store.list_entries() for m in e.stock_moves)


def low_stock(store):
    out = []
    for p in store.list_products():
        qty = store.stock_on_hand(p.id)
        if qty <= p.reorder_level and tracks_stock(store, p.id):
            out.append({"product_id": p.id, "name": p.name, "qty": qty, "unit": p.base_unit,
                        "reorder_level": p.reorder_level})
    return out


def report(store, kind, period="today"):
    if kind == "pnl":
        return profit_and_loss(store, period)
    if kind == "cash":
        return cash_position(store)
    if kind == "debtors":
        return debtors(store)
    if kind == "low_stock":
        return low_stock(store)
    if kind == "sales":
        return sales_pipeline(store)
    if kind == "restock":
        return [r for r in (restock_advice(store, p.id) for p in store.list_products()) if r["due"]]
    if kind == "savings":
        return savings(store)
    if kind == "coach":
        from app.ledger.coach import tips
        return tips(store, limit=5)
    raise ValueError(f"unknown report {kind!r}")


def daily_summary_text(store, today=None):
    pnl = profit_and_loss(store, "today", today)
    cash = cash_position(store)
    owed = debtors(store, today)
    low = low_stock(store)
    parts = [
        f"Today: sales {whole_naira(pnl['sales_kobo'])}, profit {whole_naira(pnl['net_profit_kobo'])}.",
        f"Cash {naira(cash['cash_kobo'])}, bank {naira(cash['bank_kobo'])}.",
    ]
    if owed:
        top = ", ".join(f"{d['name']} {naira(d['owes_kobo'])}" + (f" ({d['overdue_days']}d late)" if d["overdue_days"] else "")
                        for d in owed[:3])
        parts.append(f"Owing you: {top}" + (f" +{len(owed) - 3} more" if len(owed) > 3 else "") + ".")
    restock = [r for r in (restock_advice(store, p.id, today) for p in store.list_products()) if r["due"]]
    if restock:
        parts.append("Restock soon: " + ", ".join(
            f"{r['name']} ({r['stock']} left" + (f", ~{r['days_left']:g} days" if r["days_left"] is not None else "") + ")"
            for r in restock[:3]) + ".")
    elif low:
        parts.append("Running low: " + ", ".join(f"{l['name']} ({l['qty']} left)" for l in low[:3]) + ".")
    parts += sales_summary_lines(store, today)
    week = sales_pipeline(store, today, days=7)
    wanted = [m for m in week["missed"] if m["times"] >= 2 and "(out of stock)" not in m["item"]]
    if wanted:
        parts.append(f"{wanted[0]['times']} customers asked for {wanted[0]['item'].title()} this week. Worth stocking?")
    impact = impact_today(store, today)
    if impact["entries"] or impact["customer_replies"]:
        took = orders_taken(store, today)
        orders = (f", took {took['count']} order{'s' if took['count'] != 1 else ''} worth {naira(took['total_kobo'])}"
                  if took["count"] else "")
        parts.append(f"While you worked, Sabi answered {impact['customer_replies']} customer messages{orders} "
                     f"and recorded {impact['entries']} entries (about {impact['minutes_saved']} minutes of your time).")
    parts += savings_lines(store, pnl["net_profit_kobo"])
    parts += coach_lines(store, today, limit=1)
    return "\n".join(parts)


def orders_taken(store, today=None, days=1):
    """Orders customers placed with Sabi in the period, paid or not."""
    s = sales_pipeline(store, today, days=days)
    rows = [r for r in s["leads"] if r["order_total_kobo"] and r["stage"] != "asking"]
    return {"count": len(rows), "total_kobo": sum(r["order_total_kobo"] for r in rows),
            "paid": sum(1 for r in rows if r["stage"] == "paid")}


def savings(store):
    from app import prefs
    p = prefs.get(store)["savings"]
    saved = store.account_balance("savings") if "savings" in store.accounts else 0
    history = [{"date": e.entry_date, "amount_kobo": headline_kobo(e), "memo": e.memo}
               for e in store.list_entries() if e.kind == "saving" and e.status != "reversed"][-10:][::-1]
    return {"name": p["name"], "percent": p["percent"], "goal_kobo": p["goal_kobo"], "saved_kobo": saved,
            "progress_pct": round(100 * saved / p["goal_kobo"]) if p["goal_kobo"] else None, "history": history}


def savings_lines(store, profit_kobo):
    s = savings(store)
    if not s["percent"]:
        return []
    line = ""
    if profit_kobo > 0:
        put = round(profit_kobo * s["percent"] / 100 / 10_000) * 10_000 or 10_000
        line = f"Put aside {naira(put)} for {s['name']} today ({s['percent']:g}% of profit). "
    line += f"Saved so far: {naira(s['saved_kobo'])}"
    if s["goal_kobo"]:
        line += f" of {naira(s['goal_kobo'])} ({s['progress_pct']}%)"
    return [line + "."]


def coach_lines(store, today=None, limit=1):
    """Tips from the coach, and what happened after tips she acted on. Tips given are remembered."""
    from app.ledger import coach
    lines = [f"📈 {t}" for t in coach.follow_ups(store, today)]
    given = coach.tips(store, today, limit=limit)
    coach.remember(store, given, today)
    return lines + [f"💡 {t['text']}" for t in given]


def weekly_summary_text(store, today=None):
    """The week in one message: for owners who want one summary on a quiet day, not one a night."""
    from app.ledger.coach import best_sellers
    today = today or now().astimezone(LAGOS).date()
    start = today - timedelta(days=6)

    def week(a, b):
        sales = -store.account_balance("sales", start=a, end=b)
        cogs = store.account_balance("cogs", start=a, end=b)
        exp = sum(store.account_balance(c, start=a, end=b) for c in ("stock_loss", "transport", "power", "other_exp"))
        return sales, sales - cogs - exp

    sales, profit = week(start, today)
    before_sales, _ = week(start - timedelta(days=7), start - timedelta(days=1))
    change = ""
    if before_sales:
        pct = round(100 * (sales - before_sales) / before_sales)
        change = f" ({'up' if pct >= 0 else 'down'} {abs(pct)}% on the week before)"
    cash = cash_position(store)
    parts = [f"Last 7 days: sales {whole_naira(sales)}{change}, profit {whole_naira(profit)}.",
             f"Cash {naira(cash['cash_kobo'])}, bank {naira(cash['bank_kobo'])}."]
    top = best_sellers(store, start, today)
    if top:
        parts.append("Best sellers: " + ", ".join(f"{t['name']} ({t['qty']})" for t in top) + ".")
    owed = debtors(store, today)
    if owed:
        parts.append("Owing you " + naira(sum(d["owes_kobo"] for d in owed)) + ":\n" + "\n".join(
            f"• {d['name']} {naira(d['owes_kobo'])}" + (f", {d['overdue_days']} days late" if d["overdue_days"] else "")
            for d in owed[:8]) + (f"\n• +{len(owed) - 8} more" if len(owed) > 8 else ""))
    restock = [r for r in (restock_advice(store, p.id, today) for p in store.list_products()) if r["due"]]
    if restock:
        parts.append("Restock soon: " + ", ".join(f"{r['name']} (order ~{r['order_qty']})" for r in restock[:4]) + ".")
    took = orders_taken(store, today, days=7)
    s = sales_pipeline(store, today, days=7)
    if s["chats"]:
        parts.append(f"Sabi talked with {s['chats']} customers and took {took['count']} orders "
                     f"worth {naira(took['total_kobo'])} ({took['paid']} paid).")
    wanted = [m for m in s["missed"] if m["times"] >= 2 and "(out of stock)" not in m["item"]]
    if wanted:
        parts.append(f"Customers asked {wanted[0]['times']} times for {wanted[0]['item'].title()}. Worth stocking?")
    parts += savings_lines(store, profit)
    parts += coach_lines(store, today, limit=3)
    return "\n".join(parts)


# --- sales ------------------------------------------------------------------

def lead_stage(store, lead):
    """asking → ordered → paid; an order that expired unpaid makes the deal lost."""
    if lead["stage"] != "ordered":
        return lead["stage"]
    order = store.get_order(lead["order_id"]) if lead.get("order_id") else None
    if order is None:
        return "asking"
    if order["status"] == "paid":
        return "paid"
    if order["status"] in ("cancelled", "expired") or order["expires_at"] <= now():
        return "lost"
    return "ordered"


def with_owner(lead, at=None):
    return bool(lead and lead.get("handoff_until") and lead["handoff_until"] > (at or now()))


def sales_pipeline(store, today=None, days=1):
    """Customer chats active in the last `days` days (Lagos time), by stage."""
    today = today or now().astimezone(LAGOS).date()
    start = today - timedelta(days=days - 1)
    products = {p.id: p.name for p in store.list_products()}
    rows, missed = [], Counter()
    for lead in store.list_leads():
        if not start <= lead["updated_at"].astimezone(LAGOS).date() <= today:
            continue
        contact = store.get_contact(lead["contact_id"])
        order = store.get_order(lead["order_id"]) if lead.get("order_id") else None
        rows.append({"contact_id": lead["contact_id"], "name": contact.name if contact else "?",
                     "stage": lead_stage(store, lead), "with_owner": with_owner(lead),
                     "handoff_reason": lead.get("handoff_reason") if with_owner(lead) else None,
                     "interests": [products.get(i, i) for i in lead.get("interests") or []],
                     "order_total_kobo": order["total_kobo"] if order else None,
                     "updated_at": lead["updated_at"]})
        missed.update(lead.get("unmet") or [])
    stages = Counter(r["stage"] for r in rows)
    return {"chats": len(rows), "asking": stages["asking"], "ordered": stages["ordered"], "paid": stages["paid"],
            "lost": stages["lost"], "paid_kobo": sum(r["order_total_kobo"] or 0 for r in rows if r["stage"] == "paid"),
            "waiting_for_owner": [r["name"] for r in rows if r["with_owner"]],
            "missed": [{"item": k, "times": n} for k, n in missed.most_common(5)], "leads": rows}


def sales_summary_lines(store, today=None):
    s = sales_pipeline(store, today)
    if not s["chats"]:
        return []
    ordered = s["ordered"] + s["paid"] + s["lost"]
    lines = [f"Customer chats: {s['chats']} ({ordered} ordered, {s['paid']} paid"
             + (f", {s['lost']} didn't pay" if s["lost"] else "") + ")."]
    if s["waiting_for_owner"]:
        lines.append("Waiting for you: " + ", ".join(s["waiting_for_owner"]) + ".")
    if s["missed"]:
        lines.append("Asked for but not available: "
                     + ", ".join(m["item"] + (f" ×{m['times']}" if m["times"] > 1 else "") for m in s["missed"]) + ".")
    return lines


# --- credit: how a customer has paid before ----------------------------------------------

def credit_profile(store, contact_id, today=None):
    """What she owes now, how late, and how late her past debts were paid (oldest debt paid first)."""
    today = today or date.today()
    debts, payments = [], []
    for e in store.list_entries():
        if e.status == "reversed" or e.kind == "reversal":
            continue
        for l in e.lines:
            if l.account != "ar" or l.contact_id != contact_id:
                continue
            if l.debit_kobo:
                debts.append([e.entry_date, e.due_date or e.entry_date, l.debit_kobo])
            if l.credit_kobo:
                payments.append((e.entry_date, l.credit_kobo))
    debts.sort(key=lambda d: d[0])
    late, i = [], 0
    for when, amount in sorted(payments):
        while amount > 0 and i < len(debts):
            take = min(amount, debts[i][2])
            debts[i][2] -= take
            amount -= take
            if debts[i][2] == 0:
                late.append(max((when - debts[i][1]).days, 0))
                i += 1
    still_open = [d for d in debts[i:] if d[2] > 0]
    return {"owes_kobo": sum(d[2] for d in still_open),
            "overdue_days": max([(today - d[1]).days for d in still_open] + [0]),
            "debts_paid": len(late), "paid_late": sum(1 for d in late if d > 0),
            "avg_days_late": round(sum(late) / len(late), 1) if late else None}


# --- restock: how fast each product sells --------------------------------------------------

def last_purchase(store, product_id):
    for e in reversed(store.list_entries()):
        if e.kind != "purchase" or e.status == "reversed":
            continue
        for l in e.lines:
            if l.account == "inventory" and l.product_id == product_id and l.qty:
                supplier = store.get_contact(e.contact_id) if e.contact_id else None
                return {"unit_cost_kobo": l.debit_kobo // l.qty, "date": e.entry_date,
                        "supplier_id": e.contact_id, "supplier": supplier.name if supplier else None}
    return None


def restock_advice(store, product_id, today=None, days=14):
    """Stock left, days left at the recent pace, and how many to order for about a week."""
    today = today or date.today()
    p = store.get_product(product_id)
    stock = store.stock_on_hand(product_id)
    sold = store.sales_qty(product_id, start=today - timedelta(days=days - 1), end=today)
    per_day = sold / days
    days_left = round(stock / per_day, 1) if per_day else None
    never_stocked = not tracks_stock(store, product_id)
    due = not never_stocked and (stock <= p.reorder_level or (days_left is not None and days_left <= 3))
    order = max(math.ceil(per_day * 7) - stock, p.reorder_level * 2 - stock, 1)
    return {"product_id": p.id, "name": p.name, "unit": p.base_unit, "stock": stock, "per_day": round(per_day, 1),
            "days_left": days_left, "due": due, "order_qty": order, "last_purchase": last_purchase(store, product_id)}


# --- what Sabi saved her ----------------------------------------------------------------------

MINUTES_PER_ENTRY = 2      # writing it in the notebook and adding it up later
MINUTES_PER_REPLY = 2      # stopping work to answer a customer's message


def impact_today(store, today=None):
    today = today or now().astimezone(LAGOS).date()
    entries = [e for e in store.list_entries(start=today, end=today)
               if (e.created_by or "").startswith("agent") and e.kind != "reversal"]
    replies = 0
    for r in store.recent_runs(1000):
        created = r["created_at"]
        if isinstance(created, datetime) and created.astimezone(LAGOS).date() == today and r["persona"] == "customer":
            replies += 1
    return {"entries": len(entries), "customer_replies": replies,
            "minutes_saved": len(entries) * MINUTES_PER_ENTRY + replies * MINUTES_PER_REPLY}


# --- the statement a lender can read ----------------------------------------------------------

def _months(start, end):
    m = start.replace(day=1)
    while m <= end:
        nxt = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield max(m, start), min(nxt - timedelta(days=1), end)
        m = nxt


def business_statement(store, today=None, days=90):
    """The shop's trading record from its own books, for a loan or supplier-credit application."""
    today = today or now().astimezone(LAGOS).date()
    trading = [e for e in store.list_entries() if e.kind not in ("opening",)]
    first = min((e.entry_date for e in trading), default=today)
    start = max(first, today - timedelta(days=days - 1))

    def pnl(a, b):
        sales = -store.account_balance("sales", start=a, end=b)
        cogs = store.account_balance("cogs", start=a, end=b)
        exp = sum(store.account_balance(c, start=a, end=b) for c in ("stock_loss", "transport", "power", "other_exp"))
        return {"sales_kobo": sales, "gross_profit_kobo": sales - cogs, "expenses_kobo": exp,
                "net_profit_kobo": sales - cogs - exp}

    months = [{"label": a.strftime("%B %Y"), "from": a, "to": b, **pnl(a, b)} for a, b in _months(start, today)]
    total = pnl(start, today)
    period = [e for e in trading if start <= e.entry_date <= today]
    live = [e for e in period if e.status != "reversed" and e.kind != "reversal"]
    sale_days = {e.entry_date for e in live if e.kind == "sale"}
    n_days = (today - start).days + 1
    credit_sold = sum(l.debit_kobo for e in live if e.kind == "sale" for l in e.lines if l.account == "ar")
    collected = sum(l.credit_kobo for e in live if e.kind == "payment_in" for l in e.lines if l.account == "ar")
    by_product = Counter()
    for e in live:
        if e.kind == "sale":
            for l in e.lines:
                if l.account == "sales" and l.product_id:
                    by_product[l.product_id] += l.credit_kobo
    stock_value = sum(store.stock_on_hand(p.id) * p.avg_cost_kobo for p in store.list_products())
    cash = cash_position(store)
    owed_to_her = sum(d["owes_kobo"] for d in debtors(store, today))
    owes_suppliers = -store.account_balance("ap")
    with_evidence = sum(1 for e in period if e.evidence_id)
    return {
        "shop": getattr(store, "business", {}).get("name", "Shop"),
        "address": re.split(r"(?<=\.)\s|\n", getattr(store, "business", {}).get("shop_info") or "")[0][:160],
        "prepared": today, "from": start, "to": today, "days": n_days,
        "months": months, "total": total,
        "avg_weekly_sales_kobo": round(total["sales_kobo"] / max(n_days / 7, 1)),
        "trading_days": len(sale_days), "margin_pct": round(100 * total["gross_profit_kobo"] / total["sales_kobo"], 1)
        if total["sales_kobo"] else 0,
        "cash_kobo": cash["cash_kobo"], "bank_kobo": cash["bank_kobo"], "stock_value_kobo": stock_value,
        "owed_to_shop_kobo": owed_to_her, "owed_to_suppliers_kobo": max(owes_suppliers, 0),
        "credit_sold_kobo": credit_sold, "collected_kobo": collected,
        "collection_pct": round(100 * collected / credit_sold) if credit_sold else None,
        "top_products": [{"name": (store.get_product(pid).name if store.get_product(pid) else pid), "sales_kobo": v}
                         for pid, v in by_product.most_common(5)],
        "entries": len(period), "with_evidence": with_evidence,
        "corrections": sum(1 for e in period if e.kind == "reversal"),
    }
