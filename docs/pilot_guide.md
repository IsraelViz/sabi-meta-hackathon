# Running the pilot with real testers

Each tester gets her own shop with empty books. She uses the owner app's **Chat** tab to talk to Sabi, and she can share a **customer chat link** (`/c/<shop>`) with her customers. Her books are kept apart from every other shop's.

## Before the testers start

1. **Keep the laptop on and awake** all day, plugged in. Go to Settings → System → Power, and set sleep to *Never* while plugged in. Closing the lid can also put it to sleep.
2. **Start the server and the tunnel**, and leave both running:

   ```bash
   python scripts/dev_server.py
   ```

   ```bash
   cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000
   ```

   The tunnel prints an address like `https://something.trycloudflare.com`. That is the address of the pilot.
3. Open `https://<tunnel>/admin/` and enter the admin key (`ADMIN_TOKEN` in `.env`).
4. **Create one shop per tester**: her shop name, her first name and what kind of seller she is (shop, online seller or bookseller). Then tap **Send on WhatsApp** to send her app link. That link is her private key, so send it only to her.

## What to tell each tester

- Open the link on your phone. A welcome page explains what Sabi keeps; tap **OK, let's start**.
- Add Sabi to your home screen: in Chrome, tap **⋮** then **Add to Home screen**; on an iPhone, tap **Share** then **Add to Home Screen**.
- Start with a photo of your price list, or type your products and prices.
- Then tell Sabi what you sell or buy, the way you'd say it: type it, or send a voice note.
- To let customers chat with Sabi, go to **Shop ⚙** and share your customer chat link (for example in your WhatsApp status or Instagram bio).
- If a reply is wrong, tap **👎** under it. For anything else, tap **✍** at the top.
- Sabi works in any language. A tester can write in French or Swahili, or choose her language in **Shop ⚙ → Settings**. A tester outside Nigeria can choose her money there too (F CFA, cedi, shilling, rand).
- Things to try: forward a bank alert and ask "is it real?"; tap any entry to see **why** it's in the books; ask "any advice for my business?"; tap 🔊 to hear a reply.

## During the day

- The admin page refreshes itself every 30 seconds. For each shop it shows: messages from the owner and from customers, entries, products, average reply time, errors, feedback, and when she was last active. Tap **Chats** to read every conversation, with the tools Sabi used and how long each reply took.
- If a tester loses her link or it reaches the wrong person, tap **New key**. Her old link stops working.
- Each shop gets its summary in its Chat at 8 pm (Lagos time) by default. A tester can change the time, switch to once a week, or turn it off in **Shop ⚙ → Settings**, or by telling Sabi.
- **Notifications:** ask each tester to tap **Shop ⚙ → Turn on notifications**, so her phone tells her when a customer needs her. On an iPhone this only works after Sabi is added to the home screen and opened from there.

## If the tunnel address changes

A quick tunnel gets a new address every time it restarts, and it can also expire on its own (this happened during testing). When it changes:

1. Restart `cloudflared`.
2. On the admin page, tap **New key** for each shop and send the new links.

Installed home-screen apps keep pointing at the old address, so testers should open the new link and add it to the home screen again.

A named Cloudflare tunnel keeps the same address. It needs a free Cloudflare account and a domain.

## Limits and data

- **Daily limit:** each shop can send 400 messages in 24 hours, owner and customers together (`SHOP_DAILY_MESSAGES`). Each customer can send 60.
- **Where the data is:** in `%LOCALAPPDATA%\sabi-data\pg` on this laptop, outside OneDrive. It survives restarts.
- **Deleting a shop:** a tester can delete her shop and everything in it from **Shop ⚙**.

## After the pilot: results for the submission

The admin page has the numbers:

- each tester's messages, entries and customers
- average reply time
- errors
- the feedback testers wrote

Read the chats to collect quotes and to find the mistakes worth fixing. When a tester agrees, tick **Show on the public results page** under her feedback.

The public page `https://<tunnel>/results` shows the totals, with no names or chats, ready to link in the submission. For accuracy, `python scripts/benchmark.py` runs 64 trader messages in seven languages through the real model and writes `docs/benchmark.md`.
