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
    ensure_job_configuration(settings, provider, ref, kind)
    return ref
