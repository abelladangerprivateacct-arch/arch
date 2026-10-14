from flask import Blueprint, request, jsonify
from bson.objectid import ObjectId
from pymongo import MongoClient
from datetime import datetime
from dotenv import load_dotenv
import os
import jwt
from auth_helpers import require_admin_permission

# --- Load environment variables ---
load_dotenv()
mongo_uri = os.getenv("MONGO_URI")
db_name = os.getenv("DB_NAME")
jwt_secret = os.getenv("JWT_SECRET", "supersecret")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
APP_BASE_URL = os.getenv("APP_BASE_URL")

# --- Database setup ---
client = MongoClient(mongo_uri)
db = client[db_name]
users = db.users

# --- Blueprint setup ---
referral_bp = Blueprint("referral_bp", __name__, url_prefix="/api/referrals")


def unique_referred_users(referred_users):
    """Return one referral entry per referred user, preferring confirmed data."""
    unique = {}
    for index, referral in enumerate(referred_users or []):
        if isinstance(referral, dict):
            user_id = str(referral.get("userId") or "").strip()
            email = str(referral.get("email") or "").strip().lower()
            key = user_id or email or f"legacy:{index}"
        else:
            key = f"legacy:{index}"

        existing = unique.get(key)
        if existing is None or (
            isinstance(referral, dict)
            and referral.get("investmentConfirmed")
            and not existing.get("investmentConfirmed")
        ):
            unique[key] = referral

    return list(unique.values())


# --- Helper: Decode JWT token ---
def get_user_from_token():
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.split(" ")[1]
    try:
        decoded = jwt.decode(token, jwt_secret, algorithms=["HS256"])
        user_id = decoded.get("user_id")
        if not user_id:
            return None
        return users.find_one({"_id": ObjectId(user_id)})
    except Exception as e:
        print(f"JWT decode error: {e}")
        return None


def get_current_origin():
    frontend_origin = request.headers.get("Origin")
    if frontend_origin:
        return frontend_origin.rstrip("/")

    if APP_BASE_URL:
        return APP_BASE_URL.rstrip("/")

    forwarded_proto = request.headers.get("X-Forwarded-Proto") or request.headers.get("X-Forwarded-Protocol")
    forwarded_host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host")

    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}".rstrip("/")

    return request.host_url.rstrip("/")


# ✅ GET: Fetch referral info for authenticated user
@referral_bp.route("/me", methods=["GET"])
def get_my_referral_info():
    try:
        user = get_user_from_token()
        if not user:
            return jsonify({"success": False, "error": "Invalid or missing token"}), 401

        referral_code = user.get("referralCode")
        if not referral_code:
            return jsonify({"success": False, "error": "Referral code missing"}), 400

        referred_users = unique_referred_users(user.get("referredUsers", []))
        total_referrals = len(referred_users)
        total_earnings = float(user.get("referralEarnings", 0.0))
        referral_link = f"{get_current_origin()}/auth/register?ref={referral_code}"

        return jsonify({
            "success": True,
            "referralCode": referral_code,
            "referralLink": referral_link,
            "totalReferrals": total_referrals,
            "totalEarnings": total_earnings,
            "referredUsers": referred_users
        }), 200

    except Exception as e:
        print(f"Referral fetch error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


@referral_bp.route("/admin-summary", methods=["GET"])
@require_admin_permission("referrals:read")
def get_referral_admin_summary():
    try:
        users_data = list(users.find({}, {
            "firstName": 1,
            "lastName": 1,
            "email": 1,
            "referralCode": 1,
            "referralEarnings": 1,
            "referredUsers": 1,
            "referredBy": 1,
            "createdAt": 1
        }))

        total_referral_codes = sum(1 for user in users_data if user.get("referralCode"))
        unique_referrals = [
            unique_referred_users(user.get("referredUsers", []))
            for user in users_data
        ]
        total_users_with_referrals = sum(1 for referrals in unique_referrals if referrals)
        total_referral_earnings = sum(float(user.get("referralEarnings", 0) or 0) for user in users_data)

        return jsonify({
            "success": True,
            "summary": {
                "totalReferralCodes": total_referral_codes,
                "totalUsersWithReferrals": total_users_with_referrals,
                "totalReferralEarnings": total_referral_earnings,
                "totalReferralsTriggered": sum(len(referrals) for referrals in unique_referrals)
            }
        }), 200
    except Exception as e:
        print(f"Error fetching referral admin summary: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# ✅ Optional: Admin route to view all referrals
@referral_bp.route("/", methods=["GET"])
@require_admin_permission("referrals:read")
def get_all_referrals():
    try:
        pipeline = [
            {"$project": {
                "firstName": 1,
                "lastName": 1,
                "email": 1,
                "referralCode": 1,
                "referredUsers": 1,
                "referralEarnings": 1
            }},
            {"$sort": {"referralEarnings": -1}}
        ]

        all_referrals = list(users.aggregate(pipeline))
        for u in all_referrals:
            u["totalReferrals"] = len(unique_referred_users(u.pop("referredUsers", [])))
            u["_id"] = str(u["_id"])

        return jsonify({"success": True, "referrals": all_referrals}), 200

    except Exception as e:
        print(f"Error fetching all referrals: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
