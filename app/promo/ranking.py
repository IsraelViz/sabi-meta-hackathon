"""Which product to promote, decided by code from the books (not by the model).

A good pick has plenty of stock (weeks of cover at the usual sales rate), a
healthy margin, and is selling slower than usual this week. Products at or
below their reorder level are never promoted: no point advertising what is
about to run out.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from app.normalize.amounts import naira

WEIGHTS = {"cover": 0.4, "margin": 0.35, "slow": 0.25}
MAX_COVER_WEEKS = 12
GOOD_MARGIN = 0.30


@dataclass
class Pick:
    product_id: str
    name: str
    score: float
    on_hand: int
    weekly_avg: float
    this_week: int
    margin: float | None
    reason: str


def _clamp(x):
    return max(0.0, min(1.0, x))


def rank(store, today=None, limit=3):
    today = today or date.today()
    week_start = today - timedelta(days=6)
    month_start = today - timedelta(days=27)
    picks = []
    for p in store.list_products():
        on_hand = store.stock_on_hand(p.id) - store.reserved(p.id)
        if on_hand <= max(p.reorder_level, 0):
            continue
        this_week = store.sales_qty(p.id, week_start, today)
        weekly_avg = store.sales_qty(p.id, month_start, today) / 4
        cover_weeks = on_hand / max(weekly_avg, 0.25)
        margin = (p.sell_price_kobo - p.avg_cost_kobo) / p.sell_price_kobo if p.avg_cost_kobo and p.sell_price_kobo else None
        slow = 1.0 if weekly_avg == 0 else _clamp(1 - this_week / weekly_avg)
        score = (WEIGHTS["cover"] * _clamp(cover_weeks / MAX_COVER_WEEKS)
                 + WEIGHTS["margin"] * _clamp((margin or 0) / GOOD_MARGIN)
                 + WEIGHTS["slow"] * slow)
        picks.append(Pick(p.id, p.name, round(score, 3), on_hand, weekly_avg, this_week, margin,
                          _reason(p, on_hand, cover_weeks, weekly_avg, this_week, margin)))
    picks.sort(key=lambda x: x.score, reverse=True)
    return picks if limit is None else picks[:limit]


def _reason(p, on_hand, cover_weeks, weekly_avg, this_week, margin):
    parts = [f"{on_hand} {p.base_unit} in stock"]
    if weekly_avg:
        parts.append(f"about {cover_weeks:.0f} weeks' worth")
        parts.append(f"sold {this_week} this week vs {weekly_avg:.1f} usual")
    else:
        parts.append("no sales in the last 4 weeks")
    if margin is not None:
        parts.append(f"{margin:.0%} margin ({naira(p.sell_price_kobo - p.avg_cost_kobo)} per {p.base_unit})")
    return ", ".join(parts)
