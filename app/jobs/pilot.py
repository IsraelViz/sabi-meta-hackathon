"""Pilot shops' timed messages: each owner's summary in her Chat, at the time and as often as she chose,
and follow-ups to quiet customers."""
import asyncio
import logging
from datetime import datetime

from app.jobs.daily_summary import due, summary_message
from app.agent.sales import followups_due
from app.shops import OWNER

log = logging.getLogger("sabi.jobs")
TICK_SECONDS = 60
FOLLOWUP_EVERY = 10     # ticks


def send_summaries(shops, now=None, force=None):
    """Each shop whose summary time it is gets it. `force` ('daily'/'weekly') sends to every shop now."""
    for shop in shops.all():
        try:
            kind = force or due(shop.store, now)
            if kind:
                msg = summary_message(shop.store, kind, llm=shops.llm)
                shops.log(shop.id, OWNER, "out", msg["body"], buttons=msg["buttons"])
                shops.notify(shop.id, "Your summary is ready", msg["body"][:120])
        except Exception:
            log.exception("summary for shop %s failed", shop.id)


def send_followups(shops, at=None):
    for shop in shops.all():
        for to, text in followups_due(shop.store, at=at) if at else followups_due(shop.store):
            shops.log(shop.id, to, "out", text)


async def run_forever(shops):
    tick = 0
    while True:
        await asyncio.sleep(TICK_SECONDS)
        tick += 1
        try:
            await asyncio.to_thread(send_summaries, shops)
            if tick % FOLLOWUP_EVERY == 0:
                await asyncio.to_thread(send_followups, shops)
        except Exception:
            log.exception("pilot jobs failed")
