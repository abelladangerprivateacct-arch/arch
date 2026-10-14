from flask import Blueprint, request, jsonify
from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
import os
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

# --- Flask Blueprint ---
withdrawal_bp = Blueprint("withdrawal", __name__, url_prefix="/api/withdrawal")

# --- Helper: Send email ---
def send_email(to, subject, html):
    try:
        resend.Emails.send({
            "from": "Capitello <noreply@destinytch.com.ng>",
            "to": to if isinstance(to, list) else [to],
            "subject": subject,
            "html": html
        })
    except Exception as e:
        print(f"⚠️ Email sending failed: {e}")

# =========================================================
# ✅ USER: Create Withdrawal Request
# =========================================================
@withdrawal_bp.route("/", methods=["POST"])
@authenticate_user
def create_withdrawal():
    try:
        data = request.get_json(silent=True) or {}
        destination = str(data.get("destination") or "").strip()
        if not 5 <= len(destination) <= 256:
            return jsonify({"success": False, "error": "Enter a valid payout destination (5–256 characters)."}), 400

        user_id = ObjectId(request.user_id)
        user = users.find_one({"_id": user_id})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        available_balance = float(user.get("availableBalance", 0))
        total_invested = sum(inv["amount"] for inv in user.get("investments", []) if inv.get("status") == "active")

        # Must have earnings (profit)
        if available_balance <= total_invested:
            return jsonify({
                "success": False,
                "error": "No earnings yet — you can only withdraw after profit."
            }), 400

        # Create withdrawal record
        withdrawal_id = f"withdraw_{int(datetime.utcnow().timestamp() * 1000)}"
        amount = available_balance
        fee = 0.0

        new_withdrawal = {
            "id": withdrawal_id,
            "amount": amount,
            "fee": fee,
            "status": "pending",
            "destination": destination,
            "requestedAt": datetime.utcnow(),
            "updatedAt": datetime.utcnow()
        }

        # Only one request may lock the active investments at a time.
        now = datetime.utcnow()
        new_withdrawal["requestedAt"] = now
        new_withdrawal["updatedAt"] = now
        created = users.update_one(
            {
                "_id": user_id,
                "investments.status": "active",
                "withdrawals": {"$not": {"$elemMatch": {"status": "pending"}}},
            },
            {
                "$push": {"withdrawals": new_withdrawal},
                "$set": {
                    "investments.$[investment].status": "pending-withdrawal",
                    "investments.$[investment].updatedAt": now,
                }
            },
            array_filters=[{"investment.status": "active"}],
        )
        if created.modified_count != 1:
            return jsonify({"success": False, "error": "A withdrawal is already pending. Refresh and try again."}), 409

        record_user_notification(
            user_id,
            f"withdrawal-requested:{withdrawal_id}",
            "withdrawal_requested",
            "Withdrawal requested",
            f"Your ${amount:.2f} withdrawal request is pending review.",
            related_id=withdrawal_id,
            amount=amount,
        )
        customer_name = " ".join(filter(None, [user.get("firstName"), user.get("lastName")])) or "A customer"
        record_admin_notification(
            f"withdrawal-requested:{withdrawal_id}",
            "withdrawal_requested",
            "New withdrawal requested",
            f"{customer_name} requested a ${amount:.2f} withdrawal. Awaiting review.",
            user_id=user_id,
            related_id=withdrawal_id,
            amount=amount,
        )

        return jsonify({
            "success": True,
            "message": "Withdrawal request created successfully and pending admin approval.",
            "data": new_withdrawal
        }), 201

    except Exception as e:
        print(f"Create withdrawal error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# =========================================================
# ✅ USER: Get All Withdrawals
# =========================================================
@withdrawal_bp.route("/", methods=["GET"])
@authenticate_user
def get_user_withdrawals():
    try:
        user = users.find_one({"_id": ObjectId(request.user_id)}, {"withdrawals": 1})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404
        withdrawals = user.get("withdrawals", [])
        return jsonify({"success": True, "withdrawals": withdrawals}), 200
    except Exception as e:
        print(f"Get withdrawals error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# =========================================================
# ✅ ADMIN: List Withdrawals by Status
# =========================================================
@withdrawal_bp.route("/admin/list", methods=["GET"])
@require_admin_permission("withdrawals:read")
def list_withdrawals_by_status():
    try:
        status = request.args.get("status", "all").lower()
        allowed_statuses = {"all", "pending", "completed", "cancelled"}
        if status not in allowed_statuses:
            return jsonify({"success": False, "error": "Invalid withdrawal status filter"}), 400
        result = []

        for user in users.find({}):
            for wd in user.get("withdrawals", []):
                withdrawal_status = wd.get("status", "").lower()
                if status == "all" or withdrawal_status == status:
                    result.append({
                        "withdrawalId": wd.get("id"),
                        "userId": str(user.get("_id")),
                        "email": user.get("email"),
                        "amount": wd.get("amount"),
                        "destination": wd.get("destination"),
                        "fee": wd.get("fee"),
                        "status": wd.get("status"),
                        "requestedAt": wd.get("requestedAt"),
                        "updatedAt": wd.get("updatedAt")
                    })

        return jsonify({"success": True, "count": len(result), "withdrawals": result}), 200
    except Exception as e:
        print(f"List withdrawals error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# =========================================================
# ✅ ADMIN: Approve Withdrawal
# =========================================================
@withdrawal_bp.route("/admin/approve", methods=["POST"])
@require_admin_permission("withdrawals:manage")
def approve_withdrawal():
    try:
        data = request.get_json()
        user_id = data.get("userId") if isinstance(data, dict) else None
        withdrawal_id = data.get("withdrawalId") if isinstance(data, dict) else None
        if not user_id or not withdrawal_id or not ObjectId.is_valid(user_id):
            return jsonify({"success": False, "error": "Valid userId and withdrawalId are required"}), 400

        user = users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        withdrawal = next((item for item in user.get("withdrawals", []) if item.get("id") == withdrawal_id), None)
        now = datetime.utcnow()
        transition = users.update_one(
            {"_id": ObjectId(user_id), "withdrawals": {"$elemMatch": {"id": withdrawal_id, "status": "pending"}}},
            {
                "$set": {
                    "withdrawals.$.status": "completed",
                    "withdrawals.$.updatedAt": now,
                    "investments.$[investment].status": "completed",
                    "investments.$[investment].updatedAt": now,
                    "availableBalance": 0,
                    "earnings": 0,
                    "withdrawFeePaid": False,
                    "feeApproved": False
                }
            },
            array_filters=[{"investment.status": "pending-withdrawal"}],
        )
        if transition.modified_count != 1:
            return jsonify({"success": False, "error": "Pending withdrawal was already processed"}), 409

        record_user_notification(
            user_id,
            f"withdrawal-completed:{withdrawal_id}",
            "withdrawal_completed",
            "Withdrawal processed",
            f"Your ${float((withdrawal or {}).get('amount', 0)):.2f} withdrawal has been marked as processed.",
            related_id=withdrawal_id,
            amount=(withdrawal or {}).get("amount"),
        )
        record_admin_action(
            request.user_id,
            "withdrawal.completed",
            user_id=user_id,
            related_id=withdrawal_id,
            details={"amount": (withdrawal or {}).get("amount")},
        )

        return jsonify({"success": True, "message": "Withdrawal approved successfully."}), 200
    except Exception as e:
        print(f"Approve withdrawal error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# =========================================================
# ✅ ADMIN: Cancel Withdrawal
# =========================================================
@withdrawal_bp.route("/admin/cancel", methods=["POST"])
@require_admin_permission("withdrawals:manage")
def cancel_withdrawal():
    try:
        data = request.get_json()
        user_id = data.get("userId") if isinstance(data, dict) else None
        withdrawal_id = data.get("withdrawalId") if isinstance(data, dict) else None
        if not user_id or not withdrawal_id or not ObjectId.is_valid(user_id):
            return jsonify({"success": False, "error": "Valid userId and withdrawalId are required"}), 400

        user = users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        reason = str(data.get("reason") or "").strip()
        if not 3 <= len(reason) <= 500:
            return jsonify({"success": False, "error": "A cancellation reason of 3–500 characters is required"}), 400
        withdrawal = next((item for item in user.get("withdrawals", []) if item.get("id") == withdrawal_id), None)
        now = datetime.utcnow()
        transition = users.update_one(
            {"_id": ObjectId(user_id), "withdrawals": {"$elemMatch": {"id": withdrawal_id, "status": "pending"}}},
            {
                "$set": {
                    "withdrawals.$.status": "cancelled",
                    "withdrawals.$.updatedAt": now,
                    "withdrawals.$.rejectionReason": reason,
                    "investments.$[investment].status": "active",
                    "investments.$[investment].updatedAt": now,
                }
            },
            array_filters=[{"investment.status": "pending-withdrawal"}],
        )
        if transition.modified_count != 1:
            return jsonify({"success": False, "error": "Pending withdrawal was already processed"}), 409

        record_user_notification(
            user_id,
            f"withdrawal-cancelled:{withdrawal_id}",
            "withdrawal_cancelled",
            "Withdrawal needs attention",
            reason,
            related_id=withdrawal_id,
            amount=(withdrawal or {}).get("amount"),
        )
        record_admin_action(
            request.user_id,
            "withdrawal.cancelled",
            user_id=user_id,
            related_id=withdrawal_id,
            details={"reason": reason, "amount": (withdrawal or {}).get("amount")},
        )

        return jsonify({"success": True, "message": "Withdrawal cancelled and investment reactivated."}), 200
    except Exception as e:
        print(f"Cancel withdrawal error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
@withdrawal_bp.route("/confirm-fee", methods=["POST"])
@authenticate_user
def confirm_withdrawal_fee():
    return jsonify({
        "success": False,
        "error": "No withdrawal fee is required. You can submit a withdrawal request directly."
    }), 410

# =========================================================
# ❌ ADMIN: REJECT WITHDRAWAL FEE CONFIRMATION
# =========================================================
@withdrawal_bp.route("/admin/reject-fee", methods=["POST"])
@require_admin_permission("withdrawals:manage")
def reject_withdrawal_fee():
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get("userId")
        reason = data.get("reason", "No reason provided")
        fee_request_id = data.get("feeRequestId")

        if not user_id or not ObjectId.is_valid(user_id):
            return jsonify({"success": False, "error": "A valid userId is required"}), 400

        user = users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        fee_requests = user.get("withdrawalFeeRequests", [])
        rejected_at = datetime.utcnow()
        matched = False
        for fee_request in fee_requests:
            if fee_request.get("status") == "pending" and (not fee_request_id or fee_request.get("id") == fee_request_id):
                fee_request["status"] = "rejected"
                fee_request["updatedAt"] = rejected_at
                fee_request["rejectionReason"] = reason
                matched = True

        if fee_request_id and not matched:
            return jsonify({"success": False, "error": "Pending fee request not found"}), 404

        users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "withdrawFeePaid": False,
                "feeApproved": False,
                "feeRejectedAt": rejected_at,
                "feeRejectionReason": reason,
                "withdrawalFeeRequests": fee_requests
            }}
        )

        # Email user that their fee was rejected
        send_email(
            user["email"],
            "❌ Withdrawal Fee Payment Rejected",
            f"""
            <div style="font-family:Arial,sans-serif;background-color:#f6f8fb;padding:24px;">
                <div style="max-width:600px;margin:auto;background:#ffffff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.05);">
                    <div style="background-color:#8B0000;color:#ffffff;padding:18px 28px;border-top-left-radius:12px;border-top-right-radius:12px;">
                        <h2 style="margin:0;">Withdrawal Fee Payment Rejected</h2>
                    </div>
                    <div style="padding:28px;color:#333;">
                        <p>Hi {user.get('firstName','Investor')},</p>
                        <p>We were unable to verify your withdrawal fee payment.</p>
                        <p><strong>Reason:</strong> {reason}</p>
                        <p>Please double-check your transaction and try again. If you believe this is an error, contact support.</p>
                        <p style="margin-top:24px;color:#999;">— The Capitello Team</p>
                    </div>
                </div>
            </div>
            """
        )

        # Email admin confirmation
        send_email(
            ["destinytch001@gmail.com", "destinytch@proton.me"],
            f"Withdrawal Fee Rejected for {user.get('email')}",
            f"""
            <div style="font-family:Arial,sans-serif;background-color:#f6f8fb;padding:24px;">
                <div style="max-width:600px;margin:auto;background:#ffffff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.05);">
                    <div style="background-color:#8B0000;color:#ffffff;padding:18px 28px;border-top-left-radius:12px;border-top-right-radius:12px;">
                        <h2 style="margin:0;">Withdrawal Fee Rejected</h2>
                    </div>
                    <div style="padding:28px;color:#333;">
                        <p><strong>User:</strong> {user.get('firstName','')} {user.get('lastName','')}</p>
                        <p><strong>Email:</strong> {user.get('email')}</p>
                        <p><strong>Reason:</strong> {reason}</p>
                        <p><strong>User ID:</strong> {user_id}</p>
                        <p>The withdrawal fee confirmation was rejected successfully.</p>
                        <p style="margin-top:24px;color:#999;">— Capitello System</p>
                    </div>
                </div>
            </div>
            """
        )

        return jsonify({
            "success": True,
            "message": "Withdrawal fee payment rejected successfully."
        }), 200

    except Exception as e:
        print(f"Reject fee error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500

@withdrawal_bp.route("/admin/fee-requests", methods=["GET"])
@require_admin_permission("withdrawals:read")
def list_fee_requests():
    try:
        result = []

        for user in users.find({}):
            for fr in user.get("withdrawalFeeRequests", []):
                if fr.get("status", "").lower() == "pending":
                    result.append({
                        "feeRequestId": fr.get("id"),
                        "userId": str(user.get("_id")),
                        "email": user.get("email"),
                        "amount": fr.get("amount"),
                        "status": fr.get("status"),
                        "requestedAt": fr.get("requestedAt"),
                        "updatedAt": fr.get("updatedAt")
                    })

        return jsonify({"success": True, "count": len(result), "feeRequests": result}), 200

    except Exception as e:
        print(f"List fee requests error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


@withdrawal_bp.route("/admin/fee-requests/approve", methods=["POST"])
@require_admin_permission("withdrawals:manage")
def approve_fee_request():
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get("userId")
        fee_request_id = data.get("feeRequestId")
        if not user_id or not fee_request_id or not ObjectId.is_valid(user_id):
            return jsonify({"success": False, "error": "Valid userId and feeRequestId are required"}), 400

        user = users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        updated = False
        for fr in user.get("withdrawalFeeRequests", []):
            if fr.get("id") == fee_request_id and fr.get("status") == "pending":
                fr["status"] = "approved"
                fr["updatedAt"] = datetime.utcnow()
                updated = True

        if not updated:
            return jsonify({"success": False, "error": "Fee request not found or already processed"}), 404

        users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "withdrawalFeeRequests": user.get("withdrawalFeeRequests"),
                "withdrawFeePaid": True,
                "feeApproved": True,
                "feeApprovedAt": datetime.utcnow(),
                "feeApprovedBy": str(request.user_id)
            }}
        )

        # Notify user
        send_email(
            user.get("email"),
            "✅ Withdrawal Fee Approved",
            f"Hello {user.get('firstName','Investor')}, your withdrawal fee has been approved. You may now proceed to request withdrawal."
        )

        return jsonify({"success": True, "message": "Fee request approved successfully"}), 200

    except Exception as e:
        print(f"Approve fee request error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500


# =========================================================
# ✅ ADMIN: List Users with Pending Withdrawal Fee Confirmations
# =========================================================
@withdrawal_bp.route("/pending-fees", methods=["GET"])
@require_admin_permission("withdrawals:read")
def list_users_pending_fees():
    try:
        pending_users = []

        # Include current string-based records and legacy boolean records.
        for user in users.find({"withdrawFeePaid": {"$in": [True, "pending"]}}):
            if user.get("feeApproved", False):
                continue

            pending_fee = next(
                (fee for fee in reversed(user.get("withdrawalFeeRequests", [])) if fee.get("status") == "pending"),
                None
            )

            pending_users.append({
                "_id": str(user["_id"]),
                "firstName": user.get("firstName", ""),
                "lastName": user.get("lastName", ""),
                "email": user.get("email", ""),
                "feePaidAt": user.get("feePaidAt"),
                "feeRequestId": pending_fee.get("id") if pending_fee else None,
                "feeAmount": pending_fee.get("amount") if pending_fee else None
            })

        return jsonify({
            "success": True,
            "count": len(pending_users),
            "users": pending_users
        }), 200

    except Exception as e:
        print(f"Error fetching pending fee users: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
@withdrawal_bp.route("/admin/approve-fee", methods=["POST"])
@require_admin_permission("withdrawals:manage")
def approve_withdrawal_fee():
    try:
        data = request.get_json(silent=True) or {}
        user_id = data.get("userId")
        fee_request_id = data.get("feeRequestId")

        if not user_id or not ObjectId.is_valid(user_id):
            return jsonify({"success": False, "error": "A valid userId is required"}), 400

        user = users.find_one({"_id": ObjectId(user_id)})
        if not user:
            return jsonify({"success": False, "error": "User not found"}), 404

        fee_requests = user.get("withdrawalFeeRequests", [])
        pending_requests = [fee for fee in fee_requests if fee.get("status") == "pending"]
        request_to_approve = next(
            (fee for fee in pending_requests if fee.get("id") == fee_request_id),
            None
        ) if fee_request_id else (pending_requests[-1] if pending_requests else None)
        if fee_request_id and not request_to_approve:
            return jsonify({"success": False, "error": "Pending fee request not found"}), 404
        if not request_to_approve and user.get("withdrawFeePaid") not in [True, "pending"]:
            return jsonify({"success": False, "error": "No pending fee confirmation for this user"}), 409

        approved_at = datetime.utcnow()
        if request_to_approve:
            request_to_approve["status"] = "approved"
            request_to_approve["updatedAt"] = approved_at

        users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {
                "withdrawFeePaid": True,
                "feeApproved": True,
                "feeApprovedAt": approved_at,
                "feeApprovedBy": str(request.user_id),
                "withdrawalFeeRequests": fee_requests
            }}
        )

        # ✅ Notify user by email
        send_email(
            user["email"],
            "✅ Withdrawal Fee Approved",
            f"""
            <div style="font-family:Arial,sans-serif;background-color:#f6f8fb;padding:24px;">
                <div style="max-width:600px;margin:auto;background:#ffffff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,0.05);">
                    <div style="background-color:#16a34a;color:#ffffff;padding:18px 28px;border-top-left-radius:12px;border-top-right-radius:12px;">
                        <h2 style="margin:0;">Withdrawal Fee Approved</h2>
                    </div>
                    <div style="padding:28px;color:#333;">
                        <p>Hi {user.get('firstName','Investor')},</p>
                        <p>Your withdrawal fee payment has been <strong>approved</strong> by our team.</p>
                        <p>You can now proceed with making withdrawal requests from your available balance.</p>
                        <p style="margin-top:24px;color:#999;">— The Capitello Team</p>
                    </div>
                </div>
            </div>
            """
        )

        return jsonify({
            "success": True,
            "message": "Withdrawal fee approved successfully."
        }), 200

    except Exception as e:
        print(f"Approve fee error: {e}")
        return jsonify({"success": False, "error": "Server error"}), 500
