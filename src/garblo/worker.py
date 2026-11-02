from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database

from garblo.application.wardrobe import ApplicationError, RecommendationService, metadata_embedding_text, normalise_metadata
from garblo.config import Settings, get_settings
from garblo.database import get_database
from garblo.domain.ports import GarmentDetector, ImageEmbedder, OpenAIWardrobePort, OutfitIntentPort, VectorStore
from garblo.infrastructure.fpt import FPTIntentProvider
from garblo.infrastructure.local_ml import GroundingDinoGarmentDetector, SigLIPImageEmbedder
from garblo.infrastructure.mongo_repository import MongoWardrobeRepository
from garblo.infrastructure.openai_provider import OpenAIWardrobeProvider
from garblo.infrastructure.qdrant import QdrantVectorStore
from garblo.models import document
from garblo.storage import ObjectStorage, crop_storage_key, generated_storage_key


logger = logging.getLogger(__name__)
INGESTION_JOB_TYPES = {"catalogue", "ingest_asset"}
RECOMMENDATION_JOB_TYPES = {"recommendation"}
RENDER_JOB_TYPES = {"lookbook", "variation", "render"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobWorker:
    """Queue worker and infrastructure composition root."""

    def __init__(
        self,
        db: Database,
        storage: ObjectStorage,
        settings: Settings,
        *,
        job_types: set[str] | None = None,
        detector: GarmentDetector | None = None,
        image_embedder: ImageEmbedder | None = None,
        openai_provider: OpenAIWardrobePort | None = None,
        intent_provider: OutfitIntentPort | None = None,
        vectors: VectorStore | None = None,
    ):
        self.db = db
        self.repository = MongoWardrobeRepository(db)
        self.storage = storage
        self.settings = settings
        self.job_types = job_types
        self._detector = detector
        self._image_embedder = image_embedder
        self._openai_provider = openai_provider
        self._intent_provider = intent_provider
        self._vectors = vectors

    def claim_next(self) -> dict[str, Any] | None:
        query: dict[str, Any] = {"status": "queued"}
        if self.job_types:
            query["job_type"] = {"$in": sorted(self.job_types)}
        now = utcnow()
        return self.db.ai_jobs.find_one_and_update(
            query,
            {"$set": {"status": "running", "started_at": now, "locked_at": now, "updated_at": now}, "$inc": {"attempts": 1}},
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )

    def process_next(self) -> bool:
        job = self.claim_next()
        if not job:
            return False
        try:
            if job["job_type"] in INGESTION_JOB_TYPES:
                output = self._catalogue(job)
            elif job["job_type"] in RECOMMENDATION_JOB_TYPES:
                output = self._recommend(job)
            elif job["job_type"] in RENDER_JOB_TYPES:
                output = self._render_look(job)
            else:
                raise ApplicationError(f"Unsupported job type: {job['job_type']}")
            updates = {"output_ref": output, "status": "succeeded", "error_code": None, "error_message": None}
        except ApplicationError as exc:
            logger.warning("AI job %s failed: %s", job["id"], exc)
            updates = {"status": "failed", "error_code": "provider_or_model_error", "error_message": str(exc)}
            self._mark_related_failure(job, str(exc))
        except Exception:
            logger.exception("AI job %s failed unexpectedly", job["id"])
            updates = {"status": "failed", "error_code": "internal_error", "error_message": "The AI job could not be completed"}
            self._mark_related_failure(job, "The AI job could not be completed")
        now = utcnow()
        updates.update({"completed_at": now, "locked_at": None, "updated_at": now})
        self.repository.update_job(job["id"], updates)
        self._refresh_upload_batch(job, now)
        return True

    def _catalogue(self, job: dict[str, Any]) -> dict[str, Any]:
        asset_id = str(job["input_ref"].get("asset_id", ""))
        asset = self.repository.get_asset(job["user_id"], asset_id)
        if not asset:
            raise ApplicationError("Source asset no longer exists")
        detector = self._detector or GroundingDinoGarmentDetector(self.settings)
        provider = self._provider()
        image_embedder = self._image_embedder or SigLIPImageEmbedder(self.settings)
        vectors = self._vectors or QdrantVectorStore(self.settings)
        detections = detector.detect(self.storage.get_bytes(asset["object_key"]), asset["mime_type"])
        garment_ids: list[str] = []
        for index, detected in enumerate(detections):
            crop_key = crop_storage_key(job["user_id"], job["id"], index)
            crop_asset = self.repository.create_asset(
                document(
                    user_id=job["user_id"], object_key=crop_key, asset_type="garment_crop", mime_type=detected.mime_type,
                    checksum=hashlib.sha256(detected.crop_bytes).hexdigest(), byte_size=len(detected.crop_bytes),
                    metadata_json={"source_asset_id": asset["id"], "detection_index": index}, status="ready",
                    parent_asset_id=asset["id"], upload_id=asset.get("upload_id"),
                )
            )
            self.storage.put_bytes(crop_key, detected.crop_bytes, detected.mime_type)
            metadata = normalise_metadata(provider.analyse_garment(detected.crop_bytes, detected.mime_type), detected.category)
            garment = self.repository.get_ingested_garment(job["id"], index)
            if not garment:
                garment = self.repository.create_garment(
                    document(
                        user_id=job["user_id"], source_asset_id=crop_asset["id"], original_asset_id=asset["id"],
                        crop_asset_id=crop_asset["id"], ingestion_job_id=job["id"], detection_index=index,
                        detection={"bbox": detected.bbox, "confidence": detected.confidence, "category": detected.category},
                        category=metadata["category"], color=metadata["color"], material=metadata["material"],
                        season=metadata["season"], tags=metadata["tags"], metadata=metadata,
                        metadata_text=metadata_embedding_text(metadata), vector_status="pending", wear_count=0,
                        last_worn_at=None, catalog_status="active",
                    )
                )
            metadata_text = garment.get("metadata_text") or metadata_embedding_text(metadata)
            vectors.upsert_garment(
                garment_id=garment["id"], user_id=job["user_id"], metadata_vector=provider.embed_text(metadata_text),
                image_vector=image_embedder.embed_image(detected.crop_bytes, detected.mime_type),
                payload={"category": garment.get("category"), "catalog_status": "active"},
            )
            self.repository.update_garment(garment["id"], job["user_id"], {"vector_status": "indexed", "updated_at": utcnow()})
            garment_ids.append(garment["id"])
        status = "catalogued" if garment_ids else "no_garments_detected"
        self.repository.update_asset(asset["id"], job["user_id"], {"status": status, "updated_at": utcnow()})
        return {"asset_id": asset["id"], "garment_ids": garment_ids, "detected_count": len(detections)}

    def _recommend(self, job: dict[str, Any]) -> dict[str, Any]:
        search_id = str(job["input_ref"].get("search_id", ""))
        search = self.repository.get_search(job["user_id"], search_id)
        if not search:
            raise ApplicationError("Wardrobe search no longer exists")
        service = RecommendationService(
            self._intent_provider or FPTIntentProvider(self.settings), self._provider(), self._vectors or QdrantVectorStore(self.settings)
        )
        result = service.recommend(
            user_id=job["user_id"], prompt=str(search["prompt"]), garments=self.repository.garments_for_user(job["user_id"])
        )
        self.repository.update_search(job["user_id"], search_id, {"status": "succeeded", "result": result, "updated_at": utcnow()})
        return {"search_id": search_id, **result}

    def _render_look(self, job: dict[str, Any]) -> dict[str, str]:
        lookbook_id = str(job["input_ref"].get("lookbook_id", ""))
        garment_ids = [str(value) for value in job["input_ref"].get("garment_ids", [])]
        lookbook = self.repository.owned_lookbook(job["user_id"], lookbook_id)
        garments = self.repository.garments_by_ids(job["user_id"], garment_ids)
        if not lookbook or len(garments) != len(set(garment_ids)):
            raise ApplicationError("Lookbook or selected garments no longer exist")
        images: list[tuple[str, bytes, str]] = []
        for garment in garments:
            asset = self.repository.get_asset(job["user_id"], garment["source_asset_id"])
            if not asset:
                raise ApplicationError("A selected garment image no longer exists")
            images.append((f"{garment['id']}.png", self.storage.get_bytes(asset["object_key"]), asset["mime_type"]))
        rendered = self._provider().render_look(
            garments=[
                {"id": garment["id"], "category": garment.get("category"), "color": garment.get("color"),
                 "material": garment.get("material"), "season": garment.get("season"), "tags": garment.get("tags", [])}
                for garment in garments
            ],
            images=images, theme=lookbook.get("theme"), occasion=lookbook.get("occasion"),
            instruction=self._text_or_none(job["input_ref"].get("instruction")),
        )
        generated_asset = document(
            user_id=job["user_id"], object_key=generated_storage_key(job["user_id"], job["id"]), asset_type="generated_look",
            mime_type=rendered.mime_type, checksum=hashlib.sha256(rendered.image_bytes).hexdigest(), byte_size=len(rendered.image_bytes),
            metadata_json={"job_id": job["id"], "synthetic_person": True}, status="ready",
        )
        self.storage.put_bytes(generated_asset["object_key"], rendered.image_bytes, rendered.mime_type)
        self.repository.create_asset(generated_asset)
        look = document(
            lookbook_id=lookbook["id"], generated_asset_id=generated_asset["id"], generation_prompt=rendered.prompt,
            model_reference=rendered.model_reference, cmax_score=None, status="preview",
        )
        self.db.looks.insert_one(look)
        for index, garment in enumerate(garments):
            self.db.look_items.insert_one(document(look_id=look["id"], garment_id=garment["id"], role=None, layer_order=index))
        approval = document(
            user_id=job["user_id"], subject_type="look", subject_id=look["id"], action="save_look", status="pending",
            decision_payload={}, requested_at=utcnow(), decided_at=None,
        )
        self.db.approval_requests.insert_one(approval)
        self.db.lookbooks.update_one({"_id": lookbook["_id"]}, {"$set": {"status": "pending_approval", "updated_at": utcnow()}})
        return {"look_id": look["id"], "generated_asset_id": generated_asset["id"], "approval_id": approval["id"]}

    def _provider(self) -> OpenAIWardrobePort:
        return self._openai_provider or OpenAIWardrobeProvider(self.settings)

    def _mark_related_failure(self, job: dict[str, Any], message: str) -> None:
        if job["job_type"] == "recommendation" and job["input_ref"].get("search_id"):
            self.repository.update_search(job["user_id"], str(job["input_ref"]["search_id"]), {"status": "failed", "error_message": message, "updated_at": utcnow()})

    def _refresh_upload_batch(self, job: dict[str, Any], now: datetime) -> None:
        upload_id = job.get("input_ref", {}).get("upload_id")
        if not upload_id:
            return
        batch = self.repository.get_upload_batch(job["user_id"], str(upload_id))
        if not batch:
            return
        jobs = list(self.db.ai_jobs.find({"id": {"$in": batch.get("job_ids", [])}}))
        succeeded = sum(item.get("status") == "succeeded" for item in jobs)
        failed = sum(item.get("status") == "failed" for item in jobs)
        terminal = succeeded + failed == len(jobs)
        status = "completed" if terminal and not failed else "completed_with_failures" if terminal else "processing"
        self.repository.update_upload_batch(str(upload_id), job["user_id"], {"status": status, "processed_count": succeeded, "failed_count": failed, "updated_at": now})

    @staticmethod
    def _text_or_none(value: object) -> str | None:
        return value.strip()[:1000] if isinstance(value, str) and value.strip() else None


def run_worker(job_types: set[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    worker = JobWorker(get_database(), ObjectStorage(settings), settings, job_types=job_types)
    while True:
        if not worker.process_next():
            time.sleep(settings.worker_poll_seconds)


def run() -> None:
    run_worker()


def run_ingestion() -> None:
    run_worker(INGESTION_JOB_TYPES)


def run_recommendation() -> None:
    run_worker(RECOMMENDATION_JOB_TYPES)


def run_render() -> None:
    run_worker(RENDER_JOB_TYPES)


if __name__ == "__main__":
    run()
