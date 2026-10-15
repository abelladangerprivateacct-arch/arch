# Standalone Telegram notification worker

This is a separate worker service for the existing Telegram notification queue. It is intentionally independent of the API repo so it can be hosted on its own host or container.

## Local run

1. Create a virtual environment.
2. Install the dependencies from this folder: `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and set `MONGO_URI`, `DB_NAME`, and `TELEGRAM_BOT_TOKEN`.
4. Run: `python worker.py`.

Use a development database and development bot for local testing. This worker continuously polls; stop it with Ctrl+C.

## Deploy as a separate service

Choose a **background worker** / **worker service** on your host, not a web service or one-shot cron job.

This worker is self-contained: it does not import code from the main API repository. The only required production secrets are:

- `MONGO_URI`: the same MongoDB connection string used by the production API.
- `DB_NAME`: the same database used by the production API.
- `TELEGRAM_BOT_TOKEN`: the same bot token used by the production API.
- `TELEGRAM_WORKER_POLL_SECONDS`: optional; defaults to `1`.
- `LOG_LEVEL`: optional; defaults to `INFO`.

The production API must also have the same `TELEGRAM_BOT_TOKEN` configured so it can write events to the outbox. The worker's database user needs access to the same collections. Ensure MongoDB network access allows connections from the worker host, and enable automatic restart on process failure.

Do not commit `.env` or bake secrets into the image.
