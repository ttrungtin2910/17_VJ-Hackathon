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
from garblo.catalogue.budget import CallLedger
from garblo.catalogue.fallback import execution_claim
from garblo.catalogue.quality import assess_quality
from garblo.catalogue.stages import run_analysis_stages
from garblo.config import Settings, get_settings
from garblo.consent import active_ai_consent
from garblo.database import SessionLocal
from garblo.images import sanitize_image
from garblo.jobs import attempt_limit, ensure_job_configuration
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
            failed = job.attempts >= attempt_limit(self.settings, job.input_ref)
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
        if job.attempts >= attempt_limit(self.settings, job.input_ref):
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
        ledger = None
        effective = claim
        try:
            effective = execution_claim(self, claim)
            ensure_job_configuration(
                self.settings, effective.provider, effective.ref, effective.job_type
            )
            provider = self.providers.resolve(
                effective.provider, effective.ref["model"], effective.ref.get("image_model")
            )
            if claim.job_type == "catalogue":
                ledger = CallLedger(self, claim)
                ledger.provider = effective.provider
                configure = getattr(provider, "configure_catalogue", None)
                if configure:
                    configure(effective.ref)
                provider.on_result = ledger.on_result
                provider.on_validation = ledger.on_validation
                result = self._analyze(effective, provider, ledger)
                result = Analysis(
                    result.result,
                    {
                        **result.metadata,
                        "effective_provider": effective.provider,
                        "effective_model": effective.ref["model"],
                    },
                )
                ledger.finish_unknown()
                ledger.ensure_deadline()
                if getattr(result.result, "schema_version", 1) != claim.ref.get(
                    "schema_version", 1
                ):
                    raise AIProviderError("Schema không khớp snapshot job", "invalid_model_output")
            elif claim.job_type == "recommendation_edit":
                from garblo.recommendations.conversation import plan_edit

                ledger = CallLedger(self, claim)
                ledger.stage = "intent"
                provider.on_result = ledger.on_result
                provider.on_validation = ledger.on_validation
                result = plan_edit(self.db, claim.user_id, claim.ref, provider, ledger)
                ledger.finish_unknown()
                ledger.ensure_deadline()
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
            if claim.job_type == "recommendation_edit":
                from garblo.recommendations.conversation import save_edit

                output = save_edit(self.db, claim.user_id, claim.ref, result)
            elif isinstance(result, Analysis):
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
                effective.provider,
                effective.ref["model"],
            )
        except Exception as exc:
            self.db.rollback()
            error = (
                exc
                if isinstance(exc, AIProviderError)
                else AIProviderError("Không thể hoàn thành tác vụ AI", "internal_error")
            )
            if ledger:
                ledger.finish_unknown(error.code)
            # Không ghi traceback/raw exception: có thể chứa body, prompt, key hoặc signed URL.
            logger.warning(
                "AI job=%s provider=%s code=%s", claim.id, effective.provider, error.code
            )
            job = self._owned_claim(claim, lock=True)
            if job:
                retry = error.retryable and job.attempts < attempt_limit(
                    self.settings, job.input_ref
                )
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

    def _analyze(self, claim: Claim, provider, ledger) -> Analysis:
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
        return run_analysis_stages(self, claim, image, mime, ledger, provider)

    def _save_analysis(self, claim: Claim, analysis: Analysis) -> dict:
        asset = self.db.scalar(
            select(MediaAsset)
            .where(MediaAsset.id == claim.ref.get("asset_id"), MediaAsset.user_id == claim.user_id)
            .with_for_update()
        )
        if not asset:
            raise AIProviderError("Ảnh nguồn không còn tồn tại", "source_not_found")
        ids = []
        if getattr(analysis.result, "image_type", None) == "unrelated" and analysis.result.items:
            raise AIProviderError(
                "Kết quả phân loại ảnh mâu thuẫn với components", "invalid_model_output"
            )
        segments = {part["candidate_index"]: part for part in analysis.metadata.get("segments", [])}
        for index, attributes in enumerate(analysis.result.items):
            quality = assess_quality(attributes)
            basic, details, reasons = quality.attributes, quality.details, quality.reasons
            component_asset_id = None
            mask_asset_id = None
            if index in segments:
                part = segments[index]
                mask = None
                if part.get("mask_bytes"):
                    mask = self._save_component_asset(
                        claim, asset.id, index, "mask", part["mask_bytes"], "image/png"
                    )
                component = self._save_component_asset(
                    claim,
                    asset.id,
                    index,
                    "component",
                    part["image_bytes"],
                    part["mime_type"],
                    mask.id if mask else None,
                )
                component_asset_id = component.id
                mask_asset_id = mask.id if mask else None
            candidate = GarmentCandidate(
                user_id=claim.user_id,
                asset_id=asset.id,
                job_id=claim.id,
                candidate_index=index,
                attributes_json=basic,
                original_attributes=basic,
                schema_version=getattr(analysis.result, "schema_version", 1),
                details_json=details,
                component_asset_id=component_asset_id,
                review_json={
                    "needs_review": True,
                    "reason_codes": reasons,
                    "original_details": details,
                    "edited_by_user": False,
                    "mask_asset_id": mask_asset_id,
                    "analysis_model": analysis.metadata.get("analysis_model", claim.ref["model"]),
                    "prompt_version": claim.ref.get("prompt_version"),
                },
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
            "effective_provider": analysis.metadata.get("effective_provider", claim.provider),
            "effective_model": analysis.metadata.get("effective_model", claim.ref["model"]),
            "analysis_model": analysis.metadata.get("analysis_model", claim.ref["model"]),
            "latency_ms": analysis.metadata.get("latency_ms"),
            "usage": self._safe_usage(analysis.metadata.get("usage")),
            "schema_version": getattr(analysis.result, "schema_version", 1),
            "stage": "human_review" if ids else "completed",
        }

    def _save_component_asset(self, claim, parent_id, index, role, body, mime, mask_id=None):
        asset_id = new_id()
        key = f"users/{claim.user_id}/components/{claim.id}/{claim.token}/{index}-{role}-{asset_id}.png"
        asset = MediaAsset(
            id=asset_id,
            user_id=claim.user_id,
            object_key=key,
            asset_type=role,
            mime_type=mime,
            checksum=hashlib.sha256(body).hexdigest(),
            byte_size=len(body),
            parent_asset_id=parent_id,
            status="ready",
            metadata_json={
                "job_id": claim.id,
                "candidate_index": index,
                "mask_asset_id": mask_id,
                "model": claim.ref.get("image_model") or claim.ref.get("segmentation_model"),
            },
        )
        self.storage.put_bytes(key, body, mime)
        self.db.add(asset)
        self.db.flush()
        return asset

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
        image_options = {}
        if claim.ref.get("render_mode") == "garment_images":
            if any(garment.catalog_status != "active" for garment in garments):
                raise AIProviderError("Món đồ không còn hoạt động", "wardrobe_changed")
            images = []
            for garment in garments:
                self._before_call(claim)
                asset = self.db.scalar(
                    select(MediaAsset).where(
                        MediaAsset.id == (garment.component_asset_id or garment.source_asset_id),
                        MediaAsset.user_id == claim.user_id,
                    )
                )
                if not asset:
                    raise AIProviderError("Ảnh món đồ không còn tồn tại", "source_not_found")
                try:
                    body, mime = sanitize_image(
                        self.storage.get_bytes(asset.object_key),
                        self.settings.max_upload_bytes,
                        self.settings.max_image_pixels,
                    )
                except (ValueError, KeyError) as exc:
                    raise AIProviderError("Ảnh món đồ không hợp lệ", "source_not_found") from exc
                images.append((f"{garment.id}.png", body, mime))
            image_options["images"] = images
        theme, occasion = book.theme, book.occasion
        instruction = PRESETS.get(claim.ref.get("preset_id"), claim.ref.get("instruction"))
        self.db.rollback()
        return provider.render_look(
            garments=data,
            **image_options,
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
    from garblo.local_runtime import write_heartbeat

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # SDK debug logs có thể chứa URL/token; không bật chúng qua cấu hình ứng dụng.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    settings = get_settings()
    storage = ObjectStorage(settings)
    providers = ProviderRegistry(settings)
    while True:
        try:
            write_heartbeat("busy")
            with SessionLocal() as db:
                processed = JobWorker(db, storage, providers, settings).process_next()
            write_heartbeat("idle")
            if not processed:
                time.sleep(2)
        except Exception:
            write_heartbeat("failed")
            logger.error("Worker không kết nối được runtime; thử lại sau 5 giây")
            time.sleep(5)


if __name__ == "__main__":
    run()
