import os

from dotenv import load_dotenv


load_dotenv()


class Config:

    PORT = int(
        os.getenv(
            "MESSAGING_SERVICE_PORT",
            5006,
        )
    )

    DEBUG = os.getenv(
        "DEBUG",
        "True",
    ) == "True"

    MONGO_URI = os.getenv(
        "MONGO_URI"
    )

    MONGO_DB = os.getenv(
        "MONGO_DB"
    )

    CORS_ORIGINS = os.getenv(
        "CORS_ORIGINS",
        "*",
    )