"""Telegram linking, admin preferences, and durable notification delivery."""

import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from bson import ObjectId
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from admin_roles import has_permission, role_for_admin
from auth_helpers import require_admin_permission

load_dotenv()
logger = logging.getLogger(__name__)

client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("DB_NAME")]
admins = db.admins
telegram_outbox = db.telegram_outbox
telegram_deliveries = db.telegram_deliveries
audit_log = db.admin_audit_log

try:
    admins.create_index("telegramChatId", unique=True, sparse=True)
    telegram_outbox.create_index([("status", 1), ("nextAttemptAt", 1), ("createdAt", 1)])
    telegram_outbox.create_index("expireAt", expireAfterSeconds=0)
    telegram_deliveries.create_index([("status", 1), ("nextAttemptAt", 1), ("createdAt", 1)])
    telegram_deliveries.create_index([("adminId", 1), ("eventId", 1)], unique=True)
    telegram_deliveries.create_index("expireAt", expireAfterSeconds=0)
except Exception:
    logger.exception("Could not create Telegram notification indexes")

telegram_bp = Blueprint("telegram", __name__, url_prefix="/api/telegram")
admin_telegram_bp = Blueprint("admin_telegram", __name__, url_prefix="/api/admin/telegram")

EVENT_TYPES = {
    "admin_activity": {
        "label": "Admin activity",
        "description": "An administrator performs an audited action.",
        "permission": "audit:read",
    },
    "deposit_submitted": {
        "label": "New deposit submitted",
        "description": "A customer submits a deposit for review.",
        "permission": "investments:read",
    },
    "withdrawal_requested": {
        "label": "Withdrawal requested",
        "description": "A customer requests a withdrawal.",
        "permission": "withdrawals:read",
    },
    "support_ticket_created": {
        "label": "New support ticket",
        "description": "A customer opens a support ticket.",
        "permission": "support:read",
    },
    "support_message_received": {
        "label": "Customer replies to support",
        "description": "A customer adds a message to a support ticket.",
        "permission": "support:read",
    },
}
LINK_TTL = timedelta(minutes=10)
MAX_DELIVERY_ATTEMPTS = 12


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _bot_token():
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _webhook_secret():
    return os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()


def _bot_username():
    username = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
    return username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) else ""


def _send_telegram_message(chat_id, text):
    token = _bot_token()
    if not token:
        raise TelegramAPIError("Telegram bot is not configured", status_code=503)
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": str(chat_id), "text": str(text)[:4096]},
            timeout=(3.05, 8),
        )
    except requests.RequestException as error:
        raise TelegramAPIError("Telegram is temporarily unreachable", retryable=True) from error

    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code >= 400 or not payload.get("ok"):
        parameters = payload.get("parameters") or {}
        raise TelegramAPIError(
            "Telegram rejected the message",
            status_code=response.status_code,
            retry_after=parameters.get("retry_after"),
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    return payload.get("result")


class TelegramAPIError(Exception):
    def __init__(self, message, status_code=None, retry_after=None, retryable=False):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.retryable = retryable


def _eligible_event_types(admin):
    role = role_for_admin(admin)
    return [
        event_type
        for event_type, details in EVENT_TYPES.items()
        if has_permission(role, details["permission"])
    ]


def _audit(admin_id, action):
    try:
        audit_log.insert_one({
            "adminId": str(admin_id),
            "action": action,
            "createdAt": utc_now(),
            "details": {"provider": "telegram"},
        })
    except Exception:
        logger.exception("Could not write Telegram settings audit event")


def enqueue_telegram_event(event_id, event_type, title, message):
    """Persist an event for the separate worker; never make Telegram calls in a request."""
    if not _bot_token() or event_type not in EVENT_TYPES:
        return False
    now = utc_now()
    telegram_outbox.update_one(
        {"_id": str(event_id)},
        {"$setOnInsert": {
            "eventType": event_type,
            "title": str(title)[:300],
            "message": str(message)[:3500],
            "status": "pending",
            "attempts": 0,
            "createdAt": now,
            "nextAttemptAt": now,
        }},
        upsert=True,
    )
    return True


def _settings_payload(admin):
    eligible = _eligible_event_types(admin)
    configured_types = admin.get("telegramNotificationTypes")
    if configured_types is None:
        configured_types = eligible
    configured = set(configured_types).intersection(eligible)
    pending_until = admin.get("telegramPendingExpiresAt")
    pending = bool(
        admin.get("telegramPendingChatId")
        and isinstance(pending_until, datetime)
        and pending_until > utc_now()
    )
    username = _bot_username()
    return {
        "success": True,
        "configured": bool(_bot_token() and _webhook_secret() and username),
        "botUsername": username or None,
        "connected": bool(admin.get("telegramChatId")),
        "accountLabel": admin.get("telegramAccountLabel") if admin.get("telegramChatId") else None,
        "pendingLink": pending,
        "pendingAccountLabel": admin.get("telegramPendingAccountLabel") if pending else None,
        "deliveryStatus": admin.get("telegramDeliveryStatus", "ready") if admin.get("telegramChatId") else None,
        "notificationTypes": [
            {
                "key": event_type,
                "label": EVENT_TYPES[event_type]["label"],
                "description": EVENT_TYPES[event_type]["description"],
                "enabled": event_type in configured,
            }
            for event_type in eligible
        ],
    }


@admin_telegram_bp.route("/settings", methods=["GET"])
@require_admin_permission("notifications:manage")
def get_telegram_settings():
    return jsonify(_settings_payload(request.admin)), 200


@admin_telegram_bp.route("/settings", methods=["PUT"])
@require_admin_permission("notifications:manage")
def update_telegram_settings():
    data = request.get_json(silent=True) or {}
    selected = data.get("notificationTypes")
    if not isinstance(selected, list) or any(not isinstance(item, str) for item in selected):
        return jsonify({"success": False, "error": "notificationTypes must be a list of event types"}), 400
    if len(selected) != len(set(selected)):
        return jsonify({"success": False, "error": "Remove duplicate notification types"}), 400
    eligible = set(_eligible_event_types(request.admin))
    if not set(selected).issubset(eligible):
        return jsonify({"success": False, "error": "One or more notification types are not available for your role"}), 403

    admins.update_one(
        {"_id": ObjectId(request.user_id)},
        {"$set": {"telegramNotificationTypes": selected, "updatedAt": utc_now()}},
    )
    _audit(request.user_id, "admin.telegram.preferences.updated")
    admin = admins.find_one({"_id": ObjectId(request.user_id)}) or request.admin
    return jsonify(_settings_payload(admin)), 200


@admin_telegram_bp.route("/link", methods=["POST"])
@require_admin_permission("notifications:manage")
def create_telegram_link():
    username = _bot_username()
    if not (_bot_token() and _webhook_secret() and username):
        return jsonify({"success": False, "error": "Telegram bot is not configured on the server"}), 503

    token = secrets.token_urlsafe(24)
    expires_at = utc_now() + LINK_TTL
    admins.update_one(
        {"_id": ObjectId(request.user_id)},
        {
            "$set": {
                "telegramLinkTokenHash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                "telegramLinkExpiresAt": expires_at,
                "updatedAt": utc_now(),
            },
            "$unset": {
                "telegramPendingChatId": "",
                "telegramPendingAccountLabel": "",
                "telegramPendingExpiresAt": "",
            },
        },
    )
    _audit(request.user_id, "admin.telegram.link.started")
    return jsonify({
        "success": True,
        "link": f"https://t.me/{username}?start={quote(token)}",
        "expiresAt": expires_at.isoformat() + "Z",
    }), 200


@admin_telegram_bp.route("/link/confirm", methods=["POST"])
@require_admin_permission("notifications:manage")
def confirm_telegram_link():
    admin_id = ObjectId(request.user_id)
    now = utc_now()
    admin = admins.find_one({
        "_id": admin_id,
        "telegramPendingChatId": {"$exists": True},
        "telegramPendingExpiresAt": {"$gt": now},
    })
    if not admin:
        return jsonify({"success": False, "error": "No valid Telegram link is waiting for confirmation. Start the bot again."}), 409

    try:
        result = admins.update_one(
            {
                "_id": admin_id,
                "telegramPendingChatId": admin["telegramPendingChatId"],
                "telegramPendingExpiresAt": {"$gt": now},
            },
            {
                "$set": {
                    "telegramChatId": admin["telegramPendingChatId"],
                    "telegramAccountLabel": admin.get("telegramPendingAccountLabel", "Telegram account"),
                    "telegramConnectedAt": now,
                    "telegramDeliveryStatus": "ready",
                    "updatedAt": now,
                },
                "$unset": {
                    "telegramPendingChatId": "",
                    "telegramPendingAccountLabel": "",
                    "telegramPendingExpiresAt": "",
                    "telegramLinkTokenHash": "",
                    "telegramLinkExpiresAt": "",
                },
            },
        )
    except DuplicateKeyError:
        return jsonify({"success": False, "error": "That Telegram account is already linked to another administrator"}), 409
    if result.modified_count != 1:
        return jsonify({"success": False, "error": "Telegram link expired or changed. Start the bot again."}), 409
    _audit(request.user_id, "admin.telegram.link.connected")
    return jsonify({"success": True, "settings": _settings_payload(admins.find_one({"_id": admin_id}))}), 200


@admin_telegram_bp.route("/disconnect", methods=["POST"])
@require_admin_permission("notifications:manage")
def disconnect_telegram():
    admins.update_one(
        {"_id": ObjectId(request.user_id)},
        {
            "$unset": {
                "telegramChatId": "",
                "telegramAccountLabel": "",
                "telegramConnectedAt": "",
                "telegramDeliveryStatus": "",
                "telegramPendingChatId": "",
                "telegramPendingAccountLabel": "",
                "telegramPendingExpiresAt": "",
                "telegramLinkTokenHash": "",
                "telegramLinkExpiresAt": "",
            },
            "$set": {"updatedAt": utc_now()},
        },
    )
    _audit(request.user_id, "admin.telegram.link.disconnected")
    return jsonify({"success": True}), 200


@admin_telegram_bp.route("/test", methods=["POST"])
@require_admin_permission("notifications:manage")
def test_telegram_connection():
    chat_id = request.admin.get("telegramChatId")
    if not chat_id:
        return jsonify({"success": False, "error": "Connect a Telegram account first"}), 409
    try:
        _send_telegram_message(chat_id, "Capitello admin notifications are connected and ready.")
    except TelegramAPIError as error:
        if error.status_code in (400, 403):
            admins.update_one(
                {"_id": ObjectId(request.user_id), "telegramChatId": chat_id},
                {"$set": {"telegramDeliveryStatus": "needs_reconnect"}},
            )
        return jsonify({"success": False, "error": str(error)}), 502
    admins.update_one(
        {"_id": ObjectId(request.user_id), "telegramChatId": chat_id},
        {"$set": {"telegramDeliveryStatus": "ready"}},
    )
    return jsonify({"success": True, "message": "Test message sent"}), 200


@telegram_bp.route("/webhook", methods=["POST"])
def receive_telegram_update():
    expected_secret = _webhook_secret()
    supplied_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected_secret:
        return jsonify({"success": False, "error": "Telegram webhook is not configured"}), 503
    if not supplied_secret or not hmac.compare_digest(supplied_secret, expected_secret):
        return jsonify({"success": False, "error": "Unauthorized webhook request"}), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text")
    match = re.fullmatch(r"/start(?:@\w+)?(?:\s+([A-Za-z0-9_-]{1,64}))?\s*", text or "")
    payload = match.group(1) if match else None
    if chat.get("type") != "private" or not payload:
        return jsonify({"ok": True}), 200

    now = utc_now()
    token_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    admin = admins.find_one_and_update(
        {
            "telegramLinkTokenHash": token_hash,
            "telegramLinkExpiresAt": {"$gt": now},
            "isActive": {"$ne": False},
            "mfaEnabled": True,
        },
        {
            "$set": {
                "telegramPendingChatId": str(chat.get("id")),
                "telegramPendingAccountLabel": _telegram_account_label(message.get("from") or {}),
                "telegramPendingExpiresAt": now + LINK_TTL,
                "updatedAt": now,
            },
            "$unset": {"telegramLinkTokenHash": "", "telegramLinkExpiresAt": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    if admin:
        try:
            _send_telegram_message(chat["id"], "Telegram account found. Return to Capitello Admin and confirm the link to finish setup.")
        except TelegramAPIError:
            logger.warning("Telegram link confirmation message could not be sent")
    return jsonify({"ok": True}), 200


def _telegram_account_label(telegram_user):
    username = str(telegram_user.get("username") or "").strip()
    if username:
        return "@" + username[:32]
    first_name = str(telegram_user.get("first_name") or "Telegram user").strip()
    return first_name[:64] or "Telegram user"


def _claim_outbox_event():
    now = utc_now()
    lock_id = secrets.token_hex(16)
    event = telegram_outbox.find_one_and_update(
        {
            "$or": [
                {"status": "pending", "nextAttemptAt": {"$lte": now}},
                {"status": "processing", "lockUntil": {"$lte": now}},
            ]
        },
        {"$set": {"status": "processing", "lockId": lock_id, "lockUntil": now + timedelta(minutes=2)}},
        sort=[("createdAt", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return event, lock_id


def _fan_out_event(event, lock_id):
    event_type = event.get("eventType")
    if event_type not in EVENT_TYPES:
        telegram_outbox.update_one({"_id": event["_id"], "lockId": lock_id}, {"$set": {"status": "done", "expireAt": utc_now() + timedelta(days=30)}})
        return

    permission = EVENT_TYPES[event_type]["permission"]
    now = utc_now()
    active_admins = admins.find({
        "isActive": {"$ne": False},
        "mfaEnabled": True,
        "telegramChatId": {"$exists": True},
    })
    for admin in active_admins:
        if not has_permission(role_for_admin(admin), permission):
            continue
        selected_types = admin.get("telegramNotificationTypes")
        if selected_types is None:
            selected_types = _eligible_event_types(admin)
        if event_type not in selected_types:
            continue
        admin_id = str(admin["_id"])
        delivery_id = f"{event['_id']}:{admin_id}"
        telegram_deliveries.update_one(
            {"_id": delivery_id},
            {"$setOnInsert": {
                "eventId": str(event["_id"]),
                "adminId": admin_id,
                "eventType": event_type,
                "title": event.get("title", "Admin notification"),
                "message": event.get("message", ""),
                "status": "queued",
                "attempts": 0,
                "createdAt": now,
                "nextAttemptAt": now,
            }},
            upsert=True,
        )
    telegram_outbox.update_one(
        {"_id": event["_id"], "lockId": lock_id},
        {"$set": {"status": "done", "processedAt": now, "expireAt": now + timedelta(days=30)},
         "$unset": {"lockId": "", "lockUntil": ""}},
    )


def _claim_delivery():
    now = utc_now()
    lock_id = secrets.token_hex(16)
    delivery = telegram_deliveries.find_one_and_update(
        {
            "$or": [
                {"status": {"$in": ["queued", "retry"]}, "nextAttemptAt": {"$lte": now}},
                {"status": "sending", "lockUntil": {"$lte": now}},
            ]
        },
        {
            "$set": {"status": "sending", "lockId": lock_id, "lockUntil": now + timedelta(minutes=1)},
            "$inc": {"attempts": 1},
        },
        sort=[("createdAt", 1)],
        return_document=ReturnDocument.AFTER,
    )
    return delivery, lock_id


def _delivery_is_still_allowed(delivery, admin):
    if not admin or admin.get("isActive") is False or not admin.get("mfaEnabled") or not admin.get("telegramChatId"):
        return False
    event = EVENT_TYPES.get(delivery.get("eventType"))
    if not event or not has_permission(role_for_admin(admin), event["permission"]):
        return False
    selected_types = admin.get("telegramNotificationTypes")
    if selected_types is None:
        selected_types = _eligible_event_types(admin)
    return delivery.get("eventType") in selected_types


def _deliver_one(delivery, lock_id):
    delivery_filter = {"_id": delivery["_id"], "lockId": lock_id}
    try:
        admin_id = ObjectId(delivery.get("adminId"))
    except Exception:
        telegram_deliveries.update_one(delivery_filter, {"$set": {"status": "skipped", "expireAt": utc_now() + timedelta(days=90)}})
        return
    admin = admins.find_one({"_id": admin_id})
    if not _delivery_is_still_allowed(delivery, admin):
        telegram_deliveries.update_one(
            delivery_filter,
            {"$set": {"status": "skipped", "expireAt": utc_now() + timedelta(days=90)}, "$unset": {"lockId": "", "lockUntil": ""}},
        )
        return

    try:
        _send_telegram_message(admin["telegramChatId"], f"{delivery.get('title', 'Admin notification')}\n{delivery.get('message', '')}")
        telegram_deliveries.update_one(
            delivery_filter,
            {"$set": {"status": "sent", "sentAt": utc_now(), "expireAt": utc_now() + timedelta(days=90)}, "$unset": {"lockId": "", "lockUntil": ""}},
        )
        if admin.get("telegramDeliveryStatus") == "needs_reconnect":
            admins.update_one({"_id": admin_id}, {"$set": {"telegramDeliveryStatus": "ready"}})
    except TelegramAPIError as error:
        attempts = int(delivery.get("attempts", 1))
        permanent = error.status_code in (400, 403)
        exhausted = attempts >= MAX_DELIVERY_ATTEMPTS
        if error.status_code in (400, 403):
            admins.update_one(
                {"_id": admin_id, "telegramChatId": admin.get("telegramChatId")},
                {"$set": {"telegramDeliveryStatus": "needs_reconnect"}},
            )
        if permanent or exhausted:
            telegram_deliveries.update_one(
                delivery_filter,
                {"$set": {"status": "failed", "lastError": str(error), "failedAt": utc_now(), "expireAt": utc_now() + timedelta(days=90)}, "$unset": {"lockId": "", "lockUntil": ""}},
            )
            logger.warning("Telegram delivery permanently failed (status=%s)", error.status_code)
            return
        delay = error.retry_after if isinstance(error.retry_after, (int, float)) else min(3600, 5 * (2 ** max(0, attempts - 1)))
        telegram_deliveries.update_one(
            delivery_filter,
            {"$set": {
                "status": "retry",
                "lastError": str(error),
                "nextAttemptAt": utc_now() + timedelta(seconds=max(1, min(float(delay), 3600))),
            }, "$unset": {"lockId": "", "lockUntil": ""}},
        )
        logger.warning("Telegram delivery will retry (attempt=%s)", attempts)


def process_telegram_queue_once():
    """Process one outbox event or one recipient delivery; safe across worker replicas."""
    if not _bot_token():
        return False
    event, lock_id = _claim_outbox_event()
    if event:
        try:
            _fan_out_event(event, lock_id)
        except Exception:
            logger.exception("Telegram event fan-out failed")
            telegram_outbox.update_one(
                {"_id": event["_id"], "lockId": lock_id},
                {"$set": {"status": "pending", "nextAttemptAt": utc_now() + timedelta(seconds=10)}, "$unset": {"lockId": "", "lockUntil": ""}},
            )
        return True
    delivery, delivery_lock = _claim_delivery()
    if delivery:
        _deliver_one(delivery, delivery_lock)
        return True
    return False
