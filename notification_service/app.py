from flask import Flask
from config import Config
from extensions import cors, init_mongo
from routes import notification_api


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    cors.init_app(
        app,
        origins=app.config["CORS_ORIGINS"],
    )

    init_mongo(app)

    app.register_blueprint(
        notification_api,
        url_prefix="/api/notification",
    )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=app.config["PORT"],
        debug=app.config["DEBUG"],
    )