from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import httpx
from openai import APIError, OpenAI

from garblo.application.wardrobe import ApplicationError, validate_vector
from garblo.config import Settings
from garblo.domain.entities import RenderedLook


class OpenAIProviderError(ApplicationError):
    pass


class OpenAIWardrobeProvider:
    """OpenAI adapter for vision metadata, text vectors, and synthetic-person renders."""

    def __init__(self, settings: Settings, client: OpenAI | None = None):
        if not settings.openai_is_configured:
            raise OpenAIProviderError("OpenAI is not configured")
        self.settings = settings
        self.client = client or OpenAI(api_key=settings.open_ai_api_key)

    def analyse_garment(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        message = [
            {
                "type": "text",
                "text": (
                    "Analyse one clothing item. Return JSON only with category, color, material, pattern, "
                    "style, season, formality, occasions, tags, confidence. Arrays must contain short strings. "
                    "Use null or [] when uncertain."
                ),
            },
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.settings.openai_vision_model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": "You are a precise fashion catalogue assistant."}, {"role": "user", "content": message}],  # type: ignore[arg-type]
            )
            content = response.choices[0].message.content or "{}"
            value = self._json_object(content)
            return value
        except OpenAIProviderError:
            raise
        except (APIError, httpx.HTTPError, IndexError, AttributeError) as exc:
            raise OpenAIProviderError("OpenAI garment analysis failed") from exc

    def embed_text(self, text: str) -> list[float]:
        try:
            response = self.client.embeddings.create(
                model=self.settings.openai_embedding_model,
                input=text,
                dimensions=self.settings.metadata_embedding_dimensions,
                encoding_format="float",
            )
            return validate_vector(
                list(response.data[0].embedding), self.settings.metadata_embedding_dimensions, "OpenAI embeddings"
            )
        except ApplicationError:
            raise
        except (APIError, httpx.HTTPError, IndexError, AttributeError) as exc:
            raise OpenAIProviderError("OpenAI embedding request failed") from exc

    def render_look(
        self,
        *,
        garments: list[dict[str, Any]],
        images: list[tuple[str, bytes, str]],
        theme: str | None,
        occasion: str | None,
        instruction: str | None,
    ) -> RenderedLook:
        wardrobe = json.dumps(garments, ensure_ascii=False)
        prompt = (
            "Create a full-body fashion editorial photograph of an adult synthetic model wearing only the supplied "
            f"garments. Wardrobe metadata: {wardrobe}. Theme: {theme or 'minimal studio'}. "
            f"Occasion: {occasion or 'not specified'}. User direction: {instruction or 'none'}. "
            "Do not depict a real user or claim these are photographs of a real person."
        )
        files: list[io.BytesIO] = []
        for filename, image_bytes, _mime_type in images:
            image_file = io.BytesIO(image_bytes)
            image_file.name = filename
            files.append(image_file)
        if not files:
            raise OpenAIProviderError("At least one garment image is required for rendering")
        try:
            response = self.client.images.edit(
                model=self.settings.openai_image_model,
                image=files if len(files) > 1 else files[0],
                prompt=prompt,
                size="1024x1536",
                # `extra_body` retains compatibility with the installed OpenAI 1.x client while
                # passing current GPT Image parameters to the API.
                extra_body={"output_format": "png", "quality": "medium"},
            )
            image = response.data[0]
            if not image.b64_json:
                raise OpenAIProviderError("OpenAI image response did not contain image bytes")
            return RenderedLook(
                image_bytes=base64.b64decode(image.b64_json),
                mime_type="image/png",
                prompt=prompt,
                model_reference=self.settings.openai_image_model,
            )
        except OpenAIProviderError:
            raise
        except (APIError, httpx.HTTPError, IndexError, AttributeError) as exc:
            raise OpenAIProviderError("OpenAI synthetic outfit render failed") from exc
        finally:
            for image_file in files:
                image_file.close()

    @staticmethod
    def _json_object(text: str) -> dict[str, Any]:
        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
        try:
            value = json.loads(fenced.group(1) if fenced else text.strip())
        except json.JSONDecodeError as exc:
            raise OpenAIProviderError("OpenAI vision response was not valid JSON") from exc
        if not isinstance(value, dict):
            raise OpenAIProviderError("OpenAI vision response must be a JSON object")
        return value
