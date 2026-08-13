import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv(
        "SECRET_KEY",
        "change-this-development-secret",
    )

    JWT_SECRET_KEY = os.getenv(
        "JWT_SECRET_KEY",
        "change-this-development-jwt-secret",
    )

    JWT_ACCESS_TOKEN_EXPIRES_HOURS = int(
        os.getenv(
            "JWT_ACCESS_TOKEN_EXPIRES_HOURS",
            "24",
        )
    )

    MONGO_URI = os.getenv(
        "MONGO_URI",
        "mongodb://localhost:27017",
    )

    DB_NAME = os.getenv(
        "DB_NAME",
        "cashvm",
    )
     
    CORS_ORIGINS = [
        origin.strip().rstrip("/")
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://127.0.0.1:5500,http://localhost:5500,https://wiune.surge.sh",
        ).split(",")
        if origin.strip()
    ]

    PORT = int(
        os.getenv(
            "CORE_SERVICE_PORT",
            "5000",
        )
    )

    DEBUG = os.getenv(
        "FLASK_DEBUG",
        "True",
    ).lower() in {
        "true",
        "1",
        "yes",
        "on",
    }
    
    RATELIMIT_STORAGE_URI = os.getenv(
    "RATELIMIT_STORAGE_URI",
    "redis://127.0.0.1:6379/0",
)
    PASSWORD_MIN_LENGTH = int(
        os.getenv(
            "PASSWORD_MIN_LENGTH",
            "12",
        )
    )

    LOGIN_MAX_ATTEMPTS = int(
        os.getenv(
            "LOGIN_MAX_ATTEMPTS",
            "5",
        )
    )

    LOGIN_LOCKOUT_MINUTES = int(
        os.getenv(
            "LOGIN_LOCKOUT_MINUTES",
            "15",
        )
    )
    WITHDRAWAL_PIN_LENGTH = int(
    os.getenv(
        "WITHDRAWAL_PIN_LENGTH",
        "6",
    )
)