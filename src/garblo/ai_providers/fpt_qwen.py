from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from garblo.ai import AIProviderError
from garblo.ai_providers.contracts import (
    ANALYSIS_PROMPT,
    Analysis,
    parse_analysis,
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
        content = choice["message"]["content"]
        if choice.get("finish_reason") not in (None, "stop"):
            raise AIProviderError("FPT chưa hoàn tất nội dung", "invalid_model_output")
    except (KeyError, TypeError, IndexError) as exc:
        raise AIProviderError("Response FPT thiếu nội dung", "invalid_model_output") from exc
    usage = root.get("usage", payload.get("usage", {}))
    return content, usage if isinstance(usage, dict) else {}


def parse_sse(text: str) -> tuple[str, dict]:
    parts, event, usage = [], [], {}
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
                root = completion_root(json.loads(data))
                if isinstance(root.get("usage"), dict):
                    usage = root["usage"]
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

    def _request(self, payload: dict) -> tuple[Any, dict]:
        url = self.settings.fpt_vlm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Authorization": f"Bearer {self.settings.fpt_vlm_api_key.get_secret_value()}"}
        client = self.client or httpx.Client(timeout=self.settings.fpt_vlm_timeout_seconds)
        deadline = time.monotonic() + self.settings.fpt_vlm_timeout_seconds
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
                    return parse_sse(text)
                return normalize_response(json.loads(text))
        except httpx.RequestError as exc:
            raise upstream_error() from exc
        except (ValueError, UnicodeError) as exc:
            raise AIProviderError(
                "Response FPT không phải JSON hợp lệ", "invalid_model_output"
            ) from exc
        finally:
            if self.client is None:
                client.close()

    def analyze(self, image: bytes, mime_type: str, before_call: Callable[[], None]) -> Analysis:
        started = time.monotonic()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": ANALYSIS_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
                        },
                    },
                ],
            }
        ]
        for attempt in range(2):
            before_call()
            try:
                content, usage = self._request(
                    {
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0,
                        "max_tokens": 1024,
                        "stream": False,
                    }
                )
                result = parse_analysis(content)
                return Analysis(
                    result, {"latency_ms": int((time.monotonic() - started) * 1000), "usage": usage}
                )
            except AIProviderError as exc:
                if exc.code != "invalid_model_output" or attempt:
                    raise
                # Không đưa raw response có thể nhạy cảm vào prompt/log hoặc DB.
                messages.append(
                    {
                        "role": "user",
                        "content": "The previous result was invalid. Return only JSON matching the requested schema.",
                    }
                )
        raise AssertionError("unreachable")
