"""One loop for every incoming message.

1. Store the evidence (what was received, exactly as received).
2. Owner button taps and typed answers to pending questions are handled by
   code, without the model.
3. Otherwise run Muse Spark with the persona's prompt and tools, up to
   MAX_TOOL_CALLS, and log the whole run.
"""
import json
import logging
import time
from collections import defaultdict, deque
from datetime import date
from pathlib import Path

from app.agent import approvals
from app.agent.context import RunContext
from app.agent.tools import toolset
from app.ledger.model import Contact
from app.ledger.store import new_id
from app.normalize.amounts import normalize

log = logging.getLogger("sabi.agent")
PROMPTS = Path(__file__).parent / "prompts"
PROMPT_VERSION = "2026-10-27.1"
MAX_TOOL_CALLS = 6
HISTORY_TURNS = 6

_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))
_pending_documents: dict[str, dict] = {}   # photo whose numbers we asked the owner about


def _system_prompt(persona, store, customer_name=None):
    shop = getattr(store, "business", {}).get("name", "the shop")
    text = (PROMPTS / f"{persona}.md").read_text(encoding="utf-8")
    return text.format(shop=shop, today=date.today().strftime("%A %d %B %Y"), customer=customer_name or "unknown")


def _customer_contact(store, phone, profile_name):
    c = store.contact_by_phone(phone)
    if c is None:
        c = store.add_contact(Contact(id=new_id(), name=profile_name or f"Customer {phone[-4:]}", phone=phone))
    return c


class Agent:
    def __init__(self, store, llm, owner_phone, auto_post_limit_kobo, counter=None):
        self.store = store
        self.llm = llm
        self.owner_phone = owner_phone
        self.limit = auto_post_limit_kobo
        self.counter = counter          # stock-take counter; None = chosen from settings

    def handle(self, *, sender, channel="whatsapp", text=None, button_id=None, button_title=None,
               image_data_url=None, media_url=None, transcript_of=None, profile_name=None):
        """Process one message. Returns the context, whose outbox holds the replies to send."""
        persona = "owner" if sender == self.owner_phone else "customer"
        ctx = RunContext(store=self.store, persona=persona, sender=sender, owner_phone=self.owner_phone,
                         auto_post_limit_kobo=self.limit, image_data_url=image_data_url, llm=self.llm,
                         counter=self.counter)
        if persona == "customer":
            ctx.contact_id = _customer_contact(self.store, sender, profile_name).id

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
            if persona == "owner" and button_id and approvals.handle_button(button_id, ctx):
                return ctx
            if persona == "owner" and text and not image_data_url and approvals.handle_typed_answer(text, ctx):
                return ctx
            self._run_model(ctx, image_data_url)
        except Exception as e:  # never leave the sender without a reply
            log.exception("agent run failed")
            error = f"{type(e).__name__}: {e}"
            ctx.say("Sorry, something went wrong on my side. Please send that again.")
        finally:
            self.store.log_run(trigger=channel, persona=persona, evidence_id=ctx.evidence_id,
                               tool_calls=ctx.tool_log, reply=[o.body for o in ctx.outbox],
                               latency_ms=int((time.monotonic() - started) * 1000), error=error)
        return ctx

    def _run_model(self, ctx, image_data_url):
        tools, handlers = toolset(ctx.persona)
        customer = self.store.get_contact(ctx.contact_id).name if ctx.contact_id else None
        history = _history[ctx.sender]
        user_text = ctx.message.text if ctx.message else "(no text)"
        if ctx.message and ctx.message.text != ctx.message.original:
            user_text += f"\n\n(original words: {ctx.message.original})"
        content = [{"type": "text", "text": user_text}]
        if image_data_url:
            content.append({"type": "image_url", "image_url": {"url": image_data_url}})
        user_msg = {"role": "user", "content": content if image_data_url else user_text}

        system = _system_prompt(ctx.persona, self.store, customer)
        pending_doc = _pending_documents.pop(ctx.sender, None)
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

        if final and final.strip():
            ctx.say(final.strip())
        if ctx.document_issues:
            _pending_documents[ctx.sender] = {"document": ctx.document, "arithmetic_issues": ctx.document_issues}
        # Keep history short and text-only; images are not re-sent.
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": final or ""})

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
