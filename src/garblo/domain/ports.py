from __future__ import annotations

from typing import Any, Protocol

from garblo.domain.entities import DetectedGarment, ParsedOutfitIntent, RenderedLook, VectorMatch


class GarmentDetector(Protocol):
    def detect(self, image_bytes: bytes, mime_type: str) -> list[DetectedGarment]: ...


class ImageEmbedder(Protocol):
    def embed_image(self, image_bytes: bytes, mime_type: str) -> list[float]: ...


class OpenAIWardrobePort(Protocol):
    def analyse_garment(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]: ...

    def embed_text(self, text: str) -> list[float]: ...

    def render_look(
        self, *, garments: list[dict[str, Any]], images: list[tuple[str, bytes, str]], theme: str | None,
        occasion: str | None, instruction: str | None,
    ) -> RenderedLook: ...


class OutfitIntentPort(Protocol):
    def parse_intent(self, prompt: str) -> ParsedOutfitIntent: ...


class VectorStore(Protocol):
    def upsert_garment(
        self, *, garment_id: str, user_id: str, metadata_vector: list[float], image_vector: list[float],
        payload: dict[str, Any],
    ) -> None: ...

    def search_metadata(self, *, user_id: str, vector: list[float], limit: int) -> list[VectorMatch]: ...
