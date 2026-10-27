"""Read-only API for the owner app.

Every route needs the owner's token (OWNER_APP_TOKEN) as a Bearer header.
With no token configured, only requests from this machine are allowed, for
local development. Media (voice notes, photos, flyer files) is served through
the same check: it is the owner's private evidence, never a public URL.
"""
import hmac
import mimetypes
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse

from app.ledger import reports

LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
HEADLINE_ACCOUNTS = {          # the one number a trader cares about, per kind of entry
    "sale": ("sales", "credit"), "purchase": ("inventory", "debit"), "payment_in": ("ar", "credit"),
    "payment_out": ("ap", "debit"), "drawing": ("drawings", "debit"), "stock_loss": ("stock_loss", "debit"),
    "opening": ("inventory", "debit"),
}


def require_owner(request: Request):
    token = request.app.state.owner_token
    if not token:
        if request.client and request.client.host in LOCAL_HOSTS:
            return
        raise HTTPException(401, "set OWNER_APP_TOKEN to use the app from another device")
    given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), token.encode()):
        raise HTTPException(401, "wrong or missing token")


router = APIRouter(prefix="/api", dependencies=[Depends(require_owner)])


def _store(request: Request):
    return request.app.state.store


ITEM_ACCOUNT = {"sale": "sales", "purchase": "inventory", "stock_loss": "stock_loss", "opening": "inventory"}


def _shown(entry, store):
    """A reversal is shown with the amount and items of the entry it cancels."""
    if entry.kind == "reversal" and entry.reverses_id:
        return store.get_entry(entry.reverses_id) or entry
    return entry


def headline_kobo(entry):
    if entry.kind == "expense":
        return sum(l.debit_kobo for l in entry.lines if l.account not in ("cash", "bank"))
    account, side = HEADLINE_ACCOUNTS.get(entry.kind, (None, None))
    if account:
        return sum(getattr(l, f"{side}_kobo") for l in entry.lines if l.account == account)
    return entry.total_debits


def _names(store):
    products = {p.id: p.name for p in store.list_products()}
    contacts = {c.id: c.name for c in store.list_contacts()}
    accounts = {code: a["name"] for code, a in store.accounts.items()}
    return products, contacts, accounts


def _entry_summary(e, names, store, reversed_by=None):
    products, contacts, _ = names
    shown = _shown(e, store)
    item_account = ITEM_ACCOUNT.get(shown.kind)
    items = [f"{l.qty} × {products.get(l.product_id, '?')}" for l in shown.lines
             if l.account == item_account and l.qty and l.product_id]
    return {"id": e.id, "seq": e.seq, "date": e.entry_date, "kind": e.kind, "memo": e.memo, "status": e.status,
            "amount_kobo": headline_kobo(shown), "contact": contacts.get(e.contact_id), "contact_id": e.contact_id,
            "items": items, "due_date": e.due_date, "created_by": e.created_by, "reverses_id": e.reverses_id,
            "reversed_by": reversed_by, "has_evidence": bool(e.evidence_id)}


def _reversals(entries):
    return {e.reverses_id: e.id for e in entries if e.reverses_id}


@router.get("/summary")
def summary(request: Request):
    store = _store(request)
    debt = reports.debtors(store)
    entries = [e for e in store.list_entries() if e.kind != "opening"]
    names = _names(store)
    rev = _reversals(entries)
    return {
        "shop": getattr(store, "business", {}).get("name", "My shop"),
        "today": reports.profit_and_loss(store, "today"),
        "week": reports.profit_and_loss(store, "week"),
        "cash": reports.cash_position(store),
        "owed_total_kobo": sum(d["owes_kobo"] for d in debt),
        "debtors": debt[:5],
        "low_stock": reports.low_stock(store),
        "pending_questions": len(store.pending_approvals()),
        "recent": [_entry_summary(e, names, store, rev.get(e.id)) for e in reversed(entries[-6:])],
    }


@router.get("/report/{kind}")
def report(kind: str, request: Request, period: str = "today"):
    try:
        return reports.report(_store(request), kind, period)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/entries")
def entries(request: Request, since_seq: int = 0, before_seq: int | None = None, limit: int = 50,
            kind: str | None = None, contact_id: str | None = None):
    """Newest first. `since_seq` is the sync cursor; `before_seq` pages back through history."""
    store = _store(request)
    all_entries = [e for e in store.list_entries(since_seq=since_seq) if e.kind != "opening"]
    rev = _reversals(all_entries)
    rows = [e for e in all_entries
            if (before_seq is None or e.seq < before_seq) and (kind is None or e.kind == kind)
            and (contact_id is None or e.contact_id == contact_id
                 or any(l.contact_id == contact_id for l in e.lines))]
    names = _names(store)
    page = list(reversed(rows))[:max(1, min(limit, 200))]
    return {"entries": [_entry_summary(e, names, store, rev.get(e.id)) for e in page],
            "next_before_seq": page[-1].seq if len(rows) > len(page) else None,
            "latest_seq": max((e.seq for e in all_entries), default=since_seq)}


@router.get("/entries/{entry_id}")
def entry_detail(entry_id: str, request: Request):
    store = _store(request)
    e = store.get_entry(entry_id)
    if e is None:
        raise HTTPException(404, "no such entry")
    names = _names(store)
    products, contacts, accounts = names
    rev = _reversals(store.list_entries())
    ev = store.get_evidence(e.evidence_id) if e.evidence_id else None
    return {
        **_entry_summary(e, names, store, rev.get(e.id)),
        "lines": [{"account": l.account, "account_name": accounts.get(l.account, l.account),
                   "debit_kobo": l.debit_kobo, "credit_kobo": l.credit_kobo, "qty": l.qty,
                   "product": products.get(l.product_id), "contact": contacts.get(l.contact_id)} for l in e.lines],
        "evidence": _evidence(ev) if ev else None,
    }


def _evidence(ev):
    media = ev.get("media_url") or ""
    return {"id": ev["id"], "created_at": ev.get("created_at"), "channel": ev.get("channel"),
            "original_text": ev.get("original_text"), "normalized_text": ev.get("normalized_text"),
            "extracted_json": ev.get("extracted_json"), "model": ev.get("model"),
            "prompt_version": ev.get("prompt_version"),
            "media": {"kind": _media_kind(media), "url": f"/api/evidence/{ev['id']}/media"}
            if media.startswith("private:") else None}


def _media_kind(media_url):
    mime = mimetypes.guess_type(media_url.removeprefix("private:"))[0] or ""
    return "audio" if mime.startswith("audio") else "image" if mime.startswith("image") else "file"


@router.get("/evidence/{evidence_id}/media")
def evidence_media(evidence_id: str, request: Request):
    ev = _store(request).get_evidence(evidence_id)
    media = (ev or {}).get("media_url") or ""
    if not media.startswith("private:"):
        raise HTTPException(404, "no stored media for this evidence")
    return _file(request.app.state.private_media_dir, media.removeprefix("private:"))


def _file(directory, name):
    base = Path(directory).resolve()
    path = (base / name).resolve()
    if base not in path.parents or not path.is_file():   # no ../ tricks
        raise HTTPException(404, "file not found")
    return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/debtors")
def debtors(request: Request):
    return reports.debtors(_store(request))


@router.get("/debtors/{contact_id}")
def debtor_statement(contact_id: str, request: Request):
    """Every receivable movement for one customer, oldest first, with a running balance."""
    store = _store(request)
    c = store.get_contact(contact_id)
    if c is None:
        raise HTTPException(404, "no such contact")
    rows, balance = [], 0
    for e in store.list_entries():
        moves = [l for l in e.lines if l.account == "ar" and l.contact_id == contact_id]
        if not moves:
            continue
        change = sum(l.debit_kobo - l.credit_kobo for l in moves)
        balance += change
        rows.append({"entry_id": e.id, "date": e.entry_date, "kind": e.kind, "memo": e.memo, "status": e.status,
                     "change_kobo": change, "balance_kobo": balance, "due_date": e.due_date})
    return {"contact": {"id": c.id, "name": c.name, "phone": c.phone}, "owes_kobo": balance, "rows": rows}


@router.get("/products")
def products(request: Request):
    store = _store(request)
    return [{"id": p.id, "name": p.name, "unit": p.base_unit, "price_kobo": p.sell_price_kobo,
             "avg_cost_kobo": p.avg_cost_kobo, "stock": store.stock_on_hand(p.id), "reserved": store.reserved(p.id),
             "reorder_level": p.reorder_level} for p in store.list_products()]


@router.get("/posts")
def posts(request: Request):
    store = _store(request)
    names = _names(store)[0]
    return [{"id": p["id"], "created_at": p["created_at"], "product": names.get(p.get("product_id")),
             "reason": p.get("reason"), "caption": p.get("caption"), "caption_source": p.get("caption_source"),
             "status": p.get("status"), "platform_ids": p.get("platform_ids"), "error": p.get("error"),
             "tools": p.get("tools"), "image": f"/api/posts/{p['id']}/image" if p.get("asset_path") else None}
            for p in store.list_posts()]


@router.get("/posts/{post_id}/image")
def post_image(post_id: str, request: Request):
    p = _store(request).get_post(post_id)
    if not p or not p.get("asset_path"):
        raise HTTPException(404, "no image")
    path = Path(p["asset_path"])
    return _file(path.parent, path.name)


@router.get("/runs")
def runs(request: Request, limit: int = 20):
    return _store(request).recent_runs(limit)


@router.get("/me")
def me(request: Request):
    return {"ok": True, "shop": getattr(_store(request), "business", {}).get("name"), "today": date.today()}
