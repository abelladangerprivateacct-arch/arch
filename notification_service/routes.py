from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request
from bson import ObjectId


notification_api = Blueprint(
    "notification_api",
    __name__,
)


def serialize_notification(notification):
    return {
        "id": str(notification["_id"]),
        "user_id": str(notification["user_id"]),
        "type": notification["type"],
        "title": notification["title"],
        "message": notification["message"],
        "read": notification["read"],
        "created_at": (
            notification["created_at"].isoformat()
            if notification.get("created_at")
            else None
        ),
    }


@notification_api.route(
    "/health",
    methods=["GET"],
)
def health():
    return jsonify({
        "status": "success",
        "message": "Notification service is running.",
    }), 200


@notification_api.route(
    "/",
    methods=["GET"],
)
def get_notifications():
    user_id = request.args.get("user_id")

    if not user_id:
        return jsonify({
            "status": "error",
            "message": "User ID is required.",
        }), 400

    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    notifications = list(
        current_app.db["notifications"].find({
            "user_id": object_id,
        }).sort(
            "created_at",
            -1,
        )
    )

    return jsonify({
        "status": "success",
        "data": [
            serialize_notification(item)
            for item in notifications
        ],
    }), 200


@notification_api.route(
    "/create",
    methods=["POST"],
)
def create_notification():
    payload = request.get_json(
        silent=True
    ) or {}

    try:
        user_id = ObjectId(
            payload.get("user_id")
        )
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    title = str(
        payload.get("title", "")
    ).strip()

    message = str(
        payload.get("message", "")
    ).strip()

    if not title or not message:
        return jsonify({
            "status": "error",
            "message": "Title and message are required.",
        }), 400

    notification = {
        "user_id": user_id,
        "type": payload.get(
            "type",
            "general",
        ),
        "title": title,
        "message": message,
        "read": False,
        "created_at": datetime.now(
            timezone.utc
        ),
    }

    result = current_app.db[
        "notifications"
    ].insert_one(notification)

    notification["_id"] = result.inserted_id

    return jsonify({
        "status": "success",
        "message": "Notification created successfully.",
        "data": serialize_notification(
            notification
        ),
    }), 201

@notification_api.route(
    "/<notification_id>/read",
    methods=["POST"],
)
def mark_notification_read(notification_id):
    try:
        object_id = ObjectId(notification_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid notification ID.",
        }), 400

    result = current_app.db[
        "notifications"
    ].update_one(
        {
            "_id": object_id,
        },
        {
            "$set": {
                "read": True,
            },
        },
    )

    if result.modified_count == 0:
        return jsonify({
            "status": "error",
            "message": "Notification not found.",
        }), 404

    return jsonify({
        "status": "success",
        "message": "Notification marked as read.",
    }), 200