"""The intelligence that comes with every entry: after a sale, purchase, payment or expense is recorded,
the one or two things her books now say that she would want to know. All computed here; the model
picks the most useful one and says it in a short line after the confirmation.
"""
from datetime import timedelta

from app.ledger.reports import credit_profile, debtors, last_purchase, restock_advice, whole_naira as naira

ORDINAL = {1: "1st", 2: "2nd", 3: "3rd"}


def _nth(n):
    return ORDINAL.get(n if n < 20 else n % 10, f"{n}th") if n not in (11, 12, 13) else f"{n}th"


def _sales_on(store, day):
    entries = [e for e in store.list_entries(start=day, end=day) if e.kind == "sale" and e.status != "reversed"]
    return len(entries), sum(l.credit_kobo for e in entries for l in e.lines if l.account == "sales")


def _account_total(store, accounts, start, end):
    return sum(l.debit_kobo for e in store.list_entries(start=start, end=end) if e.status != "reversed"
               for l in e.lines if l.account in accounts)


def after(entry, store):
    """[str]: what the books now say, most useful first. Empty when there is nothing worth saying."""
    day = entry.entry_date
    out = []
    if entry.kind == "sale":
        count, total = _sales_on(store, day)
        if count >= 2:
            out.append(f"That's your {_nth(count)} sale today: {naira(total)} so far.")
        cogs = sum(l.debit_kobo for l in entry.lines if l.account == "cogs")
        sales = sum(l.credit_kobo for l in entry.lines if l.account == "sales")
        if cogs and sales:
            made = sales - cogs
            out.append(f"You made {naira(made)} on this sale ({round(100 * made / sales)}% margin)." if made > 0 else
                       f"This sale lost {naira(-made)}: the price is below what the stock cost you.")
        for pid in {l.product_id for l in entry.lines if l.account == "sales" and l.product_id}:
            r = restock_advice(store, pid, day)
            if r["stock"] <= 0:
                out.append(f"{r['name']} is now sold out.")
            elif r["days_left"] is not None and r["days_left"] <= 10:
                out.append(f"{r['stock']} {r['name']} left, about {max(1, round(r['days_left']))} days at your pace.")
        cid = entry.contact_id or next((l.contact_id for l in entry.lines if l.contact_id), None)
        if cid and any(l.account == "ar" and l.debit_kobo for l in entry.lines):
            prof, name = credit_profile(store, cid, day), store.get_contact(cid).name
            if prof["owes_kobo"] <= 0:
                pass
            elif prof["avg_days_late"]:
                out.append(f"On credit: {name} usually pays about {prof['avg_days_late']:g} days late. They now owe "
                           f"{naira(prof['owes_kobo'])}.")
            elif prof["debts_paid"]:
                out.append(f"On credit: {name} has paid on time before. They now owe {naira(prof['owes_kobo'])}.")
            else:
                out.append(f"On credit: {name} now owes {naira(prof['owes_kobo'])}.")
    elif entry.kind == "purchase":
        for m in entry.stock_moves:
            p = store.get_product(m.product_id)
            before = [e for e in store.list_entries() if e.kind == "purchase" and e.id != entry.id and e.status != "reversed"
                      and any(x.product_id == m.product_id for x in e.stock_moves)]
            if before:
                last = max(before, key=lambda e: (e.entry_date, e.seq))
                old = next(x.unit_cost_kobo for x in last.stock_moves if x.product_id == m.product_id)
                if old and abs(m.unit_cost_kobo - old) * 100 >= old * 3:
                    word = "more" if m.unit_cost_kobo > old else "less"
                    out.append(f"{p.name} cost {naira(abs(m.unit_cost_kobo - old))} {word} each than last time.")
            if p and p.sell_price_kobo and m.unit_cost_kobo:
                margin = round(100 * (p.sell_price_kobo - m.unit_cost_kobo) / p.sell_price_kobo)
                if margin < 10:
                    out.append(f"At {naira(p.sell_price_kobo)} you'd make only {margin}% on these. Worth a new price?")
    elif entry.kind == "payment_in" and entry.contact_id:
        left = credit_profile(store, entry.contact_id, day)["owes_kobo"]
        name = store.get_contact(entry.contact_id).name
        out.append(f"{name} is all paid up." if left <= 0 else f"{name} still owes {naira(left)}.")
        owed = sum(d["owes_kobo"] for d in debtors(store, day))
        if owed:
            out.append(f"{naira(owed)} is owed to you in total.")
    elif entry.kind == "expense":
        accounts = {l.account for l in entry.lines if l.debit_kobo}
        week = _account_total(store, accounts, day - timedelta(days=6), day)
        before = _account_total(store, accounts, day - timedelta(days=13), day - timedelta(days=7))
        what = next(iter(accounts)).replace("_", " ").replace("other exp", "other expenses")
        if before and week > before * 1.3:
            out.append(f"{what.capitalize()} this week: {naira(week)}, up from {naira(before)} last week.")
    return out[:3]
