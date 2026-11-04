"""Tool schemas and handlers.

Owner mode gets the bookkeeping tools. Customer mode gets a smaller set that
cannot read the ledger or anyone else's data, whatever the DM says.
"""
import json
from datetime import date

from app import money
from app.money import CURRENCIES
from app import prefs as shop_prefs
from app.agent import books, voice, knowledge, onboarding, orders, payments, product_photos, reminders, sales, stocktake
from app.agent.approvals import CANCEL, CONFIRM, buttons_for, describe, submit
from app.ledger import reports
from app.ledger.proposals import KINDS, Item, Proposal
from app.ledger.validator import money_out, validate
from app.media import documents
from app.promo import flow as promo_flow
from app.promo import share
from app.normalize.amounts import naira
from app.normalize.fuzzy import best_match, name_similarity


def _fn(name, description, properties, required=()):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required)}}}


_ITEMS = {"type": "array", "items": {"type": "object", "properties": {
    "product_id": {"type": "string"},
    "qty": {"type": "integer"},
    "unit_price_naira": {"type": ["number", "null"], "description": "Price per unit if said, else null"},
}, "required": ["product_id", "qty"]}}

FIND_PRODUCT = _fn("find_product", "Look up products by what the person called them. Returns ids, prices and stock.",
                   {"query": {"type": "string"}}, ["query"])
FIND_CONTACT = _fn("find_contact", "Look up a customer or supplier by name. Returns ids and match scores.",
                   {"query": {"type": "string"}}, ["query"])
RECORD = _fn(
    "record_entry",
    "Propose one business event for the books. Code checks it and either records it, or asks the owner. "
    "Never compute totals, cost or balances yourself.",
    {
        "kind": {"type": "string", "enum": list(KINDS)},
        "items": _ITEMS,
        "contact_id": {"type": ["string", "null"]},
        "new_contact_name": {"type": ["string", "null"], "description": "Only if find_contact found nobody"},
        "amount_naira": {"type": ["number", "null"], "description": "payment_in/payment_out/expense/drawing"},
        "paid_naira": {"type": ["number", "null"],
                       "description": "sale/purchase: amount paid now. 0 if on credit. null if not said."},
        "payment_account": {"type": ["string", "null"], "enum": ["cash", "bank", None],
                            "description": "bank = transfer/POS"},
        "expense_account": {"type": ["string", "null"], "enum": ["transport", "power", "other_exp", None]},
        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD when credit is due, if said"},
        "memo": {"type": "string"},
        "unclear_amounts": {"type": "array", "description":
            "Every amount marked [UNCLEAR AMOUNT] in the message. path is the field it belongs to, "
            "e.g. 'items.0.unit_price_kobo', 'paid_kobo' or 'amount_kobo'.",
            "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "text": {"type": "string"},
                "candidates_naira": {"type": "array", "items": {"type": "number"}}},
                "required": ["path", "text", "candidates_naira"]}},
    },
    ["kind", "memo"],
)
READ_DOC = _fn("read_document",
                "Read the receipt, supplier invoice or notebook page in the attached photo. Code checks its arithmetic.",
                {})
BATCH = _fn("record_batch", "Record several entries from one photo with a single owner confirmation.", {
    "title": {"type": "string"},
    "entries": {"type": "array", "items": RECORD["function"]["parameters"]}}, ["entries"])
STOCK_TAKE = _fn("stock_take",
                 "Count products in the attached shelf photo and compare with the books. "
                 "Pass product_ids to count only those; omit to count every countable product.",
                 {"product_ids": {"type": "array", "items": {"type": "string"}}})
PROMO_IDEAS = _fn("promo_ideas", "Which products the books say are worth promoting now, with the reasons.", {})
MAKE_PROMO = _fn(
    "make_promo",
    "Make a flyer and caption from the owner's shelf photo and send her the preview to approve. "
    "Omit product_id to let the books pick. Without a photo it asks her for one.",
    {"product_id": {"type": ["string", "null"]},
     "scene": {"type": "string", "description": "Optional background idea, e.g. 'kitchen', 'market stall'"}})
SEND_REMINDER = _fn("send_reminder",
                    "Draft a payment reminder to a customer who owes, and send the owner the wording to approve.",
                    {"contact_id": {"type": "string"},
                     "instruction": {"type": ["string", "null"], "description": "How she wants it worded, if she said"}},
                    ["contact_id"])
SET_PHONE = _fn("set_contact_phone", "Save a customer's or supplier's WhatsApp number that the owner gave you.",
                {"contact_id": {"type": "string"}, "phone": {"type": "string"}}, ["contact_id", "phone"])
RECENT = _fn("recent_entries", "The latest ledger entries, newest first, to find one to correct.",
             {"limit": {"type": "integer"}})
REVERSE = _fn("cancel_entry", "Cancel a wrong entry with a reversing entry. The owner confirms first.",
              {"entry_id": {"type": "string"}, "reason": {"type": "string"}}, ["entry_id", "reason"])
REPORT = _fn("report", "Books report.", {
    "kind": {"type": "string", "enum": ["pnl", "cash", "debtors", "low_stock", "sales", "restock", "coach", "savings"],
             "description": "sales = today's customer chats: who asked, ordered, paid, and what they wanted that we lack. "
                            "restock = what will run out soon at her selling pace, and how many to order. "
                            "coach = tips from her numbers: prices that lose money or have thin margins, costs that went up, "
                            "slow stock, best seller. savings = what she has put aside and her goal"},
    "period": {"type": "string", "enum": ["today", "week", "month", "all"]}}, ["kind"])
ASK = _fn("ask_owner", "Ask the owner a question with up to 3 short button options.", {
    "question": {"type": "string"},
    "options": {"type": "array", "items": {"type": "string"}, "maxItems": 3}}, ["question"])

SETUP_PHOTO = _fn("setup_from_photo",
                  "The photo is her price list, product list or stock notebook (not a receipt of a sale or purchase): "
                  "add the products, prices and opening stock to the books. She approves the list first.", {})
ADD_PRODUCTS = _fn(
    "add_products",
    "Add products she sells that aren't in her list yet (find_product found nothing), with their selling prices. "
    "She approves the list first. Only give qty_in_stock and cost_naira for stock she ALREADY HAD before today; "
    "stock she just bought is a purchase: record it with record_entry after the product is added.",
    {"products": {"type": "array", "items": {"type": "object", "properties": {
        "name": {"type": "string"},
        "unit": {"type": "string", "description": "what one is: piece, copy, bag, carton, pack, yard..."},
        "price_naira": {"type": "number", "description": "her selling price for one unit"},
        "qty_in_stock": {"type": ["integer", "null"]},
        "cost_naira": {"type": ["number", "null"], "description": "what she paid for one, if said"},
        "book": {"type": ["object", "null"], "description": "Books only: what she said about it",
                 "properties": {"title": {"type": "string"}, "author": {"type": ["string", "null"]},
                                "publisher": {"type": ["string", "null"]}, "edition": {"type": ["string", "null"]},
                                "level": {"type": ["string", "null"], "description": "class, e.g. JSS1"},
                                "subject": {"type": ["string", "null"]},
                                "category": {"type": ["string", "null"], "enum": [
                                    "textbook", "literature", "exam", "children", "novel", "religious",
                                    "reference", "stationery", None]}}}},
        "required": ["name", "price_naira"]}}}, ["products"])
STATEMENT = _fn("business_statement",
                "Her business statement for a loan, microfinance or supplier credit: sales, profit, stock, cash, "
                "credit collected, month by month, with a link to the printable page she can share.", {})
LEARN = _fn("learn_business",
            "Read the shop's own website, Instagram, Facebook and WhatsApp profile and draft the shop info that "
            "customers are told (hours, location, delivery, payment...). She approves it before it is used. "
            "Pass any link she just gave; links given before are remembered.", {
                "website": {"type": ["string", "null"]}, "instagram": {"type": ["string", "null"], "description": "handle or URL"},
                "facebook": {"type": ["string", "null"], "description": "page name or URL"},
                "whatsapp": {"type": ["string", "null"]}})
SHOP_INFO = _fn("update_shop_info",
                "She told you a fact about the shop for customers (hours, address, delivery fees, payment, returns, "
                "bulk terms). Drafts the updated shop info for her to approve.",
                {"change": {"type": "string", "description": "the change in her words"}}, ["change"])

SET_PREFS = _fn(
    "set_preferences",
    "Change her settings when she asks: how you sound (style: 'be more professional', 'you can play with me'), "
    "how you sound to customers (customer_style), when her summary comes (every day, once a week, or off; the hour; the day), "
    "the language of her messages and summaries, the money her books are kept in, how payment reminders sound, how much of her profit to put aside and her savings goal, low-data mode, "
    "and whether her Sabi Score shows on her statement or her supplier prices are shared (anonymously) with other shops. "
    "Only pass what she asked to change.",
    {"summary_frequency": {"type": ["string", "null"], "enum": ["daily", "weekly", "off", None]},
     "summary_hour": {"type": ["integer", "null"], "description": "24-hour clock, 5 to 23. 7 am = 7, 8 pm = 20"},
     "summary_weekday": {"type": ["string", "null"], "enum": [*shop_prefs.WEEKDAYS, None]},
     "reminder_tone": {"type": ["string", "null"], "enum": [*shop_prefs.TONES, None]},
     "style": {"type": ["string", "null"], "enum": [*shop_prefs.STYLES, None],
               "description": "how you talk to her: auto (match each message), professional, or playful"},
     "customer_style": {"type": ["string", "null"], "enum": [*shop_prefs.STYLES, None],
                        "description": "how you talk to her customers: auto, professional, or playful"},
     "language": {"type": ["string", "null"],
                  "description": "the language she wants Sabi's messages and summaries in, in English: 'French', "
                                 "'Swahili', 'Pidgin'... or 'auto' to follow whatever she writes"},
     "currency": {"type": ["string", "null"], "enum": [*CURRENCIES, None],
                  "description": "the money her books are kept in: NGN naira, XOF F CFA (West Africa), XAF F CFA "
                                 "(Central Africa), GHS cedi, KES/TZS/UGX shilling, ZAR rand. Amounts already "
                                 "recorded are not converted, so only change it when she says so"},
     "savings_percent": {"type": ["number", "null"], "description": "share of each day's profit to put aside, 0-50"},
     "savings_goal_naira": {"type": ["number", "null"]},
     "savings_name": {"type": ["string", "null"], "description": "e.g. 'Ajo', 'school fees', 'new freezer'"},
     "low_data": {"type": ["boolean", "null"]},
     "share_score": {"type": ["boolean", "null"]},
     "share_prices": {"type": ["boolean", "null"]}})
CHECK_PAYMENT = _fn(
    "check_payment",
    "She forwarded a bank alert SMS or a transfer screenshot, or asks if a payment is real. Code checks it for signs "
    "of a fake (pending, old date, wrong account, sender's receipt instead of her bank's alert) and matches it to "
    "open orders and debts, then tells her. Pass the alert text if it was typed or pasted.",
    {"text": {"type": ["string", "null"]}})
SCORE = _fn("sabi_score",
            "Her Sabi Score out of 100: how ready her records are to back a loan, with each part, what it measured "
            "and the one thing that would raise it most.", {})
MARKET = _fn("market_prices",
             "What other Sabi shops paid suppliers for this product recently (anonymous: lowest, middle, highest), "
             "and how many are restocking it now (a chance to buy together).",
             {"product_id": {"type": "string"}}, ["product_id"])

CUST_PRODUCT = _fn("find_product", "Look up products the shop sells. Returns price and whether it is in stock.",
                   {"query": {"type": "string"}}, ["query"])
CREATE_ORDER = _fn("create_order", "Create an order for this customer at the shop's prices. Reserves stock for 24h.", {
    "items": {"type": "array", "items": {"type": "object", "properties": {
        "product_id": {"type": "string"}, "qty": {"type": "integer"}}, "required": ["product_id", "qty"]}}},
    ["items"])
PAYMENT_CLAIM = _fn("record_payment_claim",
                    "The customer says they paid (e.g. sent a transfer screenshot). The owner must confirm.", {
                        "order_id": {"type": "string"}, "amount_naira": {"type": ["number", "null"]},
                        "reference": {"type": ["string", "null"]}}, ["order_id"])
MY_ORDERS = _fn("my_open_orders", "This customer's open orders.", {})
SHOP_DETAILS = _fn("shop_details",
                   "Search the shop's own website and social pages for a detail the shop info above doesn't cover.",
                   {"question": {"type": "string"}}, ["question"])
HAND_TO_OWNER = _fn("hand_to_owner",
                    "Pass this chat to the shop owner: discounts or price haggling, bulk or wholesale buying, "
                    "complaints, anything not in the shop info, or when they ask for a person.", {
                        "reason": {"type": "string", "description": "a few words, e.g. 'wants a discount on 10 cartons'"},
                        "summary": {"type": "string", "description": "one or two lines: what they want so far"}},
                    ["reason", "summary"])

SAVE_PHOTO = _fn("save_product_photo",
                  "She sent a photo of ONE of her products to be its picture ('this is my Peak milk', 'use this "
                  "picture for rice'). Customers who send a picture of it, or ask to see it, get this photo back.",
                  {"product_id": {"type": "string"}}, ["product_id"])
MATCH_PHOTO = _fn("match_photo",
                  "The customer sent a photo of something they want. Finds which of the shop's products it is, "
                  "whether it is in stock, and similar products that are in stock. Call it before answering about "
                  "their photo. Notes what we couldn't offer for the owner.", {})
SEND_PHOTO = _fn("send_product_photo",
                 "Send the customer the shop's own picture of a product with its price: after a photo matched, "
                 "when offering something similar, or when they ask to see it. Only for has_photo products.",
                 {"product_id": {"type": "string"}}, ["product_id"])

_BOOKLIST_LINES = {"type": "array", "description": "Every book on the list, one per line, exactly as written",
                   "items": {"type": "object", "properties": {
                       "title": {"type": "string"},
                       "author": {"type": ["string", "null"]},
                       "edition": {"type": ["string", "null"], "description": "edition or year if the list says"},
                       "level": {"type": ["string", "null"], "description": "class if the line says, e.g. JSS1"},
                       "qty": {"type": ["integer", "null"], "description": "the number the list says: 2 for '2 dozen'"},
                       "unit": {"type": ["string", "null"], "description": "the list's unit if it says one: dozen, piece, copy"}},
                                 "required": ["title"]}}
PRICE_BOOKLIST = _fn(
    "price_booklist",
    "Price a school booklist: each title is matched to the shelf at the right class and edition, priced from the "
    "books, with what is missing. Pass the lines you read from their photo or message, with the school and class "
    "if shown. With no lines, prices the list the shop keeps for that school and class.",
    {"items": _BOOKLIST_LINES, "school": {"type": ["string", "null"]},
     "level": {"type": ["string", "null"], "description": "the class the list is for: Primary 4, JSS1, SS2..."}})
INSIGHTS = _fn("business_insights",
               "Her business at a glance from the books, last 30 days against the 30 before: sales trend, best "
               "sellers, what makes the most profit, stock not selling, what customers asked for that she lacks, "
               "who owes, and what is coming (restock, school resumption), with the takeaways worth acting on.", {})
VOICE = _fn("voice_replies",
            "They want to hear your answer as a voice note: 'reply with voice', 'send me voice note', 'talk to me'. "
            "this_once for this answer, always for every answer from now on, off to go back to text.",
            {"mode": {"type": "string", "enum": ["this_once", "always", "off"]}}, ["mode"])
SET_OFFERS = _fn("set_offers",
                 "The customer says yes to getting the shop's offers and new arrivals on WhatsApp, or asks to stop them.",
                 {"opt_in": {"type": "boolean"}}, ["opt_in"])
CLASS_BOOKS = _fn("class_books", "Every book the shop has for one class (optionally one subject), with prices.",
                  {"level": {"type": "string"}, "subject": {"type": ["string", "null"]}}, ["level"])
SAVE_BOOKLIST = _fn("save_booklist",
                    "Keep a school's booklist (from her photo or message) so parents only need to say the school "
                    "and class.", {"school": {"type": "string"}, "level": {"type": "string"},
                                   "items": _BOOKLIST_LINES}, ["school", "level", "items"])
TERM_RESTOCK = _fn("term_restock",
                   "What books to order before schools resume: the next resumption date, what sold in the last "
                   "resumption rush, what parents asked for that she lacked, old editions to clear, and order "
                   "quantities.", {})
IDENTIFY_PHOTO = _fn("identify_photo",
                     "Which of her products is in the attached photo (a book cover, a pack): returns the product_id, "
                     "stock and price. Use it before recording a sale or stock of 'this one'.", {})

OWNER_TOOLS = [FIND_PRODUCT, FIND_CONTACT, RECORD, READ_DOC, BATCH, STOCK_TAKE, PROMO_IDEAS, MAKE_PROMO, LEARN, SHOP_INFO, STATEMENT, SETUP_PHOTO, ADD_PRODUCTS,
               SEND_REMINDER, SET_PHONE, RECENT, REVERSE, REPORT, ASK, SET_PREFS, CHECK_PAYMENT, SCORE, MARKET, SAVE_PHOTO,
               PRICE_BOOKLIST, CLASS_BOOKS, SAVE_BOOKLIST, TERM_RESTOCK, IDENTIFY_PHOTO, VOICE, INSIGHTS]
CUSTOMER_TOOLS = [CUST_PRODUCT, MATCH_PHOTO, SEND_PHOTO, CREATE_ORDER, PAYMENT_CLAIM, MY_ORDERS, SHOP_DETAILS, HAND_TO_OWNER,
                  PRICE_BOOKLIST, CLASS_BOOKS, SET_OFFERS, VOICE]


def _kobo(naira_value):
    return None if naira_value is None else round(float(naira_value) * 100)


# --- owner handlers --------------------------------------------------------

def find_product(ctx, query, customer=False):
    scored = sorted(((books.score(query, p), p) for p in ctx.store.list_products()), key=lambda x: x[0], reverse=True)[:3]
    out = []
    for score, p in scored:
        if score < 0.5:
            continue
        ctx.match_scores[p.id] = max(score, ctx.match_scores.get(p.id, 0))
        available = ctx.store.stock_on_hand(p.id) - ctx.store.reserved(p.id)
        row = {"product_id": p.id, "name": p.name, "unit": p.base_unit, "price": naira(p.sell_price_kobo)}
        if customer:
            row["in_stock"] = available > 0 or not reports.tracks_stock(ctx.store, p.id)
            row["has_photo"] = bool(p.photo)
        else:
            row.update(match=round(score, 2), stock=available, usual_price_range=
                       f"{naira(p.price_floor)}-{naira(p.price_ceiling)}")
        row.update(books.facts(ctx.store, p, customer))
        out.append(row)
    return {"matches": out} if out else {"matches": [], "note": "no product matches; ask what they meant"}


def find_contact(ctx, query):
    scored = sorted(((max(name_similarity(query, n) for n in [c.name, *c.aliases]), c)
                     for c in ctx.store.list_contacts()), key=lambda x: x[0], reverse=True)[:3]
    out = []
    for score, c in scored:
        if score < 0.5:
            continue
        ctx.match_scores[c.id] = max(score, ctx.match_scores.get(c.id, 0))
        out.append({"contact_id": c.id, "name": c.name, "kind": c.kind, "match": round(score, 2),
                    "owes": naira(max(ctx.store.account_balance("ar", c.id), 0))})
    return {"matches": out} if out else {"matches": [], "note": "nobody by that name; use new_contact_name"}


def record_entry(ctx, **a):
    p, errors = _proposal_from_args(ctx, a)
    if errors:
        return {"status": "rejected", "errors": errors}
    return submit(p, ctx)


def _proposal_from_args(ctx, a):
    """Build a Proposal from tool arguments and apply the checks only code may make. Returns (p, errors)."""
    if ctx.document_issues:
        return None, ["the photo's numbers don't add up; wait for the owner to say which figures are right"]
    try:
        p = Proposal(
            kind=a["kind"],
            items=[Item(i["product_id"], i["qty"], _kobo(i.get("unit_price_naira"))) for i in a.get("items") or []],
            contact_id=a.get("contact_id"),
            new_contact_name=a.get("new_contact_name"),
            amount_kobo=_kobo(a.get("amount_naira")),
            paid_kobo=_kobo(a.get("paid_naira")),
            payment_account=a.get("payment_account"),
            expense_account=a.get("expense_account"),
            due_date=date.fromisoformat(a["due_date"]) if a.get("due_date") else None,
            memo=a.get("memo", ""),
            evidence_id=ctx.evidence_id,
            unclear_amounts=[{"path": _kobo_path(u["path"]), "text": u["text"],
                              "candidates_kobo": [_kobo(c) for c in u["candidates_naira"]]}
                             for u in a.get("unclear_amounts") or []],
        )
    except (KeyError, TypeError, ValueError) as e:
        return None, [f"bad arguments: {e}"]

    # Code, not the model, decides whether money was unclear: every ambiguous
    # amount the normaliser found must be handed to the owner as a question.
    if ctx.message:
        listed = " ".join(u["text"].lower() for u in p.unclear_amounts)
        missing = [m.text for m in ctx.message.ambiguous if m.text.lower() not in listed]
        if missing:
            return None, [f"the message has unclear amounts {missing}; list each in unclear_amounts with its candidates"]

    ids = [i.product_id for i in p.items] + ([p.contact_id] if p.contact_id else [])
    p.match_scores = {i: ctx.match_scores[i] for i in ids if i in ctx.match_scores}
    unseen = [i for i in ids if i not in ctx.match_scores]
    if unseen:
        return None, [f"look these up with find_product/find_contact first: {unseen}"]
    return p, []


def _kobo_path(path):
    """Models often name the field as the tool does ("amount_naira", "items.0.unit_price_naira")."""
    path = path.strip().replace("_naira", "_kobo")
    return {"amount": "amount_kobo", "paid": "paid_kobo"}.get(path, path)


def read_document(ctx):
    if not ctx.image_data_url:
        return {"error": "no photo in this message"}
    if ctx.llm is None:
        return {"error": "vision model not configured"}
    doc = documents.extract(ctx.llm, ctx.image_data_url)
    issues = documents.check_arithmetic(doc)
    ctx.document = doc
    ctx.store.set_evidence_extracted(ctx.evidence_id, {"document": doc, "arithmetic_issues": issues})
    if issues:
        ctx.document_issues = issues
        ctx.tell_owner("I read the photo, but the numbers don't agree:\n• " + "\n• ".join(issues)
                       + "\nWhich figures are right? Reply with the correct ones.")
        return {"status": "waiting_for_owner", "document": doc, "arithmetic_issues": issues,
                "instruction": "Record nothing from this photo until the owner answers."}
    return {"status": "ok", "document": doc,
            "next": "Look up each product and person, then record every line with record_batch."}


def record_batch(ctx, entries, title="From your photo"):
    """Several entries from one photo, confirmed with a single tap. Lines with open questions are asked separately."""
    built, errors = [], []
    for i, a in enumerate(entries or []):
        p, errs = _proposal_from_args(ctx, a)
        errors += [f"entries[{i}]: {e}" for e in errs]
        built.append(p)
    if errors:
        return {"status": "rejected", "errors": errors}
    clean, asked = [], []
    for p in built:
        v = validate(p, ctx.store, ctx.auto_post_limit_kobo)
        if v.errors:
            errors += v.errors
        elif v.questions:
            asked.append(submit(p, ctx))     # sends its own question
        else:
            clean.append(p)
    if errors:
        return {"status": "rejected", "errors": errors}
    if clean:
        total = sum(p.items_total_kobo or p.amount_kobo or 0 for p in clean)
        lines = "\n".join(f"• {describe(p, ctx.store)}" for p in clean)
        # Each line was checked alone; together they can still empty the till.
        lines += "".join(f"\n⚠️ Together these take your {account} to {naira(left)}."
                         for account, left in _left_after(clean, ctx.store))
        a = ctx.store.add_approval("batch", {"proposals": [p.to_json() for p in clean]},
                                   f"{title}: {len(clean)} entr{'y' if len(clean) == 1 else 'ies'}, "
                                   f"{naira(total)}\n{lines}\nRecord all?", [CONFIRM, CANCEL])
        ctx.tell_owner(a["question"], buttons_for(a))
    return {"status": "waiting_for_owner", "batched": len(clean), "asked_separately": len(asked)}


def _left_after(proposals, store):
    """[(account, balance after)] for each of cash/bank the batch would take below zero."""
    out = {}
    for p in proposals:
        account, kobo = money_out(p)
        if account in ("cash", "bank"):
            out[account] = out.get(account, 0) + kobo
    return [(a, store.account_balance(a) - k) for a, k in out.items() if k and store.account_balance(a) - k < 0]


def normalize_phone(raw):
    """Nigerian numbers to WhatsApp's digits-only international form: 0803 123 4567 -> 2348031234567."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("0") and len(digits) == 11:        # 0803 123 4567
        return "234" + digits[1:]
    if len(digits) == 10 and digits[0] in "789":            # 803 123 4567
        return "234" + digits
    if digits.startswith("234"):                           # +234 803 123 4567
        return digits if len(digits) == 13 else None
    return digits if 10 <= len(digits) <= 15 and not digits.startswith("0") else None   # other countries


def set_contact_phone(ctx, contact_id, phone):
    contact = ctx.store.get_contact(contact_id)
    if contact is None:
        return {"error": "no such contact; use find_contact"}
    number = normalize_phone(phone)
    if number is None:
        return {"error": f"{phone!r} doesn't look like a phone number; ask her to check it"}
    other = ctx.store.contact_by_phone(number)
    if other and other.id != contact_id:
        return {"error": f"that number already belongs to {other.name}"}
    ctx.store.set_contact_phone(contact_id, number)
    return {"status": "saved", "contact": contact.name, "phone": number}


def recent_entries(ctx, limit=5):
    entries = ctx.store.list_entries()[-max(1, min(limit or 5, 20)):]
    out = []
    for e in reversed(entries):
        if e.kind in ("opening",):
            continue
        amount = max(e.total_debits, 0)
        who = ctx.store.get_contact(e.contact_id).name if e.contact_id else None
        out.append({"entry_id": e.id, "kind": e.kind, "date": str(e.entry_date), "memo": e.memo,
                    "who": who, "amount": naira(amount), "status": e.status})
    return {"entries": out}


def cancel_entry(ctx, entry_id, reason):
    e = ctx.store.get_entry(entry_id)
    if e is None:
        return {"error": "no such entry; use recent_entries"}
    if e.status != "committed" or e.kind == "reversal":
        return {"error": "that entry is already cancelled or is itself a cancellation"}
    a = ctx.store.add_approval("reversal", {"entry_id": entry_id, "reason": reason},
                               f"Cancel this entry? {e.memo or e.kind} ({naira(e.total_debits)}), "
                               f"{e.entry_date}. Reason: {reason}", [CONFIRM, {**CANCEL, "label": "Keep it"}])
    ctx.tell_owner(a["question"], buttons_for(a))
    return {"status": "waiting_for_owner"}


def report(ctx, kind, period="today"):
    if kind == "coach":
        from app.ledger import coach
        return {"tips": [t["text"] for t in coach.tips(ctx.store, limit=5)] or ["Nothing to flag: prices and stock look fine."]}
    data = reports.savings(ctx.store) if kind == "savings" else reports.report(ctx.store, kind, period)
    return _in_naira(json.loads(json.dumps(data, default=str)))


def _in_naira(data, kobo=False):
    """{"sales_kobo": 11130000} → {"sales": "₦111,300"}: whole naira, as she would say it.
    Everything under a *_kobo key is kobo, e.g. {"expenses_kobo": {"stock_loss": 380000}}."""
    if isinstance(data, list):
        return [_in_naira(x, kobo) for x in data]
    if isinstance(data, dict):
        return {(k[:-5] if k.endswith("_kobo") else k): _in_naira(v, kobo or k.endswith("_kobo"))
                for k, v in data.items()}
    if kobo and isinstance(data, (int, float)) and not isinstance(data, bool):
        return _whole_naira(data)
    return data


def _whole_naira(kobo):
    return money.fmt(kobo, exact=False)


def ask_owner(ctx, question, options=None):
    options = (options or [])[:3]
    buttons = [{"id": f"q:{i}:{o[:20]}", "title": o[:20]} for i, o in enumerate(options)]
    ctx.tell_owner(question, buttons)
    return {"status": "asked"}


def business_statement(ctx):
    from app.ledger.reports import business_statement as build
    s = build(ctx.store)
    out = {"from": str(s["from"]), "to": str(s["to"]), "days_of_records": s["days"],
           **_in_naira({k: s[k] for k in ("total", "avg_weekly_sales_kobo", "cash_kobo", "bank_kobo", "stock_value_kobo",
                                           "owed_to_shop_kobo", "owed_to_suppliers_kobo")}),
           "collection_percent": s["collection_pct"], "entries": s["entries"]}
    if ctx.statement_link:
        out["link"] = ctx.statement_link()
        out["link_note"] = "Give her this link exactly as it is. It works for 7 days; she can print it or send it to the lender."
    if s["days"] < 60:
        out["note"] = "Less than two months of records: lenders usually want three. Say so kindly."
    return out


def set_preferences(ctx, **a):
    changes = {}
    summary = {}
    if a.get("summary_frequency"):
        summary["frequency"] = a["summary_frequency"]
    if a.get("summary_hour") is not None:
        summary["hour"] = a["summary_hour"]
    if a.get("summary_weekday"):
        summary["weekday"] = shop_prefs.WEEKDAYS.index(a["summary_weekday"])
    if summary:
        changes["summary"] = summary
    savings = {}
    if a.get("savings_percent") is not None:
        savings["percent"] = a["savings_percent"]
    if a.get("savings_goal_naira") is not None:
        savings["goal_kobo"] = _kobo(a["savings_goal_naira"])
    if a.get("savings_name"):
        savings["name"] = a["savings_name"]
    if savings:
        changes["savings"] = savings
    for k in ("reminder_tone", "language", "currency", "low_data", "share_score", "share_prices", "style",
              "customer_style"):
        if a.get(k) is not None:
            changes[k] = a[k]
    if not changes:
        return {"error": "nothing to change; ask her what she wants changed"}
    saved, errors = shop_prefs.save(ctx.store, changes)
    if errors:
        return {"status": "rejected", "errors": errors}
    money.use(ctx.store)
    return {"status": "saved", "summary": shop_prefs.describe_summary(saved), "settings": shop_prefs.public(saved),
            "note": "Tell her in one line what changed. She can also change these in Shop ⚙."}


def sabi_score(ctx):
    from app.ledger.score import sabi_score as score
    s = score(ctx.store)
    return {"score": s["score"], "band": s["band"], "note": s["note"], "next_step": s["next_step"],
            "parts": [{"part": p["name"], "out_of_100": p["value"], "weight_percent": p["weight"], "measured": p["measured"]}
                      for p in s["parts"]],
            "explain": "It reads only her own books. It is not a bank's credit score. Give her the score, the band and "
                       "the one next step; list the parts only if she asks."}


def market_prices(ctx, product_id):
    if ctx.market is None:
        return {"note": "Shared prices come from other Sabi shops; none are connected to this shop yet."}
    data = ctx.market(product_id)
    return _in_naira(data)


# --- customer handlers -----------------------------------------------------

def my_open_orders(ctx):
    return {"orders": [{"order_id": o["id"], "ref": orders.order_ref(o["id"]), "total": naira(o["total_kobo"]),
                        "status": o["status"],
                        "items": [f"{i['qty']} x {ctx.store.get_product(i['product_id']).name}" for i in o["items"]]}
                       for o in ctx.store.open_orders(ctx.contact_id)]}


OWNER_HANDLERS = {
    "find_product": find_product, "find_contact": find_contact, "record_entry": record_entry,
    "read_document": read_document, "record_batch": record_batch,
    "stock_take": lambda ctx, product_ids=None: stocktake.run(ctx, product_ids),
    "send_reminder": lambda ctx, contact_id, instruction=None: reminders.offer(ctx, contact_id, instruction),
    "set_contact_phone": lambda ctx, contact_id, phone: set_contact_phone(ctx, contact_id, phone),
    "promo_ideas": lambda ctx: promo_flow.ideas(ctx),
    "make_promo": lambda ctx, product_id=None, scene="default": promo_flow.make_promo(ctx, ctx.promo, product_id, scene),
    "recent_entries": recent_entries, "cancel_entry": cancel_entry, "report": report, "ask_owner": ask_owner,
    "learn_business": knowledge.learn, "update_shop_info": knowledge.update_shop_info,
    "business_statement": business_statement, "setup_from_photo": onboarding.setup_from_photo,
    "add_products": onboarding.add_products,
    "set_preferences": set_preferences, "check_payment": payments.check_payment, "sabi_score": sabi_score,
    "market_prices": market_prices, "save_product_photo": product_photos.save_product_photo,
    "price_booklist": books.price_booklist, "class_books": books.class_books, "save_booklist": books.save_booklist,
    "term_restock": lambda ctx: books.term_restock(ctx), "identify_photo": product_photos.identify_for_owner,
    "voice_replies": lambda ctx, mode: voice.set_mode(ctx, mode),
    "business_insights": lambda ctx: _in_naira(_insights(ctx.store)),
}


def _insights(store):
    from app.ledger.insights import insights
    data = insights(store)
    data["note"] = ("Lead with the takeaways, in her words; numbers only as given. Offer the Insights tab in the "
                    "app for the charts.")
    return data


def customer_find_product(ctx, query):
    result = find_product(ctx, query, customer=True)
    sales.note_lookup(ctx, query, result["matches"])
    return result


CUSTOMER_HANDLERS = {
    "find_product": customer_find_product,
    "match_photo": product_photos.match_photo,
    "send_product_photo": product_photos.send_product_photo,
    "create_order": lambda ctx, items: orders.create_order(ctx, items),
    "record_payment_claim": orders.record_payment_claim,
    "my_open_orders": my_open_orders,
    "shop_details": knowledge.shop_details,
    "hand_to_owner": sales.hand_to_owner,
    "price_booklist": books.price_booklist, "class_books": books.class_books,
    "set_offers": lambda ctx, opt_in: share.set_offers(ctx, opt_in),
    "voice_replies": lambda ctx, mode: voice.set_mode(ctx, mode),
}


def toolset(persona):
    return (OWNER_TOOLS, OWNER_HANDLERS) if persona == "owner" else (CUSTOMER_TOOLS, CUSTOMER_HANDLERS)
