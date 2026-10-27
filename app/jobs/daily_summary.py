"""The 8 pm summary to the owner, Lagos time."""
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.ledger.reports import daily_summary_text

log = logging.getLogger("sabi.jobs")
LAGOS = ZoneInfo("Africa/Lagos")
SUMMARY_HOUR = 20


def seconds_until(hour, now=None):
    now = now or datetime.now(LAGOS)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def summary_message(store):
    return {
        "body": "Today's books 📒\n" + daily_summary_text(store, datetime.now(LAGOS).date()),
        "buttons": [{"id": "summary:reminders", "title": "Send reminders"},
                    {"id": "summary:post", "title": "Make tomorrow's post"}],
    }


async def run_forever(store, send, owner_phone):
    while True:
        await asyncio.sleep(seconds_until(SUMMARY_HOUR))
        try:
            msg = summary_message(store)
            await send(owner_phone, msg["body"], msg["buttons"])
        except Exception:
            log.exception("daily summary failed")
