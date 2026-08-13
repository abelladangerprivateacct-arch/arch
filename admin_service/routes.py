from datetime import datetime, timedelta, timezone

import jwt

from bson import ObjectId

from flask import Blueprint, current_app, jsonify, request

from extensions import bcrypt
from auth import token_required


admin_api = Blueprint(
    "admin_api",
    __name__,
)


def serialize_user(user):
    def serialize_date(value):
        if isinstance(value, datetime):
            return value.isoformat()
        return None

    return {
        "id": str(user["_id"]),
        "full_name": user.get("full_name", ""),
        "email": user.get("email", ""),
        "phone": user.get("phone", ""),
        "role": user.get("role", "user"),
        "is_active": user.get("is_active", True),
        "is_flagged": user.get("is_flagged", False),
        "flag_reason": user.get("flag_reason"),
        "flagged_at": serialize_date(
            user.get("flagged_at")
        ),
        "flagged_by": user.get("flagged_by"),
        "created_at": serialize_date(
            user.get("created_at")
        ),
        "updated_at": serialize_date(
            user.get("updated_at")
        ),
        "last_login_at": serialize_date(
            user.get("last_login_at")
        ),
    }


def create_initial_admin():
    required_values = {
        "INITIAL_ADMIN_FULL_NAME": current_app.config[
            "INITIAL_ADMIN_FULL_NAME"
        ],
        "INITIAL_ADMIN_EMAIL": current_app.config[
            "INITIAL_ADMIN_EMAIL"
        ],
        "INITIAL_ADMIN_PHONE": current_app.config[
            "INITIAL_ADMIN_PHONE"
        ],
        "INITIAL_ADMIN_PASSWORD": current_app.config[
            "INITIAL_ADMIN_PASSWORD"
        ],
    }

    if not all(required_values.values()):
        raise RuntimeError(
            "All initial admin environment variables are required."
        )

    users = current_app.db["users"]

    existing = users.find_one({
        "email": required_values[
            "INITIAL_ADMIN_EMAIL"
        ],
    })

    if existing:
        if existing.get("role") != "admin":
            raise RuntimeError(
                "Initial admin email belongs to a non-admin account."
            )
        return

    now = datetime.now(timezone.utc)

    users.insert_one({
        "full_name": required_values[
            "INITIAL_ADMIN_FULL_NAME"
        ],
        "email": required_values[
            "INITIAL_ADMIN_EMAIL"
        ],
        "phone": required_values[
            "INITIAL_ADMIN_PHONE"
        ],
        "password_hash": bcrypt.generate_password_hash(
            required_values[
                "INITIAL_ADMIN_PASSWORD"
            ]
        ).decode("utf-8"),
        "role": "admin",
        "is_active": True,
        "is_flagged": False,
        "created_at": now,
        "updated_at": now,
    })


@admin_api.route(
    "/login",
    methods=["POST"],
)
def login():
    payload = request.get_json(
        silent=True
    ) or {}

    email = str(
        payload.get("email", "")
    ).strip().lower()

    password = str(
        payload.get("password", "")
    )

    if not email or not password:
        return jsonify({
            "status": "error",
            "message": "Email and password are required.",
        }), 400

    admin = current_app.db["users"].find_one({
        "email": email,
        "role": "admin",
    })

    if not admin:
        return jsonify({
            "status": "error",
            "message": "Invalid admin credentials.",
        }), 401

    if not admin.get("is_active", True):
        return jsonify({
            "status": "error",
            "message": "Admin account is inactive.",
        }), 403

    if not bcrypt.check_password_hash(
        admin.get("password_hash", ""),
        password,
    ):
        return jsonify({
            "status": "error",
            "message": "Invalid admin credentials.",
        }), 401

    now = datetime.now(timezone.utc)

    current_app.db["users"].update_one(
        {
            "_id": admin["_id"],
        },
        {
            "$set": {
                "last_login_at": now,
                "updated_at": now,
            },
        },
    )

    token = jwt.encode(
        {
            "sub": str(admin["_id"]),
            "email": admin["email"],
            "role": "admin",
            "iat": now,
            "exp": now + timedelta(
                hours=current_app.config[
                    "JWT_ACCESS_TOKEN_EXPIRES_HOURS"
                ]
            ),
        },
        current_app.config["JWT_SECRET_KEY"],
        algorithm="HS256",
    )

    return jsonify({
        "status": "success",
        "message": "Admin login successful.",
        "data": {
            "admin": {
                "id": str(admin["_id"]),
                "full_name": admin.get(
                    "full_name",
                    "",
                ),
                "email": admin["email"],
                "phone": admin.get(
                    "phone",
                    "",
                ),
                "role": admin["role"],
            },
            "access_token": token,
        },
    }), 200


@admin_api.route(
    "/users",
    methods=["GET"],
)
@token_required
def get_users():
    users = current_app.db["users"]

    documents = users.find(
        {},
        {
            "password_hash": 0,
        },
    ).sort(
        "created_at",
        -1,
    )

    result = [
        serialize_user(user)
        for user in documents
    ]

    return jsonify({
        "status": "success",
        "data": result,
        "count": len(result),
    }), 200


@admin_api.route(
    "/users/<user_id>",
    methods=["GET"],
)
@token_required
def get_user(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    user = current_app.db["users"].find_one(
        {
            "_id": object_id,
        },
        {
            "password_hash": 0,
        },
    )

    if not user:
        return jsonify({
            "status": "error",
            "message": "User not found.",
        }), 404

    return jsonify({
        "status": "success",
        "data": serialize_user(user),
    }), 200


@admin_api.route(
    "/users/<user_id>/block",
    methods=["POST"],
)
@token_required
def block_user(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    if object_id == request.current_admin["_id"]:
        return jsonify({
            "status": "error",
            "message": "An administrator cannot block their own account.",
        }), 400

    result = current_app.db["users"].update_one(
        {
            "_id": object_id,
            "role": "user",
        },
        {
            "$set": {
                "is_active": False,
                "updated_at": datetime.now(
                    timezone.utc
                ),
            },
        },
    )

    if result.matched_count == 0:
        return jsonify({
            "status": "error",
            "message": "User not found.",
        }), 404

    return jsonify({
        "status": "success",
        "message": "User blocked successfully.",
        "user_id": user_id,
    }), 200


@admin_api.route(
    "/users/<user_id>/unblock",
    methods=["POST"],
)
@token_required
def unblock_user(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    result = current_app.db["users"].update_one(
        {
            "_id": object_id,
            "role": "user",
        },
        {
            "$set": {
                "is_active": True,
                "updated_at": datetime.now(
                    timezone.utc
                ),
            },
        },
    )

    if result.matched_count == 0:
        return jsonify({
            "status": "error",
            "message": "User not found.",
        }), 404

    return jsonify({
        "status": "success",
        "message": "User unblocked successfully.",
        "user_id": user_id,
    }), 200


@admin_api.route(
    "/users/<user_id>/flag",
    methods=["POST"],
)
@token_required
def flag_user(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    payload = request.get_json(
        silent=True
    ) or {}

    reason = str(
        payload.get("reason", "")
    ).strip()

    if not reason:
        return jsonify({
            "status": "error",
            "message": "A flag reason is required.",
        }), 400

    now = datetime.now(timezone.utc)

    result = current_app.db["users"].update_one(
        {
            "_id": object_id,
            "role": "user",
        },
        {
            "$set": {
                "is_flagged": True,
                "flag_reason": reason,
                "flagged_at": now,
                "flagged_by": request.current_admin_id,
                "updated_at": now,
            },
        },
    )

    if result.matched_count == 0:
        return jsonify({
            "status": "error",
            "message": "User not found.",
        }), 404

    return jsonify({
        "status": "success",
        "message": "User flagged successfully.",
        "user_id": user_id,
    }), 200


@admin_api.route(
    "/users/<user_id>/unflag",
    methods=["POST"],
)
@token_required
def unflag_user(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    now = datetime.now(timezone.utc)

    result = current_app.db["users"].update_one(
        {
            "_id": object_id,
            "role": "user",
        },
        {
            "$set": {
                "is_flagged": False,
                "flag_reason": None,
                "flagged_at": None,
                "flagged_by": None,
                "updated_at": now,
            },
        },
    )

    if result.matched_count == 0:
        return jsonify({
            "status": "error",
            "message": "User not found.",
        }), 404

    return jsonify({
        "status": "success",
        "message": "User unflagged successfully.",
        "user_id": user_id,
    }), 200

@admin_api.route(
    "/dashboard",
    methods=["GET"],
)
@token_required
def get_dashboard():
    users = current_app.db["users"]

    total_users = users.count_documents({
        "role": "user",
    })

    active_users = users.count_documents({
        "role": "user",
        "is_active": True,
    })

    blocked_users = users.count_documents({
        "role": "user",
        "is_active": False,
    })

    flagged_users = users.count_documents({
        "role": "user",
        "is_flagged": True,
    })

    total_admins = users.count_documents({
        "role": "admin",
    })

    return jsonify({
        "status": "success",
        "data": {
            "total_users": total_users,
            "active_users": active_users,
            "blocked_users": blocked_users,
            "flagged_users": flagged_users,
            "total_admins": total_admins,
        },
    }), 200

@admin_api.route(
    "/withdrawals",
    methods=["GET"],
)
@token_required
def get_withdrawals():
    withdrawals = current_app.db["withdrawals"]

    documents = withdrawals.find().sort(
        "created_at",
        -1,
    )

    result = []

    for withdrawal in documents:
        result.append({
            "id": str(withdrawal["_id"]),
            "user_id": str(withdrawal["user_id"]),
            "voucher_id": str(withdrawal["voucher_id"]),
            "amount": withdrawal.get("amount", 0),
            "status": withdrawal.get(
                "status",
                "submitted",
            ),
            "created_at": (
                withdrawal["created_at"].isoformat()
                if withdrawal.get("created_at")
                else None
            ),
            "updated_at": (
                withdrawal["updated_at"].isoformat()
                if withdrawal.get("updated_at")
                else None
            ),
            "approved_at": (
                withdrawal["approved_at"].isoformat()
                if withdrawal.get("approved_at")
                else None
            ),
            "approved_by": withdrawal.get(
                "approved_by"
            ),
            "rejected_at": (
                withdrawal["rejected_at"].isoformat()
                if withdrawal.get("rejected_at")
                else None
            ),
            "rejected_by": withdrawal.get(
                "rejected_by"
            ),
            "rejection_reason": withdrawal.get(
                "rejection_reason"
            ),
        })

    return jsonify({
        "status": "success",
        "data": result,
        "count": len(result),
    }), 200

@admin_api.route(
    "/withdrawals/<withdrawal_id>/approve",
    methods=["POST"],
)
@token_required
def approve_withdrawal(withdrawal_id):
    try:
        object_id = ObjectId(withdrawal_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid withdrawal ID.",
        }), 400

    now = datetime.now(timezone.utc)

    result = current_app.db["withdrawals"].update_one(
        {
            "_id": object_id,
            "status": "submitted",
        },
        {
            "$set": {
                "status": "approved",
                "approved_at": now,
                "approved_by": request.current_admin_id,
                "updated_at": now,
            },
        },
    )

    if result.matched_count == 0:
        existing = current_app.db["withdrawals"].find_one({
            "_id": object_id,
        })

        if not existing:
            return jsonify({
                "status": "error",
                "message": "Withdrawal not found.",
            }), 404

        return jsonify({
            "status": "error",
            "message": "Withdrawal is no longer pending.",
        }), 409

    return jsonify({
        "status": "success",
        "message": "Withdrawal approved successfully.",
        "withdrawal_id": withdrawal_id,
    }), 200

@admin_api.route(
    "/withdrawals/<withdrawal_id>/reject",
    methods=["POST"],
)
@token_required
def reject_withdrawal(withdrawal_id):
    try:
        object_id = ObjectId(withdrawal_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid withdrawal ID.",
        }), 400

    payload = request.get_json(
        silent=True
    ) or {}

    reason = str(
        payload.get("reason", "")
    ).strip()

    if not reason:
        return jsonify({
            "status": "error",
            "message": "Rejection reason is required.",
        }), 400

    now = datetime.now(timezone.utc)

    try:
        with current_app.mongo_client.start_session() as session:
            with session.start_transaction():

                withdrawal = current_app.db[
                    "withdrawals"
                ].find_one(
                    {
                        "_id": object_id,
                        "status": "submitted",
                    },
                    session=session,
                )

                if not withdrawal:
                    existing = current_app.db[
                        "withdrawals"
                    ].find_one(
                        {"_id": object_id},
                        session=session,
                    )

                    if not existing:
                        return jsonify({
                            "status": "error",
                            "message": "Withdrawal not found.",
                        }), 404

                    return jsonify({
                        "status": "error",
                        "message": "Withdrawal is no longer pending.",
                    }), 409

                user_id = withdrawal["user_id"]
                amount = withdrawal["amount"]

                result = current_app.db[
                    "withdrawals"
                ].update_one(
                    {
                        "_id": object_id,
                        "status": "submitted",
                    },
                    {
                        "$set": {
                            "status": "rejected",
                            "rejected_at": now,
                            "rejected_by": request.current_admin_id,
                            "rejection_reason": reason,
                            "updated_at": now,
                        },
                    },
                    session=session,
                )

                if result.modified_count != 1:
                    raise ValueError(
                        "WITHDRAWAL_UNAVAILABLE"
                    )

                balance_result = current_app.db[
                    "users"
                ].update_one(
                    {
                        "_id": user_id,
                    },
                    {
                        "$inc": {
                            "balance": amount,
                        },
                        "$set": {
                            "updated_at": now,
                        },
                    },
                    session=session,
                )

                if balance_result.modified_count != 1:
                    raise ValueError(
                        "BALANCE_RESTORE_FAILED"
                    )

    except ValueError as exc:
        if str(exc) == "WITHDRAWAL_UNAVAILABLE":
            return jsonify({
                "status": "error",
                "message": "Withdrawal is no longer pending.",
            }), 409

        return jsonify({
            "status": "error",
            "message": "Withdrawal rejection could not be completed.",
        }), 400

    except Exception:
        current_app.logger.exception(
            "Withdrawal rejection transaction failed"
        )

        return jsonify({
            "status": "error",
            "message": "Withdrawal rejection could not be completed.",
        }), 500

    return jsonify({
        "status": "success",
        "message": "Withdrawal rejected successfully.",
        "withdrawal_id": withdrawal_id,
    }), 200

@admin_api.route(
    "/users/search",
    methods=["GET"],
)
@token_required
def search_users():
    query = str(
        request.args.get("q", "")
    ).strip()

    if not query:
        return jsonify({
            "status": "error",
            "message": "Search query is required.",
        }), 400

    users = current_app.db["users"]

    documents = users.find(
        {
            "role": "user",
            "$or": [
                {
                    "full_name": {
                        "$regex": query,
                        "$options": "i",
                    }
                },
                {
                    "email": {
                        "$regex": query,
                        "$options": "i",
                    }
                },
                {
                    "phone": {
                        "$regex": query,
                        "$options": "i",
                    }
                },
            ],
        },
        {
            "password_hash": 0,
        },
    ).sort(
        "created_at",
        -1,
    )

    result = [
        serialize_user(user)
        for user in documents
    ]

    return jsonify({
        "status": "success",
        "data": result,
        "count": len(result),
    }), 200

@admin_api.route(
    "/users/<user_id>/balance",
    methods=["GET"],
)
@token_required
def get_user_balance(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    user = current_app.db["users"].find_one(
        {
            "_id": object_id,
            "role": "user",
        },
        {
            "balance": 1,
        },
    )

    if not user:
        return jsonify({
            "status": "error",
            "message": "User not found.",
        }), 404

    return jsonify({
        "status": "success",
        "data": {
            "user_id": user_id,
            "balance": user.get(
                "balance",
                0,
            ),
        },
    }), 200

@admin_api.route(
    "/users/<user_id>/balance/credit",
    methods=["POST"],
)
@token_required
def credit_user_balance(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    payload = request.get_json(
        silent=True
    ) or {}

    try:
        amount = float(
            payload.get("amount")
        )
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "A valid amount is required.",
        }), 400

    if amount <= 0:
        return jsonify({
            "status": "error",
            "message": "Amount must be greater than zero.",
        }), 400

    reason = str(
        payload.get("reason", "")
    ).strip()

    if not reason:
        return jsonify({
            "status": "error",
            "message": "A reason is required.",
        }), 400

    now = datetime.now(timezone.utc)

    result = current_app.db["users"].update_one(
        {
            "_id": object_id,
            "role": "user",
            "is_active": True,
        },
        {
            "$inc": {
                "balance": amount,
            },
            "$set": {
                "updated_at": now,
            },
        },
    )

    if result.matched_count == 0:
        return jsonify({
            "status": "error",
            "message": "User not found or inactive.",
        }), 404

    user = current_app.db["users"].find_one({
        "_id": object_id,
    })

    return jsonify({
        "status": "success",
        "message": "User balance credited successfully.",
        "data": {
            "user_id": user_id,
            "amount": amount,
            "balance": user.get("balance", 0),
            "reason": reason,
            "admin_id": request.current_admin_id,
        },
    }), 200

@admin_api.route(
    "/users/<user_id>/balance/debit",
    methods=["POST"],
)
@token_required
def debit_user_balance(user_id):
    try:
        object_id = ObjectId(user_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid user ID.",
        }), 400

    payload = request.get_json(
        silent=True
    ) or {}

    try:
        amount = float(payload.get("amount"))
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "A valid amount is required.",
        }), 400

    if amount <= 0:
        return jsonify({
            "status": "error",
            "message": "Amount must be greater than zero.",
        }), 400

    reason = str(
        payload.get("reason", "")
    ).strip()

    if not reason:
        return jsonify({
            "status": "error",
            "message": "A reason is required.",
        }), 400

    now = datetime.now(timezone.utc)

    result = current_app.db["users"].update_one(
        {
            "_id": object_id,
            "role": "user",
            "is_active": True,
            "balance": {
                "$gte": amount,
            },
        },
        {
            "$inc": {
                "balance": -amount,
            },
            "$set": {
                "updated_at": now,
            },
        },
    )

    if result.matched_count == 0:
        user = current_app.db["users"].find_one({
            "_id": object_id,
            "role": "user",
        })

        if not user:
            return jsonify({
                "status": "error",
                "message": "User not found.",
            }), 404

        if not user.get("is_active", True):
            return jsonify({
                "status": "error",
                "message": "User account is inactive.",
            }), 400

        return jsonify({
            "status": "error",
            "message": "Insufficient balance.",
        }), 400

    user = current_app.db["users"].find_one({
        "_id": object_id,
    })

    return jsonify({
        "status": "success",
        "message": "User balance debited successfully.",
        "data": {
            "user_id": user_id,
            "amount": amount,
            "balance": user.get("balance", 0),
            "reason": reason,
            "admin_id": request.current_admin_id,
        },
    }), 200

@admin_api.route(
    "/payment-verifications",
    methods=["GET"],
)
@token_required
def get_payment_verifications():
    documents = current_app.db[
        "payment_verifications"
    ].find().sort(
        "created_at",
        -1,
    )

    result = []

    for payment in documents:
        result.append({
            "id": str(payment["_id"]),
            "user_id": str(payment["user_id"]),
            "amount": payment.get("amount", 0),
            "status": payment.get("status"),
            "payment_method": payment.get(
                "payment_method"
            ),
            "reference": payment.get(
                "reference"
            ),
            "created_at": (
                payment["created_at"].isoformat()
                if payment.get("created_at")
                else None
            ),
            "updated_at": (
                payment["updated_at"].isoformat()
                if payment.get("updated_at")
                else None
            ),
            "approved_at": (
                payment["approved_at"].isoformat()
                if payment.get("approved_at")
                else None
            ),
            "approved_by": payment.get(
                "approved_by"
            ),
            "rejected_at": (
                payment["rejected_at"].isoformat()
                if payment.get("rejected_at")
                else None
            ),
            "rejected_by": payment.get(
                "rejected_by"
            ),
            "rejection_reason": payment.get(
                "rejection_reason"
            ),
        })

    return jsonify({
        "status": "success",
        "data": result,
        "count": len(result),
    }), 200

@admin_api.route(
    "/payment-verifications/<payment_id>",
    methods=["GET"],
)
@token_required
def get_payment_verification(payment_id):
    try:
        object_id = ObjectId(payment_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid payment verification ID.",
        }), 400

    payment = current_app.db[
        "payment_verifications"
    ].find_one({
        "_id": object_id,
    })

    if not payment:
        return jsonify({
            "status": "error",
            "message": "Payment verification not found.",
        }), 404

    data = {
        "id": str(payment["_id"]),
        "user_id": str(payment["user_id"]),
        "amount": payment.get("amount", 0),
        "status": payment.get("status"),
        "payment_method": payment.get(
            "payment_method"
        ),
        "reference": payment.get(
            "reference"
        ),
        "created_at": (
            payment["created_at"].isoformat()
            if payment.get("created_at")
            else None
        ),
        "updated_at": (
            payment["updated_at"].isoformat()
            if payment.get("updated_at")
            else None
        ),
        "approved_at": (
            payment["approved_at"].isoformat()
            if payment.get("approved_at")
            else None
        ),
        "approved_by": payment.get(
            "approved_by"
        ),
        "rejected_at": (
            payment["rejected_at"].isoformat()
            if payment.get("rejected_at")
            else None
        ),
        "rejected_by": payment.get(
            "rejected_by"
        ),
        "rejection_reason": payment.get(
            "rejection_reason"
        ),
    }

    return jsonify({
        "status": "success",
        "data": data,
    }), 200