"""How often does Sabi understand a trader's message correctly? Runs real messages through the real model.

Each case in tests/bench/cases.json is a message to the demo shop and the entries it should produce.
Every message gets a fresh copy of the demo shop, so cases don't affect each other. A case passes only
if every entry is right: kind, products and quantities, price (when said), customer or supplier,
how much was paid, cash or transfer, amount, and expense type. "asks" cases (an unclear amount)
pass if nothing is recorded and the owner is asked.

    python scripts/benchmark.py              # all cases, writes docs/benchmark.md
    python scripts/benchmark.py --limit 5
    python scripts/benchmark.py --lang French   # only the cases in one language

A case with "lang" also checks that Sabi replied in that language.

Needs the model configured in .env. It calls the model about three times per case.
"""
import argparse
import json
import statistics
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.language import detect  # noqa: E402
from app.agent.orchestrator import Agent  # noqa: E402
from app.config import settings  # noqa: E402
from app.ledger.seed import demo_store  # noqa: E402
from app.models.llm import LLMClient  # noqa: E402

CASES = Path(__file__).resolve().parents[1] / "tests" / "bench" / "cases.json"
OUT = Path(__file__).resolve().parents[1] / "docs" / "benchmark.md"
OWNER = settings.owner_phone or "owner"


def proposals(ctx):
    """What the model proposed for this message, as the tool arguments it sent."""
    out = []
    for c in ctx.tool_log:
        args = json.loads(c["args"] or "{}")
        if c["tool"] == "record_entry":
            out.append(args)
        elif c["tool"] == "record_batch":
            out += args.get("entries") or []
    # A retry after a rejection proposes the same event again: keep the last try per kind and items.
    seen = {}
    for a in out:
        seen[(a.get("kind"), tuple(sorted((i.get("product_id"), i.get("qty")) for i in a.get("items") or [])))] = a
    return list(seen.values())


def kobo(naira):
    return None if naira is None else round(float(naira) * 100)


def check(store, want, got):
    """[(field, ok)] for one expected entry against one proposal."""
    prices = {p.id: p.sell_price_kobo for p in store.list_products()}
    out = [("kind", got.get("kind") == want["kind"])]
    if "items" in want:
        g = {i.get("product_id"): i for i in got.get("items") or []}
        out.append(("items", sorted(g) == sorted(w[0] for w in want["items"])
                    and all(g[w[0]].get("qty") == w[1] for w in want["items"])))
        priced = [w for w in want["items"] if len(w) > 2]
        if priced:
            out.append(("price", all(kobo(g.get(w[0], {}).get("unit_price_naira")) == w[2] for w in priced)))
    total = sum(w[1] * (w[2] if len(w) > 2 else prices.get(w[0], 0)) for w in want.get("items") or [])
    if "contact" in want:
        c = want["contact"]
        ok = (got.get("new_contact_name") or "").lower().startswith(c[4:]) if c.startswith("new:") else got.get("contact_id") == c
        out.append(("person", ok))
    if "paid" in want:
        paid = kobo(got.get("paid_naira"))
        if paid is None and got.get("payment_account") in ("cash", "bank"):
            paid = total     # code reads "cash"/"transfer" with no amount as paid in full (validator._check_paid)
        out.append(("paid", paid == (total if want["paid"] == "full" else want["paid"])))
    if "amount" in want:
        out.append(("amount", kobo(got.get("amount_naira")) == want["amount"]))
    if "account" in want:
        out.append(("cash/bank", got.get("payment_account") == want["account"]))
    if "expense" in want:
        out.append(("expense type", got.get("expense_account") == want["expense"]))
    return out


def run_case(llm, case):
    store = demo_store()
    agent = Agent(store, llm, OWNER, settings.auto_post_limit_kobo)
    before = len(store.list_entries())
    started = time.monotonic()
    ctx = agent.handle(sender=OWNER, text=case["text"])
    seconds = time.monotonic() - started
    got = proposals(ctx)
    committed = len(store.list_entries()) - before
    if case["expect"] == "asks":
        asked = committed == 0 and (bool(store.pending_approvals()) or any(o.buttons or "?" in o.body for o in ctx.outbox))
        return {"text": case["text"], "pass": asked, "fields": [("asked first", asked)], "seconds": seconds,
                "reply": " / ".join(o.body for o in ctx.outbox)[:200]}
    fields = []
    for want in case["expect"]:
        match = [g for g in got if g.get("kind") == want["kind"]]
        best = max((check(store, want, g) for g in match), key=lambda f: sum(ok for _, ok in f), default=[("kind", False)])
        fields += best
    extra = len(got) > len(case["expect"])
    if extra:
        fields.append(("no extra entries", False))
    if case.get("lang"):
        replies = [o.body for o in ctx.outbox]
        fields.append(("replied in her language", detect(" ".join(replies)) == case["lang"]))
    return {"text": case["text"], "pass": all(ok for _, ok in fields), "fields": fields, "seconds": seconds,
            "reply": " / ".join(o.body for o in ctx.outbox)[:200], "error": None}


def languages(cases):
    other = Counter(c["lang"] for c in cases if c.get("lang"))
    return ", ".join(["English, Pidgin, Yoruba, Hausa", *(f"{n} {lang}" for lang, n in other.items())])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--lang", help="only cases in this language, e.g. French")
    args = ap.parse_args()
    if not settings.has_llm():
        sys.exit("No model configured: set the model keys in .env first.")
    llm = LLMClient()
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    if args.lang:
        cases = [c for c in cases if c.get("lang") == args.lang]
    cases = cases[: args.limit]
    results = []
    for i, case in enumerate(cases, 1):
        try:
            r = run_case(llm, case)
        except Exception as e:   # one bad call shouldn't lose the run
            r = {"text": case["text"], "pass": False, "fields": [("ran", False)], "seconds": 0, "reply": "", "error": str(e)}
        results.append(r)
        print(f"{i:>2}/{len(cases)} {'PASS' if r['pass'] else 'FAIL'} {r['seconds']:5.1f}s  {case['text'][:70]}", flush=True)

    passed = sum(r["pass"] for r in results)
    field_total, field_ok = Counter(), Counter()
    for r in results:
        for f, ok in r["fields"]:
            field_total[f] += 1
            field_ok[f] += ok
    times = [r["seconds"] for r in results if r["seconds"]]
    lines = [
        "# Sabi accuracy benchmark", "",
        f"Run on {date.today():%d %B %Y} with `{llm.model}`, prompt version from `app/agent/orchestrator.py`.",
        f"{len(results)} real-style trader messages ({languages(cases)}), each against a fresh copy of the demo shop.",
        "", f"**{passed} of {len(results)} messages fully right ({round(100 * passed / len(results))}%).** "
            f"A message counts only if every field of every entry is right.", "",
        f"Reply time: median {statistics.median(times):.1f} s, slowest {max(times):.1f} s." if times else "", "",
        "| Field | Right | Checked | % |", "|---|---:|---:|---:|",
        *[f"| {f} | {field_ok[f]} | {n} | {round(100 * field_ok[f] / n)}% |" for f, n in field_total.most_common()],
        "", "Wrong fields never reach the books unchecked: code validates every proposal, and anything unusual "
            "is asked before it is recorded. This measures how often the owner has to correct or answer Sabi.", "",
        "## By language", "",
        "| Language | Fully right | Messages |", "|---|---:|---:|",
        *[f"| {lang} | {sum(r['pass'] for c, r in zip(cases, results) if (c.get('lang') or 'English, Pidgin, Nigerian') == lang)} "
          f"| {n} |" for lang, n in Counter(c.get("lang") or "English, Pidgin, Nigerian" for c in cases).items()],
        "", "## Misses", "",
        *[f"- “{r['text']}”: wrong {', '.join(f for f, ok in r['fields'] if not ok)}"
          + (f" (error: {r['error']})" if r.get("error") else "") for r in results if not r["pass"]],
        "", "Cases: `tests/bench/cases.json`. Run again: `python scripts/benchmark.py`.", ""]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{passed}/{len(results)} passed. Wrote {OUT}")


if __name__ == "__main__":
    main()
