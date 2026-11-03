# Hosting Sabi for free: Render + Supabase

Sabi runs on Render's free tier, with the books in a free Supabase Postgres database.
Render wipes its disk on every restart and deploy, so everything that must last is in
Supabase: the books, the pilot shops, and every saved picture and voice note
(`app/media/durable.py` writes each file to a `saved_files` table and puts it back on
disk at start-up).

The blueprint puts the app in Render's **Virginia (US)** region: Meta's model API (SAM,
Muse Image) refuses calls from Render's Frankfurt region. The database can stay in Frankfurt.

Free-tier limits worth knowing:
- Render sleeps after 15 minutes without traffic; the next message takes about a minute.
  A free pinger (step 5) keeps it awake. 750 free hours a month covers one service all month.
- Supabase pauses a free project after a week without use. Sabi talking to it counts as use.

## 1. Supabase: the database
1. Sign up at **supabase.com** → **New project**. Name `sabi`, region **Central EU (Frankfurt)**.
   Set a database password and keep it.
2. When it is ready: **Connect** (top of the project page) → **Connection string** →
   choose **Session pooler** (Render can't reach the direct connection, which is IPv6 only).
3. Copy the string and put your password in place of `[YOUR-PASSWORD]`. It looks like
   `postgresql://postgres.abcd:PASSWORD@aws-0-eu-central-1.pooler.supabase.com:5432/postgres`.
   This is `DATABASE_URL` (and `SHOPS_DB`).

Sabi creates its tables on first start and loads the demo shop with a week of trading.

## 2. GitHub
Render deploys from the GitHub repo, so the latest code must be pushed.

## 3. Render: the app
1. Sign up at **render.com** with GitHub, and allow it to see the `sabi-meta-hackathon` repo.
2. **New → Blueprint** → pick the repo. Render reads `render.yaml` and asks for each secret.
   Copy them from your `.env`; `DATABASE_URL` and `SHOPS_DB` are the Supabase string.
   Leave `PUBLIC_BASE_URL` empty for now.
3. **Apply**. The first build takes 5–10 minutes. When it says **Live**, copy the address at the
   top, e.g. `https://sabi-xyz.onrender.com`.
4. **Environment** → set `PUBLIC_BASE_URL` to that address (no `/` at the end) → **Save**.
   Render redeploys.
5. Check: open `https://sabi-xyz.onrender.com/health`; it shows `"ok": true`.

## 4. Meta: point WhatsApp at Render
developers.facebook.com → your app → **WhatsApp → Configuration → Webhook → Edit**:
- Callback URL: `https://sabi-xyz.onrender.com/webhook/whatsapp`
- Verify token: your `WA_VERIFY_TOKEN`

**Verify and save**, then under Webhook fields subscribe to **messages**.

## 5. Keep it awake
**uptimerobot.com** (free) → **New monitor** → HTTP(s), URL `https://sabi-xyz.onrender.com/health`,
every 5 minutes.

## Updating
Push to GitHub; Render deploys the new code by itself. The books and pictures stay in Supabase.

## The WhatsApp token
The token from "Generate token" lasts 24 hours. For a token that doesn't expire, make a system user
in business.facebook.com → Settings → Users → System users, give it the app and the WhatsApp account
with full control, and generate a token with `whatsapp_business_messaging` and
`whatsapp_business_management`. Put it in Render's `WA_ACCESS_TOKEN`.
