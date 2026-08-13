from flask_cors import CORS
from pymongo import MongoClient

cors = CORS()

mongo_client = None


def init_mongo(app):
    global mongo_client

    mongo_client = MongoClient(
        app.config["MONGO_URI"]
    )

    app.mongo_client = mongo_client
    app.db = mongo_client[
        app.config["DB_NAME"]
    ]