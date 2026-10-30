from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI
from openai import AzureOpenAI

from garblo.config import Settings


class AIProviderError(RuntimeError):
    """Lỗi đã loại dữ liệu nhạy cảm, an toàn để lưu vào job."""

    def __init__(self, message: str, code: str = "provider_error", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class RenderedLook:
    image_bytes: bytes
    mime_type: str
    prompt: str
    model_reference: str


class AzureLookbookProvider:
    """Azure adapter. It is instantiated only after configuration preflight succeeds."""

    def __init__(self, settings: Settings):
        if not settings.azure_is_configured:
            raise AIProviderError("Azure OpenAI is not configured")
        self.settings = settings
        self.chat = AzureChatOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_deployment=settings.azure_openai_chat_deployment,
            temperature=0,
            max_retries=0,
            timeout=settings.openai_timeout_seconds,
        )
        self.images = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            max_retries=0,
            timeout=settings.openai_timeout_seconds,
        )

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        candidate = fenced.group(1) if fenced else text.strip()
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise AIProviderError("Azure chat response was not valid JSON") from exc
        if not isinstance(value, dict):
            raise AIProviderError("Azure chat response must be a JSON object")
        return value

    def analyse_garment(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "Analyse this single clothing image. Return JSON only with keys "
                        "category, color, material, season, tags. tags must be a JSON array of short strings. "
                        "Use null where uncertain."
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
            ]
        )
        try:
            response = self.chat.invoke(
                [SystemMessage(content="You are a fashion catalogue assistant."), message]
            )
            return self._json_object(str(response.content))
        except AIProviderError:
            raise
        except Exception as exc:  # Provider SDK surfaces heterogeneous errors.
            raise AIProviderError("Azure garment analysis failed") from exc

    def render_look(
        self,
        *,
        garments: list[dict[str, Any]],
        theme: str | None,
        occasion: str | None,
        instruction: str | None,
        before_call: Callable[[], None] = lambda: None,
    ) -> RenderedLook:
        wardrobe = json.dumps(garments, ensure_ascii=False)
        planner = HumanMessage(
            content=(
                "Create one coherent fashion-editorial image prompt using only this wardrobe: "
                f"{wardrobe}. Theme: {theme or 'not specified'}. Occasion: {occasion or 'not specified'}. "
                f"User adjustment: {instruction or 'none'}. Return JSON only with a single key image_prompt."
            )
        )
        try:
            before_call()
            response = self.chat.invoke(
                [SystemMessage(content="You are a precise fashion stylist."), planner]
            )
            prompt = self._json_object(str(response.content)).get("image_prompt")
            if not isinstance(prompt, str) or not prompt.strip():
                raise AIProviderError("Azure planner did not return image_prompt")
            before_call()
            result = self.images.images.generate(
                model=self.settings.azure_openai_image_deployment,
                prompt=prompt,
                size="1024x1024",
                response_format="b64_json",
            )
            image = result.data[0]
            if getattr(image, "b64_json", None):
                image_bytes = base64.b64decode(image.b64_json)
            else:
                raise AIProviderError("Azure image response did not contain image data")
            return RenderedLook(
                image_bytes=image_bytes,
                mime_type="image/png",
                prompt=prompt,
                model_reference=self.settings.azure_openai_image_deployment or "azure-image",
            )
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderError("Azure look rendering failed") from exc
