import logging
from datetime import datetime, timezone

from garblo.ai import AIProviderError
from garblo.config import Settings

logger = logging.getLogger(__name__)
_missing_fpt_warned = False


def attempt_limit(settings: Settings, ref: dict) -> int:
    maximum = ref.get("max_attempts", settings.worker_max_attempts)
    if type(maximum) is not int or maximum < 1:
        maximum = settings.worker_max_attempts
    return min(maximum, settings.worker_max_attempts)


def ensure_job_configuration(settings: Settings, provider: str, ref: dict, kind: str) -> None:
    if not ref.get("model"):
        raise AIProviderError("Job thiếu model snapshot", "provider_not_configured")
    if provider == "openai":
        configured = bool(settings.openai_api_key)
    elif provider == "fpt" and kind == "catalogue":
        configured = bool(settings.fpt_vlm_api_key)
    elif provider == "azure_openai":
        configured = bool(
            settings.azure_openai_endpoint
            and settings.azure_openai_api_key
            and settings.azure_openai_api_version
        )
    else:
        configured = False
    if kind in {"lookbook", "variation"} and not ref.get("image_model"):
        configured = False
    if kind == "catalogue" and ref.get("schema_version", 1) == 2:
        configured = configured and bool(settings.openai_api_key and ref.get("image_model"))
        configured = configured and not str(ref.get("image_model", "")).startswith("dall-e")
    if not configured:
        raise AIProviderError(
            "Nhà cung cấp chưa được cấu hình cho tác vụ", "provider_not_configured"
        )


def new_snapshot(settings: Settings, provider: str, kind: str) -> dict:
    ref = {
        "provider": provider,
        "model": settings.fpt_vlm_model if provider == "fpt" else settings.openai_vlm_model,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    if kind in {"lookbook", "variation"}:
        ref["image_model"] = settings.openai_image_model
        if provider == "openai":
            if (settings.openai_image_model or "").startswith("dall-e"):
                raise AIProviderError(
                    "Cần image model hỗ trợ ảnh tham chiếu", "provider_not_configured"
                )
            ref["render_mode"] = "garment_images"
    if kind == "catalogue":
        version = settings.catalogue_schema_version
        screening_model = (
            settings.fpt_screening_model
            if provider == "fpt"
            else settings.catalogue_screening_model
        )
        escalation_model = (
            settings.fpt_escalation_model
            if provider == "fpt"
            else settings.catalogue_escalation_model
        )
        if version != 2 and any(
            (
                screening_model,
                escalation_model,
                settings.catalogue_segmentation_url,
            )
        ):
            raise AIProviderError(
                "Stages mở rộng cần catalogue schema v2", "provider_not_configured"
            )
        if settings.catalogue_segmentation_url and not settings.catalogue_segmentation_model:
            raise AIProviderError("Segmentation thiếu model đã xác minh", "provider_not_configured")
        ref.update(
            max_attempts=settings.worker_max_attempts,
            schema_version=version,
            prompt_version=f"catalogue-v{version}",
            pipeline_version=f"catalogue-v{version}",
            max_calls=settings.catalogue_max_calls,
            max_output_tokens=settings.catalogue_max_output_tokens,
            token_budget=settings.catalogue_token_budget,
            call_token_reservation=max(
                settings.catalogue_call_token_reservation, settings.catalogue_max_output_tokens
            ),
            deadline_seconds=settings.catalogue_deadline_seconds,
            native_structured_output=(
                provider == "openai" and settings.catalogue_native_structured_output
            ),
            supports_temperature=settings.catalogue_supports_temperature,
            screening_model=screening_model,
            escalation_model=escalation_model,
            segmentation_url=settings.catalogue_segmentation_url,
            segmentation_model=settings.catalogue_segmentation_model,
            segmentation_timeout_seconds=settings.catalogue_segmentation_timeout_seconds,
        )
        if version == 2:
            ref["image_model"] = settings.openai_image_model
        if provider == "openai" and settings.catalogue_fpt_fallback_enabled:
            if settings.fpt_vlm_api_key:
                # Chính sách local tối đa 2 attempts primary + attempt 3 fallback;
                # không tăng cap thấp hơn, không snapshot credential.
                ref["max_attempts"] = min(settings.worker_max_attempts, 3)
                ref["vision_fallback"] = {
                    "version": 1,
                    "provider": "fpt",
                    "model": settings.fpt_vlm_model,
                    "primary_attempts": 2,
                    "screening_model": settings.fpt_screening_model,
                    "escalation_model": settings.fpt_escalation_model,
                }
            else:
                global _missing_fpt_warned
                if not _missing_fpt_warned:
                    logger.warning("Thiếu FPT key: catalogue giữ OpenAI-only trong budget gốc")
                    _missing_fpt_warned = True
    ensure_job_configuration(settings, provider, ref, kind)
    return ref
