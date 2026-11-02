from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from garblo.domain.ports import OpenAIWardrobePort, OutfitIntentPort, VectorStore


class ApplicationError(RuntimeError):
    """A safe, user-facing failure raised by an application use case."""

    def __init__(self, message: str, *, error_code: str = "provider_or_model_error", retryable: bool = False):
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


def normalise_metadata(raw: dict[str, Any], fallback_category: str) -> dict[str, Any]:
    """Keep provider output bounded and stable before it reaches MongoDB or Qdrant."""
    def text(key: str, maximum: int = 120) -> str | None:
        value = raw.get(key)
        return value.strip()[:maximum] if isinstance(value, str) and value.strip() else None

    values = {
        "category": text("category") or fallback_category,
        "color": text("color"),
        "material": text("material"),
        "pattern": text("pattern"),
        "style": text("style"),
        "season": text("season"),
        "formality": text("formality"),
        "occasions": [str(value).strip()[:80] for value in raw.get("occasions", []) if str(value).strip()][:
            12
        ]
        if isinstance(raw.get("occasions"), list)
        else [],
        "tags": [str(value).strip()[:80] for value in raw.get("tags", []) if str(value).strip()][:20]
        if isinstance(raw.get("tags"), list)
        else [],
        "confidence": float(raw.get("confidence", 0.0)) if isinstance(raw.get("confidence"), (int, float)) else 0.0,
    }
    values["confidence"] = min(1.0, max(0.0, values["confidence"]))
    return values


def metadata_embedding_text(metadata: dict[str, Any]) -> str:
    fields: list[str] = []
    for key in ("category", "color", "material", "pattern", "style", "season", "formality"):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            fields.append(f"{key}: {value}")
    for key in ("occasions", "tags"):
        values = metadata.get(key)
        if isinstance(values, list) and values:
            fields.append(f"{key}: {', '.join(str(value) for value in values)}")
    return "; ".join(fields) or "category: unknown garment"


class RecommendationService:
    """Provider-neutral retrieval and deterministic outfit selection."""

    def __init__(self, intent_parser: OutfitIntentPort, embeddings: OpenAIWardrobePort, vectors: VectorStore):
        self.intent_parser = intent_parser
        self.embeddings = embeddings
        self.vectors = vectors

    def recommend(
        self, *, user_id: str, prompt: str, garments: Iterable[dict[str, Any]], limit: int = 12
    ) -> dict[str, Any]:
        parsed = self.intent_parser.parse_intent(prompt)
        intent = parsed.intent
        query_text = json.dumps(intent, ensure_ascii=False, sort_keys=True)
        matches = self.vectors.search_metadata(
            user_id=user_id, vector=self.embeddings.embed_text(query_text), limit=limit
        )
        by_id = {str(garment["id"]): garment for garment in garments}
        excluded = {str(value).lower() for value in intent.get("avoid_categories", []) if isinstance(value, str)}
        candidates: list[dict[str, Any]] = []
        for match in matches:
            garment = by_id.get(match.garment_id)
            if not garment or str(garment.get("catalog_status", "active")) != "active":
                continue
            if str(garment.get("category", "")).lower() in excluded:
                continue
            candidates.append({"garment": garment, "score": round(match.score, 6)})

        selected = self._select_complete_outfit(candidates)
        selected_ids = {item["garment"]["id"] for item in selected}
        missing_categories = self._missing_categories(selected)
        result = {
            "normalized_intent": intent,
            "intent_provider": parsed.provider,
            "candidates": [
                {"garment_id": item["garment"]["id"], "score": item["score"]} for item in candidates
            ],
            "outfit_garment_ids": [item["garment"]["id"] for item in selected],
            "missing_categories": missing_categories,
            "rationale": self._rationale(intent, selected, missing_categories),
            "used_candidate_ids": sorted(selected_ids),
        }
        if parsed.fallback_reason:
            result["intent_fallback_reason"] = parsed.fallback_reason
        return result

    @staticmethod
    def _select_complete_outfit(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        preferred_order = ("dress", "top", "bottom", "outerwear", "shoes", "bag", "accessory")
        selected: list[dict[str, Any]] = []
        used_slots: set[str] = set()
        for slot in preferred_order:
            for item in candidates:
                category = str(item["garment"].get("category") or "").lower()
                normalised = "accessory" if category in {"hat", "jewelry", "scarf"} else category
                if normalised == slot and slot not in used_slots:
                    selected.append(item)
                    used_slots.add(slot)
                    break
        if "dress" in used_slots:
            selected = [item for item in selected if str(item["garment"].get("category")).lower() != "bottom"]
        return selected

    @staticmethod
    def _missing_categories(selected: list[dict[str, Any]]) -> list[str]:
        categories = {str(item["garment"].get("category") or "").lower() for item in selected}
        if "dress" in categories:
            return ["shoes"] if "shoes" not in categories else []
        missing = [category for category in ("top", "bottom", "shoes") if category not in categories]
        return missing

    @staticmethod
    def _rationale(intent: dict[str, Any], selected: list[dict[str, Any]], missing: list[str]) -> str:
        if not selected:
            return "No indexed garments matched this request yet."
        occasion = intent.get("occasion") or "the requested occasion"
        if missing:
            return f"Matched available garments for {occasion}; add {', '.join(missing)} for a complete outfit."
        return f"Selected a complete outfit from your wardrobe for {occasion}."


def validate_vector(vector: list[float], dimensions: int, name: str) -> list[float]:
    if len(vector) != dimensions:
        raise ApplicationError(f"{name} provider returned {len(vector)} dimensions; expected {dimensions}")
    return vector
