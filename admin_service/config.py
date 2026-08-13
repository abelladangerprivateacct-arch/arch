import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")

    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    JWT_ACCESS_TOKEN_EXPIRES_HOURS = int(
        os.getenv(
            "JWT_ACCESS_TOKEN_EXPIRES_HOURS",
            "24",
        )
    )

    MONGO_URI = os.getenv("MONGO_URI")

    DB_NAME = os.getenv(
        "DB_NAME",
        "cashvm",
    )

    CORS_ORIGINS = [
        origin.strip().rstrip("/")
        for origin in os.getenv(
            "CORS_ORIGINS",
            "",
        ).split(",")
        if origin.strip()
    ]

    PORT = int(
        os.getenv(
            "ADMIN_SERVICE_PORT",
            "5001",
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

    RATELIMIT_STORAGE_URI = os.getenv(
        "RATELIMIT_STORAGE_URI"
    )

    INITIAL_ADMIN_FULL_NAME = os.getenv(
        "INITIAL_ADMIN_FULL_NAME",
        "",
    ).strip()

    INITIAL_ADMIN_EMAIL = os.getenv(
        "INITIAL_ADMIN_EMAIL",
        "",
    ).strip().lower()

    INITIAL_ADMIN_PHONE = os.getenv(
        "INITIAL_ADMIN_PHONE",
        "",
    ).strip()

    INITIAL_ADMIN_PASSWORD = os.getenv(
        "INITIAL_ADMIN_PASSWORD",
        "",
    )