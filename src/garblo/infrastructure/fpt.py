from __future__ import annotations

import json
import re
from typing import Any

import httpx
from openai import APIStatusError, OpenAI

from garblo.application.wardrobe import ApplicationError
from garblo.config import Settings


class FPTProviderError(ApplicationError):
    """The FPT contract was unavailable or did not return valid public text."""


def text_from_fpt_message(payload: dict[str, Any]) -> str:
    """Return public text only; reasoning/thinking content is deliberately discarded."""
    content = payload.get("content")
    if not isinstance(content, list):
        raise FPTProviderError("FPT response did not contain a content array")
    text = "".join(
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ).strip()
    if not text:
        raise FPTProviderError("FPT response did not contain public text")
    return text


def json_object_from_text(text: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidate = fenced.group(1) if fenced else text.strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise FPTProviderError("FPT response was not valid JSON") from exc
    if not isinstance(value, dict):
        raise FPTProviderError("FPT response must be a JSON object")
    return value


class FPTIntentProvider:
    """Thin adapter for FPT's OpenAI-compatible /v1/messages endpoint."""

    def __init__(self, settings: Settings, client: OpenAI | None = None):
        if not settings.fpt_is_configured:
            raise FPTProviderError("FPT is not configured")
        self.settings = settings
        self.client = client or OpenAI(api_key=settings.fpt_api_key, base_url=settings.fpt_base_url)

    def parse_intent(self, prompt: str) -> dict[str, Any]:
        instruction = (
            "Extract a wardrobe-search intent from the user's request. Return JSON only, with keys: "
            "occasion (string|null), style (string|null), colors (array of strings), formality (string|null), "
            "required_categories (array of strings), avoid_categories (array of strings), "
            "clarification_needed (boolean). Never include reasoning. User request: "
            + prompt
        )
        try:
            response = self.client.post(
                "/v1/messages",
                cast_to=httpx.Response,
                body={
                    "model": self.settings.fpt_model,
                    "max_tokens": self.settings.fpt_max_tokens,
                    "messages": [{"role": "user", "content": instruction}],
                    "stream": True,
                },
            )
            response.raise_for_status()
            return self._normalise_intent(json_object_from_text(text_from_fpt_message(response.json())))
        except FPTProviderError:
            raise
        except (httpx.HTTPError, APIStatusError) as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f" (HTTP {status_code})" if status_code else ""
            raise FPTProviderError("FPT intent request failed" + suffix) from exc
        except Exception as exc:
            raise FPTProviderError("FPT intent request failed") from exc

    @staticmethod
    def _normalise_intent(raw: dict[str, Any]) -> dict[str, Any]:
        def short_text(value: object) -> str | None:
            return value.strip()[:120] if isinstance(value, str) and value.strip() else None

        def string_list(value: object) -> list[str]:
            if not isinstance(value, list):
                return []
            return [item.strip()[:80] for item in value if isinstance(item, str) and item.strip()][:12]

        return {
            "occasion": short_text(raw.get("occasion")),
            "style": short_text(raw.get("style")),
            "colors": string_list(raw.get("colors")),
            "formality": short_text(raw.get("formality")),
            "required_categories": string_list(raw.get("required_categories")),
            "avoid_categories": string_list(raw.get("avoid_categories")),
            "clarification_needed": bool(raw.get("clarification_needed", False)),
        }
