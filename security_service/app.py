from flask import Flask, jsonify

from config import Config
from db import client, db
from extensions import cors
from routes import security_api


def create_app():

    app = Flask(__name__)

    app.config.from_object(Config)

    cors.init_app(
        app,
        resources={
            r"/api/*": {
                "origins": app.config[
                    "CORS_ORIGINS"
                ],
            }
        },
    )

    app.db = db
    app.mongo_client = client

    app.register_blueprint(
        security_api,
        url_prefix="/api/security",
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
                "service": "security_service",
                "database": "connected",
            }), 200

        except Exception:

            return jsonify({
                "status": "error",
                "service": "security_service",
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

    