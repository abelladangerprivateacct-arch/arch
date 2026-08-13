from datetime import datetime, timezone

from bson import ObjectId

from flask import (
    Blueprint,
    current_app,
    jsonify,
    request,
)


security_api = Blueprint(
    "security_api",
    __name__,
)


def serialize_security_event(event):

    return {
        "id": str(event["_id"]),
        "user_id": (
            str(event["user_id"])
            if event.get("user_id")
            else None
        ),
        "event_type": event.get(
            "event_type",
            "",
        ),
        "severity": event.get(
            "severity",
            "low",
        ),
        "description": event.get(
            "description",
            "",
        ),
        "ip_address": event.get(
            "ip_address",
        ),
        "created_at": (
            event["created_at"].isoformat()
            if event.get("created_at")
            else None
        ),
    }


@security_api.route(
    "/health",
    methods=["GET"],
)
def health():

    return jsonify({
        "status": "success",
        "message": "Security service is running.",
    }), 200



@security_api.route(
    "/events",
    methods=["POST"],
)
def create_security_event():

    payload = request.get_json(
        silent=True
    ) or {}

    user_id = None

    if payload.get("user_id"):

        try:
            user_id = ObjectId(
                payload.get("user_id")
            )

        except Exception:
            return jsonify({
                "status": "error",
                "message": "Invalid user ID.",
            }), 400


    event_type = str(
        payload.get(
            "event_type",
            "",
        )
    ).strip()


    description = str(
        payload.get(
            "description",
            "",
        )
    ).strip()


    severity = str(
        payload.get(
            "severity",
            "low",
        )
    ).strip()


    if not event_type or not description:

        return jsonify({
            "status": "error",
            "message": "Event type and description are required.",
        }), 400


    event = {

        "user_id": user_id,

        "event_type": event_type,

        "severity": severity,

        "description": description,

        "ip_address": request.remote_addr,

        "created_at": datetime.now(
            timezone.utc
        ),

    }


    result = current_app.db[
        "security_events"
    ].insert_one(event)


    event["_id"] = result.inserted_id


    return jsonify({

        "status": "success",

        "message": "Security event recorded.",

        "data": serialize_security_event(
            event
        ),

    }), 201

@security_api.route(
    "/events",
    methods=["GET"],
)
def get_security_events():

    events = current_app.db[
        "security_events"
    ].find().sort(
        "created_at",
        -1,
    )


    result = [
        serialize_security_event(event)
        for event in events
    ]


    return jsonify({

        "status": "success",

        "data": result,

        "count": len(result),

    }), 200

@security_api.route(
    "/users/<user_id>/events",
    methods=["GET"],
)
def get_user_security_events(user_id):

    try:
        object_id = ObjectId(user_id)

    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400


    events = current_app.db[
        "security_events"
    ].find({
        "user_id": object_id,
    }).sort(
        "created_at",
        -1,
    )


    result = [
        serialize_security_event(event)
        for event in events
    ]


    return jsonify({

        "status": "success",

        "user_id": user_id,

        "data": result,

        "count": len(result),

    }), 200

@security_api.route(
    "/login-attempt",
    methods=["POST"],
)
def record_login_attempt():

    payload = request.get_json(
        silent=True
    ) or {}


    email = str(
        payload.get(
            "email",
            "",
        )
    ).strip().lower()


    success = bool(
        payload.get(
            "success",
            False,
        )
    )


    user_id = None

    if payload.get("user_id"):

        try:
            user_id = ObjectId(
                payload.get("user_id")
            )

        except Exception:

            return jsonify({
                "status": "error",
                "message": "Invalid user ID.",
            }), 400


    if not email:

        return jsonify({
            "status": "error",
            "message": "Email is required.",
        }), 400


    attempt = {

        "user_id": user_id,

        "email": email,

        "success": success,

        "ip_address": request.remote_addr,

        "user_agent": request.headers.get(
            "User-Agent"
        ),

        "created_at": datetime.now(
            timezone.utc
        ),

    }


    result = current_app.db[
        "login_attempts"
    ].insert_one(attempt)


    return jsonify({

        "status": "success",

        "message": "Login attempt recorded.",

        "attempt_id": str(
            result.inserted_id
        ),

    }), 201

@security_api.route(
    "/login-attempts",
    methods=["GET"],
)
def get_login_attempts():

    attempts = current_app.db[
        "login_attempts"
    ].find().sort(
        "created_at",
        -1,
    )


    result = []

    for attempt in attempts:

        result.append({

            "id": str(
                attempt["_id"]
            ),

            "user_id": (
                str(
                    attempt["user_id"]
                )
                if attempt.get("user_id")
                else None
            ),

            "email": attempt.get(
                "email",
                "",
            ),

            "success": attempt.get(
                "success",
                False,
            ),

            "ip_address": attempt.get(
                "ip_address",
            ),

            "user_agent": attempt.get(
                "user_agent",
            ),

            "created_at": (
                attempt["created_at"].isoformat()
                if attempt.get("created_at")
                else None
            ),

        })


    return jsonify({

        "status": "success",

        "data": result,

        "count": len(result),

    }), 200

@security_api.route(
    "/login-attempts/<email>/stats",
    methods=["GET"],
)
def get_login_stats(email):

    email = str(
        email
    ).strip().lower()


    attempts = list(
        current_app.db[
            "login_attempts"
        ].find({
            "email": email,
        }).sort(
            "created_at",
            -1,
        )
    )


    total_attempts = len(
        attempts
    )


    failed_attempts = len([
        attempt
        for attempt in attempts
        if not attempt.get(
            "success",
            False,
        )
    ])


    successful_attempts = len([
        attempt
        for attempt in attempts
        if attempt.get(
            "success",
            False,
        )
    ])


    last_attempt = None

    if attempts:

        last_attempt = (
            attempts[0]["created_at"].isoformat()
            if attempts[0].get("created_at")
            else None
        )


    return jsonify({

        "status": "success",

        "data": {

            "email": email,

            "total_attempts": total_attempts,

            "failed_attempts": failed_attempts,

            "successful_attempts": successful_attempts,

            "last_attempt": last_attempt,

        },

    }), 200


@security_api.route(
    "/users/<user_id>/status",
    methods=["GET"],
)
def get_security_status(user_id):

    try:
        object_id = ObjectId(user_id)

    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400


    status = current_app.db[
        "security_status"
    ].find_one({
        "user_id": object_id,
    })


    if not status:

        return jsonify({

            "status": "success",

            "data": {

                "user_id": user_id,

                "security_status": "normal",

                "reason": None,

            },

        }), 200


    return jsonify({

        "status": "success",

        "data": {

            "user_id": user_id,

            "security_status": status.get(
                "security_status",
                "normal",
            ),

            "reason": status.get(
                "reason",
            ),

            "updated_at": (
                status["updated_at"].isoformat()
                if status.get("updated_at")
                else None
            ),

        },

    }), 200

@security_api.route(
    "/users/<user_id>/status",
    methods=["POST"],
)
def update_security_status(user_id):

    try:
        object_id = ObjectId(user_id)

    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400


    payload = request.get_json(
        silent=True
    ) or {}


    security_status = str(
        payload.get(
            "security_status",
            "",
        )
    ).strip().lower()


    reason = str(
        payload.get(
            "reason",
            "",
        )
    ).strip()


    allowed_statuses = [
        "normal",
        "suspicious",
        "locked",
    ]


    if security_status not in allowed_statuses:

        return jsonify({
            "status": "error",
            "message": "Invalid security status.",
        }), 400


    now = datetime.now(
        timezone.utc
    )


    current_app.db[
        "security_status"
    ].update_one(
        {
            "user_id": object_id,
        },
        {
            "$set": {

                "security_status": security_status,

                "reason": reason,

                "updated_at": now,

            }
        },
        upsert=True,
    )


    return jsonify({

        "status": "success",

        "message": "Security status updated.",

        "data": {

            "user_id": user_id,

            "security_status": security_status,

            "reason": reason,

        },

    }), 200

