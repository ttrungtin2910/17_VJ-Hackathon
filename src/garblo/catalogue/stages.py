"""Các stage catalogue tùy chọn, không fallback provider hoặc tự duyệt kết quả.

Snapshot phải do backend/operator tạo; segmentation_url không phải dữ liệu từ
request hay model. Chỉ worker được persist metadata ``segments`` nội bộ (bytes).
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import math
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image

from garblo.ai import AIProviderError
from garblo.ai_providers.contracts import Analysis
from garblo.catalogue.contracts import AnalysisResultV2
from garblo.catalogue.quality import assess_component
from garblo.images import sanitize_image

MAX_SEGMENTATION_RESPONSE_BYTES = 32 * 1024 * 1024


def _optional_text(snapshot, key):
    value = snapshot.get(key)
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise AIProviderError(f"Cấu hình {key} không hợp lệ", "provider_request_error")
    return value


def _analyze(provider, snapshot, image, mime, ledger, stage, *, require_v2=False):
    ledger.stage, ledger.model = stage, snapshot["model"]
    configure = getattr(provider, "configure_catalogue", None)
    if require_v2 and configure:
        configure(snapshot)
    elif require_v2:
        raise AIProviderError(f"Stage {stage} không hỗ trợ catalogue v2", "provider_not_supported")
    provider.on_result = ledger.on_result
    provider.on_validation = ledger.on_validation
    result = provider.analyze(image, mime, before_call=ledger.before_call)
    expected = 2 if require_v2 else snapshot.get("schema_version", 1)
    if (
        not isinstance(result, Analysis)
        or getattr(result.result, "schema_version", 1) != expected
        or (expected == 2 and not isinstance(result.result, AnalysisResultV2))
    ):
        ledger.on_validation("invalid_model_output")
        raise AIProviderError(f"Stage {stage} trả sai schema", "invalid_model_output")
    return result


def _secondary(worker, claim, model, image, mime, ledger, stage, primary_provider):
    provider = worker.providers.resolve(claim.provider, model, None)
    try:
        # Không tái sử dụng/đóng primary, kể cả registry tùy biến trả cùng instance.
        if provider is primary_provider:
            raise AIProviderError("Stage phụ cần provider riêng", "provider_request_error")
        return _analyze(
            provider,
            {**claim.ref, "model": model},
            image,
            mime,
            ledger,
            stage,
            require_v2=True,
        )
    finally:
        if provider is not primary_provider:
            close = getattr(provider, "close", None)
            if close:
                close()


def _decode_image(value, max_bytes, max_pixels):
    if not isinstance(value, str):
        raise ValueError("Thiếu bytes ảnh base64")
    # Giới hạn trước decode, kể cả khi service trả ảnh nén/chuỗi quá lớn.
    if len(value) > 4 * ((max_bytes + 2) // 3):
        raise ValueError("Ảnh segmentation quá lớn")
    raw = base64.b64decode(value, validate=True)
    clean, mime = sanitize_image(raw, max_bytes, max_pixels)
    return raw, clean, mime


def _validate_segments(body, count, source_size, max_bytes, max_pixels):
    try:
        root = json.loads(body)
        if not isinstance(root, dict) or set(root) != {"components"}:
            raise ValueError("Envelope segmentation không hợp lệ")
        components = root["components"]
        if not isinstance(components, list) or len(components) != count:
            raise ValueError("Segmentation thiếu component")
        segments = {}
        for component in components:
            if not isinstance(component, dict) or set(component) != {
                "candidate_index",
                "image_base64",
                "mask_base64",
            }:
                raise ValueError("Component segmentation không hợp lệ")
            index = component["candidate_index"]
            if type(index) is not int or not 0 <= index < count or index in segments:
                raise ValueError("Mapping component segmentation không hợp lệ")
            _, image, mime = _decode_image(component["image_base64"], max_bytes, max_pixels)
            raw_mask, mask, mask_mime = _decode_image(
                component["mask_base64"], max_bytes, max_pixels
            )
            with Image.open(io.BytesIO(raw_mask)) as decoded:
                if decoded.format != "PNG" or decoded.size != source_size:
                    raise ValueError("Mask phải là PNG cùng kích thước ảnh nguồn")
            with Image.open(io.BytesIO(mask)) as decoded:
                if mask_mime != "image/png" or decoded.size != source_size:
                    raise ValueError("Mask sau chuẩn hóa không khớp ảnh nguồn")
            segments[index] = {
                "candidate_index": index,
                "image_bytes": image,
                "mime_type": mime,
                "mask_bytes": mask,
            }
        return [segments[index] for index in range(count)]
    except (ValueError, TypeError, KeyError, binascii.Error) as exc:
        raise AIProviderError(
            "Kết quả segmentation không hợp lệ hoặc thiếu mask", "invalid_segmentation_output"
        ) from exc


def _segment(worker, snapshot, image, mime, components, ledger, url):
    model = _optional_text(snapshot, "segmentation_model")
    timeout = snapshot.get("segmentation_timeout_seconds", 30)
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise AIProviderError(
            "Endpoint segmentation không hợp lệ", "provider_request_error"
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or type(timeout) not in (int, float)
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise AIProviderError("Cấu hình segmentation không hợp lệ", "provider_request_error")
    max_bytes = worker.settings.max_upload_bytes
    max_pixels = worker.settings.max_image_pixels
    with Image.open(io.BytesIO(image)) as source:
        source_size = source.size
    payload = {
        "model": model,
        "image_base64": base64.b64encode(image).decode("ascii"),
        "mime_type": mime,
        "components": [
            {"candidate_index": index, "category": component.category}
            for index, component in enumerate(components)
        ],
    }
    # Nhãn audit rõ ràng khi operator để service tự chọn model, không khẳng định
    # tên model thực tế. Cột ledger.model hiện không cho phép NULL.
    ledger.stage, ledger.model = "segmentation", model or "service-default"
    previous_provider = getattr(ledger, "provider", None)
    supports_provider = hasattr(ledger, "provider")
    if supports_provider:
        ledger.provider = "segmentation_http"
    try:
        # Reservation phải xảy ra trước HTTP và được giữ đúng provider tới on_result.
        ledger.before_call()
        started = time.monotonic()
        error_code = None
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False) as client:
                with client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        raise AIProviderError(
                            "Dịch vụ segmentation trả lỗi",
                            "segmentation_http_error",
                            retryable=response.status_code in {408, 429}
                            or response.status_code >= 500,
                        )
                    body = bytearray()
                    for chunk in response.iter_bytes(chunk_size=64 * 1024):
                        if time.monotonic() - started >= timeout:
                            raise AIProviderError(
                                "Segmentation quá thời gian", "segmentation_timeout", retryable=True
                            )
                        if len(body) + len(chunk) > MAX_SEGMENTATION_RESPONSE_BYTES:
                            raise AIProviderError(
                                "Response segmentation quá lớn", "invalid_segmentation_output"
                            )
                        body.extend(chunk)
            return _validate_segments(body, len(components), source_size, max_bytes, max_pixels)
        except httpx.TimeoutException as exc:
            error_code = "segmentation_timeout"
            raise AIProviderError("Segmentation quá thời gian", error_code, retryable=True) from exc
        except httpx.RequestError as exc:
            error_code = "segmentation_http_error"
            raise AIProviderError("Không thể gọi segmentation", error_code, retryable=True) from exc
        except AIProviderError as exc:
            error_code = exc.code
            raise
        finally:
            ledger.on_result({}, int((time.monotonic() - started) * 1000), error_code)
    finally:
        if supports_provider:
            ledger.provider = previous_provider


def run_analysis_stages(
    worker, claim, image: bytes, mime: str, ledger, primary_provider
) -> Analysis:
    """Chạy screening → analysis → tối đa một escalation → segmentation tùy chọn.

    Primary đã được worker configure_catalogue trước khi gọi hàm này.
    Screening là inference riêng phân loại image relevance (image_type), dùng
    cùng schema/prompt catalogue-v2, không phải moderation hay safety model. Escalation
    là một đề xuất thay thế, vẫn luôn phải được người dùng kiểm tra/duyệt.
    Primary thuộc lifecycle của worker; các provider phụ được đóng tại đây.
    """
    snapshot = claim.ref
    screening = _optional_text(snapshot, "screening_model")
    escalation = _optional_text(snapshot, "escalation_model")
    segmentation_url = _optional_text(snapshot, "segmentation_url")
    if (screening or escalation) and snapshot.get("schema_version", 1) != 2:
        raise AIProviderError("Screening/escalation cần catalogue v2", "provider_request_error")
    screening_type = None
    if screening:
        screened = _secondary(
            worker, claim, screening, image, mime, ledger, "screening", primary_provider
        )
        screening_type = screened.result.image_type
        if screening_type == "unrelated":
            return Analysis(
                AnalysisResultV2(schema_version=2, image_type="unrelated", items=[]),
                {"screening_image_type": "unrelated", "human_review_required": True},
            )
    analysis = _analyze(primary_provider, snapshot, image, mime, ledger, "analysis")
    escalated = False
    if escalation and (
        analysis.result.image_type == "unknown"
        or any(assess_component(component).needs_review for component in analysis.result.items)
    ):
        analysis = _secondary(
            worker, claim, escalation, image, mime, ledger, "escalation", primary_provider
        )
        escalated = True
    # Không tin metadata segments từ VLM; chỉ HTTP service được cấu hình mới tạo được.
    metadata = {key: value for key, value in analysis.metadata.items() if key != "segments"}
    metadata["analysis_model"] = escalation if escalated else snapshot["model"]
    metadata["human_review_required"] = True
    if screening_type is not None:
        metadata["screening_image_type"] = screening_type
    if escalated:
        metadata["escalated"] = True
    if segmentation_url and len(analysis.result.items) > 1:
        metadata["segments"] = _segment(
            worker, snapshot, image, mime, analysis.result.items, ledger, segmentation_url
        )
    return Analysis(analysis.result, metadata)
