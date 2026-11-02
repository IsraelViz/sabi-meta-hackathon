"""The owner's own settings, kept with her books: when her summary comes, how reminders sound,
how much she puts aside, what she shares. She changes them in Shop ⚙ or just tells Sabi."""
import re
from datetime import datetime, timedelta

from app.money import CURRENCIES

FREQUENCIES = ("daily", "weekly", "off")
TONES = ("gentle", "firm", "pidgin")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
LANGUAGE_NAME = re.compile(r"^[^\W\d_][^\W\d_ '-]*(?:[ '-][^\W\d_]+){0,2}$")   # "French", "Nigerian Pidgin"
LATE_BY = 3            # a summary missed by more than 3 hours (server off) is skipped, not sent at midnight

DEFAULTS = {
    "summary": {"frequency": "daily", "hour": 20, "weekday": 6},   # weekday: 0 = Monday ... 6 = Sunday
    "reminder_tone": "gentle",
    "language": "auto",     # "auto" follows her messages; or a language name ("French")
    "currency": "NGN",      # the money her books are kept in (app/money.py): NGN, XOF, GHS, KES...
    "savings": {"percent": 0, "goal_kobo": 0, "name": "Savings"},
    "low_data": False,
    "share_score": True,      # show the Sabi Score on the statement she sends a lender
    "share_prices": False,    # add her supplier prices, anonymously, to what other Sabi shops see
}


def get(store):
    saved = (getattr(store, "business", {}) or {}).get("prefs") or {}
    out = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS or k.startswith("_")}}
    for key in ("summary", "savings"):
        out[key] = {**DEFAULTS[key], **(saved.get(key) or {})}
    return out


def save(store, changes):
    """Check and store changes. Returns (prefs, errors); nothing is saved if any change is wrong."""
    current = get(store)
    errors = []
    new = {k: (dict(v) if isinstance(v, dict) else v) for k, v in current.items()}
    for key, value in (changes or {}).items():
        if key == "summary":
            s = new["summary"]
            for k, v in (value or {}).items():
                if k == "frequency" and v in FREQUENCIES:
                    s[k] = v
                elif k == "hour" and isinstance(v, int) and 5 <= v <= 23:
                    s[k] = v
                elif k == "weekday" and isinstance(v, int) and 0 <= v <= 6:
                    s[k] = v
                else:
                    errors.append(f"summary {k} can't be {v!r}")
        elif key == "savings":
            s = new["savings"]
            for k, v in (value or {}).items():
                if k == "percent" and isinstance(v, (int, float)) and 0 <= v <= 50:
                    s[k] = round(v, 1)
                elif k == "goal_kobo" and isinstance(v, int) and 0 <= v <= 10**12:
                    s[k] = v
                elif k == "name" and isinstance(v, str) and v.strip():
                    s[k] = v.strip()[:40]
                else:
                    errors.append(f"savings {k} can't be {v!r}")
        elif key == "reminder_tone" and value in TONES:
            new[key] = value
        elif key == "language" and isinstance(value, str) and LANGUAGE_NAME.match(value.strip()):
            v = value.strip()
            new[key] = "auto" if v.lower() == "auto" else v[:1].upper() + v[1:]
        elif key == "currency" and isinstance(value, str) and value.strip().upper() in CURRENCIES:
            new[key] = value.strip().upper()
        elif key in ("low_data", "share_score", "share_prices") and isinstance(value, bool):
            new[key] = value
        elif key.startswith("_"):    # Sabi's own bookkeeping (last summary sent, tips given)
            new[key] = value
        else:
            errors.append(f"{key} can't be {value!r}")
    if errors:
        return current, errors
    store.business = {**store.business, "prefs": new}
    return new, []


def public(prefs):
    """What the app shows: her settings without Sabi's own bookkeeping."""
    return {k: v for k, v in prefs.items() if not k.startswith("_")}


def summary_due(prefs, now):
    """'daily', 'weekly' or None: which summary to send at this moment (Lagos time)."""
    s = prefs["summary"]
    if s["frequency"] == "off" or prefs.get("_summary_last") == now.date().isoformat():
        return None
    if not s["hour"] <= now.hour < s["hour"] + LATE_BY:
        return None
    if s["frequency"] == "weekly" and now.weekday() != s["weekday"]:
        return None
    return s["frequency"]


def mark_sent(store, now):
    save(store, {"_summary_last": now.date().isoformat()})


def describe_summary(prefs):
    s = prefs["summary"]
    if s["frequency"] == "off":
        return "Summary: off"
    at = datetime(2000, 1, 1, s["hour"]).strftime("%I %p").lstrip("0").lower()
    if s["frequency"] == "weekly":
        return f"Summary: every {WEEKDAYS[s['weekday']]} at {at}"
    return f"Summary: every day at {at}"


def next_summary(prefs, now):
    """When the next summary goes out, for the app to show."""
    s = prefs["summary"]
    if s["frequency"] == "off":
        return None
    t = now.replace(hour=s["hour"], minute=0, second=0, microsecond=0)
    for _ in range(8):
        if t > now and (s["frequency"] == "daily" or t.weekday() == s["weekday"]):
            return t
        t += timedelta(days=1)
    return None
