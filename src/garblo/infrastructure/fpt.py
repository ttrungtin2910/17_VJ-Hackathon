from __future__ import annotations

import json
import re
from typing import Any

import httpx
from openai import APIStatusError, OpenAI

from garblo.application.intents import normalise_outfit_intent, outfit_intent_instruction
from garblo.application.wardrobe import ApplicationError
from garblo.config import Settings
from garblo.domain.entities import ParsedOutfitIntent


class FPTProviderError(ApplicationError):
    """The FPT contract was unavailable or did not return valid public text."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "fpt_contract_error",
        retryable: bool = False,
        fallback_allowed: bool = False,
    ):
        super().__init__(message, error_code=error_code, retryable=retryable)
        self.fallback_allowed = fallback_allowed


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
            raise FPTProviderError("FPT is not configured", error_code="fpt_not_configured")
        self.settings = settings
        self.client = client or OpenAI(api_key=settings.fpt_api_key, base_url=settings.fpt_base_url)

    def parse_intent(self, prompt: str) -> ParsedOutfitIntent:
        try:
            response = self.client.post(
                "/v1/messages",
                cast_to=httpx.Response,
                body={
                    "model": self.settings.fpt_model,
                    "max_tokens": self.settings.fpt_max_tokens,
                    "messages": [{"role": "user", "content": outfit_intent_instruction(prompt)}],
                    "stream": True,
                },
            )
            response.raise_for_status()
            return ParsedOutfitIntent(
                intent=normalise_outfit_intent(json_object_from_text(text_from_fpt_message(response.json()))),
                provider="fpt",
            )
        except FPTProviderError:
            raise
        except (httpx.HTTPError, APIStatusError) as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {402, 408, 429} or (isinstance(status_code, int) and status_code >= 500):
                raise FPTProviderError(
                    "FPT intent service is temporarily unavailable",
                    error_code=f"fpt_http_{status_code}",
                    retryable=True,
                    fallback_allowed=True,
                ) from exc
            if status_code in {401, 403}:
                raise FPTProviderError(
                    "FPT credentials or model access was rejected",
                    error_code="fpt_auth_or_access_error",
                ) from exc
            if status_code:
                raise FPTProviderError("FPT intent request was rejected", error_code=f"fpt_http_{status_code}") from exc
            raise FPTProviderError(
                "FPT intent service is unavailable",
                error_code="fpt_network_error",
                retryable=True,
                fallback_allowed=True,
            ) from exc
        except Exception as exc:
            raise FPTProviderError("FPT intent request failed", error_code="fpt_request_error") from exc
