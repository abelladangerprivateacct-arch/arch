from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from bson.objectid import ObjectId
from dotenv import load_dotenv
import os
import jwt
from functools import wraps
from auth_helpers import authenticate_user
# --- Load environment variables ---
load_dotenv()
MONGO_URI = os.getenv("MONGO_URI")
DB_NAME = os.getenv("DB_NAME")
JWT_SECRET = os.getenv("JWT_SECRET")
JWT_ALGORITHM = "HS256"

# --- MongoDB Setup ---
client = MongoClient(MONGO_URI)
db = client[DB_NAME]
users = db.users

# --- Flask Blueprint ---
dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")

# --- JWT Authentication decorator ---


# --- GET dashboard summary ---
@dashboard_bp.route("/", methods=["GET"])
@authenticate_user
def get_dashboard():
    try:
        user = users.find_one({"_id": ObjectId(request.user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        # Ensure investments is a list
        investments = user.get("investments", [])
        if not isinstance(investments, list):
            investments = []

        # Calculate totals for the summary
        total_invested = sum(inv.get("amount", 0.0) for inv in investments if inv.get("status") == "active")
        pending_investment = sum(inv.get("amount", 0.0) for inv in investments if inv.get("status") == "pending")
        earnings = user.get("earnings", 0.0)
        available_balance = user.get("balance", 0.0)
        wallet_balance = total_invested + earnings + available_balance

        dashboard_summary = {
            "totalInvested": total_invested,
            "pendingInvestment": pending_investment,
            "earnings": earnings,
            "availableBalance": available_balance,
            "walletBalance": wallet_balance
        }

        return jsonify({"success": True, "dashboardData": dashboard_summary}), 200

    except Exception as e:
        print(f"Dashboard fetch error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500

@dashboard_bp.route("/chart", methods=["GET"])
@authenticate_user
def get_chart_data():
    try:
        user = users.find_one({"_id": ObjectId(request.user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        investments = user.get("investments", [])
        if not investments:
            return jsonify({"success": True, "chartData": []})

        # Prepare data for Chart.js (daily earnings)
        chart_data = []
        for inv in investments:
            # Only active investments
            if inv.get("status") != "active":
                continue
            created_at = inv.get("createdAt")
            updated_at = inv.get("updatedAt")
            amount = inv.get("amount", 0.0)
            growth = inv.get("expectedGrowth", 0.0)

            # Convert to datetime
            if isinstance(updated_at, dict) and "$date" in updated_at:
                updated_at = updated_at["$date"]
            if isinstance(updated_at, str):
                updated_at = datetime.fromisoformat(updated_at)

            chart_data.append({
                "date": updated_at.isoformat(),
                "amount": amount,
                "expectedGrowth": growth
            })

        # Sort by date ascending
        chart_data.sort(key=lambda x: x["date"])

        return jsonify({"success": True, "chartData": chart_data}), 200

    except Exception as e:
        print(f"Chart fetch error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
