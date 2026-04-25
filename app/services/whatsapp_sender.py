import os
import requests
from dotenv import load_dotenv


load_dotenv()

WHATSAPP_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")

def send_whatsapp_message(to: str, message: str):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": message
        },
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        print("❌ Error sending message:", response.text)
    else:
        print(response.text)


def send_whatsapp_message_with_status(to: str, message: str) -> tuple[bool, str]:
    """
    Same wire format as send_whatsapp_message, but returns (success, response_text)
    so callers (e.g. /admin/broadcasts) can count true deliveries vs. failures.
    Existing callers continue to use send_whatsapp_message.
    """
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {
            "body": message
        },
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
    except requests.RequestException as exc:
        return (False, str(exc))

    return (response.status_code == 200, response.text)
