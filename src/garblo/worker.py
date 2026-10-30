from __future__ import annotations

import hashlib
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from garblo.ai import AIProviderError, RenderedLook
from garblo.ai_providers import ProviderRegistry
from garblo.ai_providers.contracts import Analysis
from garblo.config import Settings, get_settings
from garblo.consent import active_ai_consent
from garblo.database import SessionLocal
from garblo.images import sanitize_image
from garblo.jobs import ensure_job_configuration
from garblo.models import (
    AiJob,
    ApprovalRequest,
    Garment,
    GarmentCandidate,
    Look,
    Lookbook,
    LookItem,
    MediaAsset,
    new_id,
)
from garblo.presets import PRESETS
from garblo.storage import ObjectStorage

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Claim:
    id: str
    user_id: str
    job_type: str
    provider: str
    ref: dict[str, Any]
    token: str


class JobWorker:
    def __init__(
        self,
        db: Session,
        storage: ObjectStorage,
        providers: ProviderRegistry | None = None,
        settings: Settings | None = None,
    ):
        self.db, self.storage = db, storage
        self.settings = settings or get_settings()
        self.providers = providers or ProviderRegistry(self.settings)

    def _sync_subject(self, job: AiJob, failed: bool) -> None:
        if job.job_type == "catalogue":
            asset = self.db.scalar(
                select(MediaAsset)
                .where(
                    MediaAsset.id == job.input_ref.get("asset_id"),
                    MediaAsset.user_id == job.user_id,
                )
                .with_for_update()
            )
            if asset:
                asset.status = "analysis_failed" if failed else "analyzing"
        elif job.job_type in {"lookbook", "variation"}:
            book = self.db.scalar(
                select(Lookbook)
                .where(
                    Lookbook.id == job.input_ref.get("lookbook_id"), Lookbook.user_id == job.user_id
                )
                .with_for_update()
            )
            if book:
                book.status = "failed" if failed else "generating"

    def recover_expired(self) -> int:
        expired = list(
            self.db.scalars(
                select(AiJob)
                .where(
                    AiJob.status == "running",
                    or_(
                        AiJob.locked_at.is_(None),
                        AiJob.locked_at
                        < utcnow() - timedelta(seconds=self.settings.worker_lease_seconds),
                    ),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in expired:
            failed = job.attempts >= self.settings.worker_max_attempts
            job.status = "failed" if failed else "queued"
            job.lock_token = job.locked_at = None
            job.next_attempt_at = None
            job.completed_at = utcnow() if failed else None
            job.error_code = "worker_lease_expired"
            job.error_message = "Worker trước đã mất lease; kết quả chưa được xác nhận"
            self._sync_subject(job, failed)
        self.db.commit()
        return len(expired)

    def claim_next(self) -> AiJob | None:
        job = self.db.scalar(
            select(AiJob)
            .where(
                AiJob.status == "queued",
                or_(AiJob.next_attempt_at.is_(None), AiJob.next_attempt_at <= utcnow()),
            )
            .order_by(AiJob.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not job:
            self.db.rollback()
            return None
        if job.attempts >= self.settings.worker_max_attempts:
            job.status = "failed"
            job.error_code, job.error_message = "retry_limit_exceeded", "Đã hết số lần xử lý"
            job.completed_at = utcnow()
            self._sync_subject(job, True)
            self.db.commit()
            return job
        job.status = "running"
        job.attempts += 1
        job.started_at = job.locked_at = utcnow()
        job.completed_at = job.next_attempt_at = None
        job.lock_token = new_id()
        self.db.commit()
        return job

    def _owned_claim(self, claim: Claim, lock: bool = False) -> AiJob | None:
        statement = (
            select(AiJob)
            .where(AiJob.id == claim.id, AiJob.status == "running", AiJob.lock_token == claim.token)
            .execution_options(populate_existing=True)
        )
        if lock:
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    def _before_call(self, claim: Claim) -> None:
        self.db.rollback()
        if not self._owned_claim(claim):
            self.db.rollback()
            raise AIProviderError("Worker đã mất lease", "worker_lease_lost")
        allowed = active_ai_consent(self.db, claim.user_id)
        self.db.rollback()  # Không giữ transaction/snapshot trong khi gọi external inference.
        if not allowed:
            raise AIProviderError("Consent ai_processing không còn hiệu lực", "consent_revoked")

    def process_next(self) -> bool:
        self.recover_expired()
        job = self.claim_next()
        if not job:
            return False
        if job.status != "running":
            return True
        claim = Claim(
            job.id,
            job.user_id,
            job.job_type,
            job.model_provider,
            dict(job.input_ref),
            job.lock_token,
        )
        provider = None
        try:
            ensure_job_configuration(self.settings, claim.provider, claim.ref, claim.job_type)
            provider = self.providers.resolve(
                claim.provider, claim.ref["model"], claim.ref.get("image_model")
            )
            if claim.job_type == "catalogue":
                result = self._analyze(claim, provider)
            elif claim.job_type in {"lookbook", "variation"}:
                result = self._render(claim, provider)
            else:
                raise AIProviderError("Loại job không được hỗ trợ", "unsupported_job_type")
            self.db.rollback()
            job = self._owned_claim(claim, lock=True)
            if not job:
                self.db.rollback()
                return True
            if not active_ai_consent(self.db, claim.user_id):
                raise AIProviderError("Consent đã bị rút khi đang xử lý", "consent_revoked")
            if isinstance(result, Analysis):
                output = self._save_analysis(claim, result)
            else:
                output = self._save_render(claim, result)
            job.output_ref = output
            job.status = "succeeded"
            job.error_code = job.error_message = None
            job.completed_at = utcnow()
            job.lock_token = job.locked_at = job.next_attempt_at = None
            self.db.commit()
            logger.info(
                "AI job=%s provider=%s model=%s status=succeeded",
                claim.id,
                claim.provider,
                claim.ref["model"],
            )
        except Exception as exc:
            self.db.rollback()
            error = (
                exc
                if isinstance(exc, AIProviderError)
                else AIProviderError("Không thể hoàn thành tác vụ AI", "internal_error")
            )
            # Không ghi traceback/raw exception: có thể chứa body, prompt, key hoặc signed URL.
            logger.warning("AI job=%s provider=%s code=%s", claim.id, claim.provider, error.code)
            job = self._owned_claim(claim, lock=True)
            if job:
                retry = error.retryable and job.attempts < self.settings.worker_max_attempts
                job.status = "queued" if retry else "failed"
                job.error_code, job.error_message = error.code, str(error)
                job.completed_at = None if retry else utcnow()
                delay = self.settings.worker_retry_base_seconds * 2 ** (job.attempts - 1)
                job.next_attempt_at = (
                    utcnow() + timedelta(seconds=random.uniform(delay, delay * 1.5))
                    if retry
                    else None
                )
                job.lock_token = job.locked_at = None
                self._sync_subject(job, not retry)
                self.db.commit()
            else:
                self.db.rollback()
        finally:
            close = getattr(provider, "close", None)
            if close:
                close()
        return True

    def _analyze(self, claim: Claim, provider) -> Analysis:
        asset = self.db.scalar(
            select(MediaAsset).where(
                MediaAsset.id == claim.ref.get("asset_id"), MediaAsset.user_id == claim.user_id
            )
        )
        if not asset:
            raise AIProviderError("Ảnh nguồn không còn tồn tại", "source_not_found")
        key = asset.object_key
        self.db.rollback()
        try:
            image, mime = sanitize_image(
                self.storage.get_bytes(key),
                self.settings.max_upload_bytes,
                self.settings.max_image_pixels,
            )
        except ValueError as exc:
            raise AIProviderError("Ảnh nguồn không hợp lệ", "invalid_source_image") from exc
        return provider.analyze(image, mime, before_call=lambda: self._before_call(claim))

    def _save_analysis(self, claim: Claim, analysis: Analysis) -> dict:
        asset = self.db.scalar(
            select(MediaAsset)
            .where(MediaAsset.id == claim.ref.get("asset_id"), MediaAsset.user_id == claim.user_id)
            .with_for_update()
        )
        if not asset:
            raise AIProviderError("Ảnh nguồn không còn tồn tại", "source_not_found")
        ids = []
        for index, attributes in enumerate(analysis.result.items):
            candidate = GarmentCandidate(
                user_id=claim.user_id,
                asset_id=asset.id,
                job_id=claim.id,
                candidate_index=index,
                attributes_json=attributes.model_dump(),
                original_attributes=attributes.model_dump(),
            )
            self.db.add(candidate)
            self.db.flush()
            ids.append(candidate.id)
        asset.status = "analyzed" if ids else "no_items"
        return {
            "asset_id": asset.id,
            "candidate_ids": ids,
            "result": "candidates" if ids else "no_items",
            "provider": claim.provider,
            "model": claim.ref["model"],
            "latency_ms": analysis.metadata.get("latency_ms"),
            "usage": self._safe_usage(analysis.metadata.get("usage")),
        }

    @staticmethod
    def _safe_usage(usage: Any) -> dict:
        # Chỉ lưu counters, không tin arbitrary metadata từ upstream.
        return {
            key: value
            for key, value in (usage.items() if isinstance(usage, dict) else [])
            if key
            in {
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "input_tokens",
                "output_tokens",
            }
            and type(value) is int
            and value >= 0
        }

    def _render(self, claim: Claim, provider) -> RenderedLook:
        book = self.db.scalar(
            select(Lookbook).where(
                Lookbook.id == claim.ref.get("lookbook_id"), Lookbook.user_id == claim.user_id
            )
        )
        ids = list(dict.fromkeys(claim.ref.get("garment_ids", [])))
        garments = list(
            self.db.scalars(
                select(Garment).where(Garment.user_id == claim.user_id, Garment.id.in_(ids))
            )
        )
        if not book or not ids or len(garments) != len(ids):
            raise AIProviderError("Lookbook hoặc garment không còn tồn tại", "source_not_found")
        data = [
            {
                "id": garment.id,
                "category": garment.category,
                "color": garment.color,
                "material": garment.material,
                "season": garment.season,
                "tags": garment.tags,
            }
            for garment in garments
        ]
        theme, occasion = book.theme, book.occasion
        instruction = PRESETS.get(claim.ref.get("preset_id"), claim.ref.get("instruction"))
        self.db.rollback()
        return provider.render_look(
            garments=data,
            theme=theme,
            occasion=occasion,
            instruction=instruction,
            before_call=lambda: self._before_call(claim),
        )

    def _save_render(self, claim: Claim, rendered: RenderedLook) -> dict:
        book = self.db.scalar(
            select(Lookbook)
            .where(Lookbook.id == claim.ref.get("lookbook_id"), Lookbook.user_id == claim.user_id)
            .with_for_update()
        )
        if not book:
            raise AIProviderError("Lookbook không còn tồn tại", "source_not_found")
        try:
            image, mime = sanitize_image(
                rendered.image_bytes, self.settings.max_upload_bytes, self.settings.max_image_pixels
            )
        except ValueError as exc:
            raise AIProviderError("Provider không trả ảnh hợp lệ", "invalid_model_output") from exc
        extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime]
        # Object riêng theo claim: worker mất lease không ghi đè ảnh của worker mới.
        key = f"users/{claim.user_id}/generated/{claim.id}/{claim.token}.{extension}"
        generated = MediaAsset(
            user_id=claim.user_id,
            object_key=key,
            asset_type="generated_look",
            mime_type=mime,
            checksum=hashlib.sha256(image).hexdigest(),
            byte_size=len(image),
            metadata_json={"job_id": claim.id},
            status="ready",
        )
        self.storage.put_bytes(key, image, mime)
        self.db.add(generated)
        self.db.flush()
        look = Look(
            lookbook_id=book.id,
            source_job_id=claim.id,
            generated_asset_id=generated.id,
            generation_prompt=rendered.prompt,
            model_reference=rendered.model_reference,
            status="preview",
        )
        self.db.add(look)
        self.db.flush()
        ids = list(dict.fromkeys(claim.ref.get("garment_ids", [])))
        garments = list(
            self.db.scalars(
                select(Garment).where(Garment.user_id == claim.user_id, Garment.id.in_(ids))
            )
        )
        if len(garments) != len(ids):
            raise AIProviderError("Garment nguồn không còn tồn tại", "source_not_found")
        for index, garment_id in enumerate(ids):
            self.db.add(LookItem(look_id=look.id, garment_id=garment_id, layer_order=index))
        approval = ApprovalRequest(
            user_id=claim.user_id,
            subject_type="look",
            subject_id=look.id,
            action="save_look",
            status="pending",
        )
        self.db.add(approval)
        self.db.flush()
        book.status = "pending_approval"
        return {
            "lookbook_id": book.id,
            "look_id": look.id,
            "generated_asset_id": generated.id,
            "approval_id": approval.id,
            "provider": claim.provider,
            "model": claim.ref["model"],
            "image_model": claim.ref["image_model"],
        }


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # SDK debug logs có thể chứa URL/token; không bật chúng qua cấu hình ứng dụng.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    settings = get_settings()
    storage = ObjectStorage(settings)
    providers = ProviderRegistry(settings)
    while True:
        try:
            with SessionLocal() as db:
                processed = JobWorker(db, storage, providers, settings).process_next()
            if not processed:
                time.sleep(2)
        except Exception:
            logger.error("Worker không kết nối được runtime; thử lại sau 5 giây")
            time.sleep(5)


if __name__ == "__main__":
    run()
