from datetime import timedelta

import pytest
from sqlalchemy import delete

from garblo.impact import now_utc
from garblo.models import Garment
from garblo.recommendations.models import RecommendedOutfit
from tests.helpers import auth_headers
from tests.test_recommendations import create, outfit_path
from tests.test_recommendations import wardrobe as wardrobe_fixture

impact_wardrobe = pytest.fixture(wardrobe_fixture.__wrapped__)


def test_wear_idempotency_and_history(client, headers, impact_wardrobe, db):
    impact_wardrobe()
    session = create(client, headers)
    path = outfit_path(session) + "/wear-events"
    payload = {"worn_on": str(now_utc().date()), "timezone": "UTC", "expected_revision": 1}
    first = client.post(path, headers=headers, json=payload)
    assert first.status_code == 201, first.text
    assert "first-wear" in first.json()["new_achievements"]
    repeat = client.post(path, headers=headers, json={**payload, "expected_revision": 99})
    assert repeat.status_code == 200
    assert repeat.json()["event"]["id"] == first.json()["event"]["id"]
    assert repeat.json()["new_achievements"] == []
    other = auth_headers(client, "other-impact@example.com")
    assert client.post(path, headers=other, json=payload).status_code == 404
    assert client.get("/v1/wear-events", headers=other).json() == []
    summary = client.get("/v1/impact?timezone=UTC", headers=headers).json()
    assert summary["wearCount"] == 1
    assert summary["week"]["tasks"][0]["progress"] == 1
    db.execute(delete(RecommendedOutfit))
    db.execute(delete(Garment))
    db.commit()
    summary = client.get("/v1/impact?timezone=UTC", headers=headers).json()
    assert summary["wearCount"] == 1
    assert summary["wornItemCount"] > 0
    assert summary["utilizationPercent"] == 0
    assert summary["badges"][0]["unlocked"]


def test_validation_revision_and_weekly_days(client, headers, impact_wardrobe):
    impact_wardrobe()
    session = create(client, headers)
    path = outfit_path(session) + "/wear-events"
    today = now_utc().date()
    payload = {"worn_on": str(today), "timezone": "UTC", "expected_revision": 99}
    assert client.post(path, headers=headers, json=payload).status_code == 409
    payload["expected_revision"] = 1
    assert (
        client.post(path, headers=headers, json={**payload, "timezone": "bad/zone"}).status_code
        == 422
    )
    assert (
        client.post(
            path, headers=headers, json={**payload, "worn_on": str(today + timedelta(days=1))}
        ).status_code
        == 422
    )
    for days in range(3):
        assert (
            client.post(
                path,
                headers=headers,
                json={**payload, "worn_on": str(today - timedelta(days=days))},
            ).status_code
            == 201
        )
    result = client.get("/v1/impact?timezone=UTC", headers=headers).json()
    assert result["wearCount"] == 3
    assert result["week"]["tasks"][0]["progress"] == min(3, today.weekday() + 1)
    assert result["week"]["tasks"][1]["progress"] == 1


def test_weekly_completion_and_rollover(client, headers, impact_wardrobe, monkeypatch):
    from datetime import datetime, timezone

    import garblo.impact as impact

    impact_wardrobe()
    session = create(client, headers)
    now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(impact, "now_utc", lambda: now)
    for index, day in enumerate(("2026-11-02", "2026-11-03", "2026-11-04")):
        outfit = session["outfits"][min(index, len(session["outfits"]) - 1)]
        response = client.post(
            outfit_path(session, outfit) + "/wear-events",
            headers=headers,
            json={"worn_on": day, "timezone": "UTC", "expected_revision": 1},
        )
        assert response.status_code == 201
    summary = client.get("/v1/impact?timezone=UTC", headers=headers).json()
    assert summary["week"]["tasks"][0]["completed"]
    assert summary["week"]["tasks"][1]["completed"]
    now = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
    summary = client.get("/v1/impact?timezone=UTC", headers=headers).json()
    assert all(task["progress"] == 0 for task in summary["week"]["tasks"])
    assert next(b for b in summary["badges"] if b["id"] == "first-wear")["unlocked"]
