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
    DB_NAME = os.getenv("DB_NAME")

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
            "PAYMENT_SERVICE_PORT",
            "5002",
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

    CURRENCY = os.getenv(
        "CURRENCY"
    )

    MINIMUM_DEPOSIT = float(
    os.getenv(
        "MINIMUM_DEPOSIT"
        
    )
)

    MAXIMUM_DEPOSIT = float(
    os.getenv(
        "MAXIMUM_DEPOSIT"
        
    )
)

    MINIMUM_WITHDRAWAL = float(
    os.getenv(
        "MINIMUM_WITHDRAWAL"
        
    )
)

    MAXIMUM_WITHDRAWAL = float(
    os.getenv(
        "MAXIMUM_WITHDRAWAL"
        
    )
)

    WELCOME_AMOUNT = float(
    os.getenv(
        "WELCOME_AMOUNT"
       
    )
)