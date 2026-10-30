from datetime import datetime, timezone

from garblo.ai import AIProviderError
from garblo.config import Settings


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
    ensure_job_configuration(settings, provider, ref, kind)
    return ref
