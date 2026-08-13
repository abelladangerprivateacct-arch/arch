from datetime import datetime, timedelta, timezone

import jwt
from bson import ObjectId
from flask import current_app

from extensions import bcrypt


def create_access_token(user):
    now = datetime.now(timezone.utc)

    payload = {
        "sub": str(user["_id"]),
        "email": user["email"],
        "role": user.get("role", "user"),
        "iat": now,
        "exp": now + timedelta(
            hours=current_app.config[
                "JWT_ACCESS_TOKEN_EXPIRES_HOURS"
            ]
        ),
    }

    return jwt.encode(
        payload,
        current_app.config["JWT_SECRET_KEY"],
        algorithm="HS256",
    )


def decode_access_token(token):
    try:
        return jwt.decode(
            token,
            current_app.config["JWT_SECRET_KEY"],
            algorithms=["HS256"],
        )
    except jwt.InvalidTokenError:
        return None


def find_user_by_email(email):
    return current_app.db["users"].find_one({
        "email": email.lower().strip(),
    })


def find_user_by_id(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return None

    return current_app.db["users"].find_one({
        "_id": object_id,
    })


def hash_password(password):
    return bcrypt.generate_password_hash(
        password
    ).decode("utf-8")


def verify_password(password_hash, password):
    return bcrypt.check_password_hash(
        password_hash,
        password,
    )


def serialize_user(user):
    return {
        "id": str(user["_id"]),
        "full_name": user.get("full_name", ""),
        "email": user.get("email", ""),
        "phone": user.get("phone", ""),
        "role": user.get("role", "user"),
        "is_active": user.get("is_active", True),
        "balance": user.get("balance", 0),
        "has_withdrawal_pin": bool(
            user.get("withdrawal_pin_hash")
        ),
        "created_at": (
            user["created_at"].isoformat()
            if isinstance(
                user.get("created_at"),
                datetime,
            )
            else None
        ),
    }

def hash_pin(pin):
    return bcrypt.generate_password_hash(
        pin
    ).decode("utf-8")


def verify_pin(pin_hash, pin):
    return bcrypt.check_password_hash(
        pin_hash,
        pin,
    )