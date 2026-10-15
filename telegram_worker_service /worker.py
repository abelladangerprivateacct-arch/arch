"""Run the Telegram outbox worker as a standalone deployable service."""

import logging
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from telegram_notifications import process_telegram_queue_once

SERVICE_DIR = Path(__file__).resolve().parent
load_dotenv(SERVICE_DIR / ".env")

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("telegram_worker_service")


def main():
    required = ("TELEGRAM_BOT_TOKEN", "MONGO_URI", "DB_NAME")
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

    try:
        poll_seconds = max(0.1, float(os.getenv("TELEGRAM_WORKER_POLL_SECONDS", "1")))
    except ValueError as error:
        raise SystemExit("TELEGRAM_WORKER_POLL_SECONDS must be a number") from error

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
