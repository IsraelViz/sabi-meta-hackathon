"""Load the chart of accounts and the demo shop into a store."""
import json
from datetime import date, timedelta
from pathlib import Path

from app.ledger.model import Contact, Entry, Line, Product, StockMove
from app.ledger.store import MemoryStore

SEED = Path(__file__).resolve().parents[2] / "seed"


DEMO_SHOPS = {"bookshop": SEED / "bookshop.json", "provisions": SEED / "demo_shop.json",
              "broadway": SEED / "broadway.json"}   # Ife's Broadway Books, the judges' demo shop


def demo_shop_path(name=None):
    """The demo shop the app opens with (DEMO_SHOP): the bookshop, or the provisions shop."""
    from app.config import settings
    return DEMO_SHOPS.get(name or settings.demo_shop, DEMO_SHOPS["bookshop"])


def add_demo_history(store, path, media=None):
    """The demo trading history that belongs to this shop file; nothing for her own shop file."""
    name = Path(path).name
    if name == DEMO_SHOPS["bookshop"].name:
        from app.ledger.bookshop_week import add_history
    elif name == DEMO_SHOPS["provisions"].name:
        from app.ledger.demo_week import add_history
    elif name == DEMO_SHOPS["broadway"].name:
        from app.ledger.broadway_history import add_history
    else:
        return store
    return add_history(store, media=media)


def chart_of_accounts(template="ng_retail"):
    return json.loads((SEED / "coa_templates" / f"{template}.json").read_text(encoding="utf-8"))


def demo_store(path=SEED / "demo_shop.json", store=None):
    """The demo shop in a fresh MemoryStore, or loaded into the (empty) store given."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if store is None:
        store = MemoryStore(chart_of_accounts())
    else:
        store.seed_accounts(chart_of_accounts())
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
        opened = date.today() - timedelta(days=data["opening_days_ago"]) if data.get("opening_days_ago") else None
        store.commit(Entry(kind="opening", lines=lines, stock_moves=moves,
                           memo="Opening stock", created_by="seed", entry_date=opened))
    return store
