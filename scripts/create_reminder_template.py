"""Submit the payment-reminder template to Meta for approval (run once).

WhatsApp only delivers business-initiated messages outside the 24-hour window
if they use an approved template. Its wording must match
app/agent/reminders.py TEMPLATE_TEXT, because that text is what the owner
previews before sending.

    python scripts/create_reminder_template.py            # prints the request, sends nothing
    python scripts/create_reminder_template.py --submit   # submits it (needs WABA_ID and WA_ACCESS_TOKEN)

Approval usually takes minutes to a day; check its status in WhatsApp Manager.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.reminders import TEMPLATE_TEXT  # noqa: E402
from app.config import settings  # noqa: E402

GRAPH = "https://graph.facebook.com"


def template_request():
    return {
        "name": settings.reminder_template,
        "language": settings.reminder_template_lang,
        "category": "UTILITY",   # a payment reminder about an existing purchase
        "components": [{
            "type": "BODY",
            "text": TEMPLATE_TEXT,
            "example": {"body_text": [["Mama Tunde", "Mama Nkechi Provisions", "₦37,500"]]},
        }],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()
    body = template_request()
    assert len(re.findall(r"\{\{\d\}\}", TEMPLATE_TEXT)) == 3
    print(json.dumps(body, indent=2, ensure_ascii=False))
    if not args.submit:
        print("\nDry run. Add --submit to send it to Meta.")
        return
    if not (settings.waba_id and settings.wa_access_token):
        sys.exit("Set WABA_ID and WA_ACCESS_TOKEN first.")
    import httpx
    r = httpx.post(f"{GRAPH}/{settings.graph_version}/{settings.waba_id}/message_templates",
                   headers={"Authorization": f"Bearer {settings.wa_access_token}"}, json=body, timeout=30)
    print(r.status_code, r.text)


if __name__ == "__main__":
    main()
