from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv
import os
import resend
from flask_cors import CORS


from auth_helpers import authenticate_user  # same as in your investment_bp
from notifications import record_user_notification

# --- Load environment variables ---
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
RESEND_API_KEY = os.getenv("RESEND_API_KEY")

# --- Initialize Resend ---
resend.api_key = RESEND_API_KEY

# --- MongoDB Setup ---
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users = db.users

# --- Flask Blueprint ---
profile_bp = Blueprint("profile", __name__, url_prefix="/api/user")
@profile_bp.route("/dashboard/", methods=["GET"])
@authenticate_user
def get_dashboard_data():
    try:
        user = users.find_one({"_id": ObjectId(request.user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        investments = user.get("investments", [])
        total_investments = len(investments)
        total_invested = sum(inv.get("amount", 0) for inv in investments if inv.get("status") == "active")
        total_earnings = user.get("earnings", 0.0)
        available_balance = user.get("availableBalance", 0.0)

        return jsonify({
            "success": True,
            "dashboard": {
                "totalInvestments": total_investments,
                "totalInvested": total_invested,
                "earnings": total_earnings,
                "availableBalance": available_balance
            }
        }), 200

    except Exception as e:
        print(f"Dashboard fetch error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500

# ============================================================
# 👤 UPDATE USER PROFILE (Name, Email)
# ============================================================
@profile_bp.route("/profile/", methods=["PUT"])
@authenticate_user
def update_profile():
    try:
        data = request.get_json()
        first_name = data.get("firstName")
        last_name = data.get("lastName")
        email = data.get("email")

        if not all([first_name, last_name, email]):
            return jsonify({"success": False, "error": "All fields are required"}), 400

        # Check if email is already used by another user
        existing_user = users.find_one({"email": email, "_id": {"$ne": ObjectId(request.user_id)}})
        if existing_user:
            return jsonify({"success": False, "error": "Email is already in use"}), 400

        users.update_one(
            {"_id": ObjectId(request.user_id)},
            {"$set": {
                "firstName": first_name,
                "lastName": last_name,
                "email": email,
                "updatedAt": datetime.utcnow()
            }}
        )

        updated_user = users.find_one({"_id": ObjectId(request.user_id)}, {"password": 0})
        updated_user["_id"] = str(updated_user["_id"])

        return jsonify({
            "success": True,
            "message": "Profile updated successfully",
            "user": updated_user
        }), 200

    except Exception as e:
        print(f"Profile update error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# ============================================================
# 🔐 UPDATE PASSWORD (No old password required)
# ============================================================
@profile_bp.route("/password/", methods=["PUT"])
@authenticate_user
def update_password():
    try:
        data = request.get_json(silent=True) or {}
        current_password = data.get("currentPassword")
        new_password = data.get("newPassword") or data.get("password")
        if not current_password or not new_password:
            return jsonify({"success": False, "error": "Current and new passwords are required"}), 400
        if len(new_password) < 8 or not any(char.isupper() for char in new_password) or not any(char.islower() for char in new_password) or not any(char.isdigit() for char in new_password):
            return jsonify({"success": False, "error": "Use at least 8 characters with uppercase, lowercase, and a number"}), 400

        user_id = ObjectId(request.user_id)
        user = users.find_one({"_id": user_id})
        if not user or not check_password_hash(user.get("password", ""), current_password):
            return jsonify({"success": False, "error": "Current password is incorrect"}), 401

        hashed_pw = generate_password_hash(new_password)

        users.update_one(
            {"_id": user_id},
            {"$set": {"password": hashed_pw, "updatedAt": datetime.utcnow()}}
        )
        record_user_notification(
            user_id,
            f"password-changed:{datetime.utcnow().isoformat()}",
            "security",
            "Password changed",
            "Your account password was changed. If you did not make this change, contact support.",
        )

        return jsonify({"success": True, "message": "Password updated successfully"}), 200

    except Exception as e:
        print(f"Password update error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# ============================================================
# 📄 GET PROFILE INFO
# ============================================================
@profile_bp.route("/", methods=["GET"])
@authenticate_user
def get_profile():
    try:
        user = users.find_one({"_id": ObjectId(request.user_id)}, {"password": 0})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        user["_id"] = str(user["_id"])
        return jsonify({"success": True, "user": user}), 200

    except Exception as e:
        print(f"Profile fetch error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
