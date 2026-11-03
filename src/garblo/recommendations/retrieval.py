"""Hồ sơ văn bản xác định và tìm món theo embedding."""

from __future__ import annotations

import math

from garblo.models import Garment
from garblo.recommendations import engine

PROFILE_VERSION = 1


def profile_text(garment: Garment) -> str:
    details = garment.details_json or {}
    uncertain = set(details.get("uncertain_fields") or [])
    values: list[str] = []
    for field, value in (
        ("display_name", details.get("display_name")),
        ("category", garment.category),
        ("subcategory", details.get("subcategory")),
        ("color", garment.color),
        ("material", garment.material),
        ("pattern", details.get("pattern")),
        ("fit", details.get("fit")),
        ("season", garment.season),
    ):
        if value and field not in uncertain:
            values.append(str(value))
    for field, values_field in (("tags", garment.tags), ("styles", details.get("styles", [])),
                                ("seasons", details.get("seasons", []))):
        if field not in uncertain and isinstance(values_field, list):
            values.extend(str(value) for value in values_field if value)
    return " · ".join(dict.fromkeys(values))[:1200]


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def rank_garments(
    garments: list[Garment],
    query: str,
    query_embedding: list[float] | None,
    limit: int = 48,
    embedding_model: str | None = None,
) -> list[Garment]:
    normalized_query = engine.normalize(query)
    scored: list[tuple[float, str, Garment]] = []
    for garment in garments:
        profile = garment.retrieval_json or {}
        text = profile.get("text") or profile_text(garment)
        normalized_text = engine.normalize(text)
        overlap = sum(1 for token in set(normalized_query.split()) if len(token) > 2 and token in normalized_text)
        lexical = overlap / max(1, len(set(normalized_query.split())))
        vector = profile.get("embedding")
        same_model = not embedding_model or profile.get("embedding_model") == embedding_model
        semantic = cosine(query_embedding, vector) if query_embedding and same_model and isinstance(vector, list) else 0.0
        scored.append((semantic * 0.8 + lexical * 0.2, garment.id, garment))
    scored.sort(key=lambda row: (-row[0], row[1]))
    selected: list[Garment] = []
    per_role: dict[str, int] = {}
    for _, _, garment in scored:
        role = engine.category_role(garment.category)
        if role is None or per_role.get(role, 0) >= 8:
            continue
        selected.append(garment)
        per_role[role] = per_role.get(role, 0) + 1
        if len(selected) >= limit:
            break
    return selected




