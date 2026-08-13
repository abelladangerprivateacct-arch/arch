import os

from flask import Flask, jsonify

from config import Config
from db import client, db
from extensions import cors, limiter
from routes import payment_api


def create_app():
    app = Flask(__name__)

    app.config.from_object(Config)

    if not app.config["SECRET_KEY"]:
        raise RuntimeError(
            "SECRET_KEY is required"
        )

    if not app.config["JWT_SECRET_KEY"]:
        raise RuntimeError(
            "JWT_SECRET_KEY is required"
        )

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

    app.register_blueprint(
        payment_api,
        url_prefix="/api/payment",
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
                "service": "payment_service",
                "database": "connected",
            }), 200

        except Exception:
            return jsonify({
                "status": "error",
                "service": "payment_service",
                "database": "disconnected",
            }), 503

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                Config.PORT,
            )
        ),
        debug=Config.DEBUG,
    )