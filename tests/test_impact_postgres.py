from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select

from garblo.impact import WearEvent, now_utc
from garblo.models import Garment
from tests.helpers import auth_headers, upload
from tests.test_postgres_integration import postgres_runtime as runtime_fixture
from tests.test_recommendations import create, outfit_path

pytestmark = pytest.mark.integration
impact_runtime = pytest.fixture(runtime_fixture.__wrapped__)


def test_postgres_migration_and_concurrent_wear(impact_runtime, image_bytes):
    client = impact_runtime.client
    headers = auth_headers(client)
    user_id = client.get("/v1/me", headers=headers).json()["id"]
    asset = upload(client, headers, image_bytes)
    with impact_runtime.factory() as db:
        for category in ("shirt", "pants", "shoes"):
            db.add(
                Garment(
                    user_id=user_id,
                    source_asset_id=asset,
                    category=category,
                    color="black",
                    details_json={},
                )
            )
        db.commit()
    session = create(client, headers)
    path = outfit_path(session) + "/wear-events"
    payload = {"worn_on": str(now_utc().date()), "timezone": "UTC", "expected_revision": 1}
    barrier = Barrier(2)

    def record():
        barrier.wait(timeout=10)
        response = client.post(path, headers=headers, json=payload)
        assert response.status_code in (200, 201), response.text
        return response

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: record(), range(2)))
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert responses[0].json()["event"]["id"] == responses[1].json()["event"]["id"]
    with impact_runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(WearEvent)) == 1
        assert all(g.wear_count == 1 for g in db.scalars(select(Garment)))


def test_upgrade_backfills_existing_achievements(impact_runtime, image_bytes):
    from alembic import command
    from alembic.config import Config

    client = impact_runtime.client
    headers = auth_headers(client)
    user_id = client.get("/v1/me", headers=headers).json()["id"]
    asset = upload(client, headers, image_bytes)
    with impact_runtime.factory() as db:
        for category in ("shirt", "pants", "shoes"):
            db.add(
                Garment(
                    user_id=user_id,
                    source_asset_id=asset,
                    category=category,
                    color="black",
                    details_json={},
                )
            )
        db.commit()
    create(client, headers)
    command.downgrade(Config("alembic.ini"), "20261103_0006")
    command.upgrade(Config("alembic.ini"), "head")
    result = client.get("/v1/impact?timezone=UTC", headers=headers).json()
    assert result["badges"][0]["unlocked"]
    assert result["wearCount"] == 0
    assert result["outfitCount"] > 0
