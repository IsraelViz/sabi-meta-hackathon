"""The judges' demo account: Ife's Broadway Books (Agbowo, Ibadan) as an app shop, opened with one tap.
Her shelf, October's trading and November so far are loaded on start-up; the rest is left to show live.

It is a pilot shop, so it has Chat, talk mode, voice, booklists and flyers, and nothing in it can
reach a real WhatsApp number. It signs in with the owner's number and DEMO_LOGIN_PIN; the sign-in
pages show the number masked ("0701 XXXX 065") with a button that signs in for them, so the full
number is never shown. Because the number is taken, nobody else can register a shop with it.
"""
import logging
import threading

from app.ledger.pg_store import PostgresStore
from app.ledger.seed import DEMO_SHOPS, add_demo_history, demo_store
from app.shops import slugify

log = logging.getLogger("sabi.judge")
NAME = "Broadway Books"
RETIRED = "Nkechi Books & Stationery"     # the earlier demo shop: deleted so its number is free


def masked(phone):
    """2347015065065 -> '0701 XXXX 065'."""
    local = "0" + phone[3:] if phone.startswith("234") and len(phone) == 13 else phone
    return f"{local[:4]} XXXX {local[-3:]}" if len(local) >= 8 else "XXXX"


def find(shops, phone):
    r = shops.db.execute("select id from pilot.shops where owner_phone = %s and deleted_at is null", (phone,)).fetchone()
    return r["id"] if r else None


def ensure(shops, phone, pin):
    """On start-up: make the demo shop if it isn't there yet (in the background: it takes minutes on a
    far-away database), and keep its PIN the configured one. The earlier demo shop is deleted first."""
    if not (shops and phone and pin):
        return
    for r in shops.db.execute("select id from pilot.shops where name = %s and owner_name = 'Nkechi' "
                              "and deleted_at is null", (RETIRED,)).fetchall():
        log.info("deleting the earlier demo shop %s", r["id"])
        shops.delete(r["id"])
    # A demo shop a restart cut off while it was loading never got its sign-in: nobody can use it, and it
    # holds the shop's link name. This start-up makes the shop again, so it goes.
    for r in shops.db.execute("select id from pilot.shops where name = %s and owner_name = 'Ife' "
                              "and owner_phone is null and deleted_at is null", (NAME,)).fetchall():
        log.info("deleting a half-made demo shop %s", r["id"])
        shops.delete(r["id"])
    sid = find(shops, phone)
    if sid:
        shops.accounts.set_login(sid, phone, pin)
        _take_link_name(shops, sid)
        return
    threading.Thread(target=_create, args=(shops, phone, pin), daemon=True).start()


def _take_link_name(shops, sid):
    """The demo shop's link is /c/broadway-books, even if a half-made one held that name when it was made."""
    want = slugify(NAME)
    if shops.row(sid)["slug"] != want and not shops.db.execute(
            "select 1 from pilot.shops where slug = %s", (want,)).fetchone():
        shops.db.execute("update pilot.shops set slug = %s where id = %s", (want, sid))
        log.info("demo shop %s now at /c/%s", sid, want)


def _create(shops, phone, pin):
    from app.promo.publish import LocalMedia

    path = DEMO_SHOPS["broadway"]
    row, _ = shops.create(NAME, "books", "Ife")
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
