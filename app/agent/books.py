"""Bookshop skills: books know their class, author and edition, and Sabi prices whole booklists.

- A book is a product with `details` (title, author, publisher, edition, level, subject, category,
  isbn, replaced_by). `names` gives fuzzy lookup every way a parent says it ("NGM JSS 1",
  "New General Maths Basic 7"); `facts` adds author, class and edition to tool results, and points
  an old edition to the new one.
- `price_booklist`: Muse Spark reads the parent's booklist photo and passes each line; code matches
  every line to the shelf at the right class and edition, prices it from the books and says what is
  missing. The model never prices anything. Lists the shop keeps for nearby schools work the same way
  from just the school and class.
- `class_books`: everything the shop has for one class.
- `term_restock`: when schools resume, what sold in the last resumption rush, what parents asked for
  that the shop lacked, old-edition copies to clear, and how many to order before the depots run short.
"""
import math
import re
from datetime import date, timedelta

from app.agent import sales
from app.ledger import reports
from app.normalize.amounts import naira
from app.normalize.fuzzy import best_match, similarity

MATCH = 0.72          # a booklist line below this is not the book we have
SAME_TITLE = 0.85     # sure enough to say "we have it, but for another class"
SCHOOL_MATCH = 0.75

_LEVEL = re.compile(r"\b(?:(nursery|kg|kindergarten)|(primary|pry|pri|basic|p)|(jss|js|junior secondary)|"
                    r"(sss|ss|senior secondary))\s*\.?\s*(\d|one|two|three|four|five|six)\b", re.I)
_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def level_of(text):
    """'JSS 1', 'js1', 'Basic 7' -> 'JSS1'; 'Pry 4', 'Basic 4' -> 'Primary 4'; 'SSS 2' -> 'SS2'. None if none."""
    m = _LEVEL.search(text or "")
    if not m:
        return None
    n = int(_NUM.get(m.group(5).lower(), m.group(5)))
    if m.group(1):
        return f"Nursery {n}"
    if m.group(2):
        if m.group(2).lower() == "basic" and n >= 7:
            return f"JSS{n - 6}" if n <= 9 else None
        return f"Primary {n}"
    return f"JSS{n}" if m.group(3) else f"SS{n}"


def _spellings(level):
    if not level:
        return []
    if level.startswith("JSS"):
        n = level[3:]
        return [level, f"JSS {n}", f"JS{n}", f"Basic {int(n) + 6}"]
    if level.startswith("SS"):
        n = level[2:]
        return [level, f"SS {n}", f"SSS{n}", f"SSS {n}"]
    if level.startswith("Primary"):
        n = level.split()[-1]
        return [level, f"Pry {n}", f"Basic {n}", f"P{n}"]
    return [level]


# How booklists shorten things. Expanded before matching, so "Basic Sci & Tech" finds
# "Basic Science and Technology" and "NGM JSS 1" finds "New General Mathematics JSS1".
SHORT = {"maths": "mathematics", "math": "mathematics", "mths": "mathematics", "ngm": "new general mathematics",
         "agric": "agricultural", "eng": "english", "lit": "literature", "bio": "biology", "chem": "chemistry",
         "phy": "physics", "phys": "physics", "govt": "government", "econs": "economics", "bst": "basic science technology",
         "qr": "quantitative reasoning", "vr": "verbal reasoning", "crs": "christian religious studies",
         "irs": "islamic religious studies", "dict": "dictionary", "ex": "exercise", "bk": "book", "bks": "books"}
FILLER = {"and", "for", "the", "of", "a", "an", "in", "to", "new", "book", "books", "textbook", "text"}


def _words(text):
    out = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        out += SHORT.get(w, w).split()
    return out


def _prefix_score(query, name):
    """Every word of the query starts a word of the name ('sci' -> 'science'). Short forms score high."""
    q = [w for w in _words(query) if w not in FILLER]
    n = [w for w in _words(name) if w not in FILLER]
    if not q or not n:
        return 0.0
    used = set()
    for w in q:
        hit = next((i for i, m in enumerate(n) if i not in used and (m == w or (len(w) >= 2 and m.startswith(w)))), None)
        if hit is None:
            return 0.0
        used.add(hit)
    return 0.75 + 0.25 * len(used) / len(n)


def score(query, p):
    return max(max(similarity(query, n), _prefix_score(query, n)) for n in names(p))


def is_book(p):
    """A book: has book details, or its name says a class ("Basic Science JSS2" from a price-list photo)."""
    return bool((p.details or {}).get("title")) or bool(level_of(p.name))


def level_for(p):
    return (p.details or {}).get("level") or level_of(p.name)


def names(p):
    """Every string a person might use for this product."""
    d = p.details or {}
    out = [p.name, *p.aliases]
    title = d.get("title")
    if title:
        out.append(title)
        out += [f"{title} {s}" for s in _spellings(d.get("level"))]
        if d.get("author"):
            out.append(f"{title} {d['author']}")
        if d.get("isbn"):
            out.append(d["isbn"])
    return out


def _available(store, p):
    return store.stock_on_hand(p.id) - store.reserved(p.id)


def _in_stock(store, p):
    """As find_product says it: some left, or a product she never put stock in for (we can't tell)."""
    return _available(store, p) > 0 or not reports.tracks_stock(store, p.id)


def link_editions(store):
    """Same title and class in two editions: the older one points to the newest (replaced_by), so Sabi
    can say "most schools now use the newer one". Editions are compared by the year or number in them."""
    groups = {}
    for p in _books(store):
        d = p.details or {}
        if d.get("title") and d.get("edition"):
            groups.setdefault((d["title"].lower(), level_for(p)), []).append(p)
    linked = 0
    for group in groups.values():
        dated = [(max(map(int, re.findall(r"\d+", p.details["edition"])), default=-1), p) for p in group]
        dated = [(n, p) for n, p in dated if n >= 0]
        if len(dated) < 2:
            continue
        newest = max(dated, key=lambda x: x[0])[1]
        for n, p in dated:
            if p.id != newest.id and not p.details.get("replaced_by"):
                store.set_product_details(p.id, {**p.details, "replaced_by": newest.id})
                linked += 1
    return linked


def facts(store, p, customer=False):
    """Book facts for a find_product row; nothing for other products."""
    d = p.details or {}
    if not is_book(p):
        return {}
    row = {k: d[k] for k in ("author", "publisher", "edition", "level", "subject", "category") if d.get(k)}
    newer = store.get_product(d["replaced_by"]) if d.get("replaced_by") else None
    if newer:
        row["old_edition"] = True
        row["newer_edition"] = {"product_id": newer.id, "name": newer.name, "edition": (newer.details or {}).get("edition"),
                                "price": naira(newer.sell_price_kobo), "in_stock": _in_stock(store, newer)}
        row["note"] = ("This is an old edition. Most schools now use the newer one: tell them, and let them check "
                       "with the school before buying this one.")
    return row


def _books(store):
    return [p for p in store.list_products() if is_book(p)]


def _current(store, p):
    """The edition to offer by default: follow replaced_by to the newest one the shop lists."""
    seen = set()
    while (p.details or {}).get("replaced_by") and p.id not in seen:
        seen.add(p.id)
        newer = store.get_product(p.details["replaced_by"])
        if newer is None:
            break
        p = newer
    return p


def _older(store, p):
    return [o for o in _books(store) if (o.details or {}).get("replaced_by") == p.id]


def _edition_score(asked, p):
    """How well the edition a list asks for fits this book: years and edition numbers must agree."""
    have = str((p.details or {}).get("edition") or "")
    nums_asked, nums_have = set(re.findall(r"\d+", asked)), set(re.findall(r"\d+", have))
    if nums_asked and nums_have:
        return 1.0 if nums_asked & nums_have else 0.0
    return similarity(asked, have) if have else 0.5


def match_line(store, title, level=None, author=None, edition=None):
    """The book on the shelf for one booklist line. Returns (product, score, note)."""
    level = level_of(level or "") or level_of(title)
    products = store.list_products()
    scored = sorted(((score(title, p), p) for p in products), key=lambda x: x[0], reverse=True)
    if author:   # the author settles a tie between two titles
        scored = sorted(scored, key=lambda x: x[0] + 0.05 * similarity(author, (x[1].details or {}).get("author") or ""),
                        reverse=True)
    at_level = [(s, p) for s, p in scored if not level or not level_for(p) or level_for(p) == level]
    if not at_level or at_level[0][0] < MATCH:
        other = next((p for s, p in scored if s >= SAME_TITLE), None)
        if other and level and level_for(other) not in (None, level):
            title = (other.details or {}).get("title") or other.name
            return None, 0.0, f"we have {title} only for {level_for(other)}, not {level}"
        return None, (at_level[0][0] if at_level else 0.0), None
    score_, p = at_level[0]
    if not is_book(p):
        return p, score_, None
    family = [_current(store, p), *_older(store, _current(store, p))]
    if edition:
        best = max(family, key=lambda q: _edition_score(edition, q))
        if _edition_score(edition, best) >= 0.6:
            return best, score_, None
        return _current(store, p), score_, f"the list asks for the {edition} edition; we have the {_current(store, p).details.get('edition')} edition"
    return _current(store, p), score_, None


def _in_shop_units(qty, unit, p):
    """'2 dozen' exercise books where the shop counts dozens is 2; '24 pieces' is also 2."""
    unit, base = (unit or "").lower().rstrip("s"), p.base_unit.lower().rstrip("s")
    if not unit or unit == base:
        return qty
    if base == "dozen" and unit in ("piece", "pc", "book", "copy"):
        return max(math.ceil(qty / 12), 1)
    if unit == "dozen" and base in ("piece", "copy", "book"):
        return qty * 12
    return qty


def _line_row(store, p, qty):
    available = max(_available(store, p), 0)
    tracked = reports.tracks_stock(store, p.id)
    supply = qty if not tracked else min(qty, available)
    status = "available" if supply == qty else ("short" if supply else "out_of_stock")
    d = p.details or {}
    row = {"product_id": p.id, "name": p.name, "qty": qty, "price": naira(p.sell_price_kobo),
           "status": status, "can_supply": supply, "line_total": naira(supply * p.sell_price_kobo),
           **{k: d[k] for k in ("author", "edition", "level") if d.get(k)}}
    if status == "short":
        row["note"] = f"only {available} left"
    newer = store.get_product(d["replaced_by"]) if d.get("replaced_by") else None
    if newer:
        row["newer_edition"] = {"product_id": newer.id, "edition": (newer.details or {}).get("edition"),
                                "price": naira(newer.sell_price_kobo), "in_stock": _in_stock(store, newer)}
        row["note"] = "old edition, as the list asks; say the newer one exists in case the school changed"
    old = [o for o in _older(store, p) if _available(store, o) > 0]
    if old:
        row["old_edition_also"] = {"product_id": old[0].id, "edition": old[0].details.get("edition"),
                                   "price": naira(old[0].sell_price_kobo), "copies": _available(store, old[0])}
    return row, supply * p.sell_price_kobo


def find_school_list(store, school, level):
    level = level_of(level or "")
    lists = (getattr(store, "business", {}) or {}).get("booklists") or []
    fits = [(similarity(school, b["school"]), b) for b in lists if level_of(b.get("level", "")) == level]
    fits = [f for f in fits if f[0] >= SCHOOL_MATCH]
    return max(fits, key=lambda f: f[0])[1] if fits else None


def price_booklist(ctx, items=None, school=None, level=None):
    """Price a booklist line by line. `items` are the lines read from their photo or typed; with none,
    the list the shop keeps for that school and class."""
    store = ctx.store
    source = "their list"
    if not items:
        if not (school and level):
            return {"error": "no booklist lines: read them from the photo, or ask which school and class"}
        kept = find_school_list(store, school, level)
        if kept is None:
            return {"status": "no_list_kept", "note": f"the shop has no list for {school} {level}; ask them to send "
                                                      "a photo of the booklist"}
        source = f"the shop's list for {kept['school']} {kept['level']}"
        items = [{"title": store.get_product(i["product_id"]).details.get("title") or store.get_product(i["product_id"]).name,
                  "level": kept["level"],
                  "qty": i.get("qty", 1), "product_id": i["product_id"]}
                 for i in kept["items"] if store.get_product(i["product_id"])]
        items += [{"title": t, "level": kept["level"]} for t in kept.get("not_stocked") or []]
    lines, total, order_items, missing = [], 0, [], []
    for raw in items[:40]:
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        qty = max(int(raw.get("qty") or 1), 1)
        want_level = raw.get("level") or level
        if raw.get("product_id") and store.get_product(raw["product_id"]):
            p, note = _current(store, store.get_product(raw["product_id"])), None
        else:
            p, _, note = match_line(store, title, want_level, raw.get("author"), raw.get("edition"))
        if p is None:
            row = {"asked": title, "status": "not_stocked", "qty": qty}
            if note:
                row["note"] = note
            lines.append(row)
            lvl = level_of(want_level or "")
            missing.append(f"{title} {lvl}" if lvl and not level_of(title) else title)
            continue
        row, line_total = _line_row(store, p, _in_shop_units(qty, raw.get("unit"), p))
        row["asked"] = title
        if note:
            row["edition_note"] = note
        lines.append(row)
        total += line_total
        if row["can_supply"]:
            order_items.append({"product_id": p.id, "qty": row["can_supply"]})
        if row["status"] != "available":
            missing.append(f"{p.name} ({row['status'].replace('_', ' ')})")
    if ctx.persona == "customer" and ctx.contact_id:
        _note_lead(ctx, order_items, missing)
    ready = sum(1 for l in lines if l["status"] == "available")
    # The summary stays in the chat history, so "reserve them" can go straight to create_order.
    summary = (f"{ready} of {len(lines)} ready, {naira(total)}; order_items "
               + ", ".join(f"{i['product_id']} x{i['qty']}" for i in order_items))
    return {"status": "priced", "summary": summary, "source": source, "lines": lines, "books_on_list": len(lines), "ready": ready,
            "total_for_what_we_have": naira(total), "order_items": order_items, "missing": missing,
            "note": "Totals come from the shop's prices. Offer to reserve what we have with create_order(order_items); "
                    "say plainly what we don't have."}


def _note_lead(ctx, order_items, missing):
    lead = sales.lead_for(ctx.store, ctx.contact_id)
    interests, unmet = lead["interests"], lead["unmet"]
    for i in order_items:
        interests = sales._add(interests, i["product_id"])
    for m in missing:
        unmet = sales._add(unmet, f"{m.lower()[:40]} (booklist)")
    ctx.store.save_lead(ctx.contact_id, interests=interests, unmet=unmet)


def save_booklist(ctx, school, level, items):
    """Owner: keep a school's booklist so parents only need to say the school and class."""
    lvl = level_of(level or "")
    if not lvl:
        return {"error": "which class is this list for? e.g. JSS1, Primary 4, SS2"}
    kept, not_stocked = [], []
    for raw in items[:40]:
        p, _, _ = match_line(ctx.store, str(raw.get("title") or ""), lvl, raw.get("author"), raw.get("edition"))
        if p:
            kept.append({"product_id": p.id, "qty": max(int(raw.get("qty") or 1), 1)})
        elif raw.get("title"):
            not_stocked.append(str(raw["title"])[:80])
    business = dict(ctx.store.business)
    lists = [b for b in business.get("booklists") or []
             if not (level_of(b.get("level", "")) == lvl and similarity(school, b["school"]) >= SCHOOL_MATCH)]
    lists.append({"school": school.strip(), "level": lvl, "items": kept, "not_stocked": not_stocked})
    business["booklists"] = lists
    ctx.store.business = business
    return {"status": "saved", "school": school.strip(), "level": lvl, "books_matched": len(kept),
            "not_stocked": not_stocked,
            "summary": f"Kept the {school.strip()} {lvl} list: {len(kept)} books we stock"
                       + (f", {len(not_stocked)} we don't ({', '.join(not_stocked[:5])})" if not_stocked else "")}


def class_books(ctx, level, subject=None):
    lvl = level_of(level or "")
    if not lvl:
        return {"error": "which class? e.g. Primary 4, JSS1, SS2"}
    rows = []
    for p in _books(ctx.store):
        d = p.details or {}
        if level_for(p) != lvl or d.get("replaced_by"):
            continue
        if subject and similarity(subject, d.get("subject") or "") < 0.6 and subject.lower() not in p.name.lower():
            continue
        rows.append({"product_id": p.id, "name": p.name, "subject": d.get("subject"), "author": d.get("author"),
                     "edition": d.get("edition"), "price": naira(p.sell_price_kobo),
                     "in_stock": _in_stock(ctx.store, p)})
    schools = [b["school"] for b in (ctx.store.business.get("booklists") or []) if b.get("level") == lvl]
    return {"level": lvl, "books": rows, "school_lists_kept": schools,
            "note": "Ask which school: if we keep its list, price_booklist(school, level) prices it all at once."}


# --- the school year ------------------------------------------------------------------------

# Most Nigerian schools: first term from mid-September (the new session: new class, new books),
# second term from mid-January, third term from late April. Schools differ by a week or two.
TERM_DEMAND = {"first": 1.0, "second": 0.4, "third": 0.3}   # share of a September rush; starting guesses
RUSH_DAYS = 21          # parents buy in the first three weeks of term
ORDER_AHEAD = 21        # order three weeks before resumption, before the depots run short


def _nth_weekday(year, month, n, weekday=0):
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year, month, weekday=0):
    d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def resumptions(year):
    return [("second", _nth_weekday(year, 1, 2)), ("third", _last_weekday(year, 4)), ("first", _nth_weekday(year, 9, 2))]


def school_calendar(today=None):
    today = today or date.today()
    dates = sorted(resumptions(today.year - 1) + resumptions(today.year) + resumptions(today.year + 1), key=lambda t: t[1])
    nxt = next(t for t in dates if t[1] > today)
    last = [t for t in dates if t[1] <= today][-1]
    last_first = [t for t in dates if t[0] == "first" and t[1] <= today][-1]
    return {"next_term": nxt[0], "resumes": nxt[1], "days_to_go": (nxt[1] - today).days,
            "last_resumed": last[1], "last_term": last[0], "last_new_session": last_first[1]}


def term_restock(ctx, today=None):
    """What to order before schools resume, from her own resumption sales and what parents asked for."""
    store = ctx.store
    today = today or date.today()
    cal = school_calendar(today)
    rush_start = cal["last_new_session"]
    rush_end = rush_start + timedelta(days=RUSH_DAYS - 1)
    factor = TERM_DEMAND[cal["next_term"]]
    asked = _asked_for(store)
    rows, clear = [], []
    for p in _books(store):
        d = p.details or {}
        available = _available(store, p)
        if d.get("replaced_by"):
            if available > 0:
                clear.append({"name": p.name, "copies": available, "edition": d.get("edition"),
                              "advice": "sell these first or return them to the depot; don't reorder"})
            continue
        if d.get("category") not in ("textbook", "literature", "exam") and not (not d.get("category") and level_for(p)):
            continue
        rush = store.sales_qty(p.id, start=rush_start, end=rush_end)
        wanted = asked.get(p.id, 0)
        need = math.ceil(rush * factor) + wanted + p.reorder_level
        order = max(need - available, 0)
        if order:
            rows.append({"product_id": p.id, "name": p.name, "level": level_for(p), "stock": available,
                         "sold_in_last_rush": rush, "parents_asked_but_we_lacked": wanted, "order_qty": order})
    rows.sort(key=lambda r: r["order_qty"], reverse=True)
    book_ids = {p.id for p in _books(store)}
    not_stocked = sorted(({"title": t, "times": n} for t, n in asked.items() if t not in book_ids),
                         key=lambda r: r["times"], reverse=True)
    order_by = cal["resumes"] - timedelta(days=ORDER_AHEAD)
    return {"next_term": f"{cal['next_term']} term", "resumes": cal["resumes"].strftime("%A %d %B %Y"),
            "days_to_go": cal["days_to_go"], "order_by": order_by.strftime("%A %d %B"),
            "last_rush": f"{rush_start:%d %b} to {rush_end:%d %b %Y}",
            "assumption": f"The {cal['next_term']} term usually needs about {round(factor * 100)}% of the books a "
                          "new session does; parents' unmet requests are added in full.",
            "order": rows[:15], "clear_old_editions": clear, "asked_for_not_stocked": not_stocked[:8],
            "note": "Dates are the usual pattern; her schools may resume a week earlier or later."}


def _asked_for(store):
    """Product id (or the words, if we don't stock it) -> how many parents asked and we couldn't supply."""
    books = _books(store)
    by_name = {p.name.lower(): p.id for p in books}
    out = {}
    for lead in store.list_leads():
        for item in lead.get("unmet") or []:
            words = re.sub(r"\s*\((?:out of stock|short|booklist|photo)\)\s*", "", item).strip()
            pid = by_name.get(words.lower())
            if pid is None:
                s, p = (best_match(words, books, names) or [(0, None)])[0]
                pid = p.id if p and s >= MATCH else words
            out[pid] = out.get(pid, 0) + 1
    return out
