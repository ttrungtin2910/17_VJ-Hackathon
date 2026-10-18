from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from garblo.ai import AIProviderError, AzureLookbookProvider
from garblo.config import get_settings
from garblo.database import SessionLocal
from garblo.models import ApprovalRequest, AiJob, Garment, Look, LookItem, Lookbook, MediaAsset
from garblo.storage import ObjectStorage


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobWorker:
    def __init__(self, db: Session, storage: ObjectStorage, provider: AzureLookbookProvider):
        self.db = db
        self.storage = storage
        self.provider = provider

    def claim_next(self) -> AiJob | None:
        statement = (
            select(AiJob)
            .where(AiJob.status == "queued")
            .order_by(AiJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = self.db.execute(statement).scalar_one_or_none()
        if not job:
            return None
        job.status = "running"
        job.attempts += 1
        job.started_at = utcnow()
        job.locked_at = utcnow()
        self.db.commit()
        self.db.refresh(job)
        return job

    def process_next(self) -> bool:
        job = self.claim_next()
        if not job:
            return False
        try:
            if job.job_type == "catalogue":
                output = self._catalogue(job)
            elif job.job_type in {"lookbook", "variation"}:
                output = self._render_look(job)
            else:
                raise AIProviderError(f"Unsupported job type: {job.job_type}")
            job.output_ref = output
            job.status = "succeeded"
            job.error_code = None
            job.error_message = None
        except AIProviderError as exc:
            logger.warning("AI job %s failed: %s", job.id, exc)
            job.status = "failed"
            job.error_code = "provider_error"
            job.error_message = str(exc)
        except Exception:
            logger.exception("AI job %s failed unexpectedly", job.id)
            job.status = "failed"
            job.error_code = "internal_error"
            job.error_message = "The AI job could not be completed"
        finally:
            job.completed_at = utcnow()
            job.locked_at = None
            self.db.commit()
        return True

    def _catalogue(self, job: AiJob) -> dict[str, str]:
        asset_id = str(job.input_ref.get("asset_id", ""))
        asset = self.db.scalar(
            select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.user_id == job.user_id)
        )
        if not asset:
            raise AIProviderError("Source asset no longer exists")
        analysis = self.provider.analyse_garment(self.storage.get_bytes(asset.object_key), asset.mime_type)
        tags = analysis.get("tags") if isinstance(analysis.get("tags"), list) else []
        garment = Garment(
            user_id=job.user_id,
            source_asset_id=asset.id,
            category=self._text_or_none(analysis.get("category")),
            color=self._text_or_none(analysis.get("color")),
            material=self._text_or_none(analysis.get("material")),
            season=self._text_or_none(analysis.get("season")),
            tags=[str(tag)[:80] for tag in tags[:20]],
        )
        asset.status = "catalogued"
        asset.metadata_json = {**asset.metadata_json, "catalogue": analysis}
        self.db.add(garment)
        self.db.flush()
        return {"garment_id": garment.id, "asset_id": asset.id}

    def _render_look(self, job: AiJob) -> dict[str, str]:
        lookbook_id = str(job.input_ref.get("lookbook_id", ""))
        garment_ids = [str(value) for value in job.input_ref.get("garment_ids", [])]
        lookbook = self.db.scalar(
            select(Lookbook).where(Lookbook.id == lookbook_id, Lookbook.user_id == job.user_id)
        )
        garments = list(
            self.db.scalars(
                select(Garment).where(Garment.user_id == job.user_id, Garment.id.in_(garment_ids))
            )
        )
        if not lookbook or len(garments) != len(set(garment_ids)):
            raise AIProviderError("Lookbook or selected garments no longer exist")
        rendered = self.provider.render_look(
            garments=[
                {
                    "id": garment.id,
                    "category": garment.category,
                    "color": garment.color,
                    "material": garment.material,
                    "season": garment.season,
                    "tags": garment.tags,
                }
                for garment in garments
            ],
            theme=lookbook.theme,
            occasion=lookbook.occasion,
            instruction=self._text_or_none(job.input_ref.get("instruction")),
        )
        generated_asset = MediaAsset(
            user_id=job.user_id,
            object_key=f"users/{job.user_id}/generated/{job.id}.png",
            asset_type="generated_look",
            mime_type=rendered.mime_type,
            checksum="pending",
            byte_size=len(rendered.image_bytes),
            metadata_json={"job_id": job.id},
            status="ready",
        )
        import hashlib

        generated_asset.checksum = hashlib.sha256(rendered.image_bytes).hexdigest()
        self.storage.put_bytes(generated_asset.object_key, rendered.image_bytes, rendered.mime_type)
        self.db.add(generated_asset)
        self.db.flush()
        look = Look(
            lookbook_id=lookbook.id,
            generated_asset_id=generated_asset.id,
            generation_prompt=rendered.prompt,
            model_reference=rendered.model_reference,
            status="preview",
        )
        self.db.add(look)
        self.db.flush()
        for index, garment in enumerate(garments):
            self.db.add(LookItem(look_id=look.id, garment_id=garment.id, layer_order=index))
        approval = ApprovalRequest(
            user_id=job.user_id,
            subject_type="look",
            subject_id=look.id,
            action="save_look",
            status="pending",
        )
        lookbook.status = "pending_approval"
        self.db.add(approval)
        self.db.flush()
        return {"look_id": look.id, "generated_asset_id": generated_asset.id, "approval_id": approval.id}

    @staticmethod
    def _text_or_none(value: object) -> str | None:
        return value.strip()[:120] if isinstance(value, str) and value.strip() else None


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    storage = ObjectStorage(settings)
    provider = AzureLookbookProvider(settings)
    while True:
        with SessionLocal() as db:
            processed = JobWorker(db, storage, provider).process_next()
        if not processed:
            time.sleep(2)


if __name__ == "__main__":
    run()

