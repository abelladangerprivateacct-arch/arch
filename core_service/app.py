import os

from flask import Flask, jsonify

from config import Config
from db import client, db
from extensions import bcrypt, cors, limiter
from auth.routes import auth_api


def create_app():
    app = Flask(__name__)

    app.config.from_object(Config)

    bcrypt.init_app(app)

    cors_origins = app.config.get("CORS_ORIGINS")

    if not cors_origins:
        cors_origins = [
            "https://wiune.surge.sh",
        ]

    if isinstance(cors_origins, str):
        cors_origins = [
            origin.strip()
            for origin in cors_origins.split(",")
            if origin.strip()
        ]

    cors.init_app(
        app,
        resources={
            r"/api/*": {
                "origins": cors_origins,
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

    @app.route("/api/health", methods=["GET"])
    def health():
        try:
            app.mongo_client.admin.command("ping")

            return jsonify({
                "status": "online",
                "service": "core_service",
                "database": "connected",
            }), 200

        except Exception:
            return jsonify({
                "status": "error",
                "service": "core_service",
                "database": "disconnected",
            }), 503

    app.register_blueprint(
        auth_api,
        url_prefix="/api/auth",
    )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", Config.PORT)),
        debug=Config.DEBUG,
    )