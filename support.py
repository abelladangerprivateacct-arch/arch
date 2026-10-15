import os
from datetime import datetime, timezone

from bson import ObjectId
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request
from pymongo import MongoClient, ReturnDocument

from auth_helpers import authenticate_user, require_admin_permission
from notifications import record_admin_action, record_admin_notification, record_user_notification

load_dotenv()
client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("DB_NAME")]
users = db.users
support_tickets = db.support_tickets

try:
    support_tickets.create_index([("userId", 1), ("updatedAt", -1)])
    support_tickets.create_index([("status", 1), ("updatedAt", -1)])
except Exception as error:
    print(f"Create support ticket indexes error: {error}")

support_bp = Blueprint("support", __name__, url_prefix="/api/support")
admin_support_bp = Blueprint("admin_support", __name__, url_prefix="/api/admin/support")


def _serialize(ticket, admin_id=None):
    if admin_id:
        last_customer_message_at = ticket.get("lastCustomerMessageAt") or ticket.get("updatedAt")
        read_at = (ticket.get("adminReadAtBy") or {}).get(admin_id)
        ticket["isUnread"] = (
            ticket.get("status") == "open"
            and last_customer_message_at is not None
            and (read_at is None or last_customer_message_at > read_at)
        )
    ticket["id"] = str(ticket.pop("_id"))
    ticket["userId"] = str(ticket.get("userId", ""))
    for message in ticket.get("messages", []):
        if isinstance(message.get("createdAt"), datetime):
            message["createdAt"] = message["createdAt"].isoformat() + "Z"
    for key in ("createdAt", "updatedAt", "lastCustomerMessageAt"):
        if isinstance(ticket.get(key), datetime):
            ticket[key] = ticket[key].isoformat() + "Z"
    return ticket


def _valid_message(data):
    message = str((data or {}).get("message") or "").strip()
    if not 1 <= len(message) <= 5000:
        return None, "Message must be between 1 and 5,000 characters"
    return message, None


@support_bp.route("/tickets", methods=["GET"])
@authenticate_user
def list_user_tickets():
    try:
        records = support_tickets.find({"userId": ObjectId(request.user_id)}).sort("updatedAt", -1).limit(50)
        return jsonify({"success": True, "tickets": [_serialize(record) for record in records]}), 200
    except Exception as error:
        print(f"List support tickets error: {error}")
        return jsonify({"success": False, "error": "Could not load support requests"}), 500


@support_bp.route("/tickets", methods=["POST"])
@authenticate_user
def create_ticket():
    data = request.get_json(silent=True) or {}
    subject = str(data.get("subject") or "").strip()
    message, error = _valid_message(data)
    if not 4 <= len(subject) <= 120:
        return jsonify({"success": False, "error": "Subject must be between 4 and 120 characters"}), 400
    if error:
        return jsonify({"success": False, "error": error}), 400

    try:
        user_id = ObjectId(request.user_id)
        user = users.find_one({"_id": user_id}, {"email": 1, "firstName": 1})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404
        now = datetime.utcnow()
        ticket = {
            "userId": user_id,
            "email": user.get("email"),
            "subject": subject,
            "status": "open",
            "messages": [{"author": "user", "message": message, "createdAt": now}],
            "createdAt": now,
            "updatedAt": now,
            "lastCustomerMessageAt": now,
        }
        result = support_tickets.insert_one(ticket)
        ticket["_id"] = result.inserted_id
        record_admin_notification(
            f"support-ticket-created:{result.inserted_id}",
            "support_ticket_created",
            "New support ticket",
            f'A customer opened a ticket: "{subject}".',
            user_id=user_id,
            related_id=result.inserted_id,
        )
        return jsonify({"success": True, "ticket": _serialize(ticket)}), 201
    except Exception as error:
        print(f"Create support ticket error: {error}")
        return jsonify({"success": False, "error": "Could not submit your support request"}), 500


@support_bp.route("/tickets/<ticket_id>/messages", methods=["POST"])
@authenticate_user
def reply_to_ticket(ticket_id):
    if not ObjectId.is_valid(ticket_id):
        return jsonify({"success": False, "error": "Invalid support request"}), 400
    message, error = _valid_message(request.get_json(silent=True) or {})
    if error:
        return jsonify({"success": False, "error": error}), 400
    now = datetime.utcnow()
    result = support_tickets.update_one(
        {"_id": ObjectId(ticket_id), "userId": ObjectId(request.user_id), "status": {"$ne": "resolved"}},
        {"$push": {"messages": {"author": "user", "message": message, "createdAt": now}}, "$set": {"status": "open", "updatedAt": now, "lastCustomerMessageAt": now}},
    )
    if result.modified_count != 1:
        return jsonify({"success": False, "error": "Open support request not found"}), 404
    return jsonify({"success": True}), 200


@admin_support_bp.route("/tickets", methods=["GET"])
@require_admin_permission("support:read")
def list_support_tickets():
    status = request.args.get("status", "all").lower()
    allowed = {"all", "open", "waiting_user", "resolved"}
    if status not in allowed:
        return jsonify({"success": False, "error": "Invalid ticket status"}), 400
    query = {} if status == "all" else {"status": status}
    ticket_id = str(request.args.get("ticket_id") or "").strip()
    if ticket_id:
        if not ObjectId.is_valid(ticket_id):
            return jsonify({"success": False, "error": "Invalid support request"}), 400
        query["_id"] = ObjectId(ticket_id)
    try:
        page = max(request.args.get("page", 1, type=int), 1)
        per_page = min(max(request.args.get("per_page", 20, type=int), 1), 50)
        total = support_tickets.count_documents(query)
        total_pages = (total + per_page - 1) // per_page
        page = min(page, total_pages) if total_pages else 1
        records = support_tickets.find(query).sort("updatedAt", -1).skip((page - 1) * per_page).limit(per_page)
        return jsonify({
            "success": True,
            "tickets": [_serialize(record, request.user_id) for record in records],
            "page": page,
            "perPage": per_page,
            "total": total,
            "totalPages": total_pages,
        }), 200
    except Exception as error:
        print(f"Admin list support tickets error: {error}")
        return jsonify({"success": False, "error": "Could not load support requests"}), 500


@admin_support_bp.route("/unread-count", methods=["GET"])
@require_admin_permission("support:read")
def count_unread_support_tickets():
    read_at_field = f"$adminReadAtBy.{request.user_id}"
    query = {
        "status": "open",
        "$expr": {
            "$gt": [
                {"$ifNull": ["$lastCustomerMessageAt", "$updatedAt"]},
                {"$ifNull": [read_at_field, datetime(1970, 1, 1)]},
            ]
        },
    }
    try:
        unread_count = support_tickets.count_documents(query)
        return jsonify({"success": True, "unreadCount": unread_count}), 200
    except Exception as error:
        print(f"Count unread support tickets error: {error}")
        return jsonify({"success": False, "error": "Could not load unread support requests"}), 500


@admin_support_bp.route("/tickets/<ticket_id>/read", methods=["POST"])
@require_admin_permission("support:read")
def mark_support_ticket_read(ticket_id):
    if not ObjectId.is_valid(ticket_id):
        return jsonify({"success": False, "error": "Invalid support request"}), 400
    try:
        read_field = f"adminReadAtBy.{request.user_id}"
        result = support_tickets.update_one(
            {"_id": ObjectId(ticket_id)},
            {"$set": {read_field: datetime.now(timezone.utc).replace(tzinfo=None)}},
        )
        if result.matched_count != 1:
            return jsonify({"success": False, "error": "Support request not found"}), 404
        return jsonify({"success": True}), 200
    except Exception as error:
        print(f"Mark support ticket read error: {error}")
        return jsonify({"success": False, "error": "Could not mark support request as read"}), 500


@admin_support_bp.route("/tickets/<ticket_id>/reply", methods=["POST"])
@require_admin_permission("support:reply")
def reply_to_user(ticket_id):
    if not ObjectId.is_valid(ticket_id):
        return jsonify({"success": False, "error": "Invalid support request"}), 400
    data = request.get_json(silent=True) or {}
    message, error = _valid_message(data)
    if error:
        return jsonify({"success": False, "error": error}), 400
    status = str(data.get("status") or "waiting_user").strip().lower()
    if status not in {"waiting_user", "resolved"}:
        return jsonify({"success": False, "error": "Status must be waiting_user or resolved"}), 400

    now = datetime.utcnow()
    ticket = support_tickets.find_one_and_update(
        {"_id": ObjectId(ticket_id)},
        {"$push": {"messages": {"author": "admin", "message": message, "createdAt": now}}, "$set": {"status": status, "updatedAt": now}},
        return_document=ReturnDocument.AFTER,
    )
    if not ticket:
        return jsonify({"success": False, "error": "Support request not found"}), 404

    record_user_notification(
        ticket["userId"],
        f"support-reply:{ticket_id}:{now.isoformat()}",
        "support_reply",
        "Support replied",
        f"There is a new reply to your support request: {ticket.get('subject', 'Support request')}.",
        related_id=ticket_id,
    )
    record_admin_notification(
        f"support-message-received:{ticket_id}:{now.isoformat()}",
        "support_message_received",
        "Customer replied to a support ticket",
        f'A customer replied to: "{ticket.get("subject", "Support request")}".',
        user_id=ticket["userId"],
        related_id=ticket_id,
    )
    record_admin_action(request.user_id, "support.ticket.replied", user_id=ticket["userId"], related_id=ticket_id, details={"status": status})
    return jsonify({"success": True, "ticket": _serialize(ticket)}), 200
