"""Sales: leads kept by code, handing a chat to the owner and back, follow-ups, and the owner's view."""
from datetime import timedelta

from app.agent import sales
from app.agent.orchestrator import Agent
from app.ledger.reports import daily_summary_text, sales_pipeline
from app.ledger.store import now
from tests.test_agent import CUSTOMER, LIMIT, OWNER, ScriptedLLM, tool_results


def ask_and_order(store):
    llm = ScriptedLLM([
        [("find_product", {"query": "indomie carton"}), ("find_product", {"query": "golden morn"})],
        "Indomie is ₦12,500 a carton. We don't sell Golden Morn.",
        [("create_order", {"items": [{"product_id": "p-indomie", "qty": 2}]})],
        "That's ₦25,000. Transfer to ...",
    ])
    agent = Agent(store, llm, OWNER, LIMIT)
    agent.handle(sender=CUSTOMER, text="How much indomie? You get golden morn?", profile_name="Chioma A")
    agent.handle(sender=CUSTOMER, text="Give me 2 carton")
    return agent, llm


def test_chat_becomes_a_lead_without_the_model_deciding(store):
    agent, llm = ask_and_order(store)
    contact = store.contact_by_phone(CUSTOMER)
    lead = store.get_lead(contact.id)
    assert lead["stage"] == "ordered" and lead["interests"] == ["p-indomie"]
    assert lead["unmet"] == ["golden morn"]

    s = sales_pipeline(store)
    assert (s["chats"], s["ordered"], s["paid"]) == (1, 1, 0)
    assert s["missed"] == [{"item": "golden morn", "times": 1}]
    text = daily_summary_text(store)
    assert "Customer chats: 1 (1 ordered, 0 paid)." in text and "Asked for but not available: golden morn." in text


def test_paid_order_closes_the_deal_and_an_expired_one_is_lost(store):
    agent, llm = ask_and_order(store)
    contact = store.contact_by_phone(CUSTOMER)
    order_id = store.get_lead(contact.id)["order_id"]

    store.update_order(order_id, expires_at=now() - timedelta(minutes=1))
    assert sales_pipeline(store)["lost"] == 1
    assert "1 didn't pay" in daily_summary_text(store)

    store.update_order(order_id, expires_at=now() + timedelta(hours=1))
    llm.turns = [[("record_payment_claim", {"order_id": order_id, "amount_naira": 25000})], "Thanks!"]
    claim = agent.handle(sender=CUSTOMER, text="I don pay")
    ask = next(o for o in claim.outbox if o.to == OWNER)
    agent.handle(sender=OWNER, button_id=ask.buttons[0]["id"])
    assert sales_pipeline(store)["paid"] == 1 and sales_pipeline(store)["paid_kobo"] == 2_500_000


def test_out_of_stock_interest_is_noted(store):
    store.add_order("c-tunde", [{"product_id": "p-sugar", "qty": 5, "unit_price_kobo": 1_650_000}], 8_250_000)
    llm = ScriptedLLM([[("find_product", {"query": "sugar"})], "Sorry, sugar is finished."])
    Agent(store, llm, OWNER, LIMIT).handle(sender=CUSTOMER, text="una get sugar?")
    assert tool_results(llm)[0]["matches"][0]["in_stock"] is False
    assert store.get_lead(store.contact_by_phone(CUSTOMER).id)["unmet"] == ["Sugar bag (out of stock)"]


def test_handoff_relays_both_ways_until_the_owner_hands_back(store):
    llm = ScriptedLLM([
        [("hand_to_owner", {"reason": "wants a discount on 10 cartons", "summary": "Asking ₦11,000 each for 10 Indomie"})],
        "I've passed this to the owner. She'll reply here soon.",
    ])
    agent = Agent(store, llm, OWNER, LIMIT)
    ctx = agent.handle(sender=CUSTOMER, text="If I buy 10 carton, you go do 11k?", profile_name="Chioma A")
    to_owner = next(o for o in ctx.outbox if o.to == OWNER)
    assert "needs you: wants a discount" in to_owner.body
    assert [b["title"] for b in to_owner.buttons] == ["Reply", "Let Sabi handle it"]
    assert any(o.to == CUSTOMER and "owner" in o.body for o in ctx.outbox)

    # While the owner has the chat, Sabi relays instead of answering: no model call.
    calls = len(llm.seen)
    relayed = agent.handle(sender=CUSTOMER, text="Abeg reply me fast")
    assert len(llm.seen) == calls and not any(o.to == CUSTOMER for o in relayed.outbox)
    latest = next(o for o in relayed.outbox if o.to == OWNER)
    assert latest.body == "💬 Chioma A: Abeg reply me fast"
    # The older message's buttons are retired; only the newest works.
    old = agent.handle(sender=OWNER, button_id=to_owner.buttons[0]["id"])
    assert "already been answered" in old.outbox[0].body

    tap = agent.handle(sender=OWNER, button_id=latest.buttons[0]["id"])
    assert "Type your reply to Chioma A" in tap.outbox[0].body
    sent = agent.handle(sender=OWNER, text="Ok 11,500 each for 10. Come tomorrow")
    assert [(o.to, o.body) for o in sent.outbox][0] == (CUSTOMER, "Ok 11,500 each for 10. Come tomorrow")
    done = sent.outbox[1]
    assert "Sent to Chioma A" in done.body and [b["title"] for b in done.buttons] == ["Reply again", "Let Sabi handle it"]
    # Her next ordinary message is bookkeeping again, not another reply to the customer.
    llm.turns = ["Noted."]
    assert all(o.to == OWNER for o in agent.handle(sender=OWNER, text="how my sales today").outbox)

    back = agent.handle(sender=OWNER, button_id=done.buttons[1]["id"])
    assert "take care of Chioma A again" in back.outbox[0].body
    llm.turns = ["Good morning! How can I help?"]
    agent.handle(sender=CUSTOMER, text="Good morning")
    system = llm.seen[-1][0][0]["content"]
    assert "“Ok 11,500 each for 10. Come tomorrow”" in system   # Sabi knows what the owner promised
    assert sales_pipeline(store)["waiting_for_owner"] == []


def test_customer_prompt_has_the_shop_info(store):
    llm = ScriptedLLM(["We're at Oke-Arin Market."])
    Agent(store, llm, OWNER, LIMIT).handle(sender=CUSTOMER, text="Where una dey?")
    system = llm.seen[0][0][0]["content"]
    assert "Oke-Arin Market" in system and "hand_to_owner" in system
    assert "hand_to_owner" in llm.seen[0][1]


def test_one_follow_up_inside_the_24_hour_window(store):
    agent, llm = ask_and_order(store)
    assert sales.followups_due(store) == []                      # too soon
    later = now() + timedelta(hours=3, minutes=1)
    [(phone, text)] = sales.followups_due(store, at=later)
    assert phone == CUSTOMER and "2 × Indomie carton, ₦25,000" in text and "DEMO BANK" in text
    assert sales.followups_due(store, at=later + timedelta(hours=1)) == []   # only once
    contact = store.contact_by_phone(CUSTOMER)
    store.save_lead(contact.id, followed_up_at=None)
    assert sales.followups_due(store, at=now() + timedelta(hours=23, minutes=30)) == []   # window closing


def test_quiet_lead_is_asked_once_about_what_they_wanted(store):
    llm = ScriptedLLM([[("find_product", {"query": "groundnut oil"})], "₦11,000 for 5L."])
    Agent(store, llm, OWNER, LIMIT).handle(sender=CUSTOMER, text="how much groundnut oil", profile_name="Ada")
    [(_, text)] = sales.followups_due(store, at=now() + timedelta(hours=4))
    assert text.startswith("Hi Ada, still interested in the Groundnut oil 5L? It's ₦11,000 per keg")


def test_no_follow_up_while_the_owner_has_the_chat(store):
    llm = ScriptedLLM([[("find_product", {"query": "oil"}), ("hand_to_owner", {"reason": "complaint", "summary": "oil leaked"})],
                       "Passed to the owner."])
    Agent(store, llm, OWNER, LIMIT).handle(sender=CUSTOMER, text="the oil wey I buy leak")
    assert sales.followups_due(store, at=now() + timedelta(hours=4)) == []


def test_demo_skips_ahead_to_the_follow_up():
    from tests.test_demo import client_for, registry

    llm = ScriptedLLM([[("find_product", {"query": "peak milk"})], "Peak milk is ₦700 a tin."])
    c = client_for(registry(llm))
    sid = c.post("/demo/api/session").json()["id"]
    first = c.post(f"/demo/api/{sid}/later").json()["messages"]
    assert first[0]["to"] == "owner" and "No customer needs a follow-up" in first[0]["body"]
    c.post(f"/demo/api/{sid}/message", json={"who": "customer", "text": "how much peak milk"})
    [nudge] = c.post(f"/demo/api/{sid}/later").json()["messages"]
    assert nudge["to"] == "customer" and nudge["body"].startswith("Hi Mama Tunde, still interested in the Peak milk tin?")


def test_customer_sees_a_short_order_ref_and_can_pay_against_it(store):
    agent, llm = ask_and_order(store)
    created = tool_results(llm)[-1]
    assert created["ref"] == created["order_id"][:6].upper()
    llm.turns = [[("record_payment_claim", {"order_id": created["ref"], "amount_naira": 25000})], "Thanks!"]
    claim = agent.handle(sender=CUSTOMER, text=f"I don pay for {created['ref']}")
    assert store.get_order(created["order_id"])["status"] == "claimed_paid"
    assert f"for order {created['ref']}." in next(o for o in claim.outbox if o.to == OWNER).body


def test_markdown_bold_becomes_whatsapp_bold(store):
    llm = ScriptedLLM(["Your order ref is **586E70**. Pay to **DEMO BANK**."])
    ctx = Agent(store, llm, OWNER, LIMIT).handle(sender=CUSTOMER, text="ref?")
    assert ctx.outbox[-1].body == "Your order ref is *586E70*. Pay to *DEMO BANK*."
