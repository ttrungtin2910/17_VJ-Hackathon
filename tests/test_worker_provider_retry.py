from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from garblo.ai import AIProviderError
from garblo.models import AiJob, GarmentCandidate, MediaAsset
from garblo.worker import Claim, utcnow
from tests.helpers import catalogue


@pytest.mark.parametrize("provider", ["openai", "fpt"])
def test_bounded_backoff_keeps_provider_model(
    client, headers, asset_id, worker, providers, settings, db, provider
):
    settings.fpt_vlm_api_key = SecretStr("test-fpt")
    job_id = catalogue(client, headers, asset_id, {"vlm_provider": provider})
    providers.error = AIProviderError("Tạm lỗi", "provider_transient_error", retryable=True)
    for attempt in range(1, 4):
        assert worker.process_next()
        db.expire_all()
        job = db.get(AiJob, job_id)
        assert job.attempts == attempt
        assert job.status == ("queued" if attempt < 3 else "failed")
        if attempt < 3:
            assert job.next_attempt_at is not None
            assert not worker.process_next()  # Chưa đến lịch, không busy retry.
            job.next_attempt_at = utcnow() - timedelta(seconds=1)
            db.commit()
    assert [call[1] for call in providers.calls] == [provider] * 3
    assert client.post(f"/v1/jobs/{job_id}/retry", headers=headers).status_code == 409
    assert db.scalar(select(func.count()).select_from(GarmentCandidate)) == 0


def test_manual_retry_fpt_uses_original_snapshot(
    client, headers, asset_id, worker, providers, settings, db
):
    settings.fpt_vlm_api_key = SecretStr("test-fpt")
    job_id = catalogue(client, headers, asset_id, {"vlm_provider": "fpt"})
    providers.error = AIProviderError("Sai xác thực", "provider_auth_error")
    worker.process_next()
    providers.error = None
    settings.fpt_vlm_model = "different-model"
    response = client.post(f"/v1/jobs/{job_id}/retry", headers=headers)
    assert response.status_code == 202, response.text
    worker.process_next()
    db.expire_all()
    assert db.get(AiJob, job_id).status == "succeeded"
    assert [call[2] for call in providers.calls] == ["Qwen2.5-VL-7B-Instruct"] * 2


def test_reclaim_and_fencing(client, headers, asset_id, worker, db):
    job_id = catalogue(client, headers, asset_id)
    old = worker.claim_next()
    old_claim = Claim(
        old.id, old.user_id, old.job_type, old.model_provider, dict(old.input_ref), old.lock_token
    )
    old.locked_at = utcnow() - timedelta(seconds=1000)
    db.commit()
    assert worker.recover_expired() == 1
    new = worker.claim_next()
    assert new.lock_token != old_claim.token
    assert new.attempts == 2
    assert worker._owned_claim(old_claim, lock=True) is None
    db.rollback()
    with pytest.raises(AIProviderError) as error:
        worker._before_call(old_claim)
    assert error.value.code == "worker_lease_lost"
    assert db.get(AiJob, job_id).status == "running"


def test_legacy_without_snapshot_is_not_silently_moved_to_openai(
    client, headers, asset_id, db, worker, providers
):
    job_id = catalogue(client, headers, asset_id)
    job = db.get(AiJob, job_id)
    job.model_provider = "azure_openai"
    job.input_ref = {"asset_id": asset_id}
    db.commit()
    worker.process_next()
    db.expire_all()
    assert db.get(AiJob, job_id).error_code == "provider_not_configured"
    assert providers.calls == []


def test_provider_failure_sets_asset_status(client, headers, asset_id, worker, providers, db):
    catalogue(client, headers, asset_id)
    providers.error = AIProviderError("Sai output", "invalid_model_output")
    worker.process_next()
    db.expire_all()
    assert db.get(MediaAsset, asset_id).status == "analysis_failed"
