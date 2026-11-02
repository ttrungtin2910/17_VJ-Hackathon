from __future__ import annotations

from typing import Any


def outfit_intent_instruction(prompt: str) -> str:
    """Return the provider-neutral contract for wardrobe-search intent parsing."""
    return (
        "Extract a wardrobe-search intent from the user's request. Return JSON only, with keys: "
        "occasion (string|null), style (string|null), colors (array of strings), formality (string|null), "
        "required_categories (array of strings), avoid_categories (array of strings), "
        "clarification_needed (boolean). Never include reasoning. User request: "
        + prompt
    )


def normalise_outfit_intent(raw: dict[str, Any]) -> dict[str, Any]:
    """Bound intent fields before they are embedded, persisted, or returned."""

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
