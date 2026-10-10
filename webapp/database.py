"""Official PyMongo connection and security/identity indexes."""

from pymongo import ASCENDING, MongoClient
from pymongo.database import Database

from webapp.config import Settings


def connect(settings: Settings) -> tuple[MongoClient, Database]:
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    client.admin.command("ping")
    db = client[settings.mongo_database]
    create_indexes(db)
    return client, db


def create_indexes(db: Database) -> None:
    db.users.create_index("email", unique=True, sparse=True)
    # A unique multikey index enforces wallet ownership across account documents.
    # Empty arrays are excluded, allowing password-only accounts.
    db.users.create_index(
        "wallets", unique=True, partialFilterExpression={"wallets": {"$type": "string"}}
    )
    for collection in [
        "sessions",
        "challenges",
        "reset_tokens",
        "throttles",
        "index_leases",
        "mfa_setups",
    ]:
        db[collection].create_index("expires_at", expireAfterSeconds=0)
    db.sessions.create_index("user_id")
    db.reset_tokens.create_index("user_id")
    db.audit.create_index([("user_id", ASCENDING), ("created_at", ASCENDING)])
    db.chain_events.create_index([("deployment", ASCENDING), ("block_number", ASCENDING)])
    db.chain_events.create_index([("deployment", ASCENDING), ("owner", ASCENDING)])
    db.notes.create_index([("user_id", ASCENDING), ("record", ASCENDING)], unique=True)
