from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from dashboard_bp import JWT_ALGORITHM, JWT_SECRET
from pymongo import MongoClient, ReturnDocument
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os
import re
import resend
import jwt
from bson.objectid import ObjectId
import random
import string
import logging

# --- Setup logging ---
logger = logging.getLogger(__name__)

# --- Flask Blueprint ---
auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')

# --- Load Environment Variables ---
load_dotenv()
mongo_uri = os.getenv("MONGO_URI")
db_name = os.getenv("DB_NAME")
resend_api_key = os.getenv("RESEND_API_KEY")

if not mongo_uri or not db_name:
    raise RuntimeError("MONGO_URI and DB_NAME environment variables must be set")

# --- MongoDB Setup ---
client = MongoClient(mongo_uri)
db = client[db_name]
users = db.users
otp_codes = db.otp_codes
password_reset_otps = db.password_reset_otps

# Create TTL indexes (expire documents when their expires_at passes)
# NOTE: expireAfterSeconds applies to the indexed field's value interpreted as a date; set to 0 to expire at that time.
try:
    otp_codes.create_index("expires_at", expireAfterSeconds=0)
    password_reset_otps.create_index("expires_at", expireAfterSeconds=0)
except Exception:
    # Index creation may fail on some managed DBs due to permissions — log and continue
    logger.exception("Failed to create TTL indexes for OTP collections")

# --- Initialize Resend ---
if not resend_api_key:
    logger.warning("RESEND_API_KEY not set. Email sending will fail in production.")
else:
    resend.api_key = resend_api_key

# --- Helpers ---

def generate_otp():
    return ''.join(random.choices(string.digits, k=6))


def generate_referral_code(length=6):
    return 'CAP' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))


def normalize_referral_code(referral_code):
    if referral_code is None:
        return None
    cleaned = str(referral_code).strip().upper()
    return cleaned or None


EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def send_otp_email(user_email, otp_code, first_name, purpose="verification"):
    """Send OTP verification email using Resend. Returns True on success, False otherwise."""
    try:
        if purpose == "password_reset":
            subject = "🔐 Password Reset Code - Capitello"
            greeting = f"Hi {first_name},"
            main_message = "We received a request to reset your password. Use the verification code below to proceed:"
        else:
            subject = "🔐 Your Capitello Verification Code"
            greeting = f"Welcome to Capitello, {first_name}! 🎉"
            main_message = "To complete your registration, please use the following verification code:"

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
        </head>
        <body>
            <div>
                <h1>Capitello</h1>
                <h2>{greeting}</h2>
                <p>{main_message}</p>
                <div style="font-size:36px;font-weight:700;letter-spacing:6px;">{otp_code}</div>
                <p>⚠️ This code will expire in 10 minutes</p>
                <p>If you didn't request this code, please ignore this email.</p>
                <hr>
                <small>© 2025 Capitello</small>
            </div>
        </body>
        </html>
        """

        params = {
            "from": "Capitello <destinytch.com.ng>",
            "to": [user_email],
            "subject": subject,
            "html": html_content
        }

        # Resend SDK: handle both possible return types. If the SDK raises, it will be caught below.
        response = resend.Emails.send(params)

        # Response validation: depending on SDK version this may be a dict-like object
        if hasattr(response, 'get'):
            success = bool(response.get('id') or response.get('message_id') or response.get('status'))
        else:
            # If SDK returns an object, attempt to read .id
            success = bool(getattr(response, 'id', None))

        if not success:
            logger.error("Resend returned an unexpected response: %s", response)
            return False

        return True

    except Exception as e:
        logger.exception("Failed to send OTP email: %s", e)
        return False


# --- Routes ---

@auth_bp.route('/register', methods=['POST'])
def register():
    try:
        data = request.get_json()
        if not data or not isinstance(data, dict):
            return jsonify({'success': False, 'error': 'Invalid JSON payload'}), 400

        first_name = data.get('firstName', '').strip()
        last_name = data.get('lastName', '').strip()
        email = data.get('email', '').lower().strip()
        password = data.get('password', '').strip()
        referral_code_submitted = normalize_referral_code(data.get('referralCode'))

        errors = []
        if len(first_name) < 2: errors.append("First name must be at least 2 characters")
        if len(last_name) < 2: errors.append("Last name must be at least 2 characters")
        if not EMAIL_REGEX.match(email): errors.append("Valid email required")
        if len(password) < 8: errors.append("Password must be at least 8 characters")
        if not any(c.isdigit() for c in password): errors.append("Password must contain a number")
        if not any(c.isupper() for c in password): errors.append("Password must contain an uppercase letter")
        if not any(c.islower() for c in password): errors.append("Password must contain a lowercase letter")
        if errors:
            return jsonify({'success': False, 'error': 'Validation failed', 'details': errors}), 400

        if users.find_one({'email': email}):
            return jsonify({'success': False, 'error': 'Email already registered', 'message': 'Account exists'}), 409

        referred_by = None
        if referral_code_submitted:
            referrer = users.find_one({"referralCode": referral_code_submitted})
            if not referrer:
                return jsonify({'success': False, 'error': 'Referral code is invalid or not active.'}), 400
            if referrer.get('email', '').lower() == email:
                return jsonify({'success': False, 'error': 'You cannot refer yourself.'}), 400
            referred_by = referral_code_submitted

        referral_code = generate_referral_code()

        new_user = {
            'firstName': first_name,
            'lastName': last_name,
            'email': email,
            'password': generate_password_hash(password),
            'verified': True,
            'createdAt': datetime.utcnow(),
            'updatedAt': datetime.utcnow(),
            'balance': 0.0,
            'availableBalance': 0.0,
            'investments': [],
            'earnings': 0.0,
            'pending-investments': 0.0,
            'profileCompleted': False,
            'lastLogin': None,
            'referralCode': referral_code,
            'referredBy': referred_by,
            'referredUsers': [],
            'referralEarnings': 0.0
        }

        try:
            result = users.insert_one(new_user)
            if referred_by:
                referrer = users.find_one({"referralCode": referred_by})
                if referrer:
                    users.update_one(
                        {"_id": referrer["_id"]},
                        {"$push": {"referredUsers": {
                            "userId": str(result.inserted_id),
                            "email": email,
                            "firstName": first_name,
                            "investmentConfirmed": False,
                            "reward": 0.0,
                            "referredAt": datetime.utcnow()
                        }}}
                    )
        except Exception as db_error:
            logger.exception("Database error: %s", db_error)
            return jsonify({'success': False, 'error': 'Database operation failed'}), 500

        return jsonify({
            'success': True,
            'message': 'Account created successfully!',
            'user': {
                'id': str(result.inserted_id),
                'firstName': first_name,
                'lastName': last_name,
                'email': email,
                'referralCode': referral_code,
                'referredBy': referred_by
            }
        }), 201

    except Exception as e:
        logger.exception("Registration error: %s", e)
        return jsonify({'success': False, 'error': 'Server error. Try again later.'}), 500


@auth_bp.route('/verify-otp', methods=['POST'])
def verify_otp():
    try:
        data = request.get_json()
        email = data.get('email', '').lower().strip()
        otp_code = data.get('otp_code', '').strip()
        otp_id = data.get('otp_id', '').strip()

        if not all([email, otp_code, otp_id]):
            return jsonify({'success': False, 'error': 'Email, OTP code, and OTP ID are required'}), 400

        try:
            oid = ObjectId(otp_id)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid OTP ID'}), 400

        # Use find_one_and_update to atomically increment attempts when an invalid code is submitted
        otp_record = otp_codes.find_one({'_id': oid, 'email': email, 'used': False})
        if not otp_record:
            return jsonify({'success': False, 'error': 'Invalid or expired verification code'}), 400

        if datetime.utcnow() > otp_record['expires_at']:
            otp_codes.delete_one({'_id': oid})
            return jsonify({'success': False, 'error': 'Verification code expired'}), 400

        if otp_record['otp_code'] != otp_code:
            updated = otp_codes.find_one_and_update(
                {'_id': oid},
                {'$inc': {'attempts': 1}},
                return_document=ReturnDocument.AFTER
            )
            attempts_left = 5 - updated['attempts']
            if attempts_left <= 0:
                otp_codes.delete_one({'_id': oid})
                return jsonify({'success': False, 'error': 'Too many failed attempts. Request new OTP.'}), 400
            return jsonify({'success': False, 'error': f'Invalid OTP. {attempts_left} attempts left.'}), 400

        # Create user
        user_data = otp_record['user_data']
        referred_by = user_data.get('referral_code')
        referral_code = generate_referral_code()

        new_user = {
            'firstName': user_data['firstName'],
            'lastName': user_data['lastName'],
            'email': email,
            'password': user_data['password'],
            'verified': True,
            'createdAt': datetime.utcnow(),
            'updatedAt': datetime.utcnow(),
            'balance': 0.0,
            'availableBalance': 0.0,
            'investments': [],
            'earnings': 0.0,
            'pending-investments': 0.0,
            'profileCompleted': False,
            'lastLogin': None,
            'referralCode': referral_code,
            'referredBy': referred_by if referred_by else None,
            'referredUsers': [],
            'referralEarnings': 0.0
        }

        try:
            result = users.insert_one(new_user)
            # If referred, update referrer atomically
            if referred_by:
                referrer = users.find_one({"referralCode": referred_by})
                if referrer:
                    users.update_one(
                        {"_id": referrer["_id"]},
                        {"$push": {"referredUsers": {
                            "userId": str(result.inserted_id),
                            "email": email,
                            "firstName": user_data['firstName'],
                            "investmentConfirmed": False,
                            "reward": 0.0,
                            "referredAt": datetime.utcnow()
                        }}}
                    )
        except Exception as db_error:
            logger.exception("Database error: %s", db_error)
            return jsonify({'success': False, 'error': 'Database operation failed'}), 500

        otp_codes.update_one({'_id': oid}, {'$set': {'used': True}})
        otp_codes.delete_many({'email': email, 'used': True})

        return jsonify({
            'success': True,
            'message': 'Account created successfully!',
            'user': {
                'id': str(result.inserted_id),
                'firstName': user_data['firstName'],
                'lastName': user_data['lastName'],
                'email': email,
                'referralCode': referral_code,
                'referredBy': referred_by
            }
        }), 201

    except Exception as e:
        logger.exception("OTP verification error: %s", e)
        return jsonify({'success': False, 'error': 'Server error. Try again later.'}), 500


@auth_bp.route('/resend-otp', methods=['POST'])
def resend_otp():
    try:
        data = request.get_json()
        email = data.get('email', '').lower().strip()
        otp_id = data.get('otp_id', '').strip()

        if not all([email, otp_id]):
            return jsonify({'success': False, 'error': 'Email and OTP ID are required'}), 400

        try:
            oid = ObjectId(otp_id)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid OTP ID'}), 400

        otp_record = otp_codes.find_one({'_id': oid, 'email': email, 'used': False})
        if not otp_record:
            return jsonify({'success': False, 'error': 'Invalid OTP session'}), 400

        new_otp = generate_otp()
        expires_at = datetime.utcnow() + timedelta(minutes=10)

        otp_codes.update_one({'_id': oid}, {'$set': {'otp_code': new_otp, 'expires_at': expires_at, 'attempts': 0}})

        first_name = otp_record['user_data']['firstName']
        email_sent = send_otp_email(email, new_otp, first_name, "verification")

        if not email_sent:
            return jsonify({'success': False, 'error': 'Failed to resend verification code'}), 500

        return jsonify({'success': True, 'message': 'New verification code sent', 'otp_id': otp_id}), 200

    except Exception as e:
        logger.exception("Resend OTP error: %s", e)
        return jsonify({'success': False, 'error': 'Failed to resend code'}), 500


@auth_bp.route('/login', methods=['POST'])
def login():
    try:
        data = request.get_json()
        email = data.get('email', '').lower()
        password = data.get('password')

        if not all([email, password]):
            return jsonify({'error': 'Email and password are required'}), 400

        user = users.find_one({'email': email})
        if not user or not check_password_hash(user['password'], password):
            return jsonify({'error': 'Invalid email or password'}), 401

        if not user.get('verified', False):
            return jsonify({'error': 'Please verify your email before logging in.'}), 403

        users.update_one({'email': email}, {'$set': {'lastLogin': datetime.utcnow()}})

        payload = {
            'user_id': str(user['_id']),
            # Keep user sessions across browser restarts; manual logout clears the client token.
            'exp': datetime.utcnow() + timedelta(days=30)
        }
        token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

        return jsonify({
            'message': 'Login successful',
            'token': token,
            'user': {
                'id': str(user['_id']),
                'firstName': user['firstName'],
                'lastName': user['lastName'],
                'email': user['email']
            }
        }), 200

    except Exception as e:
        logger.exception("Login error: %s", e)
        return jsonify({'success': False, 'error': 'Server error. Try again later.'}), 500


# --- Password Reset Endpoints ---

@auth_bp.route('/forgot-password', methods=['POST'])
def forgot_password():
    try:
        data = request.get_json()
        email = data.get('email', '').lower().strip()

        if not email:
            return jsonify({'success': False, 'error': 'Email is required'}), 400

        user = users.find_one({'email': email})
        # Silent success to avoid user enumeration
        if not user:
            return jsonify({'success': True, 'message': 'If the email exists, a reset code has been sent'}), 200

        otp_code = generate_otp()
        expires_at = datetime.utcnow() + timedelta(minutes=10)
        reset_otp_data = {
            'email': email,
            'otp_code': otp_code,
            'expires_at': expires_at,
            'attempts': 0,
            'used': False,
            'createdAt': datetime.utcnow()
        }

        password_reset_otps.delete_many({'email': email})
        otp_result = password_reset_otps.insert_one(reset_otp_data)

        send_otp_email(email, otp_code, user['firstName'], "password_reset")

        return jsonify({'success': True, 'message': 'Password reset code sent to your email', 'email': email, 'reset_otp_id': str(otp_result.inserted_id)}), 200

    except Exception as e:
        logger.exception("Forgot password error: %s", e)
        return jsonify({'success': False, 'error': 'Failed to process request'}), 500


@auth_bp.route('/verify-reset-otp', methods=['POST'])
def verify_reset_otp():
    try:
        data = request.get_json()
        email = data.get('email', '').lower().strip()
        otp_code = data.get('otp_code', '').strip()
        reset_otp_id = data.get('reset_otp_id', '').strip()

        if not all([email, otp_code, reset_otp_id]):
            return jsonify({'success': False, 'error': 'Email, OTP code, and reset OTP ID are required'}), 400

        try:
            oid = ObjectId(reset_otp_id)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid reset OTP ID'}), 400

        otp_record = password_reset_otps.find_one({'_id': oid, 'email': email, 'used': False})
        if not otp_record:
            return jsonify({'success': False, 'error': 'Invalid or expired reset code'}), 400

        if datetime.utcnow() > otp_record['expires_at']:
            password_reset_otps.delete_one({'_id': oid})
            return jsonify({'success': False, 'error': 'Reset code expired'}), 400

        if otp_record['otp_code'] != otp_code:
            updated = password_reset_otps.find_one_and_update({'_id': oid}, {'$inc': {'attempts': 1}}, return_document=ReturnDocument.AFTER)
            attempts_left = 5 - updated['attempts']
            if attempts_left <= 0:
                password_reset_otps.delete_one({'_id': oid})
                return jsonify({'success': False, 'error': 'Too many failed attempts. Request new code.'}), 400
            return jsonify({'success': False, 'error': f'Invalid code. {attempts_left} attempts left.'}), 400

        password_reset_otps.update_one({'_id': oid}, {'$set': {'used': True}})

        return jsonify({'success': True, 'message': 'Reset code verified successfully', 'reset_token': str(oid)}), 200

    except Exception as e:
        logger.exception("Verify reset OTP error: %s", e)
        return jsonify({'success': False, 'error': 'Failed to verify reset code'}), 500


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    try:
        data = request.get_json()
        email = data.get('email', '').lower().strip()
        reset_token = data.get('reset_token', '').strip()
        new_password = data.get('new_password', '').strip()

        if not all([email, reset_token, new_password]):
            return jsonify({'success': False, 'error': 'Email, reset token, and new password are required'}), 400

        if len(new_password) < 8:
            return jsonify({'success': False, 'error': 'Password must be at least 8 characters'}), 400
        if not any(c.isdigit() for c in new_password):
            return jsonify({'success': False, 'error': 'Password must contain a number'}), 400
        if not any(c.isupper() for c in new_password):
            return jsonify({'success': False, 'error': 'Password must contain an uppercase letter'}), 400
        if not any(c.islower() for c in new_password):
            return jsonify({'success': False, 'error': 'Password must contain a lowercase letter'}), 400

        try:
            oid = ObjectId(reset_token)
        except Exception:
            return jsonify({'success': False, 'error': 'Invalid reset token'}), 400

        otp_record = password_reset_otps.find_one({'_id': oid, 'email': email, 'used': True})
        if not otp_record:
            return jsonify({'success': False, 'error': 'Invalid or expired reset session'}), 400

        hashed_password = generate_password_hash(new_password)
        users.update_one({'email': email}, {'$set': {'password': hashed_password, 'updatedAt': datetime.utcnow()}})

        password_reset_otps.delete_one({'_id': oid})

        return jsonify({'success': True, 'message': 'Password reset successfully'}), 200

    except Exception as e:
        logger.exception("Reset password error: %s", e)
        return jsonify({'success': False, 'error': 'Failed to reset password'}), 500
