from functools import wraps

import jwt

from bson import ObjectId

from flask import current_app, jsonify, request
from extensions import bcrypt


def decode_token():
    authorization = request.headers.get(
        "Authorization",
        "",
    )

    if not authorization.startswith("Bearer "):
        return None

    token = authorization[7:].strip()

    if not token:
        return None

    try:
        return jwt.decode(
            token,
            current_app.config["JWT_SECRET_KEY"],
            algorithms=["HS256"],
        )

    except jwt.InvalidTokenError:
        return None


def hash_pin(pin):
    return bcrypt.generate_password_hash(
        pin
    ).decode("utf-8")


def verify_pin(pin_hash, pin):
    return bcrypt.check_password_hash(
        pin_hash,
        pin,
    )


def user_token_required(function):

    @wraps(function)
    def decorated(*args, **kwargs):

        payload = decode_token()

        if not payload:
            return jsonify({
                "status": "error",
                "message": "Invalid or expired token.",
            }), 401


        user_id = payload.get("sub")


        try:
            object_id = ObjectId(user_id)

        except Exception:
            return jsonify({
                "status": "error",
                "message": "Invalid user identity.",
            }), 401


        user = current_app.db["users"].find_one({
            "_id": object_id,
            "is_active": True,
        })


        if not user:
            return jsonify({
                "status": "error",
                "message": "Account is inactive or unavailable.",
            }), 403


        request.current_user = user

        request.current_user_id = str(
            user["_id"]
        )


        return function(*args, **kwargs)


    return decorated



def withdrawal_pin_required(function):

    @wraps(function)
    def decorated(*args, **kwargs):

        user = current_app.db["users"].find_one({
            "_id": ObjectId(
                request.current_user_id
            )
        })


        if not user:
            return jsonify({
                "status": "error",
                "message": "User not found.",
            }),404


        if not user.get(
            "withdrawal_pin_hash"
        ):
            return jsonify({
                "status": "error",
                "message": "Withdrawal PIN setup required.",
                "code": "PIN_REQUIRED",
            }),403


        return function(*args, **kwargs)


    return decorated



def admin_token_required(function):

    @wraps(function)
    def decorated(*args, **kwargs):

        payload = decode_token()


        if not payload:
            return jsonify({
                "status": "error",
                "message": "Invalid or expired token.",
            }),401


        if payload.get("role") != "admin":
            return jsonify({
                "status": "error",
                "message": "Admin access required.",
            }),403


        user_id = payload.get("sub")


        try:
            object_id = ObjectId(user_id)

        except Exception:
            return jsonify({
                "status":"error",
                "message":"Invalid administrator identity.",
            }),401



        admin = current_app.db["users"].find_one({
            "_id":object_id,
            "role":"admin",
            "is_active":True,
        })


        if not admin:
            return jsonify({
                "status":"error",
                "message":"Administrator account is inactive or unavailable.",
            }),403


        request.current_admin = admin

        request.current_admin_id = str(
            admin["_id"]
        )


        return function(*args, **kwargs)


    return decorated