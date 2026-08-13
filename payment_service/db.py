import certifi

from pymongo import MongoClient

from config import Config


if not Config.MONGO_URI:
    raise RuntimeError("MONGO_URI is required")

client = MongoClient(
    Config.MONGO_URI,
    tlsCAFile=certifi.where(),
    maxIdleTimeMS=45000,
    socketTimeoutMS=30000,
    connectTimeoutMS=20000,
    retryWrites=True,
    retryReads=True,
)

db = client[Config.DB_NAME]