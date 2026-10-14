from flask import Flask, jsonify
from flask_cors import CORS
from auth import auth_bp
from dashboard_bp import dashboard_bp
from investment import investment_bp
from withdrawal import withdrawal_bp
from admin_summary_routes import admin_summary_bp
from admin_auth import admin_auth_bp
from admin_users import admin_users_bp

from profile_bp import profile_bp
from referral_routes import referral_bp
from wallet_addresses import wallet_addresses_bp
from notifications import notifications_bp, admin_notifications_bp, admin_audit_bp
from telegram_notifications import telegram_bp, admin_telegram_bp
from support import support_bp, admin_support_bp
import os

app = Flask(__name__)
cors_origins = {
    "http://127.0.0.1:5500",
    "http://127.0.0.1:8001",
    "http://localhost:8001",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}
cors_origins.update(
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
)
if "*" in cors_origins:
    raise RuntimeError("CORS_ALLOWED_ORIGINS must contain exact origins, not a wildcard")
CORS(
    app,
    resources={
        r"/api/*": {
            "origins": sorted(cors_origins),
            "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "Authorization"],
            "supports_credentials": True
        }
    }
)

# Register blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(investment_bp)
app.register_blueprint(withdrawal_bp)
app.register_blueprint(admin_summary_bp)
app.register_blueprint(admin_auth_bp)
app.register_blueprint(referral_bp)
app.register_blueprint(profile_bp)
app.register_blueprint(admin_users_bp)
app.register_blueprint(wallet_addresses_bp)
app.register_blueprint(notifications_bp)
app.register_blueprint(admin_notifications_bp)
app.register_blueprint(admin_audit_bp)
app.register_blueprint(telegram_bp)
app.register_blueprint(admin_telegram_bp)
app.register_blueprint(support_bp)
app.register_blueprint(admin_support_bp)

# ✅ Add root route
@app.route('/')
def home():
    return jsonify({
        "message": "Welcome to the Capitello API 🚀",
        "status": "running",
    })

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '').strip().lower() in {'1', 'true', 'yes'}
    app.run(host='0.0.0.0', port=port, debug=debug)
