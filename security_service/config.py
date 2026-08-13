import os

from dotenv import load_dotenv


load_dotenv()


class Config:

    PORT = int(
        os.getenv(
            "PORT",
            5007,
        )
    )

    DEBUG = os.getenv(
        "DEBUG",
        "true",
    ).lower() == "true"

    MONGO_URI = os.getenv(
        "MONGO_URI"
    )

    MONGO_DB_NAME = os.getenv(
        "MONGO_DB_NAME"
    )

    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS",
        "*",
    )