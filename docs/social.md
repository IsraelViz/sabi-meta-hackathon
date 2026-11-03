# Posting to Instagram, Facebook and WhatsApp

When the owner taps **Post** under a flyer, Sabi publishes it on the shop's Facebook Page and
Instagram account (`app/promo/publish.py`). It then asks where on WhatsApp it should go
(`app/promo/share.py`):

- **My Status**: Sabi sends her the flyer with its caption, and she forwards it to her Status
  (tap and hold → Forward → My status). WhatsApp's API cannot post to a Status itself.
- **Recent customers**: people who messaged the shop in the last 24 hours, when WhatsApp
  allows free messages. Anyone who said STOP is left out.
- **Offer list**: customers who said yes to offers. Those outside the 24-hour window get the
  approved `new_arrival` template, with the flyer on top. Meta charges a small fee per message.

She sees who will get it and taps **Send** first. A customer who replies STOP comes off the list
straight away, with no model call.

## 1. A Facebook Page
1. On facebook.com, open **Pages → Create new Page**. Use the shop's name, category **Bookstore**.
2. In **business.facebook.com → Settings → Accounts → Pages → Add → Add a Page**, add it
   to the **Double Sales** portfolio.

## 2. An Instagram professional account, linked to the Page
1. Create the shop's Instagram account in the app.
2. **Settings → Account type and tools → Switch to professional account → Business**, category Bookstore.
3. Link it to the Page: in the Page's **Settings → Linked accounts → Instagram → Connect account**.
4. In **Business settings → Accounts → Instagram accounts → Add**, add it to Double Sales.

## 3. Let the Sabi app post
1. Open **developers.facebook.com → Sabi → Use cases → Add use case**, and add:
   - **Manage everything on your Page**;
   - **Manage messaging & content on Instagram** (Instagram API with Facebook Login).
2. In each use case's **Customize → Permissions**, make sure these are added:
   - `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`;
   - `instagram_basic`, `instagram_content_publish`.

## 4. Give sabi-bot the Page and the Instagram account
1. In **Business settings → Users → System users → sabi-bot → Assign assets**:
   - **Pages**: the shop's Page, with full control.
   - **Instagram accounts**: the shop's account, with full control.
2. Click **Generate new token**, choose app **Sabi**, set expiry to **Never**, and tick:
   - `whatsapp_business_messaging`, `whatsapp_business_management`, `business_management`;
   - the five permissions from step 3.

   Copy the token. Don't paste it into a chat.

## 5. Connect
In PowerShell, in the `sabi` folder:

```powershell
& "$env:LOCALAPPDATA\sabi-venv\Scripts\python.exe" scripts\connect_social.py
```

Paste the token when asked. It is read hidden and never shown. The script finds the Page and its
Instagram account and writes `FB_PAGE_ID`, `IG_USER_ID` and `PAGE_ACCESS_TOKEN` into `.env`.

Then open **Render → sabi-us → Environment** and add the same three values.
`SHOP_WHATSAPP_DISPLAY` (e.g. `0701 506 5065`) prints the shop's number on flyers.
Save, and Render redeploys.

## 6. The offers template (once)
```powershell
& "$env:LOCALAPPDATA\sabi-venv\Scripts\python.exe" scripts\create_promo_template.py --submit --app-id 2598994910582670
```
Meta reviews it, usually within minutes to a day; check WhatsApp Manager → Message templates.
**Recent customers** and **My Status** work without it.

## 7. Try it
On WhatsApp, send a photo of a book with "make a post for this". Tap **Post**, then
check the Page and Instagram, then tap **My Status**.
