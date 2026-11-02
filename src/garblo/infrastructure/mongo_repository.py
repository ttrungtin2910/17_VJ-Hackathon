from __future__ import annotations

from datetime import datetime
from typing import Any

from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError


class MongoWardrobeRepository:
    """Persistence adapter used by application services and workers."""

    def __init__(self, database: Database):
        self.db = database

    def get_owned_asset(self, user_id: str, asset_id: str) -> dict[str, Any] | None:
        return self.db.media_assets.find_one({"id": asset_id, "user_id": user_id})

    def get_asset(self, user_id: str, asset_id: str) -> dict[str, Any] | None:
        return self.get_owned_asset(user_id, asset_id)

    def create_asset(self, asset: dict[str, Any]) -> dict[str, Any]:
        try:
            self.db.media_assets.insert_one(asset)
            return asset
        except DuplicateKeyError:
            existing = self.db.media_assets.find_one({"object_key": asset["object_key"]})
            if not existing:
                raise
            return existing

    def update_asset(self, asset_id: str, user_id: str, updates: dict[str, Any]) -> None:
        self.db.media_assets.update_one({"id": asset_id, "user_id": user_id}, {"$set": updates})

    def get_ingested_garment(self, job_id: str, detection_index: int) -> dict[str, Any] | None:
        return self.db.garments.find_one({"ingestion_job_id": job_id, "detection_index": detection_index})

    def create_garment(self, garment: dict[str, Any]) -> dict[str, Any]:
        try:
            self.db.garments.insert_one(garment)
            return garment
        except DuplicateKeyError:
            existing = self.get_ingested_garment(garment["ingestion_job_id"], garment["detection_index"])
            if not existing:
                raise
            return existing

    def update_garment(self, garment_id: str, user_id: str, updates: dict[str, Any]) -> None:
        self.db.garments.update_one({"id": garment_id, "user_id": user_id}, {"$set": updates})

    def garments_for_user(self, user_id: str) -> list[dict[str, Any]]:
        return list(self.db.garments.find({"user_id": user_id}).sort("created_at", DESCENDING))

    def garments_by_ids(self, user_id: str, garment_ids: list[str]) -> list[dict[str, Any]]:
        return list(self.db.garments.find({"user_id": user_id, "id": {"$in": garment_ids}}))

    def create_upload_batch(self, batch: dict[str, Any]) -> None:
        self.db.upload_batches.insert_one(batch)

    def get_upload_batch(self, user_id: str, upload_id: str) -> dict[str, Any] | None:
        return self.db.upload_batches.find_one({"id": upload_id, "user_id": user_id})

    def update_upload_batch(self, upload_id: str, user_id: str, updates: dict[str, Any]) -> None:
        self.db.upload_batches.update_one({"id": upload_id, "user_id": user_id}, {"$set": updates})

    def create_search(self, search: dict[str, Any]) -> None:
        self.db.wardrobe_searches.insert_one(search)

    def get_search(self, user_id: str, search_id: str) -> dict[str, Any] | None:
        return self.db.wardrobe_searches.find_one({"id": search_id, "user_id": user_id})

    def update_search(self, user_id: str, search_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        return self.db.wardrobe_searches.find_one_and_update(
            {"id": search_id, "user_id": user_id}, {"$set": updates}, return_document=ReturnDocument.AFTER
        )

    def owned_lookbook(self, user_id: str, lookbook_id: str) -> dict[str, Any] | None:
        return self.db.lookbooks.find_one({"id": lookbook_id, "user_id": user_id})

    def owned_look(self, user_id: str, look_id: str) -> dict[str, Any] | None:
        look = self.db.looks.find_one({"id": look_id})
        if not look:
            return None
        return look if self.owned_lookbook(user_id, look["lookbook_id"]) else None

    def update_job(self, job_id: str, updates: dict[str, Any]) -> None:
        self.db.ai_jobs.update_one({"id": job_id}, {"$set": updates})

    @staticmethod
    def updated(now: datetime) -> dict[str, Any]:
        return {"updated_at": now}
