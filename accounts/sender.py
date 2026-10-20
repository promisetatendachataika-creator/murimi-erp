"""
Sends the 6-digit codes.

Two modes, chosen with the MURIMI_CODE_SENDER setting:

  "console"  (test mode, the default) - nothing is sent. The code is printed in
             the black server window, and the website shows it in a yellow
             "Test mode" box so you can try everything for free.
  "whatsapp" - the code is sent as a WhatsApp message through Meta's Cloud API.
             Needs WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID and an approved
             "authentication" message template (see README.md).
"""
import json
import logging
import urllib.error
import urllib.request

from django.conf import settings

log = logging.getLogger("murimi.codes")


class SendError(Exception):
    """The message couldn't be sent. The text is safe to show to the person."""


def mode():
    return "whatsapp" if settings.MURIMI_CODE_SENDER == "whatsapp" else "console"


def is_test_mode():
    return mode() == "console"


def send_code(phone, code, purpose):
    if mode() == "whatsapp":
        _send_whatsapp(phone, code)
    else:
        _print_code(phone, code, purpose)


def _print_code(phone, code, purpose):
    why = "to reset their password" if purpose == "reset" else "to confirm their WhatsApp number"
    line = "=" * 60
    print(f"\n{line}\n  MURIMI TEST MODE - no WhatsApp message was sent\n"
          f"  Code for {phone}: {code}   ({why})\n{line}\n", flush=True)


def _send_whatsapp(phone, code):
    token = settings.WHATSAPP_TOKEN
    number_id = settings.WHATSAPP_PHONE_NUMBER_ID
    if not token or not number_id:
        log.error("WhatsApp sending is switched on but WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID are missing.")
        raise SendError("We couldn't send the WhatsApp message right now. Please try again later.")
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": phone.lstrip("+"),
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TEMPLATE,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
            # An "authentication" template has the code in the text and a "Copy code" button.
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": code}]},
                {"type": "button", "sub_type": "url", "index": "0", "parameters": [{"type": "text", "text": code}]},
            ],
        },
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read()[:500].decode(errors="replace")
        log.error("WhatsApp send failed (%s): %s", e.code, detail)
        raise SendError("We couldn't send the WhatsApp message. Check the number is on WhatsApp, "
                        "or try again in a few minutes.")
    except (urllib.error.URLError, TimeoutError) as e:
        log.error("WhatsApp send failed: %s", e)
        raise SendError("We couldn't reach WhatsApp just now. Please try again in a few minutes.")
