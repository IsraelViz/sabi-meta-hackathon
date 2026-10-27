"""Load the chart of accounts and the demo shop into a store."""
import json
from pathlib import Path

from app.ledger.model import Contact, Entry, Line, Product, StockMove
from app.ledger.store import MemoryStore

SEED = Path(__file__).resolve().parents[2] / "seed"


def chart_of_accounts(template="ng_retail"):
    return json.loads((SEED / "coa_templates" / f"{template}.json").read_text(encoding="utf-8"))


def demo_store(path=SEED / "demo_shop.json"):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    store = MemoryStore(chart_of_accounts())
    load_shop(store, data)
    return store


def load_shop(store, data):
    store.business = data.get("business", {})
    for p in data["products"]:
        store.add_product(Product(**p))
    for c in data["contacts"]:
        store.add_contact(Contact(**c))
    # Opening stock is funded by the owner's capital.
    lines, moves = [], []
    for s in data.get("opening_stock", []):
        value = s["qty"] * s["unit_cost_kobo"]
        lines.append(Line("inventory", debit_kobo=value, product_id=s["product_id"], qty=s["qty"]))
        moves.append(StockMove(s["product_id"], s["qty"], s["unit_cost_kobo"]))
    if lines:
        total = sum(l.debit_kobo for l in lines)
        lines.append(Line("equity", credit_kobo=total))
        store.commit(Entry(kind="opening", lines=lines, stock_moves=moves,
                           memo="Opening stock", created_by="seed"))
    return store
