from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from garblo.ai import AIProviderError
from garblo.ai_providers.contracts import (
    Analysis,
    CatalogueOptions,
    ResultHook,
    ValidationHook,
    parse_analysis,
    safe_usage,
    text_content,
    upstream_error,
)
from garblo.config import Settings


def completion_root(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise AIProviderError("Response FPT không hợp lệ", "invalid_model_output")
    if "code" in payload and payload["code"] not in (200, "200"):
        try:
            status = int(payload["code"])
        except (ValueError, TypeError):
            status = 400
        raise upstream_error(status)
    data = payload.get("data")
    return data if isinstance(data, dict) and "choices" in data else payload


def normalize_response(payload: Any) -> tuple[Any, dict]:
    root = completion_root(payload)
    try:
        choice = root["choices"][0]
        if choice["message"].get("refusal"):
            raise AIProviderError("Provider từ chối xử lý ảnh", "provider_refused")
        content = choice["message"]["content"]
        if choice.get("finish_reason") not in (None, "stop"):
            raise AIProviderError("FPT chưa hoàn tất nội dung", "invalid_model_output")
    except (KeyError, TypeError, IndexError) as exc:
        raise AIProviderError("Response FPT thiếu nội dung", "invalid_model_output") from exc
    usage = root.get("usage", payload.get("usage", {}))
    return content, usage if isinstance(usage, dict) else {}


def parse_sse(text: str, usage_out: dict | None = None) -> tuple[str, dict]:
    parts, event = [], []
    usage = usage_out if usage_out is not None else {}
    finished = False
    saw_finish = False
    # Một SSE event có thể có nhiều dòng data; không parse từng TCP chunk như JSON.
    for line in [*text.splitlines(), ""]:
        if line.startswith("data:"):
            event.append(line[5:].lstrip(" "))
        elif not line and event:
            data = "\n".join(event)
            event = []
            if data.strip() == "[DONE]":
                finished = True
                break
            try:
                payload = json.loads(data)
                if isinstance(payload, dict):
                    usage.update(safe_usage(payload.get("usage")))
                root = completion_root(payload)
                if isinstance(root.get("usage"), dict):
                    usage.update(safe_usage(root["usage"]))
                for choice in root.get("choices", []):
                    if choice.get("index", 0) != 0:
                        continue
                    delta = choice.get("delta", {})
                    if delta.get("content") is not None:
                        parts.append(text_content(delta["content"]))
                    if choice.get("finish_reason") == "stop":
                        saw_finish = True
                    elif choice.get("finish_reason") is not None:
                        raise AIProviderError(
                            "FPT stream chưa hoàn tất nội dung", "invalid_model_output"
                        )
            except (ValueError, TypeError, AttributeError) as exc:
                raise AIProviderError("FPT SSE không hợp lệ", "invalid_model_output") from exc
    if not parts or not (finished or saw_finish):
        raise AIProviderError("FPT SSE bị ngắt hoặc không có nội dung", "invalid_model_output")
    return "".join(parts), usage


class FPTQwenAnalyzer:
    def __init__(self, settings: Settings, model: str, client: httpx.Client | None = None):
        if not settings.fpt_vlm_api_key:
            raise AIProviderError("FPT chưa được cấu hình", "provider_not_configured")
        self.settings, self.model, self.client = settings, model, client
        self.catalogue_options = CatalogueOptions(max_tokens=1024)
        self.on_result: ResultHook | None = None
        self.on_validation: ValidationHook | None = None

    def configure_catalogue(self, snapshot: Mapping[str, Any]) -> None:
        options = CatalogueOptions.from_snapshot(snapshot, default_tokens=1024)
        if options.native_structured_output:
            raise AIProviderError("FPT chưa hỗ trợ native JSON schema", "provider_not_supported")
        self.catalogue_options = options

    def _report_result(self, usage: dict, started: float, error_code: str | None) -> None:
        if self.on_result is not None:
            self.on_result(safe_usage(usage), int((time.monotonic() - started) * 1000), error_code)

    def _request(self, payload: dict) -> tuple[Any, dict]:
        started, usage = time.monotonic(), {}
        url = self.settings.fpt_vlm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.fpt_vlm_api_key.get_secret_value()}"}
        client = self.client or httpx.Client(timeout=self.settings.fpt_vlm_timeout_seconds)
        deadline = time.monotonic() + self.settings.fpt_vlm_timeout_seconds
        try:
            try:
                with client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.settings.fpt_vlm_timeout_seconds,
                ) as response:
                    if response.status_code >= 400:
                        raise upstream_error(response.status_code)
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise upstream_error()
                        body.extend(chunk)
                        if len(body) > 2 * 1024 * 1024:
                            raise AIProviderError("Response FPT quá lớn", "invalid_model_output")
                    text = body.decode("utf-8")
                    if "text/event-stream" in response.headers.get("content-type", ""):
                        content, _ = parse_sse(text, usage)
                    else:
                        root = json.loads(text)
                        # Trích usage trước validate envelope/content để không mất accounting.
                        if isinstance(root, dict):
                            usage.update(safe_usage(root.get("usage")))
                            if isinstance(root.get("data"), dict):
                                usage.update(safe_usage(root["data"].get("usage")))
                        content, _ = normalize_response(root)
            except httpx.RequestError as exc:
                raise upstream_error() from exc
            except (ValueError, UnicodeError) as exc:
                raise AIProviderError(
                    "Response FPT không phải JSON hợp lệ", "invalid_model_output"
                ) from exc
        except AIProviderError as exc:
            self._report_result(usage, started, exc.code)
            raise
        finally:
            if self.client is None:
                client.close()
        self._report_result(usage, started, None)
        return content, usage

    def analyze(self, image: bytes, mime_type: str, before_call: Callable[[], None]) -> Analysis:
        started = time.monotonic()
        options = self.catalogue_options
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": options.prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
                        },
                    },
                ],
            }
        ]
        if options.schema_version == 2:
            messages[0]["content"].pop(0)
            messages.insert(0, {"role": "system", "content": options.prompt})
        for attempt in range(2):
            before_call()
            try:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "max_tokens": options.max_tokens,
                    "stream": False,
                }
                if options.temperature is not None:
                    payload["temperature"] = options.temperature
                content, usage = self._request(payload)
            except AIProviderError as exc:
                if exc.code != "invalid_model_output" or attempt:
                    raise
            else:
                try:
                    result = parse_analysis(content, options.schema_version)
                except AIProviderError as exc:
                    if self.on_validation is not None:
                        self.on_validation(exc.code)
                    if exc.code != "invalid_model_output" or attempt:
                        raise
                else:
                    if self.on_validation is not None:
                        self.on_validation(None)
                    return Analysis(
                        result,
                        {"latency_ms": int((time.monotonic() - started) * 1000), "usage": usage},
                    )
            # Không đưa raw response có thể nhạy cảm vào prompt/log hoặc DB.
            messages.append(
                {
                    "role": "user",
                    "content": "The previous result was invalid. Return only JSON matching the requested schema.",
                }
            )
        raise AssertionError("unreachable")
