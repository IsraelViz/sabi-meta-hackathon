"""The business statement: a one-page trading record a lender, microfinance bank
or supplier can read, built from the shop's own books.

Most small traders are refused credit because they have no records. Sabi has
been keeping them all along, so this page is the payoff. It says plainly what it
is (her own records, kept as she traded, not audited), and every figure comes
from entries that each have their original message or photo behind them.

Shared by a link that expires: the token is signed, so nobody can guess one or
make it last longer.
"""
import hashlib
import hmac
import secrets
import time
from html import escape

from app.config import settings
from app.ledger.reports import whole_naira as naira   # a lender reads whole naira

LINK_DAYS = 7
_FALLBACK_SECRET = secrets.token_hex(32)   # links then last until the server restarts


def _secret():
    return (settings.statement_secret or settings.owner_app_token or _FALLBACK_SECRET).encode()


def _sign(signed):
    return hmac.new(_secret(), signed.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(days=LINK_DAYS, now=None, shop=""):
    """`shop` names a pilot shop's books; empty is the main shop."""
    expires = int((now or time.time()) + days * 86400)
    signed = f"{shop}.{expires}" if shop else str(expires)
    return f"{signed}.{_sign(signed)}"


def token_shop(token, now=None):
    """The shop a valid token is for ("" for the main shop), or None if it is forged or expired."""
    try:
        signed, sig = token.rsplit(".", 1)
        shop, _, expires = signed.rpartition(".")
        if hmac.compare_digest(sig, _sign(signed)) and int(expires) > (now or time.time()):
            return shop
    except ValueError:
        pass
    return None


def check_token(token, now=None):
    return token_shop(token, now) is not None


def _d(day):
    return f"{day.day} {day:%b %Y}"


def page(store, days=90):
    """The statement for one shop's books: figures, a plain summary, and her Sabi Score if she shares it."""
    from app import prefs
    from app.ledger.reports import business_statement
    from app.ledger.score import health_summary, sabi_score
    s = business_statement(store, days=days)
    score = sabi_score(store) if prefs.get(store)["share_score"] else None
    return render(s, health=health_summary(s), score=score)


def _score_html(score):
    if not score or score["score"] is None:
        return ""
    e = escape
    rows = "".join(f"<tr><td>{e(p['name'])} <span class='sub'>({p['weight']}%)</span><br><span class='sub'>{e(p['measured'])}</span></td>"
                   f"<td><div class='bar'><i style='width:{p['value']}%'></i></div></td><td>{p['value']}</td></tr>"
                   for p in score["parts"])
    return f"""<h2>Sabi Score</h2>
<div class="score"><div class="big"><b>{score['score']}</b><span>/100 · {e(score['band'])}</span></div>
<p class="sub">How complete and steady these records are, from the books alone. It is not a credit bureau score. Each part
is scored out of 100 and weighted as shown.</p></div>
<table class="parts"><tbody>{rows}</tbody></table>"""


def render(s, health=None, score=None):
    """HTML for business_statement(): one A4 page, printable, with no outside resources."""
    t = s["total"]
    e = escape
    months = "".join(
        f"<tr><td>{e(m['label'])}</td><td>{naira(m['sales_kobo'])}</td><td>{naira(m['gross_profit_kobo'])}</td>"
        f"<td>{naira(m['expenses_kobo'])}</td><td><b>{naira(m['net_profit_kobo'])}</b></td></tr>" for m in s["months"])
    top = "".join(f"<tr><td>{e(p['name'])}</td><td>{naira(p['sales_kobo'])}</td>"
                  f"<td>{round(100 * p['sales_kobo'] / t['sales_kobo']) if t['sales_kobo'] else 0}%</td></tr>"
                  for p in s["top_products"])
    net_position = (s["cash_kobo"] + s["bank_kobo"] + s["stock_value_kobo"] + s["owed_to_shop_kobo"]
                    - s["owed_to_suppliers_kobo"])
    evidence_pct = round(100 * s["with_evidence"] / s["entries"]) if s["entries"] else 0
    collection = (f"{s['collection_pct']}% collected" if s["collection_pct"] is not None else "No credit sales")
    short = s["days"] < 60
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{e(s['shop'])} · Business statement</title>
<style>
  :root {{ color-scheme: light; --ink: #21180F; --muted: #6B5D4D; --line: #E6DCCB; --accent: #C2410C; --soft: #FFF6EA; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #F3EEE6; color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
  .page {{ max-width: 800px; margin: 24px auto; background: #fff; padding: 36px 40px; border-radius: 6px; box-shadow: 0 2px 16px rgba(0,0,0,.08); }}
  header {{ display: flex; justify-content: space-between; gap: 16px; border-bottom: 3px solid var(--accent); padding-bottom: 14px; }}
  h1 {{ margin: 0; font-size: 24px; }}
  .sub {{ color: var(--muted); }}
  .doc {{ text-align: right; }}
  .doc b {{ display: block; font-size: 16px; color: var(--accent); }}
  .kpis {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin: 20px 0 6px; }}
  .kpi {{ background: var(--soft); border-radius: 8px; padding: 10px 12px; }}
  .kpi span {{ display: block; color: var(--muted); font-size: 12px; }}
  .kpi b {{ font-size: 18px; font-variant-numeric: tabular-nums; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 22px 0 6px; }}
  table {{ width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }}
  th, td {{ text-align: right; padding: 6px 8px; border-bottom: 1px solid var(--line); }}
  th:first-child, td:first-child {{ text-align: left; }}
  th {{ color: var(--muted); font-weight: 600; font-size: 12px; }}
  .two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  .note {{ background: var(--soft); border-left: 3px solid var(--accent); padding: 10px 12px; margin-top: 22px; font-size: 13px; }}
  .warn {{ color: var(--accent); font-weight: 600; }}
  .health {{ font-size: 15px; margin: 14px 0 0; }}
  .score {{ display: grid; grid-template-columns: auto 1fr; gap: 16px; align-items: center; }}
  .score .big b {{ font-size: 40px; color: var(--accent); font-variant-numeric: tabular-nums; }}
  .score .big span {{ color: var(--muted); margin-left: 4px; }}
  .parts td:nth-child(2) {{ width: 40%; }}
  .bar {{ height: 8px; background: var(--line); border-radius: 4px; overflow: hidden; }}
  .bar i {{ display: block; height: 100%; background: var(--accent); }}
  footer {{ margin-top: 18px; color: var(--muted); font-size: 12px; display: flex; justify-content: space-between; gap: 12px; }}
  .print {{ position: fixed; right: 16px; bottom: 16px; background: var(--accent); color: #fff; border: 0; border-radius: 999px;
           padding: 12px 18px; font: inherit; font-weight: 600; cursor: pointer; }}
  @media (max-width: 640px) {{
    .page {{ margin: 0; padding: 20px 16px 88px; border-radius: 0; }}
    header {{ flex-direction: column; }} .doc {{ text-align: left; }}
    .kpis {{ grid-template-columns: 1fr 1fr; }} .two {{ grid-template-columns: 1fr; gap: 0; }}
    th, td {{ padding: 6px 4px; font-size: 13px; }}
  }}
  @media print {{ body {{ background: #fff; }} .page {{ box-shadow: none; margin: 0; max-width: none; }} .print {{ display: none; }} }}
</style></head>
<body><main class="page">
<header>
  <div><h1>{e(s['shop'])}</h1><div class="sub">{e(s['address'])}</div></div>
  <div class="doc"><b>Business statement</b>{_d(s['from'])} – {_d(s['to'])}<br>
    <span class="sub">{s['days']} days of records · prepared {_d(s['prepared'])}</span></div>
</header>

<div class="kpis">
  <div class="kpi"><span>Sales</span><b>{naira(t['sales_kobo'])}</b></div>
  <div class="kpi"><span>Gross profit</span><b>{naira(t['gross_profit_kobo'])}</b><span>{s['margin_pct']:g}% margin</span></div>
  <div class="kpi"><span>Net profit</span><b>{naira(t['net_profit_kobo'])}</b></div>
  <div class="kpi"><span>Average weekly sales</span><b>{naira(s['avg_weekly_sales_kobo'])}</b><span>sold on {s['trading_days']} of {s['days']} days</span></div>
</div>
{f'<p class="health">{e(health)}</p>' if health else ''}
{f'<p class="warn">Only {s["days"]} days of records so far. Lenders usually look for three months or more; this statement grows every day the shop trades with Sabi.</p>' if short else ''}

<h2>Month by month</h2>
<table><thead><tr><th>Month</th><th>Sales</th><th>Gross profit</th><th>Expenses</th><th>Net profit</th></tr></thead>
<tbody>{months}</tbody></table>

<div class="two">
  <section><h2>Position today</h2><table><tbody>
    <tr><td>Cash</td><td>{naira(s['cash_kobo'])}</td></tr>
    <tr><td>Bank</td><td>{naira(s['bank_kobo'])}</td></tr>
    <tr><td>Stock (at cost)</td><td>{naira(s['stock_value_kobo'])}</td></tr>
    <tr><td>Customers owe the shop</td><td>{naira(s['owed_to_shop_kobo'])}</td></tr>
    <tr><td>Shop owes suppliers</td><td>−{naira(s['owed_to_suppliers_kobo']).replace('−', '')}</td></tr>
    <tr><td><b>Net business assets</b></td><td><b>{naira(net_position)}</b></td></tr>
  </tbody></table></section>
  <section><h2>Credit given and collected</h2><table><tbody>
    <tr><td>Sold on credit</td><td>{naira(s['credit_sold_kobo'])}</td></tr>
    <tr><td>Collected from customers</td><td>{naira(s['collected_kobo'])}</td></tr>
    <tr><td>Collection</td><td>{collection}</td></tr>
  </tbody></table>
  <h2>Best sellers</h2><table><tbody>{top or '<tr><td>No sales yet</td><td></td><td></td></tr>'}</tbody></table></section>
</div>

{_score_html(score)}

<div class="note"><b>How these figures were kept.</b> {s['entries']} entries were recorded as the shop traded: spoken, typed or
photographed on WhatsApp and kept in double-entry books by Sabi. {evidence_pct}% have the original message, voice note or
receipt attached. Entries are never edited: {s['corrections']} mistake{'s were' if s['corrections'] != 1 else ' was'} corrected
by a visible reversal. These are the owner's own records, not audited accounts.</div>

<footer><span>Prepared by Sabi · WhatsApp bookkeeping for small traders</span><span>Figures in Nigerian naira</span></footer>
</main>
<button class="print" onclick="window.print()">Print / save as PDF</button>
</body></html>"""
