"""Sign-up and sign-in for shop owners: her phone number and a PIN or password she chose.

- The PIN is stored only as a salted scrypt hash; nobody, including Sabi's team, can read it.
- Five wrong tries lock the shop's sign-in for 15 minutes.
- Signing in gives the device its own key (a session). "Keep me signed in" makes it last 180
  days; otherwise 12 hours. Signing out on one phone ends only that phone's session.
- The shop's original app key (from /admin/) still works, so nothing that exists breaks.
"""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from app.ledger.store import now

SCHEMA = """
alter table pilot.shops add column if not exists owner_phone text unique;
alter table pilot.shops add column if not exists pin_hash text;
alter table pilot.shops add column if not exists failed_logins int not null default 0;
alter table pilot.shops add column if not exists locked_until timestamptz;
create table if not exists pilot.sessions (
  token_hash   text primary key,
  shop_id      text not null,
  device       text,
  created_at   timestamptz not null default now(),
  last_used_at timestamptz not null default now(),
  expires_at   timestamptz not null
);
create index if not exists sessions_shop on pilot.sessions (shop_id);
"""
REMEMBER = timedelta(days=180)
SESSION = timedelta(hours=12)
MAX_TRIES = 5
LOCK = timedelta(minutes=15)
WEAK = {"0000", "1111", "1234", "12345", "123456", "4321", "2222", "9999", "1212", "password", "abcdef"}


class AuthError(Exception):
    pass


def check_secret(secret):
    """A PIN of 4 to 8 digits, or a password of 6 or more characters. Returns the problem, or None."""
    s = secret or ""
    if s.isdigit():
        if not 4 <= len(s) <= 8:
            return "a PIN is 4 to 8 digits"
    elif len(s) < 6:
        return "a password needs at least 6 characters (or use a PIN of 4 to 8 digits)"
    if s.lower() in WEAK or len(set(s)) == 1:
        return "that one is too easy to guess; choose another"
    return None


def hash_secret(secret):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(secret.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_secret(secret, stored):
    try:
        _, salt, digest = stored.split("$")
    except (AttributeError, ValueError):
        return False
    got = hashlib.scrypt((secret or "").encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return hmac.compare_digest(got.hex(), digest)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def device_name(user_agent):
    ua = user_agent or ""
    for pattern, name in ((r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android phone"),
                          (r"Windows", "Windows computer"), (r"Mac OS", "Mac")):
        if re.search(pattern, ua):
            return name
    return "a browser"


class Accounts:
    def __init__(self, db):
        self.db = db
        db.execute(SCHEMA)

    def phone_taken(self, phone):
        return self.db.execute("select 1 from pilot.shops where owner_phone = %s and deleted_at is null",
                               (phone,)).fetchone() is not None

    def set_login(self, shop_id, phone, secret):
        self.db.execute("update pilot.shops set owner_phone = %s, pin_hash = %s where id = %s",
                        (phone, hash_secret(secret), shop_id))

    def start_session(self, shop_id, remember, device=None):
        token = secrets.token_urlsafe(32)
        expires = now() + (REMEMBER if remember else SESSION)
        self.db.execute("insert into pilot.sessions (token_hash, shop_id, device, expires_at) values (%s, %s, %s, %s)",
                        (_hash(token), shop_id, device, expires))
        return token, expires

    def login(self, phone, secret, remember, device=None):
        r = self.db.execute("select id, pin_hash, failed_logins, locked_until from pilot.shops "
                            "where owner_phone = %s and deleted_at is null", (phone,)).fetchone()
        if r is None or not r["pin_hash"]:
            raise AuthError("No Sabi shop uses that number. Check it, or create your shop.")
        if r["locked_until"] and r["locked_until"] > now():
            minutes = max(1, round((r["locked_until"] - now()).total_seconds() / 60))
            raise AuthError(f"Too many wrong tries. Try again in {minutes} minute{'s' if minutes != 1 else ''}.")
        if not verify_secret(secret, r["pin_hash"]):
            tries = r["failed_logins"] + 1
            locked = now() + LOCK if tries >= MAX_TRIES else None
            self.db.execute("update pilot.shops set failed_logins = %s, locked_until = %s where id = %s",
                            (0 if locked else tries, locked, r["id"]))
            left = MAX_TRIES - tries
            raise AuthError("Wrong PIN or password. " + (f"{left} tr{'ies' if left != 1 else 'y'} left."
                                                         if left > 0 else "Sign-in is locked for 15 minutes."))
        self.db.execute("update pilot.shops set failed_logins = 0, locked_until = null where id = %s", (r["id"],))
        token, expires = self.start_session(r["id"], remember, device)
        return r["id"], token, expires

    def shop_for_session(self, token):
        r = self.db.execute("update pilot.sessions set last_used_at = now() where token_hash = %s and expires_at > now() "
                            "returning shop_id", (_hash(token),)).fetchone()
        return r["shop_id"] if r else None

    def logout(self, token):
        self.db.execute("delete from pilot.sessions where token_hash = %s", (_hash(token),))

    def end_all(self, shop_id):
        self.db.execute("delete from pilot.sessions where shop_id = %s", (shop_id,))
