from pymongo import MongoClient

from config import Config


client = MongoClient(
    Config.MONGO_URI,
    tls=True,
    serverSelectionTimeoutMS=10000,
)

db = client[Config.DB_NAME]