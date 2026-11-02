from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DetectedGarment:
    """A single garment crop returned by the local detector."""

    category: str
    confidence: float
    bbox: dict[str, int]
    crop_bytes: bytes
    mime_type: str = "image/png"


@dataclass(frozen=True)
class VectorMatch:
    garment_id: str
    score: float


@dataclass(frozen=True)
class ParsedOutfitIntent:
    """Normalised search intent with safe provenance for audit and API responses."""

    intent: dict[str, Any]
    provider: str
    fallback_reason: str | None = None


@dataclass(frozen=True)
class RenderedLook:
    image_bytes: bytes
    mime_type: str
    prompt: str
    model_reference: str


Metadata = dict[str, Any]
