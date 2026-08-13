from datetime import datetime, timezone

from bson import ObjectId
from flask import Blueprint, current_app, jsonify, request
import secrets
from auth import (
    admin_token_required,
    user_token_required,
    verify_pin,
    hash_pin,
    withdrawal_pin_required,
)
payment_api = Blueprint(
    "payment_api",
    __name__,
)
def serialize_withdrawal(withdrawal):
    return {
        "id": str(withdrawal["_id"]),
        "user_id": str(withdrawal["user_id"]),
        "voucher_id": str(withdrawal["voucher_id"]),
        "amount": withdrawal.get("amount"),
        "status": withdrawal.get("status"),
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
    }
def serialize_voucher(voucher):
    return {
        "id": str(voucher["_id"]),
        "user_id": str(voucher["user_id"]),
        "payment_verification_id": str(
            voucher["payment_verification_id"]
        ),
        "code": voucher["code"],
        "eligible_amount": voucher.get("eligible_amount"),
        "currency": voucher.get("currency"),
        "status": voucher.get("status"),
        "created_at": (
            voucher["created_at"].isoformat()
            if voucher.get("created_at")
            else None
        ),
        "created_by": voucher.get("created_by"),
        "used_at": (
            voucher["used_at"].isoformat()
            if voucher.get("used_at")
            else None
        ),
        "used_for_withdrawal_id": (
            str(voucher["used_for_withdrawal_id"])
            if voucher.get("used_for_withdrawal_id")
            else None
        ),
    }

def serialize_deposit_account(account):
    return {
        "id": str(account["_id"]),
        "account_name": account.get("account_name", ""),
        "account_number": account.get("account_number", ""),
        "bank_name": account.get("bank_name", ""),
        "bank_code": account.get("bank_code", ""),
        "instructions": account.get("instructions", ""),
        "is_active": account.get("is_active", False),
        "created_at": (
            account["created_at"].isoformat()
            if isinstance(account.get("created_at"), datetime)
            else None
        ),
        "updated_at": (
            account["updated_at"].isoformat()
            if isinstance(account.get("updated_at"), datetime)
            else None
        ),
    }

def serialize_payment_verification(payment):
    deposit_account_id = payment.get("deposit_account_id")

    return {
        "id": str(payment["_id"]),
        "user_id": str(payment["user_id"]),
        "deposit_account_id": (
            str(deposit_account_id)
            if deposit_account_id
            else None
        ),
        "amount": payment.get("amount"),
        "currency": payment.get("currency"),
        "status": payment.get("status"),
        "reference": payment.get("reference"),
        "note": payment.get("note"),
        "rejection_reason": payment.get(
            "rejection_reason"
        ),
        "created_at": (
            payment["created_at"].isoformat()
            if payment.get("created_at")
            else None
        ),
        "approved_at": (
            payment["approved_at"].isoformat()
            if payment.get("approved_at")
            else None
        ),
        "rejected_at": (
            payment["rejected_at"].isoformat()
            if payment.get("rejected_at")
            else None
        ),
        "approved_by": payment.get("approved_by"),
        "rejected_by": payment.get("rejected_by"),
    }

def notify_payment_confirmation(payment):
    notification_service_url = current_app.config.get(
        "NOTIFICATION_SERVICE_URL"
    )

    if not notification_service_url:
        return

    current_app.logger.info(
        "Payment confirmation notification queued"
    )

def notify_payment_status_change(
    payment,
    status,
):
    notification_service_url = current_app.config.get(
        "NOTIFICATION_SERVICE_URL"
    )

    if not notification_service_url:
        current_app.logger.info(
            f"Payment status notification queued: {status}"
        )
        return

    current_app.logger.info(
        f"Payment status notification queued: {status}"
    )

@payment_api.route(
    "/config",
    methods=["GET"],
)
def payment_config():
    return jsonify({
        "status": "success",
        "data": {
            "currency": current_app.config["CURRENCY"],
            "welcome_amount": current_app.config["WELCOME_AMOUNT"],
            "minimum_withdrawal": current_app.config[
                "MINIMUM_WITHDRAWAL"
            ],
            "maximum_withdrawal": current_app.config[
                "MAXIMUM_WITHDRAWAL"
            ],
        },
    }), 200


@payment_api.route(
    "/deposit-info",
    methods=["GET"],
)
def deposit_info():
    accounts = current_app.db["deposit_accounts"].find(
        {
            "is_active": True,
        }
    ).sort(
        "created_at",
        1,
    )

    return jsonify({
        "status": "success",
        "data": [
            serialize_deposit_account(account)
            for account in accounts
        ],
    }), 200


@payment_api.route(
    "/deposit-accounts",
    methods=["POST"],
)
@admin_token_required
def create_deposit_account():
    payload = request.get_json(
        silent=True
    ) or {}

    required_fields = [
        "account_name",
        "account_number",
        "bank_name",
    ]

    for field in required_fields:
        if not str(payload.get(field, "")).strip():
            return jsonify({
                "status": "error",
                "message": f"{field} is required.",
            }), 400

    now = datetime.now(timezone.utc)

    account = {
        "account_name": str(
            payload["account_name"]
        ).strip(),
        "account_number": str(
            payload["account_number"]
        ).strip(),
        "bank_name": str(
            payload["bank_name"]
        ).strip(),
        "bank_code": str(
            payload.get("bank_code", "")
        ).strip(),
        "instructions": str(
            payload.get("instructions", "")
        ).strip(),
        "is_active": bool(
            payload.get(
                "is_active",
                True,
            )
        ),
        "created_at": now,
        "updated_at": now,
        "created_by": request.current_admin_id,
    }

    result = current_app.db[
        "deposit_accounts"
    ].insert_one(account)

    account["_id"] = result.inserted_id

    return jsonify({
        "status": "success",
        "message": "Deposit account created successfully.",
        "data": serialize_deposit_account(account),
    }), 201


@payment_api.route(
    "/deposit-accounts",
    methods=["GET"],
)
@admin_token_required
def get_deposit_accounts():
    accounts = current_app.db[
        "deposit_accounts"
    ].find().sort(
        "created_at",
        -1,
    )

    return jsonify({
        "status": "success",
        "data": [
            serialize_deposit_account(account)
            for account in accounts
        ],
    }), 200


@payment_api.route(
    "/deposit-accounts/<account_id>",
    methods=["PUT"],
)
@admin_token_required
def update_deposit_account(account_id):
    try:
        object_id = ObjectId(account_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid deposit account ID.",
        }), 400

    payload = request.get_json(
        silent=True
    ) or {}

    allowed_fields = [
        "account_name",
        "account_number",
        "bank_name",
        "bank_code",
        "instructions",
        "is_active",
    ]

    updates = {}

    for field in allowed_fields:
        if field in payload:
            if field == "is_active":
                updates[field] = bool(
                    payload[field]
                )
            else:
                updates[field] = str(
                    payload[field]
                ).strip()

    if not updates:
        return jsonify({
            "status": "error",
            "message": "No valid fields supplied.",
        }), 400

    updates["updated_at"] = datetime.now(
        timezone.utc
    )
    updates["updated_by"] = request.current_admin_id

    result = current_app.db[
        "deposit_accounts"
    ].update_one(
        {
            "_id": object_id,
        },
        {
            "$set": updates,
        },
    )

    if result.matched_count == 0:
        return jsonify({
            "status": "error",
            "message": "Deposit account not found.",
        }), 404

    account = current_app.db[
        "deposit_accounts"
    ].find_one({
        "_id": object_id,
    })

    return jsonify({
        "status": "success",
        "message": "Deposit account updated successfully.",
        "data": serialize_deposit_account(account),
    }), 200


@payment_api.route(
    "/deposit-accounts/<account_id>",
    methods=["DELETE"],
)
@admin_token_required
def deactivate_deposit_account(account_id):
    try:
        object_id = ObjectId(account_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid deposit account ID.",
        }), 400

    result = current_app.db[
        "deposit_accounts"
    ].update_one(
        {
            "_id": object_id,
        },
        {
            "$set": {
                "is_active": False,
                "updated_at": datetime.now(
                    timezone.utc
                ),
                "updated_by": request.current_admin_id,
            },
        },
    )

    if result.matched_count == 0:
        return jsonify({
            "status": "error",
            "message": "Deposit account not found.",
        }), 404

    return jsonify({
        "status": "success",
        "message": "Deposit account deactivated successfully.",
    }), 200


@payment_api.route(
    "/confirm-deposit",
    methods=["POST"],
)
@user_token_required
def confirm_deposit():
    payload = request.get_json(
        silent=True
    ) or {}

    deposit_account_id = str(
        payload.get(
            "deposit_account_id",
            "",
        )
    ).strip()

    amount = payload.get("amount")

    note = str(
        payload.get(
            "note",
            "",
        )
    ).strip()

    if not deposit_account_id:
        return jsonify({
            "status": "error",
            "message": "Deposit account ID is required.",
        }), 400

    try:
        deposit_account_object_id = ObjectId(
            deposit_account_id
        )
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid deposit account ID.",
        }), 400

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "A valid payment amount is required.",
        }), 400

    if amount <= 0:
        return jsonify({
            "status": "error",
            "message": "Payment amount must be greater than zero.",
        }), 400

    deposit_account = current_app.db[
        "deposit_accounts"
    ].find_one({
        "_id": deposit_account_object_id,
        "is_active": True,
    })

    if not deposit_account:
        return jsonify({
            "status": "error",
            "message": "Deposit account is unavailable.",
        }), 404

    existing = current_app.db[
        "payment_verifications"
    ].find_one({
        "user_id": request.current_user_id,
        "status": "pending",
    })

    if existing:
        return jsonify({
            "status": "error",
            "message": "You already have a pending payment confirmation.",
            "data": serialize_payment_verification(
                existing
            ),
        }), 409

    now = datetime.now(timezone.utc)

    payment = {
        "user_id": ObjectId(
            request.current_user_id
        ),
        "deposit_account_id": deposit_account_object_id,
        "amount": amount,
        "currency": current_app.config["CURRENCY"],
        "status": "pending",
        "reference": None,
        "note": note,
        "created_at": now,
        "updated_at": now,
        "approved_at": None,
        "rejected_at": None,
        "approved_by": None,
        "rejected_by": None,
        "rejection_reason": None,
    }

    result = current_app.db[
        "payment_verifications"
    ].insert_one(payment)

    payment["_id"] = result.inserted_id

    notify_payment_confirmation(
        payment
    )

    return jsonify({
        "status": "success",
        "message": "Payment confirmation submitted successfully.",
        "data": serialize_payment_verification(
            payment
        ),
    }), 201

@payment_api.route(
    "/payment-verifications",
    methods=["GET"],
)
@admin_token_required
def get_payment_verifications():
    status = request.args.get(
        "status",
        "pending",
    ).strip().lower()

    allowed_statuses = {
        "pending",
        "approved",
        "rejected",
    }

    if status not in allowed_statuses:
        return jsonify({
            "status": "error",
            "message": "Invalid payment verification status.",
        }), 400

    payments = current_app.db[
        "payment_verifications"
    ].find({
        "status": status,
    }).sort(
        "created_at",
        -1,
    )

    return jsonify({
        "status": "success",
        "data": [
            serialize_payment_verification(
                payment
            )
            for payment in payments
        ],
    }), 200


@payment_api.route(
    "/payment-verifications/<payment_id>/approve",
    methods=["POST"],
)
@admin_token_required
def approve_payment_verification(payment_id):
    try:
        object_id = ObjectId(payment_id)

    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid payment verification ID.",
        }), 400


    now = datetime.now(timezone.utc)


    result = current_app.db[
        "payment_verifications"
    ].update_one(
        {
            "_id": object_id,
            "status": "pending",
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

        existing = current_app.db[
            "payment_verifications"
        ].find_one({
            "_id": object_id,
        })


        if not existing:
            return jsonify({
                "status": "error",
                "message": "Payment verification not found.",
            }), 404


        return jsonify({
            "status": "error",
            "message": "Payment verification is no longer pending.",
        }), 409



    payment = current_app.db[
        "payment_verifications"
    ].find_one({
        "_id": object_id,
    })


    if not payment:
        return jsonify({
            "status": "error",
            "message": "Payment verification not found after approval.",
        }), 404



    existing_voucher = current_app.db[
        "vouchers"
    ].find_one({
        "payment_verification_id": object_id,
    })


    if not existing_voucher:

        voucher_code = (
            f"cashml-{secrets.randbelow(100000000):08d}"
        )


        voucher = {
            "user_id": payment["user_id"],
            "payment_verification_id": object_id,
            "code": voucher_code,
            "eligible_amount": payment["amount"],
            "currency": payment["currency"],
            "status": "unused",
            "created_at": now,
            "created_by": request.current_admin_id,
            "used_at": None,
            "used_for_withdrawal_id": None,
        }


        current_app.db[
            "vouchers"
        ].insert_one(voucher)



    notify_payment_status_change(
        payment,
        "approved",
    )


    return jsonify({
        "status": "success",
        "message": "Payment verification approved successfully.",
        "data": serialize_payment_verification(
            payment
        ),
    }), 200
@payment_api.route(
    "/payment-verifications/<payment_id>/reject",
    methods=["POST"],
)
@admin_token_required
def reject_payment_verification(payment_id):
    try:
        object_id = ObjectId(payment_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid payment verification ID.",
        }), 400

    payload = request.get_json(
        silent=True
    ) or {}

    rejection_reason = str(
        payload.get(
            "reason",
            "",
        )
    ).strip()

    if not rejection_reason:
        return jsonify({
            "status": "error",
            "message": "Rejection reason is required.",
        }), 400

    now = datetime.now(timezone.utc)

    result = current_app.db[
        "payment_verifications"
    ].update_one(
        {
            "_id": object_id,
            "status": "pending",
        },
        {
            "$set": {
                "status": "rejected",
                "rejected_at": now,
                "rejected_by": request.current_admin_id,
                "rejection_reason": rejection_reason,
                "updated_at": now,
            },
        },
    )

    if result.matched_count == 0:
        existing = current_app.db[
            "payment_verifications"
        ].find_one({
            "_id": object_id,
        })

        if not existing:
            return jsonify({
                "status": "error",
                "message": "Payment verification not found.",
            }), 404

        return jsonify({
            "status": "error",
            "message": "Payment verification is no longer pending.",
        }), 409

    payment = current_app.db[
        "payment_verifications"
    ].find_one({
        "_id": object_id,
    })

    notify_payment_status_change(
        payment,
        "rejected",
    )

    return jsonify({
        "status": "success",
        "message": "Payment verification rejected successfully.",
        "data": serialize_payment_verification(
            payment
        ),
    }), 200

@payment_api.route(
    "/payment-verifications/<payment_id>/voucher",
    methods=["POST"],
)
@admin_token_required
def generate_voucher(payment_id):
    try:
        payment_object_id = ObjectId(payment_id)
    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid payment verification ID.",
        }), 400

    payment = current_app.db[
        "payment_verifications"
    ].find_one({
        "_id": payment_object_id,
        "status": "approved",
    })

    if not payment:
        return jsonify({
            "status": "error",
            "message": "Approved payment verification not found.",
        }), 404

    existing_voucher = current_app.db[
        "vouchers"
    ].find_one({
        "payment_verification_id": payment_object_id,
    })

    if existing_voucher:
        return jsonify({
            "status": "success",
            "message": "Voucher already exists.",
            "data": serialize_voucher(
                existing_voucher
            ),
        }), 200

    voucher_code = f"cashvm-{secrets.randbelow(100000000):08d}"
    now = datetime.now(timezone.utc)

    voucher = {
    "user_id": payment["user_id"],
    "payment_verification_id": payment_object_id,
    "code": voucher_code,
    "status": "unused",
    "created_at": now,
    "created_by": request.current_admin_id,
    "used_at": None,
    "used_for_withdrawal_id": None,
}

    result = current_app.db[
        "vouchers"
    ].insert_one(voucher)

    voucher["_id"] = result.inserted_id

    return jsonify({
        "status": "success",
        "message": "Voucher generated successfully.",
        "data": serialize_voucher(voucher),
    }), 201

@payment_api.route(
    "/voucher",
    methods=["GET"],
)
@user_token_required
def get_user_voucher():
    voucher = current_app.db[
        "vouchers"
    ].find_one(
        {
            "user_id": ObjectId(
                request.current_user_id
            )
        },
        sort=[
            ("created_at", -1)
        ],
    )

    if not voucher:
        return jsonify({
            "status": "error",
            "message": "No voucher available.",
        }), 404

    return jsonify({
        "status": "success",
        "data": serialize_voucher(voucher),
    }), 200

@payment_api.route(
    "/withdraw",
    methods=["POST"],
)
@user_token_required
def create_withdrawal():
    payload = request.get_json(
        silent=True
    ) or {}

    amount = payload.get("amount")
    pin = str(
        payload.get("pin", "")
    ).strip()
    voucher_code = str(
        payload.get("voucher_code", "")
    ).strip()

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return jsonify({
            "status": "error",
            "message": "A valid withdrawal amount is required.",
        }), 400

    if amount <= 0:
        return jsonify({
            "status": "error",
            "message": "Withdrawal amount must be greater than zero.",
        }), 400

    if amount < current_app.config[
        "MINIMUM_WITHDRAWAL"
    ]:
        return jsonify({
            "status": "error",
            "message": "Withdrawal amount is below the minimum allowed.",
        }), 400

    if amount > current_app.config[
        "MAXIMUM_WITHDRAWAL"
    ]:
        return jsonify({
            "status": "error",
            "message": "Withdrawal amount exceeds the maximum allowed.",
        }), 400

    if not pin:
        return jsonify({
            "status": "error",
            "message": "Withdrawal PIN is required.",
        }), 400

    if not voucher_code:
        return jsonify({
            "status": "error",
            "message": "Voucher code is required.",
        }), 400

    user_id = ObjectId(
        request.current_user_id
    )


    user = current_app.db["users"].find_one({
        "_id": user_id,
    })

    if not user:
        return jsonify({
            "status": "error",
            "message": "User not found.",
        }), 404

    withdrawal_pin_hash = user.get(
        "withdrawal_pin_hash"
    )

    if not withdrawal_pin_hash:
        return jsonify({
            "status": "error",
            "message": "Withdrawal PIN has not been set.",
        }), 400

    if not verify_pin(
        withdrawal_pin_hash,
        pin,
    ):
        return jsonify({
            "status": "error",
            "message": "Invalid withdrawal PIN.",
        }), 401

    voucher = current_app.db[
        "vouchers"
    ].find_one({
        "user_id": user_id,
        "code": voucher_code,
        "status": "unused",
    })

    if not voucher:
        return jsonify({
            "status": "error",
            "message": "Invalid or already used voucher.",
        }), 400

    now = datetime.now(timezone.utc)

    withdrawals = current_app.db[
        "withdrawals"
    ]

    users = current_app.db[
        "users"
    ]

    vouchers = current_app.db[
        "vouchers"
    ]

    try:
        with current_app.mongo_client.start_session() as session:
            with session.start_transaction():

                withdrawal = {
                    "user_id": user_id,
                    "voucher_id": voucher["_id"],
                    "amount": amount,
                    "status": "submitted",
                    "created_at": now,
                    "updated_at": now,
                    "approved_at": None,
                    "approved_by": None,
                    "rejected_at": None,
                    "rejected_by": None,
                    "rejection_reason": None,
                }

                withdrawal_result = withdrawals.insert_one(
                    withdrawal,
                    session=session,
                )

                withdrawal_id = (
                    withdrawal_result.inserted_id
                )

                balance_result = users.update_one(
                    {
                        "_id": user_id,
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
                    session=session,
                )

                if balance_result.modified_count != 1:
                    raise ValueError(
                        "INSUFFICIENT_BALANCE"
                    )

                voucher_result = vouchers.update_one(
                    {
                        "_id": voucher["_id"],
                        "user_id": user_id,
                        "status": "unused",
                    },
                    {
                        "$set": {
                            "status": "used",
                            "used_at": now,
                            "used_for_withdrawal_id": (
                                withdrawal_id
                            ),
                        },
                    },
                    session=session,
                )

                if voucher_result.modified_count != 1:
                    raise ValueError(
                        "VOUCHER_UNAVAILABLE"
                    )

    except ValueError as exc:
        if str(exc) == "INSUFFICIENT_BALANCE":
            return jsonify({
                "status": "error",
                "message": "Insufficient withdrawable balance.",
            }), 400

        if str(exc) == "VOUCHER_UNAVAILABLE":
            return jsonify({
                "status": "error",
                "message": "Voucher could not be reserved.",
            }), 409

        return jsonify({
            "status": "error",
            "message": "Withdrawal could not be completed.",
        }), 400

    except Exception:
        current_app.logger.exception(
            "Withdrawal transaction failed"
        )

        return jsonify({
            "status": "error",
            "message": "Withdrawal could not be completed.",
        }), 500

    updated_user = users.find_one({
        "_id": user_id,
    })

    return jsonify({
        "status": "success",
        "message": "Withdrawal submitted successfully.",
        "data": {
            "withdrawal_id": str(
                withdrawal_id
            ),
            "amount": amount,
            "balance": updated_user.get(
                "balance",
                0,
            ),
            "status": "submitted",
        },
    }), 201

@payment_api.route(
    "/status/<payment_id>",
    methods=["GET"],
)
@user_token_required
def get_payment_status(payment_id):

    try:
        payment_object_id = ObjectId(payment_id)

    except Exception:
        return jsonify({
            "status": "error",
            "message": "Invalid payment verification ID.",
        }), 400


    payment = current_app.db[
        "payment_verifications"
    ].find_one({
        "_id": payment_object_id,
        "user_id": ObjectId(
            request.current_user_id
        ),
    })


    if not payment:
        return jsonify({
            "status": "error",
            "message": "Payment verification not found.",
        }), 404


    return jsonify({
        "status": "success",
        "data": serialize_payment_verification(
            payment
        ),
    }), 200

@payment_api.route(
    "/withdrawals",
    methods=["GET"],
)
@user_token_required
def get_user_withdrawals():

    withdrawals = current_app.db[
        "withdrawals"
    ].find({
        "user_id": ObjectId(
            request.current_user_id
        ),
    }).sort(
        "created_at",
        -1,
    )


    return jsonify({
        "status": "success",
        "data": [
            serialize_withdrawal(
                withdrawal
            )
            for withdrawal in withdrawals
        ],
    }), 200

@payment_api.route(
    "/admin/withdrawals",
    methods=["GET"],
)
@admin_token_required
def get_all_withdrawals():

    status = request.args.get(
        "status",
        "submitted",
    )


    withdrawals = current_app.db[
        "withdrawals"
    ].find({
        "status": status,
    }).sort(
        "created_at",
        -1,
    )


    return jsonify({
        "status": "success",
        "data": [
            serialize_withdrawal(
                withdrawal
            )
            for withdrawal in withdrawals
        ],
    }),200

@payment_api.route(
    "/withdrawals/<withdrawal_id>/approve",
    methods=["POST"],
)
@admin_token_required
def approve_withdrawal(withdrawal_id):

    try:
        withdrawal_object_id = ObjectId(
            withdrawal_id
        )

    except Exception:
        return jsonify({
            "status":"error",
            "message":"Invalid withdrawal ID.",
        }),400


    now = datetime.now(timezone.utc)


    result = current_app.db[
        "withdrawals"
    ].update_one(
        {
            "_id": withdrawal_object_id,
            "status":"submitted",
        },
        {
            "$set":{
                "status":"approved",
                "approved_at":now,
                "approved_by":request.current_admin_id,
                "updated_at":now,
            }
        }
    )


    if result.matched_count == 0:
        return jsonify({
            "status":"error",
            "message":"Withdrawal not found or already processed.",
        }),409


    withdrawal = current_app.db[
        "withdrawals"
    ].find_one({
        "_id": withdrawal_object_id
    })


    return jsonify({
        "status":"success",
        "message":"Withdrawal approved successfully.",
        "data":serialize_withdrawal(
            withdrawal
        ),
    }),200

@payment_api.route(
    "/withdrawals/<withdrawal_id>/reject",
    methods=["POST"],
)
@admin_token_required
def reject_withdrawal(withdrawal_id):

    try:
        withdrawal_object_id = ObjectId(
            withdrawal_id
        )

    except Exception:
        return jsonify({
            "status":"error",
            "message":"Invalid withdrawal ID.",
        }),400


    payload = request.get_json(
        silent=True
    ) or {}


    reason = str(
        payload.get(
            "reason",
            ""
        )
    ).strip()


    if not reason:
        return jsonify({
            "status":"error",
            "message":"Rejection reason required.",
        }),400


    now = datetime.now(timezone.utc)


    result = current_app.db[
        "withdrawals"
    ].update_one(
        {
            "_id":withdrawal_object_id,
            "status":"submitted",
        },
        {
            "$set":{
                "status":"rejected",
                "rejected_at":now,
                "rejected_by":request.current_admin_id,
                "rejection_reason":reason,
                "updated_at":now,
            }
        }
    )


    if result.matched_count == 0:
        return jsonify({
            "status":"error",
            "message":"Withdrawal not found or already processed.",
        }),409


    withdrawal = current_app.db[
        "withdrawals"
    ].find_one({
        "_id":withdrawal_object_id
    })


    return jsonify({
        "status":"success",
        "message":"Withdrawal rejected successfully.",
        "data":serialize_withdrawal(
            withdrawal
        ),
    }),200

@payment_api.route(
    "/set-withdrawal-pin",
    methods=["POST"],
)
@user_token_required
def set_withdrawal_pin():

    payload = request.get_json(
        silent=True
    ) or {}

    pin = str(
        payload.get(
            "pin",
            ""
        )
    ).strip()


    if not pin.isdigit():
        return jsonify({
            "status": "error",
            "message": "PIN must contain only numbers.",
        }),400


    if len(pin) != 4:
        return jsonify({
            "status": "error",
            "message": "PIN must be exactly 4 digits.",
        }),400


    user_id = ObjectId(
        request.current_user_id
    )


    user = current_app.db[
        "users"
    ].find_one({
        "_id":user_id,
    })


    if not user:
        return jsonify({
            "status":"error",
            "message":"User not found.",
        }),404


    if user.get(
        "withdrawal_pin_hash"
    ):
        return jsonify({
            "status":"error",
            "message":"Withdrawal PIN already exists.",
        }),409


    now = datetime.now(
        timezone.utc
    )


    current_app.db[
        "users"
    ].update_one(
        {
            "_id":user_id,
        },
        {
            "$set":{
                "withdrawal_pin_hash":hash_pin(pin),
                "withdrawal_pin_created_at":now,
                "updated_at":now,
            }
        }
    )


    return jsonify({
        "status":"success",
        "message":"Withdrawal PIN created successfully.",
    }),201



@payment_api.route(
    "/change-withdrawal-pin",
    methods=["POST"],
)
@user_token_required
def change_withdrawal_pin():

    payload = request.get_json(
        silent=True
    ) or {}


    old_pin = str(
        payload.get(
            "old_pin",
            ""
        )
    ).strip()


    new_pin = str(
        payload.get(
            "new_pin",
            ""
        )
    ).strip()


    if len(new_pin) != 4 or not new_pin.isdigit():
        return jsonify({
            "status":"error",
            "message":"New PIN must be exactly 4 digits.",
        }),400


    user_id = ObjectId(
        request.current_user_id
    )


    user = current_app.db[
        "users"
    ].find_one({
        "_id":user_id
    })


    if not user:
        return jsonify({
            "status":"error",
            "message":"User not found.",
        }),404


    if not verify_pin(
        user.get(
            "withdrawal_pin_hash"
        ),
        old_pin,
    ):
        return jsonify({
            "status":"error",
            "message":"Old PIN is incorrect.",
        }),401


    current_app.db[
        "users"
    ].update_one(
        {
            "_id":user_id
        },
        {
            "$set":{
                "withdrawal_pin_hash":hash_pin(new_pin),
                "updated_at":datetime.now(timezone.utc),
            }
        }
    )


    return jsonify({
        "status":"success",
        "message":"Withdrawal PIN updated successfully.",
    }),200


@payment_api.route(
    "/withdrawal-pin-status",
    methods=["GET"],
)
@user_token_required
def withdrawal_pin_status():

    user = request.current_user

    return jsonify({
        "status":"success",
        "data":{
            "pin_set": bool(
                user.get(
                    "withdrawal_pin_hash"
                )
            )
        }
    }),200
@payment_api.route(
    "/withdrawal-eligibility",
    methods=["GET"],
)
@user_token_required
def withdrawal_eligibility():

    user = request.current_user

    referral_count = current_app.db[
        "users"
    ].count_documents({
        "referred_by": user["_id"]
    })


    required_referrals = 10


    eligible = referral_count >= required_referrals


    return jsonify({
        "status": "success",
        "data": {
            "eligible": eligible,
            "referrals": referral_count,
            "required": required_referrals
        }
    }), 200