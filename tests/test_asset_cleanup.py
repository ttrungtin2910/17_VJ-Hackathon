from datetime import timedelta

import pytest
from sqlalchemy import select

from garblo.ai import AIProviderError
from garblo.asset_cleanup import TEMPORARY_INPUT, cleanup_next
from garblo.models import AiJob, AssetCleanup, GarmentCandidate, MediaAsset, now_utc
from tests.conftest import ATTRIBUTES
from tests.helpers import catalogue
from tests.test_catalogue_pipeline import v2_provider
from tests.test_upload_recovery import keyed_upload


@pytest.mark.parametrize("keyed", [False, True])
def test_new_upload_disposes_source_after_components_commit(
    client, headers, asset_id, worker, db, storage, image_bytes, keyed
):
    if keyed:
        asset_id = keyed_upload(client, headers, image_bytes).json()["id"]
    source = db.get(MediaAsset, asset_id)
    assert source.metadata_json["retention_policy"] == TEMPORARY_INPUT
    v2_provider(worker, [dict(ATTRIBUTES)])
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    db.expire_all()
    assert db.get(AiJob, job_id).status == "succeeded"
    assert source.object_key in storage.objects
    assert db.get(AssetCleanup, asset_id)
    assert client.get(f"/v1/assets/{asset_id}/download", headers=headers).status_code == 410
    candidate = db.scalar(select(GarmentCandidate).where(GarmentCandidate.asset_id == asset_id))
    component_key = db.get(MediaAsset, candidate.component_asset_id).object_key
    assert component_key in storage.objects
    assert cleanup_next(db, storage)
    assert source.object_key not in storage.objects
    assert component_key in storage.objects
    assert (
        client.post(
            f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": [candidate.id]}
        ).status_code
        == 200
    )
    assert client.get(f"/v1/assets/{asset_id}", headers=headers).json()["status"] == "catalogued"
    assert not cleanup_next(db, storage)


def test_cleanup_retry_survives_new_session_without_inference(
    client, headers, asset_id, worker, db_factory, storage, monkeypatch
):
    v2_provider(worker, [dict(ATTRIBUTES)])
    catalogue(client, headers, asset_id)
    worker.process_next()
    original = storage.delete
    monkeypatch.setattr(storage, "delete", lambda key: (_ for _ in ()).throw(RuntimeError()))
    with db_factory() as db:
        assert cleanup_next(db, storage)
        task = db.get(AssetCleanup, asset_id)
        assert task.attempts == 1 and task.completed_at is None
        task.next_attempt_at = now_utc() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(storage, "delete", original)
    with db_factory() as db:
        assert cleanup_next(db, storage)
        assert db.get(AssetCleanup, asset_id).completed_at
        assert db.scalar(select(AiJob.attempts)) == 1


@pytest.mark.parametrize("legacy", [False, True])
def test_empty_result_cleanup_respects_legacy_policy(
    client, headers, asset_id, worker, db, storage, legacy
):
    if legacy:
        db.get(MediaAsset, asset_id).metadata_json = {}
        db.commit()
    v2_provider(worker, [])
    catalogue(client, headers, asset_id)
    worker.process_next()
    db.expire_all()
    assert db.get(MediaAsset, asset_id).status == "no_items"
    assert bool(db.get(AssetCleanup, asset_id)) is not legacy
    assert cleanup_next(db, storage) is not legacy
    assert bool(storage.objects) is legacy


def test_transient_failure_keeps_input_until_attempt_budget_exhausted(
    client, headers, asset_id, worker, db, storage, providers, settings
):
    providers.error = AIProviderError("Timeout", "provider_timeout", retryable=True)
    job_id = catalogue(client, headers, asset_id)
    for attempt in range(1, settings.worker_max_attempts + 1):
        worker.process_next()
        db.expire_all()
        job = db.get(AiJob, job_id)
        assert job.attempts == attempt
        assert bool(db.get(AssetCleanup, asset_id)) == (attempt == settings.worker_max_attempts)
        job.next_attempt_at = None
        db.commit()
    assert cleanup_next(db, storage)
    assert db.get(MediaAsset, asset_id).object_key not in storage.objects


@pytest.mark.parametrize("failure", ["missing_component", "storage_failure"])
def test_failed_component_save_never_schedules_input_cleanup(
    client, headers, asset_id, worker, db, storage, monkeypatch, failure
):
    v2_provider(worker, [dict(ATTRIBUTES)])
    if failure == "missing_component":
        from garblo.ai_providers.contracts import Analysis
        from garblo.catalogue.contracts import AnalysisResultV2

        result = Analysis(
            AnalysisResultV2.model_validate(
                {
                    "schema_version": 2,
                    "image_type": "single_garment",
                    "items": [dict(ATTRIBUTES)],
                }
            )
        )
        monkeypatch.setattr(worker, "_analyze", lambda *args: result)
    else:

        def fail_save(*args, **kwargs):
            raise RuntimeError("storage unavailable")

        monkeypatch.setattr(worker, "_save_component_asset", fail_save)
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    db.expire_all()
    assert db.get(AiJob, job_id).status == "failed"
    assert not db.get(AssetCleanup, asset_id)
    assert not list(db.scalars(select(GarmentCandidate)))
    assert db.get(MediaAsset, asset_id).object_key in storage.objects
