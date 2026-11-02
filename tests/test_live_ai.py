"""Opt-in smoke tests. They are skipped unless RUN_LIVE_AI_TESTS=1 is explicitly set."""

from __future__ import annotations

import os

import pytest

from garblo.config import Settings
from garblo.infrastructure.fpt import FPTIntentProvider
from garblo.infrastructure.openai_provider import OpenAIWardrobeProvider


pytestmark = pytest.mark.live_ai


def _enabled() -> bool:
    return os.getenv("RUN_LIVE_AI_TESTS") == "1"


@pytest.mark.skipif(not _enabled(), reason="set RUN_LIVE_AI_TESTS=1 to run paid-provider smoke tests")
def test_live_fpt_intent_and_openai_large_embedding() -> None:
    settings = Settings()
    if not settings.fpt_is_configured or not settings.openai_is_configured:
        pytest.skip("FPT_API_KEY and OPEN_AI_API_KEY are both required")
    intent = FPTIntentProvider(settings).parse_intent("a smart casual outfit for a rainy work day")
    assert set(intent) >= {"occasion", "avoid_categories", "clarification_needed"}
    vector = OpenAIWardrobeProvider(settings).embed_text("navy cotton blazer, smart casual, office")
    assert len(vector) == settings.metadata_embedding_dimensions == 3072
