import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")

    MONGO_URI = os.getenv("MONGO_URI")
    DB_NAME = os.getenv("DB_NAME")

    PORT = int(
        os.getenv(
            "NOTIFICATION_SERVICE_PORT",
            "5003",
        )
    )

    DEBUG = os.getenv(
        "FLASK_DEBUG",
        "False",
    ).lower() in {
        "true",
        "1",
        "yes",
        "on",
    }

    CORS_ORIGINS = [
        origin.strip().rstrip("/")
        for origin in os.getenv(
            "CORS_ORIGINS",
            "",
        ).split(",")
        if origin.strip()
    ]