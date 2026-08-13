from flask import Flask, jsonify

from config import Config
from db import client, db
from extensions import bcrypt, cors, limiter
from routes import admin_api, create_initial_admin


def create_app():
    app = Flask(__name__)

    app.config.from_object(Config)

    if not app.config["JWT_SECRET_KEY"]:
        raise RuntimeError(
            "JWT_SECRET_KEY is required"
        )

    bcrypt.init_app(app)

    cors.init_app(
        app,
        resources={
            r"/api/*": {
                "origins": [
                    "http://127.0.0.1:5500",
                    "http://localhost:5500",
                    "http://127.0.0.1:5000",
                    "http://localhost:5000",
                }
            }
        },
        supports_credentials=True,
        allow_headers=[
            "Content-Type",
            "Authorization",
        ],
        methods=[
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "OPTIONS",
        ],
    )

    limiter.init_app(app)

    app.db = db
    app.mongo_client = client

    with app.app_context():
        create_initial_admin()

    app.register_blueprint(
        admin_api,
        url_prefix="/api/admin",
    )

    @app.route(
        "/api/health",
        methods=["GET"],
    )
    def health():
        try:
            app.mongo_client.admin.command(
                "ping"
            )

            return jsonify({
                "status": "online",
                "service": "admin_service",
                "database": "connected",
            }), 200

        except Exception:
            return jsonify({
                "status": "error",
                "service": "admin_service",
                "database": "disconnected",
            }), 503

    return app


app = create_app()



if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=Config.PORT,
        debug=Config.DEBUG,
    )