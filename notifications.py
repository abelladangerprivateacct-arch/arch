import hashlib
import os
import re
from datetime import datetime

from bson import ObjectId
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request
from pymongo import MongoClient

from auth_helpers import authenticate_user, require_admin_permission
from telegram_notifications import enqueue_telegram_event

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("DB_NAME")]
notifications = db.user_notifications
admin_notifications = db.admin_notifications
admin_notification_migrations = db.admin_notification_migrations
users = db.users
audit_log = db.admin_audit_log

try:
    notifications.create_index([("userId", 1), ("createdAt", -1)])
    admin_notifications.create_index([("createdAt", -1)])
    audit_log.create_index([("createdAt", -1)])
except Exception as error:
    print(f"Create notification/audit indexes error: {error}")

notifications_bp = Blueprint("notifications", __name__, url_prefix="/api/notifications")
admin_notifications_bp = Blueprint("admin_notifications", __name__, url_prefix="/api/admin/notifications")
admin_audit_bp = Blueprint("admin_audit", __name__, url_prefix="/api/admin/audit-log")


def _event_id(user_id, event_key):
    value = f"{user_id}:{event_key}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def record_user_notification(user_id, event_key, event_type, title, message, related_id=None, amount=None):
    """Persist a user-visible transaction event; event_key makes retries idempotent."""
    try:
        user_object_id = ObjectId(str(user_id))
        event_id = _event_id(user_object_id, event_key)
        document = {
            "_id": event_id,
            "userId": user_object_id,
            "eventKey": event_key,
            "type": event_type,
            "title": title,
            "message": message,
            "relatedId": str(related_id) if related_id is not None else None,
            "amount": float(amount) if amount is not None else None,
            "createdAt": datetime.utcnow(),
            "readAt": None,
        }
        notifications.update_one({"_id": event_id}, {"$setOnInsert": document}, upsert=True)
        return True
    except Exception as error:
        print(f"Persist user notification error: {error}")
        return False


def record_admin_notification(event_key, event_type, title, message, user_id=None, related_id=None, amount=None):
    """Persist an idempotent notification for all admins, with per-admin read state."""
    try:
        event_id = _event_id("admin", event_key)
        document = {
            "_id": event_id,
            "eventKey": event_key,
            "type": event_type,
            "title": title,
            "message": message,
            "userId": str(user_id) if user_id is not None else None,
            "relatedId": str(related_id) if related_id is not None else None,
            "amount": float(amount) if amount is not None else None,
            "createdAt": datetime.utcnow(),
            "readBy": [],
        }
        admin_notifications.update_one({"_id": event_id}, {"$setOnInsert": document}, upsert=True)
        enqueue_telegram_event(event_id, event_type, title, message)
        return True
    except Exception as error:
        print(f"Persist admin notification error: {error}")
        return False


def _backfill_pending_deposit_notifications():
    """Create admin alerts for pending deposits submitted before these alerts existed."""
    migration_id = "pending-deposit-notifications-v1"
    try:
        if admin_notification_migrations.find_one({"_id": migration_id, "completed": True}):
            return

        for user in users.find(
            {"investments.status": "pending"},
            {"firstName": 1, "lastName": 1, "investments": 1},
        ):
            investor_name = " ".join(filter(None, [user.get("firstName"), user.get("lastName")])) or "A user"
            for investment in user.get("investments", []):
                investment_id = investment.get("id")
                if investment.get("status") != "pending" or not investment_id:
                    continue
                amount = float(investment.get("amount") or 0)
                method = str(investment.get("method") or "payment")
                saved = record_admin_notification(
                    f"investment-submitted:{investment_id}",
                    "deposit_submitted",
                    "New deposit submitted",
                    f"{investor_name} submitted a ${amount:.2f} {method} payment. Awaiting verification.",
                    user_id=user.get("_id"),
                    related_id=investment_id,
                    amount=amount,
                )
                if not saved:
                    return

        admin_notification_migrations.update_one(
            {"_id": migration_id},
            {"$set": {"completed": True, "completedAt": datetime.utcnow()}},
            upsert=True,
        )
    except Exception as error:
        print(f"Backfill pending deposit notifications error: {error}")


def record_admin_action(admin_id, action, user_id=None, related_id=None, details=None):
    """Record a server-side audit event for a privileged action."""
    try:
        result = audit_log.insert_one({
            "adminId": str(admin_id),
            "action": str(action),
            "userId": str(user_id) if user_id is not None else None,
            "relatedId": str(related_id) if related_id is not None else None,
            "details": details or {},
            "createdAt": datetime.utcnow(),
        })
        safe_action = str(action).replace("_", " ").replace(".", " ")
        enqueue_telegram_event(
            f"admin-activity:{result.inserted_id}",
            "admin_activity",
            "Administrator activity",
            f"An audited admin action occurred: {safe_action}. Review the admin audit log for details.",
        )
        return True
    except Exception as error:
        print(f"Persist admin audit event error: {error}")
        return False


def _iso(value):
    return value.isoformat() + "Z" if isinstance(value, datetime) else value


@admin_notifications_bp.route("/", methods=["GET"])
@require_admin_permission("audit:read")
def get_admin_notifications():
    try:
        _backfill_pending_deposit_notifications()
        admin_id = str(request.user_id)
        limit = min(max(request.args.get("limit", 30, type=int), 1), 100)
        records = list(admin_notifications.find().sort("createdAt", -1).limit(limit))
        unread_count = admin_notifications.count_documents({"readBy": {"$ne": admin_id}})
        for record in records:
            record["id"] = record.pop("_id")
            record["isRead"] = admin_id in record.get("readBy", [])
            record.pop("readBy", None)
            record["createdAt"] = _iso(record.get("createdAt"))
        return jsonify({"success": True, "notifications": records, "unreadCount": unread_count}), 200
    except Exception as error:
        print(f"Fetch admin notifications error: {error}")
        return jsonify({"success": False, "error": "Failed to load admin notifications"}), 500


@admin_notifications_bp.route("/<notification_id>/read", methods=["POST"])
@require_admin_permission("audit:read")
def mark_admin_notification_read(notification_id):
    result = admin_notifications.update_one(
        {"_id": notification_id, "readBy": {"$ne": str(request.user_id)}},
        {"$addToSet": {"readBy": str(request.user_id)}},
    )
    if result.matched_count == 0:
        return jsonify({"success": False, "error": "Notification not found"}), 404
    return jsonify({"success": True}), 200


@admin_notifications_bp.route("/read-all", methods=["POST"])
@require_admin_permission("audit:read")
def mark_all_admin_notifications_read():
    admin_id = str(request.user_id)
    admin_notifications.update_many(
        {"readBy": {"$ne": admin_id}},
        {"$addToSet": {"readBy": admin_id}},
    )
    return jsonify({"success": True}), 200


@notifications_bp.route("/", methods=["GET"])
@authenticate_user
def get_notifications():
    try:
        user_id = ObjectId(request.user_id)
    except Exception:
        return jsonify({"success": False, "error": "Invalid user session"}), 401

    try:
        limit = min(max(request.args.get("limit", 30, type=int), 1), 100)
        records = list(notifications.find({"userId": user_id}).sort("createdAt", -1).limit(limit))
        unread_count = notifications.count_documents({"userId": user_id, "readAt": None})
        for record in records:
            record["id"] = record.pop("_id")
            record.pop("userId", None)
            record["createdAt"] = _iso(record.get("createdAt"))
            record["readAt"] = _iso(record.get("readAt"))
        return jsonify({"success": True, "notifications": records, "unreadCount": unread_count}), 200
    except Exception as error:
        print(f"Fetch notifications error: {error}")
        return jsonify({"success": False, "error": "Failed to load notifications"}), 500


@notifications_bp.route("/<notification_id>/read", methods=["POST"])
@authenticate_user
def mark_notification_read(notification_id):
    try:
        user_id = ObjectId(request.user_id)
    except Exception:
        return jsonify({"success": False, "error": "Invalid user session"}), 401

    result = notifications.update_one(
        {"_id": notification_id, "userId": user_id, "readAt": None},
        {"$set": {"readAt": datetime.utcnow()}},
    )
    if result.matched_count == 0:
        return jsonify({"success": False, "error": "Notification not found"}), 404
    return jsonify({"success": True}), 200


@notifications_bp.route("/read-all", methods=["POST"])
@authenticate_user
def mark_all_notifications_read():
    try:
        user_id = ObjectId(request.user_id)
    except Exception:
        return jsonify({"success": False, "error": "Invalid user session"}), 401

    notifications.update_many(
        {"userId": user_id, "readAt": None},
        {"$set": {"readAt": datetime.utcnow()}},
    )
    return jsonify({"success": True}), 200


@admin_audit_bp.route("/", methods=["GET"])
@require_admin_permission("audit:read")
def get_admin_audit_log():
    try:
        page = max(request.args.get("page", 1, type=int), 1)
        per_page = min(max(request.args.get("per_page", 25, type=int), 1), 50)
        query = request.args.get("q", "").strip()
        filters = {}
        if query:
            pattern = re.escape(query)
            filters["$or"] = [
                {field: {"$regex": pattern, "$options": "i"}}
                for field in ("action", "adminId", "userId", "relatedId")
            ]

        total = audit_log.count_documents(filters)
        total_pages = (total + per_page - 1) // per_page
        page = min(page, total_pages) if total_pages else 1
        records = list(
            audit_log.find(filters)
            .sort("createdAt", -1)
            .skip((page - 1) * per_page)
            .limit(per_page)
        )
        for record in records:
            record["id"] = str(record.pop("_id"))
            record["createdAt"] = _iso(record.get("createdAt"))
        return jsonify({
            "success": True,
            "events": records,
            "page": page,
            "perPage": per_page,
            "total": total,
            "totalPages": total_pages,
        }), 200
    except Exception as error:
        print(f"Fetch admin audit log error: {error}")
        return jsonify({"success": False, "error": "Failed to load audit log"}), 500
