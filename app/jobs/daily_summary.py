"""The 8 pm summary to the owner, Lagos time."""
import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import money
from app import prefs
from app.agent import language
from app.agent.context import Outgoing
from app.ledger.reports import daily_summary_text, weekly_summary_text

log = logging.getLogger("sabi.jobs")
LAGOS = ZoneInfo("Africa/Lagos")
SUMMARY_HOUR = 20


def seconds_until(hour, now=None):
    now = now or datetime.now(LAGOS)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def summary_message(store, kind="daily", llm=None):
    money.use(store)
    """The summary, in her language when she uses one other than English and a model is connected."""
    today = datetime.now(LAGOS).date()
    body = ("This week's books 📒\n" + weekly_summary_text(store, today) if kind == "weekly"
            else "Today's books 📒\n" + daily_summary_text(store, today))
    msg = Outgoing("", body, [{"id": "summary:reminders", "title": "Send reminders"},
                              {"id": "summary:post", "title": "Make tomorrow's post"}])
    if llm is not None:
        language.localize(llm, [msg], language.for_owner(store))
    return {"body": msg.body, "buttons": msg.buttons}


def due(store, now=None):
    """'daily', 'weekly' or None, from her summary setting. Marks it sent, so it goes once."""
    now = now or datetime.now(LAGOS)
    kind = prefs.summary_due(prefs.get(store), now)
    if kind:
        prefs.mark_sent(store, now)
    return kind


async def run_forever(store, send, owner_phone, llm=None):
    while True:
        await asyncio.sleep(60)
        try:
            kind = await asyncio.to_thread(due, store)
            if kind:
                msg = await asyncio.to_thread(summary_message, store, kind, llm)
                await send(owner_phone, msg["body"], msg["buttons"])
        except Exception:
            log.exception("summary failed")
