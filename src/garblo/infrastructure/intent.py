from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

import httpx
from openai import APIError, OpenAI

from garblo.application.intents import normalise_outfit_intent, outfit_intent_instruction
from garblo.application.wardrobe import ApplicationError
from garblo.config import Settings
from garblo.domain.entities import ParsedOutfitIntent
from garblo.domain.ports import OutfitIntentPort
from garblo.infrastructure.fpt import FPTIntentProvider, FPTProviderError


class OpenAIIntentProviderError(ApplicationError):
    """A safe failure from the OpenAI fallback intent provider."""


class OpenAIIntentProvider:
    """OpenAI JSON intent adapter used only when FPT cannot serve an intent request."""

    def __init__(self, settings: Settings, client: OpenAI | None = None):
        if not settings.openai_is_configured:
            raise OpenAIIntentProviderError("OpenAI is not configured", error_code="openai_not_configured")
        self.settings = settings
        self.client = client or OpenAI(api_key=settings.open_ai_api_key)

    def parse_intent(self, prompt: str) -> ParsedOutfitIntent:
        try:
            response = self.client.chat.completions.create(
                model=self.settings.openai_intent_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You extract precise wardrobe-search intent. Never include reasoning."},
                    {"role": "user", "content": outfit_intent_instruction(prompt)},
                ],
            )
            content = response.choices[0].message.content or "{}"
            return ParsedOutfitIntent(intent=normalise_outfit_intent(self._json_object(content)), provider="openai")
        except OpenAIIntentProviderError:
            raise
        except (json.JSONDecodeError, ValueError) as exc:
            raise OpenAIIntentProviderError(
                "OpenAI intent response was not valid JSON", error_code="openai_intent_contract_error"
            ) from exc
        except (APIError, httpx.HTTPError, IndexError, AttributeError, TypeError) as exc:
            raise OpenAIIntentProviderError(
                "OpenAI intent service is unavailable", error_code="openai_intent_unavailable", retryable=True
            ) from exc

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        value = json.loads(fenced.group(1) if fenced else text.strip())
        if not isinstance(value, dict):
            raise ValueError("OpenAI intent response must be a JSON object")
        return value


class FallbackIntentProvider:
    """Use FPT first and OpenAI only for explicitly transient FPT failures."""

    def __init__(
        self,
        settings: Settings,
        *,
        fpt_provider: OutfitIntentPort | None = None,
        openai_provider: OutfitIntentPort | None = None,
    ):
        self.fpt_provider = fpt_provider or (FPTIntentProvider(settings) if settings.fpt_is_configured else None)
        self.openai_provider = openai_provider or OpenAIIntentProvider(settings)

    def parse_intent(self, prompt: str) -> ParsedOutfitIntent:
        fallback_reason = "fpt_not_configured"
        if self.fpt_provider:
            try:
                return self.fpt_provider.parse_intent(prompt)
            except FPTProviderError as exc:
                if not exc.fallback_allowed:
                    raise
                fallback_reason = exc.error_code

        parsed = self.openai_provider.parse_intent(prompt)
        return replace(parsed, provider="openai_fallback", fallback_reason=fallback_reason)
