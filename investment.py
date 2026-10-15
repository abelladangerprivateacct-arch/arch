from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import math
import os
import re
from dotenv import load_dotenv
import resend
from auth_helpers import authenticate_user, require_admin_permission
from notifications import record_user_notification, record_admin_action, record_admin_notification

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
wallet_addresses = db.wallet_addresses

# --- Flask Blueprint ---
investment_bp = Blueprint("investment", __name__, url_prefix="/api/investment")

PLAN_CATALOG = {
    "starter": {"name": "Starter Plan", "minimum": 10, "maximum": 30, "growth": 0.02},
    "growth": {"name": "Growth Plan", "minimum": 30, "maximum": 50, "growth": 0.03},
    "pro": {"name": "Pro Plan", "minimum": 50, "maximum": 100, "growth": 0.05},
    "elite": {"name": "Elite Plan", "minimum": 200, "maximum": 100000, "growth": 0.07},
}

# --- Helper: Calculate Earnings (only after 24h of last update) ---
def calculate_earnings(user):
    total_earnings = 0.0
    now = datetime.utcnow()

    for inv in user.get("investments", []):
        if inv.get("status") != "active":
            continue

        updated_at = inv.get("updatedAt")
        if not updated_at:
            continue

        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at)
            except Exception:
                continue

        if now - updated_at >= timedelta(hours=24):
            days_passed = (now - updated_at).days
            daily_profit = inv["amount"] * inv["expectedGrowth"] * days_passed
            total_earnings += daily_profit

    return round(total_earnings, 2)

# --- Helper: Send user confirmation email ---
def send_user_confirmation_email(user_email, user_name, investment_id, expected_growth):
    """Send a professional confirmation email when admin approves an investment."""
    try:
        resend.Emails.send({
            "from": "Capitello <noreply@destinytch.com.ng>",
            "to": [user_email],
            "subject": "✅ Your Capitello Investment Has Been Confirmed",
            "html": f"""
                <div style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #f6f8fb; padding: 32px;">
                    <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);">
                        
                        <div style="background-color: #14213d; color: #ffffff; padding: 20px 28px; border-top-left-radius: 12px; border-top-right-radius: 12px;">
                            <h2 style="margin: 0; font-size: 20px;">Capitello Investment Confirmation</h2>
                        </div>

                        <div style="padding: 28px;">
                            <p style="font-size: 16px; color: #222;">Hello {user_name},</p>
                            <p style="font-size: 15px; color: #444; line-height: 1.6;">
                                Your payment has been confirmed successfully, and your investment 
                                (<strong>ID: {investment_id}</strong>) is now active on <strong>Capitello</strong>.
                            </p>

                            <p style="font-size: 15px; color: #444; line-height: 1.6;">
                                You will start receiving your daily ROI within <strong>24 hours</strong>, 
                                at a rate of <strong>{expected_growth:.2f}%</strong> as per your selected plan.
                            </p>

                            <p style="margin-top: 20px; font-size: 14px; color: #555;">
                                Thank you for choosing Capitello — empowering smarter investments every day.
                            </p>

                            <p style="margin-top: 28px; font-size: 14px; color: #999;">
                                — The Capitello Team
                            </p>
                        </div>
                    </div>
                </div>
            """
        })
    except Exception as e:
        print(f"⚠️ Failed to send user confirmation email: {e}")
@investment_bp.route("/", methods=["POST"])
@authenticate_user
def create_investment():
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "A JSON request body is required"}), 400

        try:
            amount = float(data.get("amount"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Enter a valid investment amount"}), 400
        plan_id = str(data.get("planId") or "").strip().lower()
        plan = PLAN_CATALOG.get(plan_id)
        method = str(data.get("method") or "").strip().upper()
        payment_reference = str(data.get("paymentReference") or "").strip()

        if not math.isfinite(amount) or amount <= 0:
            return jsonify({"success": False, "error": "Enter a valid investment amount"}), 400
        if not plan:
            return jsonify({"success": False, "error": "Select a valid investment plan"}), 400
        if not plan["minimum"] <= amount <= plan["maximum"]:
            return jsonify({"success": False, "error": f"{plan['name']} accepts amounts from ${plan['minimum']:.2f} to ${plan['maximum']:.2f}"}), 400
        if not re.fullmatch(r"[A-Z0-9]{2,20}", method):
            return jsonify({"success": False, "error": "Select a valid payment asset"}), 400
        if not wallet_addresses.find_one({"asset": method, "enabled": True}, {"_id": 1}):
            return jsonify({"success": False, "error": "This payment asset is not currently available"}), 400
        if not 8 <= len(payment_reference) <= 200:
            return jsonify({"success": False, "error": "Enter a payment transaction reference (8–200 characters)"}), 400
        if users.find_one({"investments.paymentReference": payment_reference}, {"_id": 1}):
            return jsonify({"success": False, "error": "This payment reference has already been submitted"}), 409

        investment_id = str(ObjectId())
        now = datetime.utcnow()

        new_investment = {
            "id": investment_id,
            "planId": plan_id,
            "planName": plan["name"],
            "amount": round(amount, 2),
            "status": "pending",       # pending → active → completed
            "expectedGrowth": plan["growth"],
            "method": method,
            "paymentReference": payment_reference,
            "createdAt": now,
            "updatedAt": now
        }

        user_id = ObjectId(request.user_id)
        user = users.find_one({"_id": user_id})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        users.update_one({"_id": user_id}, {"$push": {"investments": new_investment}})
        record_user_notification(
            user_id,
            f"investment-submitted:{investment_id}",
            "deposit_submitted",
            "Deposit submitted",
            f"Your ${amount:.2f} {method} payment is awaiting verification.",
            related_id=investment_id,
            amount=amount,
        )
        investor_name = " ".join(filter(None, [user.get("firstName"), user.get("lastName")])) or "A user"
        record_admin_notification(
            f"investment-submitted:{investment_id}",
            "deposit_submitted",
            "New deposit submitted",
            f"{investor_name} submitted a ${amount:.2f} {method} payment. Awaiting verification.",
            user_id=user_id,
            related_id=investment_id,
            amount=amount,
        )

        return jsonify({
            "success": True,
            "message": "Deposit submitted and awaiting verification",
            "investmentId": investment_id
        }), 201

    except Exception as e:
        print(f"Create investment error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500

# --- Get all investments / dashboard ---
@investment_bp.route("/", methods=["GET"])
@authenticate_user
def get_investments():
    try:
        user = users.find_one({"_id": ObjectId(request.user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        investments = user.get("investments", [])
        total_invested = sum(inv["amount"] for inv in investments if inv.get("status") == "active")
        pending_investment = sum(inv["amount"] for inv in investments if inv.get("status") == "pending")

        # ✅ Calculate daily earnings
        earnings = calculate_earnings(user)

        # ✅ Persist updated earnings in the database
        users.update_one(
            {"_id": ObjectId(request.user_id)},
            {"$set": {"earnings": earnings}}
        )

        available_balance = total_invested + earnings if earnings > 0 else total_invested

        # ✅ Optionally, you can also persist the available balance if desired
        users.update_one(
            {"_id": ObjectId(request.user_id)},
            {"$set": {"availableBalance": available_balance}}
        )

        dashboard_data = {
            "totalInvested": total_invested,
            "pendingInvestment": pending_investment,
            "earnings": earnings,
            "availableBalance": available_balance,
            "withdrawFeePaid": user.get("withdrawFeePaid", False),
            "feeApproved": user.get("feeApproved", False),
            "investments": investments
        }

        return jsonify({"success": True, "dashboardData": dashboard_data}), 200

    except Exception as e:
        print(f"Get investments error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


    except Exception as e:
        print(f"Get investments error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
# --- Admin confirm investment ---
@investment_bp.route("/confirm", methods=["POST"])
@require_admin_permission("investments:manage")
def confirm_investment():
    if not request.is_admin:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        data = request.get_json()
        user_id = data.get("userId")
        investment_id = data.get("investmentId")

        if not user_id or not investment_id:
            return jsonify({"success": False, "error": "Missing userId or investmentId"}), 400

        # --- Find user making the investment ---
        user = users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        # --- Activate the user's investment ---
        updated = False
        confirmed_growth = 0.0
        investment_amount = 0.0

        for inv in user.get("investments", []):
            if inv.get("id") == investment_id and inv.get("status") == "pending":
                inv["status"] = "active"
                inv["activatedAt"] = datetime.utcnow()
                inv["updatedAt"] = datetime.utcnow()
                confirmed_growth = inv.get("expectedGrowth", 0.0)
                investment_amount = inv.get("amount", 0.0)
                updated = True
                break

        if not updated:
            return jsonify({"success": False, "error": "Pending investment not found"}), 404

        # Guard the transition in MongoDB as well as in Python so concurrent
        # admin requests cannot confirm the same deposit twice.
        now = datetime.utcnow()
        transition = users.update_one(
            {
                "_id": ObjectId(user_id),
                "investments": {"$elemMatch": {"id": investment_id, "status": "pending"}},
            },
            {"$set": {
                "investments.$.status": "active",
                "investments.$.activatedAt": now,
                "investments.$.updatedAt": now,
            }}
        )
        if transition.modified_count != 1:
            return jsonify({"success": False, "error": "Pending investment was already processed"}), 409

        record_user_notification(
            user_id,
            f"investment-confirmed:{investment_id}",
            "deposit_confirmed",
            "Deposit confirmed",
            f"Your ${investment_amount:.2f} deposit has been confirmed and your investment is active.",
            related_id=investment_id,
            amount=investment_amount,
        )
        record_admin_action(
            request.user_id,
            "investment.confirmed",
            user_id=user_id,
            related_id=investment_id,
            details={"amount": investment_amount},
        )

        # ✅ Handle Referral Reward
        reward = 0.0
        if user.get("referredBy"):
            referrer = users.find_one({"referralCode": user["referredBy"]})
            if referrer:
                # Avoid double-rewarding for same user
                already_rewarded = any(
                    ref.get("userId") == str(user["_id"]) and ref.get("reward", 0) > 0
                    for ref in referrer.get("referredUsers", [])
                )

                if not already_rewarded:
                    # ✅ Check referrer’s ACTIVE investments
                    active_investments = [
                        inv for inv in referrer.get("investments", [])
                        if inv.get("status") == "active"
                    ]
                    total_active_investment = sum(inv.get("amount", 0.0) for inv in active_investments)

                    # ✅ Determine reward
                    if total_active_investment >= 100:
                        reward = 10.0
                    else:
                        reward = 1.0  # includes $0 and <$100 cases

                    # Update the referral row created during signup instead of
                    # appending another row for the same referred user.
                    referred_user_id = str(user["_id"])
                    referral_update = {
                        "email": user.get("email"),
                        "firstName": user.get("firstName"),
                        "reward": reward,
                        "investmentId": investment_id,
                        "investmentConfirmed": True,
                        "rewardedAt": datetime.utcnow()
                    }
                    referral_filter = {
                        "_id": referrer["_id"],
                        "referredUsers.userId": referred_user_id
                    }
                    referral_result = users.update_one(
                        referral_filter,
                        {
                            "$inc": {
                                "referralEarnings": reward,
                                "availableBalance": reward
                            },
                            "$set": {
                                "referredUsers.$." + key: value
                                for key, value in referral_update.items()
                            }
                        }
                    )

                    # Older or partially migrated accounts may not have a
                    # signup referral row, so create one only in that case.
                    if referral_result.matched_count == 0:
                        users.update_one(
                            {"_id": referrer["_id"]},
                            {
                                "$inc": {
                                    "referralEarnings": reward,
                                    "availableBalance": reward
                                },
                                "$push": {
                                    "referredUsers": {
                                        "userId": referred_user_id,
                                        **referral_update
                                    }
                                }
                            }
                        )
                    record_user_notification(
                        referrer["_id"],
                        f"referral-reward:{user_id}:{investment_id}",
                        "referral_reward",
                        "Referral reward added",
                         f"A {reward:.2f} USDT referral reward was added to your account.",
                        related_id=investment_id,
                        amount=reward,
                    )

        return jsonify({
            "success": True,
            "message": "Investment confirmed, user notified, and referral reward processed",
            "referralReward": reward
        }), 200

    except Exception as e:
        print(f"Confirm investment error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# --- Admin: Get investments by status ---
@investment_bp.route("/admin/list", methods=["GET"])
@require_admin_permission("investments:read")
def list_investments_by_status():
    if not request.is_admin:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        status = request.args.get("status", "pending").lower()  # pending / active / rejected

        users_data = users.find({})
        result = []

        for user in users_data:
            user_id_str = str(user.get("_id"))  # <-- convert ObjectId to string
            for inv in user.get("investments", []):
                if inv.get("status", "").lower() == status:
                    result.append({
                        "investmentId": inv.get("id"),
                        "userId": user_id_str,  # <-- send this to frontend
                        "investorName": user.get("firstName", "Unknown"),
                        "email": user.get("email", "N/A"),
                        "amount": inv.get("amount"),
                        "expectedGrowth": inv.get("expectedGrowth"),
                        "paymentReference": inv.get("paymentReference"),
                        "status": inv.get("status"),
                        "createdAt": inv.get("createdAt"),
                        "updatedAt": inv.get("updatedAt")
                    })

        return jsonify({
            "success": True,
            "count": len(result),
            "investments": result
        }), 200

    except Exception as e:
        print(f"List investments error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500

# --- Admin: View full investment details ---
@investment_bp.route("/admin/details/<investment_id>", methods=["GET"])
@require_admin_permission("investments:read")
def view_investment_details(investment_id):
    if not request.is_admin:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        user = users.find_one({"investments.id": investment_id})
        if not user:
            return jsonify({"success": False, "error": "Investment not found"}), 404

        # Locate the specific investment in the user's list
        investment = next(
            (inv for inv in user.get("investments", []) if inv.get("id") == investment_id),
            None
        )

        if not investment:
            return jsonify({"success": False, "error": "Investment not found"}), 404

        # Build detailed response
        details = {
            "investorName": user.get("firstName", "Unknown"),
            "email": user.get("email", "N/A"),
            "investmentId": investment.get("id"),
            "amount": investment.get("amount"),
            "expectedGrowth": investment.get("expectedGrowth"),
            "paymentReference": investment.get("paymentReference"),
            "status": investment.get("status"),
            "createdAt": investment.get("createdAt"),
            "updatedAt": investment.get("updatedAt")
        }

        return jsonify({"success": True, "investment": details}), 200

    except Exception as e:
        print(f"View investment details error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500

# --- Helper: Send user rejection email ---
def send_user_rejection_email(user_email, user_name, investment_id, amount):
    """Notify user that their investment was rejected."""
    try:
        resend.Emails.send({
            "from": "Capitello <noreply@destinytch.com.ng>",
            "to": [user_email],
            "subject": "❌ Your Capitello Investment Could Not Be Confirmed",
            "html": f"""
                <div style="font-family: 'Segoe UI', Arial, sans-serif; background-color: #f6f8fb; padding: 32px;">
                    <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05);">
                        
                        <div style="background-color: #8b0000; color: #ffffff; padding: 20px 28px; border-top-left-radius: 12px; border-top-right-radius: 12px;">
                            <h2 style="margin: 0; font-size: 20px;">Investment Rejected</h2>
                        </div>

                        <div style="padding: 28px;">
                            <p style="font-size: 16px; color: #222;">Hello {user_name},</p>
                            <p style="font-size: 15px; color: #444; line-height: 1.6;">
                                We regret to inform you that your recent investment 
                                (<strong>ID: {investment_id}</strong>, Amount: <strong>${float(amount):,.2f}</strong>)
                                could not be confirmed due to a payment or verification issue.
                            </p>

                            <p style="font-size: 15px; color: #444; line-height: 1.6;">
                                If you believe this was a mistake, please contact our support team immediately 
                                with your payment proof for review.
                            </p>

                            <p style="margin-top: 20px; font-size: 14px; color: #555;">
                                We appreciate your understanding and your continued trust in <strong>Capitello</strong>.
                            </p>

                            <p style="margin-top: 28px; font-size: 14px; color: #999;">
                                — The Capitello Team
                            </p>
                        </div>
                    </div>
                </div>
            """
        })
    except Exception as e:
        print(f"⚠️ Failed to send user rejection email: {e}")


# --- Admin: Reject investment ---
@investment_bp.route("/reject", methods=["POST"])
@require_admin_permission("investments:manage")
def reject_investment():
    if not request.is_admin:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    try:
        data = request.get_json()
        user_id = data.get("userId")
        investment_id = data.get("investmentId")

        if not user_id or not investment_id:
            return jsonify({"success": False, "error": "Missing userId or investmentId"}), 400

        user = users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        # Find and mark investment as rejected.
        rejected = False
        rejected_amount = 0
        for inv in user.get("investments", []):
            if inv.get("id") == investment_id and inv.get("status") == "pending":
                rejected = True
                rejected_amount = inv.get("amount", 0)
                break

        if not rejected:
            return jsonify({"success": False, "error": "Pending investment not found"}), 404

        reason = str(data.get("reason") or "").strip()
        if not 3 <= len(reason) <= 500:
            return jsonify({"success": False, "error": "A rejection reason of 3–500 characters is required"}), 400
        now = datetime.utcnow()
        transition = users.update_one(
            {
                "_id": ObjectId(user_id),
                "investments": {"$elemMatch": {"id": investment_id, "status": "pending"}},
            },
            {"$set": {
                "investments.$.status": "rejected",
                "investments.$.updatedAt": now,
                "investments.$.rejectionReason": reason,
            }}
        )
        if transition.modified_count != 1:
            return jsonify({"success": False, "error": "Pending investment was already processed"}), 409

        record_user_notification(
            user_id,
            f"investment-rejected:{investment_id}",
            "deposit_rejected",
            "Deposit needs attention",
            reason,
            related_id=investment_id,
            amount=rejected_amount,
        )
        record_admin_action(
            request.user_id,
            "investment.rejected",
            user_id=user_id,
            related_id=investment_id,
            details={"amount": rejected_amount, "reason": reason},
        )

        return jsonify({"success": True, "message": "Investment rejected and user notified in the app"}), 200

    except Exception as e:
        print(f"Reject investment error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500

