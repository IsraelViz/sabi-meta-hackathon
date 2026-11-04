"""The judges' demo account: the demo bookshop as an app shop, opened with one tap.

It is a pilot shop, so it has Chat, talk mode, voice, booklists and flyers, and nothing in it can
reach a real WhatsApp number. It signs in with the owner's number and DEMO_LOGIN_PIN; the sign-in
pages show the number masked ("0701 XXXX 065") with a button that signs in for them, so the full
number is never shown. Because the number is taken, nobody else can register a shop with it.
"""
import logging
import threading

from app.ledger.pg_store import PostgresStore
from app.ledger.seed import DEMO_SHOPS, add_demo_history, demo_store

log = logging.getLogger("sabi.judge")
NAME = "Nkechi Books & Stationery"


def masked(phone):
    """2347015065065 -> '0701 XXXX 065'."""
    local = "0" + phone[3:] if phone.startswith("234") and len(phone) == 13 else phone
    return f"{local[:4]} XXXX {local[-3:]}" if len(local) >= 8 else "XXXX"


def find(shops, phone):
    r = shops.db.execute("select id from pilot.shops where owner_phone = %s and deleted_at is null", (phone,)).fetchone()
    return r["id"] if r else None


def ensure(shops, phone, pin, old_phone=None):
    """On start-up: make the demo shop if it isn't there yet (in the background: it takes minutes on a
    far-away database), and keep its PIN the configured one. Given a new DEMO_LOGIN_PHONE, the demo shop moves
    to it from `old_phone` (OWNER_PHONE), so the owner's own number is free for a shop of her own."""
    if not (shops and phone and pin):
        return
    sid = find(shops, phone)
    if sid is None and old_phone and old_phone != phone:
        held = find(shops, old_phone)
        if held and shops.row(held)["name"] == NAME:
            sid = held
            log.info("judges' demo shop moved to DEMO_LOGIN_PHONE; %s is free", masked(old_phone))
    if sid:
        shops.accounts.set_login(sid, phone, pin)
        return
    threading.Thread(target=_create, args=(shops, phone, pin), daemon=True).start()


def _create(shops, phone, pin):
    from app.promo.publish import LocalMedia

    path = DEMO_SHOPS["bookshop"]
    row, _ = shops.create(NAME, "books", "Nkechi")
    sid = row["id"]
    seed = PostgresStore(shops.dsn, schema=f"shop_{sid}")
    try:
        with seed.conn.transaction():      # all or nothing, like the main demo shop
            demo_store(path, store=seed)
            add_demo_history(seed, path, media=LocalMedia(shops.media_root / sid, f"/shops-media/{sid}"))
        shops.accounts.set_login(sid, phone, pin)   # only once the books are in: sign-in waits for them
        shops.consent(sid)
        log.info("judges' demo shop ready: %s", sid)
    except Exception:
        log.exception("making the judges' demo shop failed; it will be tried again on the next start")
        shops.delete(sid)
    finally:
        seed.conn.close()
