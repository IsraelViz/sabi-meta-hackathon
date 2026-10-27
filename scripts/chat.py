"""Talk to the agent in the terminal, no WhatsApp needed. Uses the real model.

    python scripts/chat.py            # you are the owner
    python scripts/chat.py --customer # you are a customer

Buttons show as [1] [2] [3]; type the number to tap one.
Type /books for the debtors and P&L, /quit to exit.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.orchestrator import Agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.ledger import reports  # noqa: E402
from app.ledger.seed import demo_store  # noqa: E402
from app.models.llm import LLMClient  # noqa: E402

OWNER, CUSTOMER = "owner-cli", "customer-cli"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer", action="store_true")
    ap.add_argument("--backend", choices=["meta", "baseten"], default=None)
    args = ap.parse_args()

    store = demo_store()
    agent = Agent(store, LLMClient(args.backend), OWNER, settings.auto_post_limit_kobo)
    me = CUSTOMER if args.customer else OWNER
    pending = {}
    print(f"Sabi on {agent.llm.model}. You are the {'customer' if args.customer else 'owner'}.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text == "/quit":
            break
        if text == "/books":
            print(json.dumps({"pnl": reports.profit_and_loss(store, "all"), "debtors": reports.debtors(store),
                              "cash": reports.cash_position(store)}, indent=2, default=str))
            continue
        sender = me
        if text in pending:
            button = pending[text]
            ctx = agent.handle(sender=button["to"], button_id=button["id"], button_title=button["title"])
        else:
            ctx = agent.handle(sender=sender, text=text, profile_name="CLI Customer")
        pending.clear()
        for out in ctx.outbox:
            who = "owner" if out.to == OWNER else "customer"
            print(f"sabi → {who}: {out.body}")
            for i, b in enumerate(out.buttons, 1):
                pending[str(i)] = {**b, "to": out.to}
                print(f"   [{i}] {b['title']}")
        for call in ctx.tool_log:
            print(f"   · {call['tool']}({call['args']}) → {json.dumps(call['result'], default=str)[:160]}")


if __name__ == "__main__":
    main()
