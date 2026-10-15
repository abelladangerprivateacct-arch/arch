# auth_helpers.py
from functools import wraps
from flask import request, jsonify
import jwt
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from bson import ObjectId
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from admin_roles import has_permission, permissions_for_role, role_for_admin

# Load environment variables
load_dotenv()
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
admin_client = MongoClient(os.getenv("MONGO_URI"))
admins = admin_client[os.getenv("DB_NAME")].admins


def admin_mfa_required():
    value = os.getenv("REQUIRE_ADMIN_MFA", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}

# --------------------------
# Token creation
# --------------------------
def create_token(user_id, is_admin=False, minutes_valid=30):
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET must be configured")
    payload = {
        "user_id": str(user_id),
        "is_admin": is_admin,
        "exp": datetime.utcnow() + timedelta(minutes=minutes_valid)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token

# --------------------------
# Regular user authentication
# --------------------------
def authenticate_user(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401

        token = auth_header.split(" ")[1]
        try:
            decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user_id = decoded.get("user_id")
            if decoded.get("is_admin") or not ObjectId.is_valid(user_id):
                return jsonify({"error": "User session required"}), 401
            request.user_id = user_id
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        return f(*args, **kwargs)
    return wrapper

# --------------------------
# Admin authentication
# --------------------------
def authenticate_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"error": "Unauthorized"}), 401

        token = auth_header.split(" ")[1]
        try:
            decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user_id = decoded.get("user_id")
            if not ObjectId.is_valid(user_id):
                return jsonify({"error": "Invalid admin session"}), 401
            if not decoded.get("is_admin"):
                return jsonify({"error": "Admin privileges required"}), 403
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401

        try:
            admin = admins.find_one({"_id": ObjectId(user_id)})
        except PyMongoError:
            return jsonify({"error": "Admin authorization is temporarily unavailable"}), 503

        if not admin or admin.get("isActive") is False:
            return jsonify({"error": "Active admin session required"}), 401
        if admin_mfa_required() and not admin.get("mfaEnabled"):
            return jsonify({"error": "Active admin session required"}), 401

        role = role_for_admin(admin)
        if not role:
            return jsonify({"error": "Admin role is not recognized"}), 403

        request.user_id = user_id
        request.is_admin = True
        request.admin_role = role
        request.admin_permissions = permissions_for_role(role)
        request.admin = admin

        return f(*args, **kwargs)
    return wrapper


def require_admin_permission(permission):
    """Authenticate an active MFA-enabled admin and enforce a named permission."""
    def decorator(f):
        @wraps(f)
        @authenticate_admin
        def wrapper(*args, **kwargs):
            if not has_permission(request.admin_role, permission):
                return jsonify({
                    "success": False,
                    "error": "This admin role is not allowed to perform that action",
                    "requiredPermission": permission,
                }), 403
            return f(*args, **kwargs)
        return wrapper
    return decorator
