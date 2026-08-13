from datetime import datetime, timezone

from bson import ObjectId

from flask import Blueprint, current_app, jsonify, request


messaging_api = Blueprint(
    "messaging_api",
    __name__,
)


def serialize_message(message):
    return {
        "id": str(message["_id"]),
        "sender_id": str(message["sender_id"]),
        "receiver_id": str(message["receiver_id"]),
        "message": message["message"],
        "read": message.get(
            "read",
            False,
        ),
        "created_at": (
            message["created_at"].isoformat()
            if message.get("created_at")
            else None
        ),
        "read_at": (
            message["read_at"].isoformat()
            if message.get("read_at")
            else None
        ),
    }


@messaging_api.route(
    "/send",
    methods=["POST"],
)
def send_message():
    payload = request.get_json(
        silent=True
    ) or {}

    try:
        sender_id = ObjectId(
            payload.get("sender_id")
        )
        receiver_id = ObjectId(
            payload.get("receiver_id")
        )
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    message = str(
        payload.get("message", "")
    ).strip()

    if not message:
        return jsonify({
            "status": "error",
            "message": "Message is required.",
        }), 400

    now = datetime.now(
        timezone.utc
    )

    document = {
        "sender_id": sender_id,
        "receiver_id": receiver_id,
        "message": message,
        "read": False,
        "created_at": now,
        "read_at": None,
    }

    result = current_app.db[
        "messages"
    ].insert_one(document)

    document["_id"] = result.inserted_id

    return jsonify({
        "status": "success",
        "message": "Message sent successfully.",
        "data": serialize_message(
            document
        ),
    }), 201


@messaging_api.route(
    "/conversation",
    methods=["GET"],
)
def get_conversation():
    sender_id = request.args.get(
        "sender_id"
    )

    receiver_id = request.args.get(
        "receiver_id"
    )

    try:
        sender = ObjectId(
            sender_id
        )
        receiver = ObjectId(
            receiver_id
        )
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    messages = current_app.db[
        "messages"
    ].find({
        "$or": [
            {
                "sender_id": sender,
                "receiver_id": receiver,
            },
            {
                "sender_id": receiver,
                "receiver_id": sender,
            },
        ],
    }).sort(
        "created_at",
        1,
    )

    return jsonify({
        "status": "success",
        "data": [
            serialize_message(item)
            for item in messages
        ],
    }), 200


@messaging_api.route(
    "/<message_id>/read",
    methods=["POST"],
)
def mark_read(message_id):
    try:
        object_id = ObjectId(
            message_id
        )
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid message ID.",
        }), 400

    now = datetime.now(
        timezone.utc
    )

    result = current_app.db[
        "messages"
    ].update_one(
        {
            "_id": object_id,
        },
        {
            "$set": {
                "read": True,
                "read_at": now,
            },
        },
    )

    if result.matched_count == 0:
        return jsonify({
            "status": "error",
            "message": "Message not found.",
        }), 404

    return jsonify({
        "status": "success",
        "message": "Message marked as read.",
    }), 200

    