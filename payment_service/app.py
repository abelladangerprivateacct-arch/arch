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

    cors.init_app(
        app,
        resources={
            r"/api/*": {
                "origins": app.config[
                    "CORS_ORIGINS"
                ],
            }
        },
        supports_credentials=True,
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
        host="127.0.0.1",
        port=Config.PORT,
        debug=Config.DEBUG,
    )