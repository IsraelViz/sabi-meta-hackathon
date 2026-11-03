"""One loop for every incoming message.

1. Store the evidence (what was received, exactly as received).
2. Owner button taps and typed answers to pending questions are handled by
   code, without the model.
3. Otherwise run Muse Spark with the persona's prompt and tools, up to
   MAX_TOOL_CALLS, and log the whole run.
"""
import json
import logging
import re
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

from app import money
from app.agent import approvals, language, sales
from app.agent.context import RunContext
from app.agent.tools import toolset
from app.promo import share
from app.ledger.model import Contact
from app.ledger.store import new_id
from app.normalize.amounts import normalize

log = logging.getLogger("sabi.agent")
PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = "2026-11-01.1"
MAX_TOOL_CALLS = 6
HISTORY_TURNS = 6
MARKDOWN_BOLD = re.compile(r"\*\*(.+?)\*\*")   # WhatsApp bolds with one asterisk: **x** would show the stars
# Muse Spark Open sometimes writes a tool call as text (its own markup) instead of making it. Its words
# then promise something that didn't happen ("reserving now"), so none of that reply is sent.
LEAKED_CALL = re.compile(r"<\|(?:start|message|eom|eot|call|channel)\|>|<\w+:function_calls>|<\w+:invoke\b")
NOT_DONE = "Sorry, I couldn't finish that. Please send it again."



def _system_prompt(persona, store, customer_name=None, owner_said=None):
    business = getattr(store, "business", {})
    text = (PROMPTS / f"{persona}.md").read_text(encoding="utf-8")
    owner_note = ""
    if owner_said:
        owner_note = ("\n- The owner has already replied to this customer herself: "
                      + " / ".join(f"“{t}”" for t in owner_said)
                      + ". Stay consistent with it; if it needs something your tools can't do (like a special "
                        "price), use `hand_to_owner`.")
    from app import prefs
    return text.format(shop=business.get("name", "the shop"), today=date.today().strftime("%A %d %B %Y"),
                       language=prefs.get(store)["language"], currency=money.describe(money.of(store)),
                       money_sign=money.of(store).fmt(1_250_000),
                       customer=customer_name or "unknown", owner_note=owner_note,
                       shop_info=business.get("shop_info") or "(none given: hand questions about the shop to the owner)")


def _make_post(ctx):
    from app.promo.flow import make_promo
    result = make_promo(ctx, ctx.promo)
    if "error" in result:
        ctx.say(f"I can't make a post right now: {result['error']}")


def _send_reminders(ctx):
    from app.agent.reminders import offer_all
    offer_all(ctx)


# The 8 pm summary's buttons are handled by code: same result every time, no model call.
SUMMARY_BUTTONS = {"summary:reminders": _send_reminders, "summary:post": _make_post}


def _customer_contact(store, phone, profile_name):
    c = store.contact_by_phone(phone)
    if c is None:
        c = store.add_contact(Contact(id=new_id(), name=profile_name or f"Customer {phone[-4:]}", phone=phone))
    return c


class Agent:
    def __init__(self, store, llm, owner_phone, auto_post_limit_kobo, counter=None, promo=None, statement_link=None,
                 market=None):
        self.store = store
        self.llm = llm
        self.owner_phone = owner_phone
        self.limit = auto_post_limit_kobo
        self.counter = counter          # stock-take counter; None = chosen from settings
        self.statement_link = statement_link   # makes a link to the business statement; None = no link
        self.market = market            # product_id -> other shops' prices (pilot shops only)
        self._promo = promo             # PromoServices; None = built from settings on first use
        # Conversation state belongs to this agent (one shop), never shared across shops or demo visitors.
        self._history: dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))
        self._pending_documents: dict[str, dict] = {}   # photo whose numbers we asked the owner about

    @property
    def promo(self):
        if self._promo is None:
            from app.promo.flow import default_services
            self._promo = default_services()
        return self._promo

    def handle(self, *, sender, channel="whatsapp", text=None, button_id=None, button_title=None,
               image_data_url=None, media_url=None, transcript_of=None, profile_name=None):
        """Process one message. Returns the context, whose outbox holds the replies to send."""
        persona = "owner" if sender == self.owner_phone else "customer"
        money.use(self.store)
        ctx = RunContext(store=self.store, persona=persona, sender=sender, owner_phone=self.owner_phone,
                         auto_post_limit_kobo=self.limit, image_data_url=image_data_url, llm=self.llm,
                         counter=self.counter)
        ctx.media_source = lambda: self.promo.media
        if persona == "owner":
            ctx.promo_source = lambda: self.promo
            ctx.statement_link = self.statement_link
            ctx.market = self.market
        if persona == "customer":
            ctx.contact_id = _customer_contact(self.store, sender, profile_name).id
            self.store.touch_inbound(ctx.contact_id)   # opens WhatsApp's 24-hour free-text window

        raw = text or button_title or ""
        ctx.message = normalize(raw) if raw else None
        ctx.evidence_id = self.store.add_evidence(
            channel=channel, sender=sender, media_url=media_url, original_text=raw,
            normalized_text=ctx.message.text if ctx.message else None,
            extracted_json={"transcript_of": transcript_of} if transcript_of else None,
            model=self.llm.model if self.llm else None, prompt_version=PROMPT_VERSION)

        started = time.monotonic()
        error = None
        try:
            if persona == "customer" and share.is_stop(raw):   # the template promises STOP works
                share.set_offers(ctx, False)
                ctx.say("Done, no more offers from us. You can still message us any time to buy.")
                self._remember(ctx, raw)
                return ctx
            if persona == "customer" and sales.with_owner(self.store.get_lead(ctx.contact_id)):
                sales.relay(ctx, raw or "(sent a photo or voice note)")
                self._remember(ctx, raw, "(passed to the owner, who is handling this chat)")
                return ctx
            if persona == "owner" and button_id in SUMMARY_BUTTONS:
                SUMMARY_BUTTONS[button_id](ctx)
                self._remember(ctx, f"(tapped: {button_title or button_id})")
                return ctx
            if persona == "owner" and button_id and approvals.handle_button(button_id, ctx):
                self._remember(ctx, f"(tapped: {button_title or 'a button'})")
                return ctx
            if persona == "owner" and text and not image_data_url and approvals.handle_typed_answer(text, ctx):
                self._remember(ctx, text)
                return ctx
            self._run_model(ctx, image_data_url)
        except Exception as e:  # never leave the sender without a reply
            log.exception("agent run failed")
            error = f"{type(e).__name__}: {e}"
            ctx.say("Sorry, something went wrong on my side. Please send that again.")
        finally:
            self._localize(ctx, raw)
            self.store.log_run(trigger=channel, persona=persona, evidence_id=ctx.evidence_id,
                               tool_calls=ctx.tool_log, reply=[o.body for o in ctx.outbox],
                               latency_ms=int((time.monotonic() - started) * 1000), error=error)
        return ctx

    def _localize(self, ctx, raw):
        """Code-written messages go out in the reader's language. Only messages to the owner, and to the
        customer who wrote; never words someone approved or typed for a customer (reminders, her replies)."""
        try:
            owner_lang = language.for_owner(self.store, raw if ctx.persona == "owner" else None)
            customer_lang = language.for_customer(raw) if ctx.persona == "customer" else None
            groups = defaultdict(list)
            for o in ctx.outbox:
                if o.localized or o.template:
                    continue
                if o.to == self.owner_phone:
                    groups[owner_lang].append(o)
                elif ctx.persona == "customer" and o.to == ctx.sender:
                    groups[customer_lang].append(o)
            for lang, msgs in groups.items():
                language.localize(self.llm, msgs, lang)
        except Exception:
            log.exception("translation failed; sending English")

    def _run_model(self, ctx, image_data_url):
        if self.llm is None:
            ctx.say("The AI model isn't connected yet, so right now I can only handle button taps and "
                    "answers to my questions.")
            return
        tools, handlers = toolset(ctx.persona)
        customer = self.store.get_contact(ctx.contact_id).name if ctx.contact_id else None
        history = self._history[ctx.sender]
        user_text = ctx.message.text if ctx.message else "(no text)"
        if ctx.message and ctx.message.text != ctx.message.original:
            user_text += f"\n\n(original words: {ctx.message.original})"
        content = [{"type": "text", "text": user_text}]
        if image_data_url:
            content.append({"type": "image_url", "image_url": {"url": image_data_url}})
        user_msg = {"role": "user", "content": content if image_data_url else user_text}

        lead = self.store.get_lead(ctx.contact_id) if ctx.contact_id else None
        system = _system_prompt(ctx.persona, self.store, customer, (lead or {}).get("owner_said"))
        pending_doc = self._pending_documents.pop(ctx.sender, None)
        if pending_doc:
            # The owner is probably answering our question about a photo's numbers.
            system += ("\n\nEarlier photo (already read, numbers did not add up):\n"
                       + json.dumps(pending_doc, ensure_ascii=False)
                       + "\nUse the owner's answer to settle the figures, then record with record_batch.")
        messages = [{"role": "system", "content": system}, *history, user_msg]
        final = None
        for _ in range(MAX_TOOL_CALLS + 1):
            reply = self.llm.chat(messages, tools=tools)
            if not reply.tool_calls:
                final = reply.content
                break
            messages.append(reply.raw_message)
            for call in reply.tool_calls:
                result = self._call_tool(ctx, handlers, call)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False, default=str)})
            if len(ctx.tool_log) >= MAX_TOOL_CALLS:
                messages.append({"role": "user", "content": "(Tool limit reached. Reply to the person now.)"})
                final = self.llm.chat(messages).content
                break

        if final and LEAKED_CALL.search(final):
            log.warning("model wrote a tool call as text; not sending it: %s", final[:300])
            final = NOT_DONE
        if final and final.strip():
            ctx.say(MARKDOWN_BOLD.sub(r"*\1*", final.strip()))
            ctx.outbox[-1].localized = True     # the model already wrote it in her language
        if ctx.document_issues:
            self._pending_documents[ctx.sender] = {"document": ctx.document, "arithmetic_issues": ctx.document_issues}
        # Keep history short and text-only; images are not re-sent.
        self._remember(ctx, user_text, final)

    def _remember(self, ctx, user_text, final=None):
        """History says what was done, not just what was said, so an old message is never acted on twice."""
        said = final.strip() if final and final.strip() else " / ".join(o.body for o in ctx.outbox if o.to == ctx.sender)
        done = [f"{c['tool']} → {c['result'].get('status')}" + (f" ({c['result']['summary']})" if c["result"].get("summary") else "")
                for c in ctx.tool_log if isinstance(c["result"], dict) and c["result"].get("status")]
        done += [f"recorded: {e.kind} {e.memo}" for e in ctx.committed if not ctx.tool_log]
        note = f"\n[Done, nothing left to do for that message: {'; '.join(done)}]" if done else ""
        history = self._history[ctx.sender]
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": (said[:600] + note).strip()})

    def _call_tool(self, ctx, handlers, call):
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            result = {"error": "arguments were not valid JSON"}
        else:
            fn = handlers.get(call.name)
            if fn is None:
                result = {"error": f"tool {call.name} is not available"}
            else:
                try:
                    result = fn(ctx, **args)
                except TypeError as e:
                    result = {"error": f"bad arguments: {e}"}
        ctx.tool_log.append({"tool": call.name, "args": call.arguments, "result": result})
        return result
