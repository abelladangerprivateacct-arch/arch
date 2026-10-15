"""Run the Telegram outbox worker as a standalone deployable service."""

import hmac
import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from telegram_notifications import process_telegram_queue_once

SERVICE_DIR = Path(__file__).resolve().parent
load_dotenv(SERVICE_DIR / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("telegram_worker_service")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_POST(self):
        if self.path != "/api/telegram/webhook":
            self.send_error(404)
            return

        secret = os.getenv("TELEGRAM_WEBHOOK_SECRET_TOKEN", "")
        if secret and not hmac.compare_digest(
            self.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
            secret,
        ):
            self.send_error(403)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_000_000:
                self.send_error(400)
                return
            update = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return

        message = update.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")
        command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""

        if chat_id is not None and command == "/start":
            token = os.environ["TELEGRAM_BOT_TOKEN"]
            payload = json.dumps({
                "chat_id": chat_id,
                "text": "Hello! The bot is running.",
            }).encode()
            request = Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urlopen(request, timeout=10) as response:
                    result = json.load(response)
                if not result.get("ok"):
                    raise RuntimeError("Telegram sendMessage returned an error")
            except Exception:
                logger.exception("Failed to reply to Telegram /start")
                self.send_error(500)
                return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        pass


def main():
    required = ("TELEGRAM_BOT_TOKEN", "MONGO_URI", "DB_NAME")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

    try:
        poll_seconds = max(0.1, float(os.getenv("TELEGRAM_WORKER_POLL_SECONDS", "1")))
    except ValueError as error:
        raise SystemExit("TELEGRAM_WORKER_POLL_SECONDS must be a number") from error

    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    logger.info("HTTP server listening on port %s", port)

    logger.info("Standalone Telegram worker started; poll interval=%s seconds", poll_seconds)
    while True:
        try:
            did_work = process_telegram_queue_once()
        except Exception:
            logger.exception("Telegram worker iteration failed")
            did_work = False
        if not did_work:
            time.sleep(poll_seconds)


if __name__ == "__main__":
    main()