"""Connect the shop's Facebook Page and Instagram account (run once, in your own terminal).

You paste a system-user token (it is read hidden, never printed). The script finds the Pages
that token can manage, takes the Page's own token and its linked Instagram professional
account, checks both, and writes FB_PAGE_ID, IG_USER_ID and PAGE_ACCESS_TOKEN into .env.
Copy the same three into Render's Environment afterwards. See docs/social.md.

    python scripts/connect_social.py
"""
import getpass
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
GRAPH = "https://graph.facebook.com/v21.0"
NEEDED = {"pages_show_list", "pages_read_engagement", "pages_manage_posts", "instagram_basic",
          "instagram_content_publish"}


def get(path, token, **params):
    r = httpx.get(f"{GRAPH}/{path}", params={**params, "access_token": token}, timeout=30)
    if r.status_code >= 400:
        sys.exit(f"Meta said no to {path}: {r.json().get('error', {}).get('message', r.text)[:300]}")
    return r.json()


def save_env(values):
    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        text = re.sub(rf"(?m)^{key}=.*$", line, text) if re.search(rf"(?m)^{key}=", text) else text.rstrip("\n") + f"\n{line}\n"
    env.write_text(text, encoding="utf-8")


def main():
    token = getpass.getpass("Paste the system-user token (hidden), then Enter: ").strip()
    if not token:
        sys.exit("No token given.")
    granted = {p["permission"] for p in get("me/permissions", token).get("data", []) if p.get("status") == "granted"}
    missing = NEEDED - granted
    if missing and granted:
        print("This token is missing: " + ", ".join(sorted(missing)) + ". Generate it again with those ticked.")
    pages = get("me/accounts", token, fields="id,name,access_token,instagram_business_account{id,username}").get("data", [])
    if not pages:
        sys.exit("This token can't see any Facebook Page. In Business settings → System users → sabi-bot → "
                 "Assign assets, give it the Page with full control, then make a new token.")
    for i, p in enumerate(pages, 1):
        ig = (p.get("instagram_business_account") or {}).get("username")
        print(f"{i}. {p['name']}" + (f"  (Instagram @{ig})" if ig else "  (no Instagram linked)"))
    pick = pages[0] if len(pages) == 1 else pages[int(input("Which Page is the shop? Number: ")) - 1]
    ig = pick.get("instagram_business_account") or {}
    values = {"FB_PAGE_ID": pick["id"], "PAGE_ACCESS_TOKEN": pick["access_token"]}
    if ig.get("id"):
        values["IG_USER_ID"] = ig["id"]
    else:
        print("No Instagram professional account is linked to this Page yet; Facebook posting will work, "
              "Instagram won't. Link it (docs/social.md, step 2) and run this again.")
    save_env(values)
    print(f"\nSaved to .env: FB_PAGE_ID={pick['id']} ({pick['name']})"
          + (f", IG_USER_ID={ig['id']} (@{ig.get('username')})" if ig.get("id") else "")
          + ", and the Page token (not shown).")
    print("Now copy FB_PAGE_ID, IG_USER_ID and PAGE_ACCESS_TOKEN from .env into Render → sabi-us → Environment.")


if __name__ == "__main__":
    main()
