from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta, timezone
import jwt
import os
import base64
import hashlib
import hmac
import secrets
import struct
import time
import re
from urllib.parse import quote
from bson import ObjectId
from dotenv import load_dotenv
from admin_roles import ROLE_DESCRIPTIONS, ROLE_PERMISSIONS, role_for_admin
from auth_helpers import authenticate_admin, require_admin_permission
from notifications import record_admin_action

# --- Load environment variables ---
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ADMIN_DEFAULT_PASSWORD = os.getenv("ADMIN_DEFAULT_PASSWORD")  # First password used
ADMIN_DEFAULT_EMAIL = os.getenv("ADMIN_DEFAULT_EMAIL", "").strip().lower()

# --- MongoDB setup ---
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
admins = db.admins

# --- Blueprint setup ---
admin_auth_bp = Blueprint("admin_auth", __name__, url_prefix="/api/admin/auth")


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --- Helper: Generate JWT ---
def generate_admin_token(admin_id, role=None):
    payload = {
        "user_id": str(admin_id),
        "is_admin": True,
        "role": role,
        "exp": utc_now() + timedelta(hours=1)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token


def generate_setup_token(admin_id):
    return jwt.encode({
        "user_id": str(admin_id),
        "purpose": "admin_mfa_setup",
        "exp": utc_now() + timedelta(minutes=10),
    }, JWT_SECRET, algorithm=JWT_ALGORITHM)


def generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def verify_totp(secret, code, timestamp=None):
    if not isinstance(code, str) or not code.isdigit() or len(code) != 6:
        return False
    try:
        padded_secret = secret + "=" * ((8 - len(secret) % 8) % 8)
        key = base64.b32decode(padded_secret, casefold=True)
        counter = int((timestamp if timestamp is not None else time.time()) // 30)
        for offset in (-1, 0, 1):
            digest = hmac.new(key, struct.pack(">Q", counter + offset), hashlib.sha1).digest()
            index = digest[-1] & 0x0F
            value = struct.unpack(">I", digest[index:index + 4])[0] & 0x7FFFFFFF
            expected = f"{value % 1_000_000:06d}"
            if hmac.compare_digest(expected, code):
                return True
    except (ValueError, TypeError):
        return False
    return False


def get_setup_admin():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    try:
        payload = jwt.decode(auth_header[7:], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        if payload.get("purpose") != "admin_mfa_setup":
            return None
        admin = admins.find_one({"_id": ObjectId(payload.get("user_id"))})
        return admin if admin and admin.get("isActive") is not False and not admin.get("mfaEnabled") else None
    except Exception:
        return None


@admin_auth_bp.route("/mfa/setup", methods=["POST"])
def setup_admin_mfa():
    admin = get_setup_admin()
    if not admin:
        return jsonify({"success": False, "error": "Valid MFA setup session required"}), 401

    secret = admin.get("mfaPendingSecret") or generate_totp_secret()
    admins.update_one({"_id": admin["_id"]}, {"$set": {"mfaPendingSecret": secret}})
    label = quote(f"Capitello:{admin.get('email', 'admin')}")
    issuer = quote("Capitello")
    uri = f"otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30"
    return jsonify({"success": True, "secret": secret, "otpauthUri": uri}), 200


@admin_auth_bp.route("/mfa/enable", methods=["POST"])
def enable_admin_mfa():
    admin = get_setup_admin()
    if not admin:
        return jsonify({"success": False, "error": "Valid MFA setup session required"}), 401
    data = request.get_json(silent=True) or {}
    secret = admin.get("mfaPendingSecret")
    if not secret or not verify_totp(secret, str(data.get("code", "")).strip()):
        return jsonify({"success": False, "error": "Authenticator code is invalid"}), 400

    updated = admins.update_one(
        {"_id": admin["_id"], "mfaPendingSecret": secret},
        {"$set": {"mfaSecret": secret, "mfaEnabled": True, "mfaEnabledAt": utc_now()},
         "$unset": {"mfaPendingSecret": ""}},
    )
    if updated.modified_count != 1:
        return jsonify({"success": False, "error": "MFA setup changed. Start setup again."}), 409
    return jsonify({
        "success": True,
        "token": generate_admin_token(admin["_id"], role_for_admin(admin)),
        "email": admin["email"],
        "role": role_for_admin(admin),
    }), 200


def valid_admin_password(password):
    return (
        isinstance(password, str)
        and len(password) >= 12
        and any(char.isupper() for char in password)
        and any(char.islower() for char in password)
        and any(char.isdigit() for char in password)
    )


@admin_auth_bp.route("/invitations/accept", methods=["POST"])
def accept_admin_invitation():
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "").strip()
    password = data.get("password")
    if not token or not valid_admin_password(password):
        return jsonify({
            "success": False,
            "error": "A valid invitation and a 12+ character password with uppercase, lowercase, and a number are required",
        }), 400

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = utc_now()
    admin = admins.find_one({
        "inviteTokenHash": token_hash,
        "inviteExpiresAt": {"$gt": now},
        "isActive": False,
    })
    if not admin:
        return jsonify({"success": False, "error": "This invitation is invalid, expired, or already used"}), 400

    result = admins.update_one(
        {
            "_id": admin["_id"],
            "inviteTokenHash": token_hash,
            "inviteExpiresAt": {"$gt": now},
            "isActive": False,
        },
        {
            "$set": {
                "password": generate_password_hash(password),
                "isActive": True,
                "passwordSetAt": now,
                "updatedAt": now,
            },
            "$unset": {"inviteTokenHash": "", "inviteExpiresAt": ""},
        },
    )
    if result.modified_count != 1:
        return jsonify({"success": False, "error": "This invitation has already been used"}), 409

    return jsonify({
        "success": True,
        "message": "Password set. Set up your authenticator to finish account activation.",
        "setupToken": generate_setup_token(admin["_id"]),
        "email": admin["email"],
        "role": role_for_admin(admin),
    }), 200


@admin_auth_bp.route("/me", methods=["GET"])
@authenticate_admin
def get_admin_session():
    return jsonify({
        "success": True,
        "admin": {
            "id": request.user_id,
            "email": request.admin.get("email"),
            "role": request.admin_role,
            "permissions": sorted(request.admin_permissions),
            "mfaEnabled": bool(request.admin.get("mfaEnabled")),
        },
        "roles": ROLE_DESCRIPTIONS,
    }), 200


@admin_auth_bp.route("/refresh", methods=["POST"])
@authenticate_admin
def refresh_admin_session():
    """Issue a fresh one-hour token for an active admin session."""
    return jsonify({
        "success": True,
        "token": generate_admin_token(request.user_id, request.admin_role),
    }), 200


@admin_auth_bp.route("/admins", methods=["GET"])
@require_admin_permission("admins:manage")
def list_admin_accounts():
    records = admins.find({}, {
        "email": 1,
        "role": 1,
        "isSuperAdmin": 1,
        "isActive": 1,
        "mfaEnabled": 1,
        "createdAt": 1,
        "lastLoginAt": 1,
        "password": 1,
    }).sort("createdAt", -1)
    result = []
    for admin in records:
        result.append({
            "id": str(admin["_id"]),
            "email": admin.get("email", ""),
            "role": role_for_admin(admin),
            "roleDescription": ROLE_DESCRIPTIONS.get(role_for_admin(admin), ""),
            "isActive": admin.get("isActive", True),
            "hasPassword": bool(admin.get("password")),
            "mfaEnabled": bool(admin.get("mfaEnabled")),
            "createdAt": admin.get("createdAt").isoformat() if admin.get("createdAt") else None,
            "lastLoginAt": admin.get("lastLoginAt").isoformat() if admin.get("lastLoginAt") else None,
        })
    return jsonify({"success": True, "admins": result, "roles": ROLE_DESCRIPTIONS}), 200


@admin_auth_bp.route("/admins", methods=["POST"])
@require_admin_permission("admins:manage")
def invite_admin():
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    role = str(data.get("role") or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        return jsonify({"success": False, "error": "Enter a valid email address"}), 400
    if role not in ROLE_PERMISSIONS:
        return jsonify({"success": False, "error": "Choose a valid admin role"}), 400
    if admins.find_one({"email": email}, {"_id": 1}):
        return jsonify({"success": False, "error": "An administrator with this email already exists"}), 409

    invite_token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(hours=72)
    result = admins.insert_one({
        "email": email,
        "role": role,
        "isActive": False,
        "mfaEnabled": False,
        "createdAt": utc_now(),
        "invitedBy": request.user_id,
        "inviteTokenHash": hashlib.sha256(invite_token.encode("utf-8")).hexdigest(),
        "inviteExpiresAt": expires_at,
    })
    record_admin_action(request.user_id, "admin.invited", related_id=result.inserted_id, details={"email": email, "role": role})
    return jsonify({
        "success": True,
        "admin": {"id": str(result.inserted_id), "email": email, "role": role},
        "inviteToken": invite_token,
        "expiresAt": expires_at.isoformat(),
        "message": "Share this one-time invite link with the new admin. They will set their own password and MFA.",
    }), 201


@admin_auth_bp.route("/admins/<admin_id>/invitation", methods=["POST"])
@require_admin_permission("admins:manage")
def renew_admin_invitation(admin_id):
    if not ObjectId.is_valid(admin_id):
        return jsonify({"success": False, "error": "Invalid admin ID"}), 400
    object_id = ObjectId(admin_id)
    admin = admins.find_one({"_id": object_id, "isActive": False, "password": {"$exists": False}})
    if not admin:
        return jsonify({"success": False, "error": "Only an unactivated invitation can be renewed"}), 409

    invite_token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(hours=72)
    updated = admins.update_one(
        {"_id": object_id, "isActive": False, "password": {"$exists": False}},
        {"$set": {
            "inviteTokenHash": hashlib.sha256(invite_token.encode("utf-8")).hexdigest(),
            "inviteExpiresAt": expires_at,
            "updatedAt": utc_now(),
        }},
    )
    if updated.modified_count != 1:
        return jsonify({"success": False, "error": "Invitation changed; refresh and try again"}), 409
    record_admin_action(request.user_id, "admin.invitation.renewed", related_id=object_id, details={"email": admin.get("email"), "role": role_for_admin(admin)})
    return jsonify({
        "success": True,
        "admin": {"id": str(object_id), "email": admin.get("email"), "role": role_for_admin(admin)},
        "inviteToken": invite_token,
        "expiresAt": expires_at.isoformat(),
    }), 200


@admin_auth_bp.route("/admins/<admin_id>", methods=["PATCH"])
@require_admin_permission("admins:manage")
def update_admin_account(admin_id):
    if not ObjectId.is_valid(admin_id):
        return jsonify({"success": False, "error": "Invalid admin ID"}), 400
    data = request.get_json(silent=True) or {}
    if not data or set(data) - {"role", "isActive"}:
        return jsonify({"success": False, "error": "Only role and isActive may be updated"}), 400
    if "role" in data and data["role"] not in ROLE_PERMISSIONS:
        return jsonify({"success": False, "error": "Choose a valid admin role"}), 400
    if "isActive" in data and not isinstance(data["isActive"], bool):
        return jsonify({"success": False, "error": "isActive must be true or false"}), 400

    object_id = ObjectId(admin_id)
    target = admins.find_one({"_id": object_id})
    if not target:
        return jsonify({"success": False, "error": "Admin not found"}), 404
    if not target.get("password") and data.get("isActive") is True:
        return jsonify({"success": False, "error": "An invitee must set a password and enroll MFA before activation"}), 409

    current_role = role_for_admin(target)
    next_role = data.get("role", current_role)
    next_active = data.get("isActive", target.get("isActive", True))
    is_self = str(object_id) == request.user_id
    if is_self and (next_role != "super_admin" or not next_active):
        return jsonify({"success": False, "error": "You cannot remove your own super-admin access"}), 409

    removing_super_admin = current_role == "super_admin" and (next_role != "super_admin" or not next_active)
    if removing_super_admin:
        active_super_admins = admins.count_documents({
            "isActive": {"$ne": False},
            "mfaEnabled": True,
            "$or": [
                {"role": "super_admin"},
                {"role": {"$exists": False}, "isSuperAdmin": True},
            ],
        })
        if active_super_admins <= 1:
            return jsonify({"success": False, "error": "At least one active super admin must remain"}), 409

    changes = {}
    if "role" in data:
        changes["role"] = next_role
    if "isActive" in data:
        changes["isActive"] = next_active
    changes["updatedAt"] = utc_now()
    admins.update_one({"_id": object_id}, {"$set": changes})
    record_admin_action(request.user_id, "admin.updated", related_id=object_id, details={"role": next_role, "isActive": next_active})
    return jsonify({"success": True, "admin": {
        "id": str(object_id),
        "email": target.get("email", ""),
        "role": next_role,
        "isActive": next_active,
    }}), 200


# --- 🔑 Admin Login ---
@admin_auth_bp.route("/login", methods=["POST"])
def admin_login():
    try:
        data = request.get_json(silent=True) or {}
        email = str(data.get("email") or "").strip().lower()
        password = data.get("password")
        otp_code = str(data.get("otpCode") or "").strip()

        if not email or not password:
            return jsonify({"success": False, "error": "Email and password are required"}), 400

        admin = admins.find_one({"email": email})

        # --- First-time admin setup ---
        if not admin:
            # If no admin exists, create first admin using default password
            if (
                ADMIN_DEFAULT_EMAIL
                and email == ADMIN_DEFAULT_EMAIL
                and password == ADMIN_DEFAULT_PASSWORD
                and admins.count_documents({}) == 0
            ):
                hashed_pw = generate_password_hash(password)
                new_admin = {
                    "email": email,
                    "password": hashed_pw,
                    "createdAt": utc_now(),
                    "isSuperAdmin": True,
                    "role": "super_admin",
                    "isActive": True,
                    "mfaEnabled": False,
                }
                inserted = admins.insert_one(new_admin)
                setup_token = generate_setup_token(inserted.inserted_id)
                return jsonify({
                    "success": True,
                    "message": "Admin account created successfully",
                    "mfaSetupRequired": True,
                    "setupToken": setup_token
                }), 201
            else:
                return jsonify({"success": False, "error": "Invalid admin credentials or admin bootstrap is unavailable"}), 401

        if admin.get("isActive") is False or not admin.get("password"):
            return jsonify({"success": False, "error": "This administrator account is not active"}), 403

        role = role_for_admin(admin)
        if not role:
            return jsonify({"success": False, "error": "This administrator account has an invalid role"}), 403

        # --- Verify password ---
        if not check_password_hash(admin["password"], password):
            return jsonify({"success": False, "error": "Invalid credentials"}), 401

        # Require a TOTP authenticator code after the initial enrollment.
        if not admin.get("mfaEnabled"):
            return jsonify({
                "success": True,
                "mfaSetupRequired": True,
                "setupToken": generate_setup_token(admin["_id"]),
            }), 200
        if not verify_totp(admin.get("mfaSecret", ""), otp_code):
            return jsonify({"success": False, "error": "A valid authenticator code is required"}), 401

        # --- Generate token ---
        admins.update_one({"_id": admin["_id"]}, {"$set": {"lastLoginAt": utc_now()}})
        token = generate_admin_token(admin["_id"], role)
        return jsonify({"success": True, "token": token, "email": admin["email"], "role": role}), 200

    except Exception as e:
        print(f"Admin login error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# --- 🔐 Change Admin Password ---
@admin_auth_bp.route("/change-password", methods=["POST"])
@authenticate_admin
def change_admin_password():
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        token = auth_header.split(" ")[1]
        try:
            decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            admin_id = decoded.get("user_id")
            is_admin = decoded.get("is_admin", False)
            if not is_admin:
                return jsonify({"success": False, "error": "Admin privileges required"}), 403
        except jwt.ExpiredSignatureError:
            return jsonify({"success": False, "error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"success": False, "error": "Invalid token"}), 401

        data = request.get_json(silent=True) or {}
        old_password = data.get("oldPassword")
        new_password = data.get("newPassword")

        if not old_password or not new_password:
            return jsonify({"success": False, "error": "Missing required fields"}), 400

        if len(new_password) < 12 or not any(char.isupper() for char in new_password) or not any(char.islower() for char in new_password) or not any(char.isdigit() for char in new_password):
            return jsonify({"success": False, "error": "Use at least 12 characters with uppercase, lowercase, and a number"}), 400

        if not ObjectId.is_valid(admin_id):
            return jsonify({"success": False, "error": "Invalid admin session"}), 401
        admin = admins.find_one({"_id": ObjectId(admin_id)})
        if not admin:
            return jsonify({"success": False, "error": "Admin not found"}), 404

        if not check_password_hash(admin["password"], old_password):
            return jsonify({"success": False, "error": "Incorrect current password"}), 401

        hashed_pw = generate_password_hash(new_password)
        admins.update_one({"_id": admin["_id"]}, {"$set": {"password": hashed_pw}})

        return jsonify({"success": True, "message": "Password updated successfully"}), 200

    except Exception as e:
        print(f"Change admin password error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# --- 📧 Change Admin Email ---
@admin_auth_bp.route("/change-email", methods=["POST"])
@authenticate_admin
def change_admin_email():
    try:
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return jsonify({"success": False, "error": "Unauthorized"}), 401

        token = auth_header.split(" ")[1]
        try:
            decoded = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            admin_id = decoded.get("user_id")
            is_admin = decoded.get("is_admin", False)
            if not is_admin:
                return jsonify({"success": False, "error": "Admin privileges required"}), 403
        except jwt.ExpiredSignatureError:
            return jsonify({"success": False, "error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"success": False, "error": "Invalid token"}), 401

        data = request.get_json(silent=True) or {}
        new_email = data.get("newEmail")

        if not new_email:
            return jsonify({"success": False, "error": "New email is required"}), 400

        if not ObjectId.is_valid(admin_id):
            return jsonify({"success": False, "error": "Invalid admin session"}), 401
        admins.update_one({"_id": ObjectId(admin_id)}, {"$set": {"email": new_email}})
        return jsonify({"success": True, "message": "Email updated successfully"}), 200

    except Exception as e:
        print(f"Change admin email error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
