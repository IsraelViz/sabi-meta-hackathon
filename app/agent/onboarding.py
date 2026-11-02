"""From paper to books in a minute: a photo of her price list or stock notebook
becomes her product list, prices and opening stock.

Muse Spark Open reads the photo. Code matches each line against products she
already has (a new price is a price change, not a duplicate), and she sees the
whole list before anything changes: one tap adds it all. Opening stock is only
recorded where the page gives both a count and what she paid; a count without
a cost would make her profit wrong, so she is told instead.
"""
import re

from app.agent.approvals import buttons_for
from app.ledger.model import Entry, Line, Product, StockMove
from app.ledger.store import new_id
from app.normalize.amounts import naira
from app.normalize.fuzzy import best_match

SAME_PRODUCT = 0.85
MAX_LINES = 30

PROMPT = """This photo is a Nigerian shop's price list, product list or stock notebook page. List every product on it.
For each: the product name as written (expand obvious short forms, e.g. "Indomie ctn" -> "Indomie carton"), the unit
it is sold in (carton, bag, tin, pack, crate, bottle, piece...), the selling price in naira, and if the page shows them,
how many are in stock and what the shop paid for one (cost). Use null for anything not on the page; never guess.
Handwriting: "12.5k" or "12,5" means 12500. Reply with JSON only:
{"products": [{"name": "...", "unit": "...", "price_naira": number or null, "qty": number or null, "cost_naira": number or null}],
 "unreadable": ["lines you could not read"]}"""


def _kobo(v):
    try:
        return None if v in (None, "") else round(float(v) * 100)
    except (TypeError, ValueError):
        return None


def read_list(llm, image_data_url):
    from app.normalize.json_reply import parse_json

    reply = llm.chat([{"role": "user", "content": [{"type": "text", "text": PROMPT},
                                                   {"type": "image_url", "image_url": {"url": image_data_url}}]}])
    data = parse_json(reply.content or "{}")
    rows = []
    for p in (data.get("products") or [])[:MAX_LINES]:
        name = str(p.get("name") or "").strip()
        price = _kobo(p.get("price_naira"))
        if not name or not price or price <= 0:
            continue
        qty = p.get("qty")
        qty = int(qty) if isinstance(qty, (int, float)) and qty >= 0 and float(qty).is_integer() else None
        rows.append({"name": name[:60], "unit": (str(p.get("unit") or "piece").strip().lower() or "piece")[:20],
                     "price_kobo": price, "qty": qty, "cost_kobo": _kobo(p.get("cost_naira"))})
    return rows, [str(u) for u in data.get("unreadable") or []][:5]


def plan(store, rows):
    """What adding this list would do: new products, price changes, unchanged, and stock that can't be valued."""
    new, changes, same = [], [], []
    products = store.list_products()
    for r in rows:
        scored = best_match(r["name"], products, lambda p: [p.name, *p.aliases])
        if scored and scored[0][0] >= SAME_PRODUCT:
            prod = scored[0][1]
            if prod.sell_price_kobo != r["price_kobo"]:
                changes.append({**r, "product_id": prod.id, "current_name": prod.name, "old_kobo": prod.sell_price_kobo})
            else:
                same.append(prod.name)
        elif not any(n["name"].lower() == r["name"].lower() for n in new):
            new.append(r)
    for n in new:
        n["stock_ok"] = bool(n["qty"] and n["cost_kobo"])
    return {"new": new, "changes": changes, "same": same}


def setup_from_photo(ctx):
    """Owner tool: read the price list in this photo and send her the changes to approve."""
    if not ctx.image_data_url:
        return {"error": "no photo in this message"}
    if ctx.llm is None:
        return {"error": "vision model not configured"}
    rows, unreadable = read_list(ctx.llm, ctx.image_data_url)
    ctx.store.set_evidence_extracted(ctx.evidence_id, {"price_list": rows, "unreadable": unreadable})
    if not rows:
        return {"status": "nothing_found", "note": "No products with prices on this photo. Ask her for a clearer one."}
    return _offer(ctx, rows, unreadable, "From your photo")


def add_products(ctx, products):
    """Owner tool: products she named in a message (a new shop starts with none). She approves the list first."""
    rows = []
    for p in (products or [])[:MAX_LINES]:
        name = str(p.get("name") or "").strip()
        price = _kobo(p.get("price_naira"))
        if not name:
            continue
        if not price or price <= 0:
            return {"error": f"no selling price for {name}: ask her what she sells it for"}
        qty = p.get("qty_in_stock")
        qty = int(qty) if isinstance(qty, (int, float)) and qty > 0 and float(qty).is_integer() else None
        rows.append({"name": name[:60], "unit": (str(p.get("unit") or "piece").strip().lower() or "piece")[:20],
                     "price_kobo": price, "qty": qty, "cost_kobo": _kobo(p.get("cost_naira"))})
    if not rows:
        return {"error": "no products given"}
    return _offer(ctx, rows, [], "To add")


def _offer(ctx, rows, unreadable, heading):
    p = plan(ctx.store, rows)
    if not p["new"] and not p["changes"]:
        return {"status": "nothing_to_change", "unchanged": p["same"]}
    lines = []
    for n in p["new"]:
        stock = (f", {n['qty']} in stock at {naira(n['cost_kobo'])}" if n["stock_ok"] else
                 f", {n['qty']} in stock (cost not shown)" if n["qty"] else "")
        lines.append(f"➕ {n['name']}: {naira(n['price_kobo'])} per {n['unit']}{stock}")
    for c in p["changes"]:
        lines.append(f"✏️ {c['current_name']}: {naira(c['old_kobo'])} → {naira(c['price_kobo'])}")
    text = (f"{heading}: {len(p['new'])} new product{'s' if len(p['new']) != 1 else ''}"
            + (f", {len(p['changes'])} price change{'s' if len(p['changes']) != 1 else ''}" if p["changes"] else "")
            + ".\n\n" + "\n".join(lines))
    no_cost = [n["name"] for n in p["new"] if n["qty"] and not n["cost_kobo"]]
    if no_cost:
        text += ("\n\nI'll add the products, but not the stock of " + ", ".join(no_cost[:4])
                 + ": I need what you paid to work out profit. Record them as purchases, or send a page with costs.")
    if unreadable:
        text += "\n\nCouldn't read: " + "; ".join(unreadable)
    a = ctx.store.add_approval("catalog", {"plan": p, "evidence_id": ctx.evidence_id}, text,
                               [{"label": "Add all", "action": "add"}, {"label": "Cancel", "action": "cancel"}])
    ctx.tell_owner(text, buttons_for(a))
    return {"status": "waiting_for_owner", "new": len(p["new"]), "price_changes": len(p["changes"]),
            "note": "The list was sent to her with Add all / Cancel. Don't repeat it."}


def _product_id(store, name):
    base = "p-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]
    pid, n = base, 2
    while store.get_product(pid):
        pid, n = f"{base}-{n}", n + 1
    return pid


def settle_catalog(a, option, ctx):
    if option.get("action") != "add":
        ctx.say("OK, nothing changed.")
        return
    p = a["payload"]["plan"]
    lines, moves, added = [], [], []
    for n in p["new"]:
        pid = _product_id(ctx.store, n["name"])
        ctx.store.add_product(Product(id=pid, name=n["name"], base_unit=n["unit"], sell_price_kobo=n["price_kobo"],
                                      aliases=[n["name"].lower()], avg_cost_kobo=0, reorder_level=2))
        added.append(n["name"])
        if n.get("stock_ok"):
            value = n["qty"] * n["cost_kobo"]
            lines.append(Line("inventory", debit_kobo=value, product_id=pid, qty=n["qty"]))
            moves.append(StockMove(pid, n["qty"], n["cost_kobo"]))
    for c in p["changes"]:
        ctx.store.update_product_price(c["product_id"], c["price_kobo"])
    if lines:
        # Stock she already had is funded by her own capital, as when the shop first started on Sabi.
        lines.append(Line("equity", credit_kobo=sum(l.debit_kobo for l in lines)))
        entry = ctx.store.commit(Entry(kind="opening", lines=lines, stock_moves=moves, memo="Opening stock from photo",
                                       created_by="agent:approved", evidence_id=a["payload"].get("evidence_id")))
        ctx.committed.append(entry)
    stocked = sum(1 for n in p["new"] if n.get("stock_ok"))
    ctx.say(f"Done. Added {len(added)} product{'s' if len(added) != 1 else ''}"
            + (f" ({stocked} with opening stock)" if stocked else "")
            + (f" and updated {len(p['changes'])} price{'s' if len(p['changes']) != 1 else ''}" if p["changes"] else "")
            + ". Customers get the new prices from now on."
            + (" If you told me about a sale or purchase of these, say \"record it\" and I'll put it in the books."
               if added else ""))
