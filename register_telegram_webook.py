"""Register the production HTTPS webhook with Telegram after deployment."""

import os
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

load_dotenv()


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    secret = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
    webhook_url = os.getenv("TELEGRAM_WEBHOOK_URL", "").strip()
    parsed = urlparse(webhook_url)

    if not token or not secret or not webhook_url:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET, and TELEGRAM_WEBHOOK_URL first")
    if len(secret) < 32 or len(secret) > 256 or not all(character.isalnum() or character in "_-" for character in secret):
        raise SystemExit("TELEGRAM_WEBHOOK_SECRET must be 32–256 characters using letters, digits, '_' or '-'")
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}:
        raise SystemExit("TELEGRAM_WEBHOOK_URL must use the deployed public HTTPS API URL")
    if not parsed.path.endswith("/api/telegram/webhook"):
        raise SystemExit("TELEGRAM_WEBHOOK_URL must end with /api/telegram/webhook")

    try:
        identity_response = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=(3.05, 10),
        )
        identity = identity_response.json()
        configured_username = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
        actual_username = (identity.get("result") or {}).get("username", "")
        if identity_response.status_code >= 400 or not identity.get("ok") or actual_username != configured_username:
            raise SystemExit("Bot token is invalid or TELEGRAM_BOT_USERNAME does not match the bot token")

        response = requests.post(
            f"https://api.telegram.org/bot{token}/setWebhook",
            json={"url": webhook_url, "secret_token": secret, "allowed_updates": ["message"]},
            timeout=(3.05, 15),
        )
        result = response.json()
    except (requests.RequestException, ValueError) as error:
        raise SystemExit("Could not register the Telegram webhook; verify network access and retry") from error

    if response.status_code >= 400 or not result.get("ok"):
        raise SystemExit("Telegram rejected the webhook configuration; verify the bot token and public HTTPS URL")
    print("Telegram webhook registered successfully.")


if __name__ == "__main__":
    main()
