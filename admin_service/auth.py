from functools import wraps

import jwt

from bson import ObjectId

from flask import current_app, jsonify, request


def decode_token(token):
    try:
        return jwt.decode(
            token,
            current_app.config["JWT_SECRET_KEY"],
            algorithms=["HS256"],
        )
    except jwt.InvalidTokenError:
        return None


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

        payload = decode_token(token)

        if not payload:
            return jsonify({
                "status": "error",
                "message": "Invalid or expired token.",
            }), 401

        if payload.get("role") != "admin":
            return jsonify({
                "status": "error",
                "message": "Admin access required.",
            }), 403

        user_id = payload.get("sub")

        try:
            object_id = ObjectId(user_id)
        except Exception:
            return jsonify({
                "status": "error",
                "message": "Invalid administrator identity.",
            }), 401

        admin = current_app.db["users"].find_one({
            "_id": object_id,
            "role": "admin",
            "is_active": True,
        })

        if not admin:
            return jsonify({
                "status": "error",
                "message": "Administrator account is inactive or unavailable.",
            }), 403

        request.current_admin = admin
        request.current_admin_id = str(
            admin["_id"]
        )

        return function(*args, **kwargs)

    return decorated