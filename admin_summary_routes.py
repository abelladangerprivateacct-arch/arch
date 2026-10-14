from flask import Blueprint, jsonify, request
from bson.objectid import ObjectId
from pymongo import MongoClient
from auth_helpers import require_admin_permission
from datetime import datetime
import os
from dotenv import load_dotenv
from flask_cors import CORS

# --- Load environment variables ---
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")

# --- MongoDB setup ---
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users = db.users

# --- Blueprint for Admin Summaries ---
admin_summary_bp = Blueprint("admin_summary", __name__, url_prefix="/api/admin/summary")
CORS(admin_summary_bp, resources={r"/*": {"origins": "http://localhost:8000/capitello/"}}, supports_credentials=True)

# --- 1️⃣ Admin Dashboard Summary ---
@admin_summary_bp.route("/", methods=["GET"])
@require_admin_permission("dashboard:read")
def admin_summary():
    if not request.is_admin:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        all_users = list(users.find({}))
        total_users = len(all_users)

        total_investments = 0
        total_pending = 0
        total_active = 0
        total_rejected = 0
        total_amount = 0.0
        total_active_amount = 0.0
        total_pending_amount = 0.0
        total_rejected_amount = 0.0

        for user in all_users:
            for inv in user.get("investments", []):
                total_investments += 1
                total_amount += inv.get("amount", 0.0)

                status = inv.get("status", "pending")
                if status == "pending":
                    total_pending += 1
                    total_pending_amount += inv.get("amount", 0.0)
                elif status == "active":
                    total_active += 1
                    total_active_amount += inv.get("amount", 0.0)
                elif status == "rejected":
                    total_rejected += 1
                    total_rejected_amount += inv.get("amount", 0.0)

        summary = {
            "totalUsers": total_users,
            "totalInvestments": total_investments,
            "totalAmount": round(total_amount, 2),
            "pending": {
                "count": total_pending,
                "amount": round(total_pending_amount, 2)
            },
            "active": {
                "count": total_active,
                "amount": round(total_active_amount, 2)
            },
            "rejected": {
                "count": total_rejected,
                "amount": round(total_rejected_amount, 2)
            }
        }

        return jsonify({"success": True, "summary": summary}), 200

    except Exception as e:
        print(f"Admin summary error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# --- 2️⃣ Get platform ROI summary ---
@admin_summary_bp.route("/roi", methods=["GET"])
@require_admin_permission("dashboard:read")
def roi_summary():
    if not request.is_admin:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        all_users = list(users.find({}))
        total_roi_percent = 0.0
        total_active = 0

        for user in all_users:
            for inv in user.get("investments", []):
                if inv.get("status") == "active":
                    total_active += 1
                    total_roi_percent += inv.get("expectedGrowth", 0.0)

        avg_roi = (total_roi_percent / total_active) if total_active > 0 else 0

        return jsonify({
            "success": True,
            "roiSummary": {
                "totalActiveInvestments": total_active,
                "averageDailyROI": round(avg_roi, 2)
            }
        }), 200

    except Exception as e:
        print(f"ROI summary error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
