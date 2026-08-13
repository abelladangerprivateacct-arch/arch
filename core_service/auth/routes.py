from datetime import datetime, timezone
from functools import wraps
import secrets
from flask import Blueprint, current_app, jsonify, request

from auth.service import (
    create_access_token,
    decode_access_token,
    find_user_by_email,
    find_user_by_id,
    hash_password,
    hash_pin,
    serialize_user,
    verify_password,
)

auth_api = Blueprint(
    "auth_api",
    __name__,
)


def token_required(function):
    @wraps(function)
    def decorated(*args, **kwargs):
        authorization = request.headers.get(
            "Authorization",
            "",
        )

        if not authorization.startswith("Bearer "):
            return jsonify({
                "status": "error",
                "message": "Authentication required.",
            }), 401

        token = authorization[7:].strip()

        if not token:
            return jsonify({
                "status": "error",
                "message": "Authentication required.",
            }), 401

        payload = decode_access_token(token)

        if not payload:
            return jsonify({
                "status": "error",
                "message": "Invalid or expired token.",
            }), 401

        user = find_user_by_id(
            payload.get("sub")
        )

        if not user:
            return jsonify({
                "status": "error",
                "message": "User not found.",
            }), 401

        if user.get("is_active", True) is not True:
            return jsonify({
                "status": "error",
                "message": "Account is inactive.",
            }), 403

        request.current_user = user
        request.current_user_id = str(
            user["_id"]
        )

        return function(*args, **kwargs)

    return decorated
@auth_api.route(
    "/register",
    methods=["POST"],
)
def register():
    payload = request.get_json(
        silent=True
    ) or {}

    full_name = str(
        payload.get("full_name", "")
    ).strip()

    email = str(
        payload.get("email", "")
    ).strip().lower()

    phone = str(
        payload.get("phone", "")
    ).strip()

    password = str(
        payload.get("password", "")
    )

    referral_code = str(
        payload.get("referral_code", "")
    ).strip()

    if (
        not full_name
        or not email
        or not phone
        or not password
    ):
        return jsonify({
            "status": "error",
            "message": (
                "Full name, email, phone number, "
                "and password are required."
            ),
        }), 400

    if len(password) < current_app.config[
        "PASSWORD_MIN_LENGTH"
    ]:
        return jsonify({
            "status": "error",
            "message": (
                "Password does not meet the minimum "
                "length requirement."
            ),
        }), 400

    if find_user_by_email(email):
        return jsonify({
            "status": "error",
            "message": (
                "An account with this email already exists."
            ),
        }), 409

    referred_by = None

    if referral_code:
        referrer = current_app.db[
            "users"
        ].find_one({
            "referral_code": referral_code,
        })

        if not referrer:
            return jsonify({
                "status": "error",
                "message": "Invalid referral code.",
            }), 400

        referred_by = referrer["_id"]

    now = datetime.now(timezone.utc)

    new_referral_code = (
        f"CVM-{secrets.token_hex(4).upper()}"
    )

    while current_app.db["users"].find_one({
        "referral_code": new_referral_code,
    }):
        new_referral_code = (
            f"CVM-{secrets.token_hex(4).upper()}"
        )

    user = {
        "full_name": full_name,
        "email": email,
        "phone": phone,
        "password_hash": hash_password(
            password
        ),
        "role": "user",
        "is_active": True,
        "referral_code": new_referral_code,
        "referred_by": referred_by,
        "created_at": now,
        "updated_at": now,
    }

    result = current_app.db[
        "users"
    ].insert_one(user)

    user["_id"] = result.inserted_id

    if referred_by:
        current_app.db[
            "referrals"
        ].insert_one({
            "referrer_id": referred_by,
            "referred_user_id": user["_id"],
            "status": "pending",
            "created_at": now,
            "qualified_at": None,
            "updated_at": now,
        })

    token = create_access_token(user)

    return jsonify({
        "status": "success",
        "message": "Account created successfully.",
        "data": {
            "user": serialize_user(user),
            "access_token": token,
        },
    }), 201
@auth_api.route(
    "/login",
    methods=["POST"],
)
def login():
    payload = request.get_json(
        silent=True
    ) or {}

    email = str(
        payload.get("email", "")
    ).strip().lower()

    password = str(
        payload.get("password", "")
    )

    if not email or not password:
        return jsonify({
            "status": "error",
            "message": "Email and password are required.",
        }), 400

    user = find_user_by_email(email)

    if not user:
        return jsonify({
            "status": "error",
            "message": "Invalid email or password.",
        }), 401

    if user.get("is_active", True) is not True:
        return jsonify({
            "status": "error",
            "message": "Account is inactive.",
        }), 403

    if not verify_password(
        user.get("password_hash", ""),
        password,
    ):
        return jsonify({
            "status": "error",
            "message": "Invalid email or password.",
        }), 401

    now = datetime.now(timezone.utc)

    current_app.db["users"].update_one(
        {
            "_id": user["_id"],
        },
        {
            "$set": {
                "last_login_at": now,
                "updated_at": now,
            },
        },
    )

    token = create_access_token(user)

    return jsonify({
        "status": "success",
        "message": "Login successful.",
        "data": {
            "user": serialize_user(user),
            "access_token": token,
        },
    }), 200


@auth_api.route(
    "/me",
    methods=["GET"],
)
@token_required
def me():
    return jsonify({
        "status": "success",
        "data": {
            "user": serialize_user(
                request.current_user
            ),
        },
    }), 200


@auth_api.route(
    "/logout",
    methods=["POST"],
)
@token_required
def logout():
    return jsonify({
        "status": "success",
        "message": "Logout successful.",
    }), 200

@auth_api.route(
    "/withdrawal-pin",
    methods=["POST"],
)
@token_required
def set_withdrawal_pin():
    payload = request.get_json(
        silent=True
    ) or {}

    pin = str(
        payload.get("pin", "")
    ).strip()

    pin_length = current_app.config[
        "WITHDRAWAL_PIN_LENGTH"
    ]

    if not pin:
        return jsonify({
            "status": "error",
            "message": "Withdrawal PIN is required.",
        }), 400

    if not pin.isdigit():
        return jsonify({
            "status": "error",
            "message": "Withdrawal PIN must contain only numbers.",
        }), 400

    if len(pin) != pin_length:
        return jsonify({
            "status": "error",
            "message": (
                f"Withdrawal PIN must be exactly "
                f"{pin_length} digits."
            ),
        }), 400

    if request.current_user.get(
        "withdrawal_pin_hash"
    ):
        return jsonify({
            "status": "error",
            "message": "Withdrawal PIN has already been set.",
        }), 409

    now = datetime.now(timezone.utc)

    current_app.db["users"].update_one(
        {
            "_id": request.current_user["_id"],
        },
        {
            "$set": {
                "withdrawal_pin_hash": hash_pin(pin),
                "updated_at": now,
            },
        },
    )

    user = current_app.db["users"].find_one({
        "_id": request.current_user["_id"],
    })

    return jsonify({
        "status": "success",
        "message": "Withdrawal PIN set successfully.",
        "data": {
            "user": serialize_user(user),
        },
    }), 200