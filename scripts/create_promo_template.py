"""Submit the offers template to Meta for approval (run once).

WhatsApp only delivers a business's message outside the 24-hour window if it uses an approved
template. This one carries the flyer as its picture and the product and price as text, for
customers who said yes to offers. Its wording must match app/promo/share.py TEMPLATE_TEXT,
because that text is what the owner is shown.

An image template needs a sample picture, uploaded first through Meta's resumable upload API
(that needs the app id: META_APP_ID, or --app-id).

    python scripts/create_promo_template.py                      # prints the request, sends nothing
    python scripts/create_promo_template.py --submit             # uploads a sample flyer and submits
    python scripts/create_promo_template.py --submit --sample my_flyer.jpg

Approval usually takes minutes to a day; check its status in WhatsApp Manager.
"""
import argparse
import io
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.promo.share import TEMPLATE_TEXT  # noqa: E402

GRAPH = "https://graph.facebook.com"


def template_request(header_handle="<uploaded sample>"):
    return {
        "name": settings.promo_template,
        "language": settings.promo_template_lang,
        "category": "MARKETING",   # offers, sent only to customers who said yes
        "components": [
            {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": [header_handle]}},
            {"type": "BODY", "text": TEMPLATE_TEXT,
             "example": {"body_text": [["Nkechi Books & Stationery", "Things Fall Apart", "₦3,500 per copy"]]}},
        ],
    }


def sample_flyer():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (800, 800), (30, 90, 160))
    ImageDraw.Draw(img).text((60, 360), "New arrival", fill="white", font_size=80)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def upload(httpx, app_id, data):
    """Meta's resumable upload: open a session, send the bytes, get the handle."""
    token = settings.wa_access_token
    s = httpx.post(f"{GRAPH}/{settings.graph_version}/{app_id}/uploads",
                   params={"file_length": len(data), "file_type": "image/jpeg", "access_token": token}, timeout=30)
    s.raise_for_status()
    r = httpx.post(f"{GRAPH}/{settings.graph_version}/{s.json()['id']}",
                   headers={"Authorization": f"OAuth {token}", "file_offset": "0"}, content=data, timeout=60)
    r.raise_for_status()
    return r.json()["h"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--sample", help="a JPEG to use as the sample picture (default: a plain one)")
    ap.add_argument("--app-id", default=os.environ.get("META_APP_ID"))
    args = ap.parse_args()
    assert len(re.findall(r"\{\{\d\}\}", TEMPLATE_TEXT)) == 3
    if not args.submit:
        print(json.dumps(template_request(), indent=2, ensure_ascii=False))
        print("\nDry run. Add --submit to upload a sample picture and send it to Meta.")
        return
    if not (settings.waba_id and settings.wa_access_token and args.app_id):
        sys.exit("Set WABA_ID, WA_ACCESS_TOKEN and META_APP_ID (or pass --app-id) first.")
    import httpx
    data = Path(args.sample).read_bytes() if args.sample else sample_flyer()
    body = template_request(upload(httpx, args.app_id, data))
    r = httpx.post(f"{GRAPH}/{settings.graph_version}/{settings.waba_id}/message_templates",
                   headers={"Authorization": f"Bearer {settings.wa_access_token}"}, json=body, timeout=30)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
