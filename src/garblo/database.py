from __future__ import annotations

from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database

from garblo.config import get_settings


def ensure_indexes(database: Database) -> None:
    database.users.create_index("id", unique=True)
    database.users.create_index("email", unique=True)
    database.consent_records.create_index([("user_id", ASCENDING), ("purpose", ASCENDING), ("granted_at", DESCENDING)])
    database.media_assets.create_index("id", unique=True)
    database.media_assets.create_index("object_key", unique=True)
    database.media_assets.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    database.media_assets.create_index([("upload_id", ASCENDING), ("created_at", ASCENDING)])
    database.garments.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    database.garments.create_index([("user_id", ASCENDING), ("catalog_status", ASCENDING)])
    database.garments.create_index([("ingestion_job_id", ASCENDING), ("detection_index", ASCENDING)], unique=True, sparse=True)
    database.upload_batches.create_index("id", unique=True)
    database.upload_batches.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    database.wardrobe_searches.create_index("id", unique=True)
    database.wardrobe_searches.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    database.lookbooks.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    database.looks.create_index([("lookbook_id", ASCENDING), ("created_at", DESCENDING)])
    database.look_items.create_index([("look_id", ASCENDING), ("garment_id", ASCENDING)], unique=True)
    database.ai_jobs.create_index([("status", ASCENDING), ("created_at", ASCENDING)])
    database.ai_jobs.create_index([("user_id", ASCENDING), ("id", ASCENDING)])
    database.approval_requests.create_index([("user_id", ASCENDING), ("requested_at", DESCENDING)])
    database.approval_requests.create_index([("user_id", ASCENDING), ("status", ASCENDING)])


@lru_cache
def get_mongo_client() -> MongoClient:
    settings = get_settings()
    return MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms)


@lru_cache
def get_database() -> Database:
    settings = get_settings()
    database = get_mongo_client()[settings.mongodb_database]
    ensure_indexes(database)
    return database


def get_db() -> Database:
    return get_database()
