import os
from motor.motor_asyncio import AsyncIOMotorClient

_client = None
_db = None


def get_db():
    global _client, _db
    if _db is None:
        mongo_url = os.environ["MONGO_URL"]
        _client = AsyncIOMotorClient(mongo_url)
        _db = _client[os.environ["DB_NAME"]]
    return _db


def close_db():
    global _client
    if _client is not None:
        _client.close()
