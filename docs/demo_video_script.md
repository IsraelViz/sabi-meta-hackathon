# Sabi: demo video script

**Length:** about 3 minutes. Check the submission form for the real limit before you record. If you need to cut, drop scene 6 first, then shorten scene 5.
**Deadline:** 5 November 2026, 6:30 AM IST.
**Recording:** the public demo page (`/demo/`) shows the owner's phone, the customer's phone and the live books side by side, so the whole video can be filmed in one browser window. If WhatsApp is connected by then, film scene 3 on a real phone: it is the strongest shot.

The voiceover is about 430 words, a comfortable pace for 3 minutes. Lines in *italics* are what you type or say into the demo. Every scene below was run live on Muse Spark Open (Baseten) and behaved as described.

---

## Before you record

- [ ] The server is running with `LLM_BACKEND=baseten`, and the demo shows `muse-spark-open-1026` with a microphone button.
- [ ] Press **↺ New demo** so the books start from the seeded week.
- [ ] Send one throwaway message first: the first model call of a session is the slowest.
- [ ] Have [`docs/demo_price_list.jpg`](demo_price_list.jpg) ready for scene 2, or a photo of a real handwritten price list (better still).
- [ ] Record each scene as its own clip. Replies take 3 to 18 seconds, so trim the waiting and add a small "waiting trimmed" note on screen.
- [ ] Set the browser zoom to 110–125%, and use a quiet room for the voice note.

---

## Scene 1: the problem (0:00 to 0:15)

**Screen:** title card "Sabi: the books keep themselves", then the demo page.

> Forty million small businesses in Nigeria run on WhatsApp and a paper notebook. With no records, a trader can't get a loan, can't see what she's losing, and can't answer every customer. Sabi is an agent that lives in her WhatsApp, built on Meta's Muse Spark Open.

---

## Scene 2: from paper to books in one photo (0:15 to 0:35)

**Screen:** on the owner phone, attach `demo_price_list.jpg` with the caption *"These are my prices, set up my shop"*. Sabi lists "➕ Golden Morn 900g: ₦3,200…, 12 in stock at ₦2,800", "➕ Milo 400g tin…", and "✏️ Peak milk tin: ₦700 → ₦750". Tap **Add all**.

> Getting started is one photo. Sabi reads her price list, spots what's new and what changed, and with one tap her products, prices and opening stock are in the books.

---

## Scene 3: a voice note, and a second opinion on credit (0:35 to 1:05)

**Screen:** on the owner phone, tap the mic and say:

> *"I sell two carton Indomie give Mama Tunde, she go pay next week."*

Sabi replies: **"⚠️ Before you give credit: Mama Tunde already owes ₦54,000, 2 days late. Still give ₦25,000 on credit?"** with the buttons Yes, give credit / Paid cash now / Cancel. Tap **Yes, give credit**: "Done. Sale: 2 carton Indomie… on credit, due Thu…". The books panel updates.

> She talks the way she talks: Pidgin, voice notes. Sabi turns it into a proper double-entry sale. But first it checks Mama Tunde's record and warns her before she gives more credit to someone who's already late. The model only proposes; code checks every entry, and the owner decides.

---

## Scene 4: it speaks first (1:05 to 1:20)

**Screen:** owner: *"I sell 4 crate egg, she paid cash"*. If Sabi asks "Has this been paid?", tap **Paid cash**. Then: **"📦 Egg crate: only 3 crates left. At your pace… Order 7?"** Tap **Draft order (7)**, and Sabi writes the message for the supplier.

> Sabi watches the stock as she sells. When something is about to run out, it says so before she notices, works out how many to order from her selling pace, and drafts the message to her supplier.

---

## Scene 5: it sells while she's busy (1:20 to 2:05)

**Screen:** on the customer phone (Mama Tunde), send these one at a time:

1. *"Good morning. How much be una Indomie carton? You get Golden Morn?"* Sabi gives the real prices, including the Golden Morn added from the photo.
2. *"If I buy 10 carton you go do 11k each?"* Sabi hands the chat to the owner. **Cut to the owner phone:** "🙋 Mama Tunde needs you: wants a discount…". Tap **Reply**, type *"I fit do 11,800 for 10. Come pick tomorrow"*, then tap **Let Sabi handle it**.
3. *"Ẹ kú àárọ̀. Ẹ̀ló ni Golden Morn? Mo fẹ́ ra méjì."* Sabi answers in Yoruba and creates the order, with a short ref and the payment details.

> Customers message the same number and get an answer in seconds, in their own language: English, Pidgin, Yoruba, Igbo or Hausa. Prices come only from her books. When someone haggles, Sabi hands the chat to the owner, then takes it back. And a payment screenshot never counts until she has seen the bank alert.

---

## Scene 6: the evening (2:05 to 2:20)

**Screen:** press **🌙 8 pm summary**. Point at "Restock soon…", "Customer chats…" and "Sabi today: N entries recorded, N customer messages answered (about N minutes of your time)."

> At eight she gets her day in one message: sales, profit, who owes her, what to restock, and how much time Sabi saved her.

---

## Scene 7: the payoff, a loan-ready statement (2:20 to 2:50)

**Screen:** owner: *"I wan apply for loan for LAPO microfinance. Wetin I go show them?"* Sabi replies with the key figures and a link. Click it to open the statement: sales, profit and margin, month by month, cash, stock, credit collected, best sellers, and "How these figures were kept". Scroll slowly and end on **Print / save as PDF**.

> This is why it matters. Most traders are turned down for credit because they have no records. Sabi has been keeping them all along. One message, and she has a statement a lender can read, with every number backed by the voice note, message or receipt it came from. She can share the link for seven days, or print it.

---

## Scene 8: close (2:50 to 3:00)

**Screen:** a title card with the demo link and the GitHub repo.

> Sabi: bookkeeper, sales assistant and loan officer's best friend, in the chat app she already uses. Built on Muse Spark Open.

---

## Fallbacks on the day

- **Voice is slow or fails:** type the same sentence. The bookkeeping is the point.
- **The model replies differently:** keep going if the outcome is right (a sale recorded, a question asked, a handoff). Don't chase exact wording.
- **Restock doesn't fire:** it fires when a product drops to its reorder level. Eggs start at 7 with a reorder level of 5, so selling 4 always crosses it. Pressing **↺ New demo** resets the stock.
- **The demo runs out of messages:** press **↺ New demo**. Each session allows 40 messages.
- **The tunnel URL changed:** record against `http://127.0.0.1:8000/demo/` locally, and put the real link on the closing card.

## Claims to keep accurate

- The statement says "7 days of records" for the demo shop, and warns that lenders look for three months. Say "a statement that grows every day", not "a three-month statement".
- Real Instagram posting, the WhatsApp reminder template and reading Instagram or Facebook through the Graph API haven't been tried live; they need Meta tokens.
- "Forty million" is the commonly quoted figure for Nigerian MSMEs (SMEDAN). Drop it if you'd rather not cite a number.
