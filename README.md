# Sabi

A WhatsApp-native agent that keeps a Nigerian trader's books from the way she already sells: voice notes in Pidgin, photos of receipts, customer DMs. Built for the Meta hackathon, Indie track ("Build for Your World"). All code in this repo was written during the event.

## Design rules (enforced in code, with tests)

1. **The model proposes, code commits.** Muse Spark proposes a business event (`record_entry`). Python builds the journal lines, cost of goods sold and average cost. The model never does accounting arithmetic.
2. **Double entry can't be broken.** Postgres constraint triggers reject any entry whose debits don't equal its credits at commit, and journal lines are append-only (`app/ledger/schema.sql`). The in-memory store enforces the same rules.
3. **Corrections reverse, never edit.** `cancel_entry` posts a mirror-image entry after the owner confirms.
4. **Unclear money always goes to the owner.** The amount normaliser (`app/normalize/amounts.py`) turns "5k" and "1k5" into figures and marks ambiguous ones such as "two-five" (₦2,500 or ₦25,000). Code rejects any proposal that doesn't hand those to the owner as buttons.
5. **Trust is earned per action.** Posting is automatic only when the validator finds nothing: known customer and product, a price within her usual range, enough stock, under the limit. Everything else becomes a WhatsApp button question, answered by code.
6. **Customers are untrusted.** The customer persona has four tools: look up price/in-stock, create an order, claim a payment, see their own orders. It can't reach the ledger, and a transfer screenshot never marks an order paid without the owner.
7. **Every number has a receipt.** Each entry links to its evidence row: the original transcript, the normalised text, the model and the prompt version.

## Layout

```
app/
  ledger/      schema.sql, model, store (memory), validator, posting, reports, seed
  normalize/   amounts.py (Naija money slang), fuzzy.py (product/contact matching)
  agent/       orchestrator, tools, approvals (button answers), orders, prompts/
  models/      llm.py — one client for Muse Spark (Meta API) and Muse Spark Open (Baseten)
  media/       transcribe.py — Muse Voice Transcribe (realtime websocket)
  channels/    whatsapp.py — webhook parsing, signature check, send, media download
  jobs/        daily_summary.py — 8 pm Lagos summary
  main.py      FastAPI app
scripts/chat.py  talk to the agent in a terminal
seed/          chart of accounts, demo shop
tests/         ledger rules, amounts, agent runs with a scripted model, WhatsApp parsing
```

## Run

```
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

Copy `.env.example` to `.env` and set `MODEL_API_KEY`. Then:

```
python scripts/chat.py          # be the owner
python scripts/chat.py --customer
uvicorn app.main:app --port 8000
```

For WhatsApp, expose port 8000 (Cloudflare Tunnel), set the callback to `/webhook/whatsapp`, and fill in the `WA_*`, `META_APP_SECRET` and `OWNER_PHONE` settings.

## Photos

- **Receipts and notebook pages** (`app/media/documents.py`): Muse Spark reads the photo into fixed JSON, and code checks every line and the total. If the numbers don't agree, the owner is asked and nothing is recorded until she answers. Clean lines go to her as one summary with a single Confirm. A line with a problem, such as an odd price, is asked about on its own.
- **Stock-take** (`app/agent/stocktake.py`): SAM 3.1 counts each countable product in a shelf photo, and code compares the counts with the books. A gap gets three buttons: *Sold, not recorded* / *Damaged or lost* / *Recount*. Her answer becomes a normal entry, with the shelf photo as its evidence. Products not in the photo are skipped, not flagged. Set `STOCKTAKE_BACKEND=vision` to count with Muse Spark instead.

## Storage

`STORE=memory` (default) or `STORE=postgres` with `DATABASE_URL` (Supabase). The Postgres store applies `schema.sql` on start and loads the demo shop into an empty database. Tests run every ledger and agent case on both stores, using an embedded Postgres (`pgserver`), so the triggers are exercised for real.

## Status

Working and tested (105 tests): the ledger and its database triggers, the Postgres and memory stores, the validator, posting and reversals, the amount normaliser, the agent with both personas, owner approvals, customer orders and payment claims, receipts and notebook pages, the stock-take, WhatsApp parsing and sending, and the daily summary.

Not yet verified against the live API: the Muse Voice Transcribe request format, image input to Muse Spark, the SAM 3.1 endpoint and response, and tool calling on Baseten. Not built yet: SAM cutouts, flyers, Instagram posting, payment reminders, and the owner app.
