"""Reports computed from journal lines. Nothing here is stored."""
from datetime import date, timedelta

from app.normalize.amounts import naira


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


def low_stock(store):
    out = []
    for p in store.list_products():
        qty = store.stock_on_hand(p.id)
        if qty <= p.reorder_level:
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
    raise ValueError(f"unknown report {kind!r}")


def daily_summary_text(store, today=None):
    pnl = profit_and_loss(store, "today", today)
    cash = cash_position(store)
    owed = debtors(store, today)
    low = low_stock(store)
    parts = [
        f"Today: sales {naira(pnl['sales_kobo'])}, profit {naira(pnl['net_profit_kobo'])}.",
        f"Cash {naira(cash['cash_kobo'])}, bank {naira(cash['bank_kobo'])}.",
    ]
    if owed:
        top = ", ".join(f"{d['name']} {naira(d['owes_kobo'])}" + (f" ({d['overdue_days']}d late)" if d["overdue_days"] else "")
                        for d in owed[:3])
        parts.append(f"Owing you: {top}" + (f" +{len(owed) - 3} more" if len(owed) > 3 else "") + ".")
    if low:
        parts.append("Running low: " + ", ".join(f"{l['name']} ({l['qty']} left)" for l in low[:3]) + ".")
    return "\n".join(parts)
