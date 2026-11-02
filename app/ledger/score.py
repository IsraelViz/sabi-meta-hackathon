"""The Sabi Score: how ready her records are to back a loan, out of 100.

It is not a credit score from a bureau. It reads only her own books, and every part says
what it measured and how to raise it, so she can see exactly why she got her number.
She decides whether it appears on the statement she sends a lender (prefs.share_score).
"""
from datetime import timedelta

from app.ledger.reports import business_statement, now, LAGOS

MIN_DAYS = 14     # fewer days of records than this and there is no score yet

# (key, weight, what it is)
PARTS = [
    ("records", 25, "Keeping records"),
    ("steady", 20, "Steady sales"),
    ("profit", 20, "Profit"),
    ("collect", 15, "Collecting credit"),
    ("proof", 10, "Proof behind entries"),
    ("growth", 10, "Growth"),
]


def _days(n):
    return f"{n} day{'' if n == 1 else 's'}"


def _clamp(x):
    return max(0, min(100, round(x)))


def _sales(store, start, end):
    return -store.account_balance("sales", start=start, end=end)


def sabi_score(store, today=None):
    today = today or now().astimezone(LAGOS).date()
    s = business_statement(store, today, days=90)
    days = s["days"]
    live = [e for e in store.list_entries(start=s["from"], end=today)
            if e.kind not in ("opening", "reversal") and e.status != "reversed"]
    entry_days = {e.entry_date for e in live}
    last30 = today - timedelta(days=29)
    recent_days = len({d for d in entry_days if d >= last30})
    window = min(days, 30)

    # Keeping records: how many days in the last month have entries, and how long she has kept them.
    records = _clamp(70 * recent_days / max(window * 0.8, 1) + 30 * min(days / 90, 1))
    # Steady sales: share of days with a sale, and how even her weeks are.
    weeks = []
    end = today
    while end - timedelta(days=6) >= s["from"]:
        weeks.append(_sales(store, end - timedelta(days=6), end))
        end -= timedelta(days=7)
    if len(weeks) >= 2 and sum(weeks):
        mean = sum(weeks) / len(weeks)
        spread = (sum((w - mean) ** 2 for w in weeks) / len(weeks)) ** 0.5 / mean
    else:
        spread = 0.5
    steady = _clamp(60 * s["trading_days"] / max(days * 0.85, 1) + 40 * max(0, 1 - spread))
    # Profit: net margin; 15% or more is full marks, a loss is zero.
    t = s["total"]
    margin = 100 * t["net_profit_kobo"] / t["sales_kobo"] if t["sales_kobo"] else 0
    profit = _clamp(30 + 70 * margin / 15) if margin > 0 else 0
    # Collecting credit: what customers on credit paid back. No credit sales is fine.
    collect = 80 if s["collection_pct"] is None else _clamp(s["collection_pct"])
    # Proof: entries with the message, voice note or photo behind them.
    proof = _clamp(100 * s["with_evidence"] / s["entries"]) if s["entries"] else 0
    # Growth: the last 30 days against the 30 before.
    now_sales = _sales(store, last30, today)
    before = _sales(store, last30 - timedelta(days=30), last30 - timedelta(days=1))
    if before > 0 and days >= 45:
        change = 100 * (now_sales - before) / before
        growth = _clamp(60 + 4 * change) if change < 10 else 100
    else:
        change, growth = None, 60

    values = {"records": records, "steady": steady, "profit": profit, "collect": collect, "proof": proof,
              "growth": growth}
    detail = {
        "records": (f"Entries on {recent_days} of the last {_days(window)}; {_days(days)} of records in all.",
                    "Tell Sabi every sale, even small ones, every day you open."),
        "steady": (f"Sales on {s['trading_days']} of {_days(days)}.",
                   "Record every trading day so lenders see a steady business."),
        "profit": (f"Net margin {round(margin)}%." if t["sales_kobo"] else "No sales recorded yet.",
                   "Check Sabi's price tips: a thin margin or a cost that went up drags this down."),
        "collect": ("No credit sales." if s["collection_pct"] is None
                    else f"{s['collection_pct']}% of credit sales collected.",
                    "Send reminders to customers who owe, and give credit to those who pay on time."),
        "proof": (f"{s['with_evidence']} of {s['entries']} entries have the message or photo behind them.",
                  "Send voice notes, receipts and photos: each one is proof a lender can trust."),
        "growth": (f"Sales {'+' if change >= 0 else ''}{round(change)}% on the 30 days before." if change is not None
                   else "Needs 45 days of records to compare.",
                   "Keep recording: growth shows once there are two months to compare."),
    }
    parts = [{"key": k, "name": name, "weight": w, "value": values[k], "measured": detail[k][0],
              "improve": detail[k][1]} for k, w, name in PARTS]
    total = round(sum(p["value"] * p["weight"] for p in parts) / 100)
    ready = days >= MIN_DAYS and s["entries"] >= 10
    band = ("Strong" if total >= 75 else "Good" if total >= 60 else "Building" if total >= 40 else "Early")
    weakest = max(parts, key=lambda p: p["weight"] * (100 - p["value"]))   # where most points are lost
    return {"score": total if ready else None, "band": band if ready else "Not enough records yet",
            "days": days, "entries": s["entries"], "parts": parts,
            "next_step": weakest["improve"],
            "note": None if ready else f"A score needs at least {MIN_DAYS} days and 10 entries. "
                                       f"You have {days} days and {s['entries']} entries."}


def health_summary(s):
    """Two or three plain sentences for the top of the statement, from business_statement()."""
    from app.ledger.reports import whole_naira as naira
    t = s["total"]
    if not t["sales_kobo"]:
        return "No sales recorded in this period yet."
    lines = [f"Over {s['days']} days, {s['shop']} sold {naira(t['sales_kobo'])} "
             f"(about {naira(s['avg_weekly_sales_kobo'])} a week) and made {naira(t['net_profit_kobo'])} profit, "
             f"a {s['margin_pct']}% gross margin, trading on {s['trading_days']} days."]
    if s["collection_pct"] is not None:
        lines.append(f"Customers on credit paid back {s['collection_pct']}% of what they bought.")
    months = s["months"]
    if len(months) >= 2 and months[-2]["sales_kobo"]:
        a, b = months[-2], months[-1]
        lines.append(f"Sales in {b['label']} so far: {naira(b['sales_kobo'])}, against {naira(a['sales_kobo'])} "
                     f"in {a['label']}.")
    return " ".join(lines)
