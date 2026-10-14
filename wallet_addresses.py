from datetime import datetime
import os
import re

from bson import ObjectId
from flask import Blueprint, jsonify, request
from pymongo import MongoClient
from dotenv import load_dotenv

from auth_helpers import authenticate_user, require_admin_permission
from notifications import record_admin_action

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"))
db = client[os.getenv("DB_NAME")]
wallet_addresses = db.wallet_addresses

wallet_addresses_bp = Blueprint("wallet_addresses", __name__, url_prefix="/api")


def serialize_wallet_address(wallet):
    wallet["_id"] = str(wallet["_id"])
    return wallet


def validate_wallet_address(data, current=None):
    current = current or {}
    result = {}

    for field in ("asset", "network", "address"):
        value = data.get(field, current.get(field))
        if not isinstance(value, str) or not value.strip():
            return None, f"{field.capitalize()} is required"
        result[field] = value.strip()

    result["asset"] = result["asset"].upper()
    if not re.fullmatch(r"[A-Z0-9]{2,20}", result["asset"]):
        return None, "Asset must contain 2 to 20 letters or numbers"
    if len(result["network"]) > 80:
        return None, "Network must be 80 characters or fewer"
    if len(result["address"]) > 500:
        return None, "Address must be 500 characters or fewer"

    enabled = data.get("enabled", current.get("enabled", True))
    if not isinstance(enabled, bool):
        return None, "Enabled must be a boolean"
    result["enabled"] = enabled

    return result, None


def find_duplicate(asset, network, exclude_id=None):
    query = {"asset": asset, "network": network}
    if exclude_id is not None:
        query["_id"] = {"$ne": exclude_id}
    return wallet_addresses.find_one(query, {"_id": 1}) is not None


@wallet_addresses_bp.route("/wallet-addresses", methods=["GET"])
@authenticate_user
def list_available_wallet_addresses():
    try:
        records = wallet_addresses.find({"enabled": True}).sort(
            [("asset", 1), ("network", 1)]
        )
        return jsonify({
            "success": True,
            "walletAddresses": [serialize_wallet_address(record) for record in records]
        }), 200
    except Exception as error:
        print(f"Fetch wallet addresses error: {error}")
        return jsonify({"success": False, "error": "Failed to fetch wallet addresses"}), 500


@wallet_addresses_bp.route("/admin/wallet-addresses", methods=["GET"])
@require_admin_permission("wallets:read")
def admin_list_wallet_addresses():
    try:
        records = wallet_addresses.find({}).sort([("asset", 1), ("network", 1)])
        return jsonify({
            "success": True,
            "walletAddresses": [serialize_wallet_address(record) for record in records]
        }), 200
    except Exception as error:
        print(f"Admin fetch wallet addresses error: {error}")
        return jsonify({"success": False, "error": "Failed to fetch wallet addresses"}), 500


@wallet_addresses_bp.route("/admin/wallet-addresses", methods=["POST"])
@require_admin_permission("wallets:manage")
def create_wallet_address():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "A JSON request body is required"}), 400

    record, error = validate_wallet_address(data)
    if error:
        return jsonify({"success": False, "error": error}), 400
    if find_duplicate(record["asset"], record["network"]):
        return jsonify({"success": False, "error": "A wallet address already exists for this asset and network"}), 409

    now = datetime.utcnow()
    record.update({"createdAt": now, "updatedAt": now})
    try:
        result = wallet_addresses.insert_one(record)
        record["_id"] = str(result.inserted_id)
        record_admin_action(request.user_id, "wallet_address.created", related_id=result.inserted_id, details={"asset": record["asset"], "network": record["network"]})
        return jsonify({"success": True, "walletAddress": record}), 201
    except Exception as error:
        print(f"Create wallet address error: {error}")
        return jsonify({"success": False, "error": "Failed to create wallet address"}), 500


@wallet_addresses_bp.route("/admin/wallet-addresses/<wallet_id>", methods=["PUT"])
@require_admin_permission("wallets:manage")
def update_wallet_address(wallet_id):
    try:
        object_id = ObjectId(wallet_id)
    except Exception:
        return jsonify({"success": False, "error": "Invalid wallet address ID"}), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "A JSON request body is required"}), 400

    existing = wallet_addresses.find_one({"_id": object_id})
    if not existing:
        return jsonify({"success": False, "error": "Wallet address not found"}), 404

    record, error = validate_wallet_address(data, existing)
    if error:
        return jsonify({"success": False, "error": error}), 400
    if find_duplicate(record["asset"], record["network"], object_id):
        return jsonify({"success": False, "error": "A wallet address already exists for this asset and network"}), 409

    record["updatedAt"] = datetime.utcnow()
    try:
        wallet_addresses.update_one({"_id": object_id}, {"$set": record})
        updated = wallet_addresses.find_one({"_id": object_id})
        record_admin_action(request.user_id, "wallet_address.updated", related_id=object_id, details={"asset": record["asset"], "network": record["network"]})
        return jsonify({"success": True, "walletAddress": serialize_wallet_address(updated)}), 200
    except Exception as error:
        print(f"Update wallet address error: {error}")
        return jsonify({"success": False, "error": "Failed to update wallet address"}), 500


@wallet_addresses_bp.route("/admin/wallet-addresses/<wallet_id>", methods=["DELETE"])
@require_admin_permission("wallets:manage")
def delete_wallet_address(wallet_id):
    try:
        object_id = ObjectId(wallet_id)
    except Exception:
        return jsonify({"success": False, "error": "Invalid wallet address ID"}), 400

    try:
        existing = wallet_addresses.find_one({"_id": object_id}, {"asset": 1, "network": 1})
        result = wallet_addresses.delete_one({"_id": object_id})
        if result.deleted_count == 0:
            return jsonify({"success": False, "error": "Wallet address not found"}), 404
        record_admin_action(request.user_id, "wallet_address.deleted", related_id=object_id, details={"asset": (existing or {}).get("asset"), "network": (existing or {}).get("network")})
        return jsonify({"success": True, "message": "Wallet address deleted successfully"}), 200
    except Exception as error:
        print(f"Delete wallet address error: {error}")
        return jsonify({"success": False, "error": "Failed to delete wallet address"}), 500
