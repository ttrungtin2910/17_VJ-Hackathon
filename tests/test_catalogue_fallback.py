from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from garblo.ai import AIProviderError
from garblo.ai_providers.contracts import upstream_error
from garblo.models import AiCallAttempt, AiJob
from garblo.worker import utcnow
from tests.helpers import catalogue


def setup_policy(settings):
    settings.catalogue_fpt_fallback_enabled = True
    settings.fpt_vlm_api_key = SecretStr("fpt-test-only")


def run_attempts(worker, db, providers, job_id, count=3):
    for _ in range(count):
        providers.error = AIProviderError("Tạm lỗi", "provider_transient_error", retryable=True)
        assert worker.process_next()
        db.expire_all()
        job = db.get(AiJob, job_id)
        job.next_attempt_at = utcnow() - timedelta(seconds=1)
        db.commit()


def test_transient_openai_failure_can_use_fpt(
    client, headers, asset_id, worker, providers, settings, db
):
    setup_policy(settings)
    job_id = catalogue(client, headers, asset_id)
    original = dict(db.get(AiJob, job_id).input_ref)
    run_attempts(worker, db, providers, job_id, 2)
    providers.error = None
    settings.fpt_vlm_model = "do-not-use-new-config"
    assert worker.process_next()
    db.expire_all()
    job = db.get(AiJob, job_id)
    assert job.status == "succeeded"
    assert job.model_provider == "openai" and job.input_ref == original
    assert job.output_ref["effective_provider"] == "fpt"
    assert job.output_ref["effective_model"] == "Qwen2.5-VL-7B-Instruct"
    assert [call[1] for call in providers.calls] == ["openai", "openai", "fpt"]
    calls = list(db.scalars(select(AiCallAttempt).order_by(AiCallAttempt.call_index)))
    assert [call.provider for call in calls if call.stage.startswith("analysis")] == [
        "openai",
        "openai",
        "fpt",
    ]
    calls = [call for call in calls if call.stage.startswith("analysis")]
    assert [call.worker_attempt for call in calls] == [1, 2, 3]
    assert calls[-1].model == "Qwen2.5-VL-7B-Instruct"
    assert "test-only" not in str(original)


def test_missing_fpt_key_stays_openai_only(
    client, headers, asset_id, worker, providers, settings, db, caplog
):
    settings.catalogue_fpt_fallback_enabled = True
    job_id = catalogue(client, headers, asset_id)
    assert "vision_fallback" not in db.get(AiJob, job_id).input_ref
    run_attempts(worker, db, providers, job_id)
    assert [call[1] for call in providers.calls] == ["openai"] * 3
    assert db.get(AiJob, job_id).status == "failed"


@pytest.mark.parametrize(
    "code",
    [
        "provider_auth_error",
        "provider_refused",
        "consent_revoked",
        "invalid_source_image",
        "invalid_model_output",
        "provider_billing_error",
        "worker_lease_lost",
    ],
)
def test_auth_safety_consent_and_invalid_input_never_fallback(
    client, headers, asset_id, worker, providers, settings, db, code
):
    setup_policy(settings)
    job_id = catalogue(client, headers, asset_id)
    providers.error = AIProviderError("Không được đổi provider", code)
    worker.process_next()
    assert db.get(AiJob, job_id).status == "failed"
    assert [call[1] for call in providers.calls] == ["openai"]


def test_fallback_preserves_budget_and_fencing(
    client, headers, asset_id, worker, providers, settings, db
):
    setup_policy(settings)
    settings.catalogue_max_calls = 2
    job_id = catalogue(client, headers, asset_id)
    run_attempts(worker, db, providers, job_id, 2)
    providers.error = None
    worker.process_next()
    db.expire_all()
    assert db.get(AiJob, job_id).error_code == "model_call_budget_exceeded"
    assert len(providers.calls) == 2
    assert len(list(db.scalars(select(AiCallAttempt)))) == 2


def test_legacy_job_does_not_gain_fallback(
    client, headers, asset_id, worker, providers, settings, db
):
    job_id = catalogue(client, headers, asset_id)
    setup_policy(settings)
    run_attempts(worker, db, providers, job_id)
    assert [call[1] for call in providers.calls] == ["openai"] * 3


@pytest.mark.parametrize("maximum", [1, 2])
def test_small_attempt_budget_is_not_increased(
    client, headers, asset_id, worker, providers, settings, db, maximum
):
    setup_policy(settings)
    settings.worker_max_attempts = maximum
    job_id = catalogue(client, headers, asset_id)
    run_attempts(worker, db, providers, job_id, maximum)
    assert db.get(AiJob, job_id).attempts == maximum
    assert db.get(AiJob, job_id).status == "failed"
    assert [call[1] for call in providers.calls] == ["openai"] * maximum


def test_429_billing_is_not_transient():
    error = upstream_error(429, "insufficient_quota")
    assert error.code == "provider_billing_error" and not error.retryable
    assert upstream_error(429, "rate_limit_exceeded").retryable


@pytest.mark.parametrize("guard", ["consent", "token", "deadline", "lease"])
def test_fallback_physical_call_still_checks_all_guards(
    client, headers, asset_id, worker, providers, settings, db, monkeypatch, guard
):
    setup_policy(settings)
    job_id = catalogue(client, headers, asset_id)
    run_attempts(worker, db, providers, job_id, 2)
    if guard == "consent":
        from garblo.consent import latest_consent

        user_id = db.get(AiJob, job_id).user_id
        latest_consent(db, user_id, "ai_processing").revoked_at = utcnow()
    elif guard == "token":
        job = db.get(AiJob, job_id)
        job.input_ref = {**job.input_ref, "token_budget": 2 * 16384}
    elif guard == "deadline":
        db.get(AiJob, job_id).execution_deadline_at = utcnow() - timedelta(seconds=1)
    elif guard == "lease":
        before_call = worker._before_call

        def lost(claim):
            db.get(AiJob, job_id).lock_token = "new-owner"
            db.commit()
            before_call(claim)

        monkeypatch.setattr(worker, "_before_call", lost)
    db.commit()
    providers.error = None
    worker.process_next()
    assert len(providers.calls) == 2
    assert len(list(db.scalars(select(AiCallAttempt)))) == 2


def test_schema_repair_failure_history_does_not_authorize_fallback(
    client, headers, asset_id, worker, providers, settings, db
):
    setup_policy(settings)
    job_id = catalogue(client, headers, asset_id)
    run_attempts(worker, db, providers, job_id, 2)
    first = db.scalar(select(AiCallAttempt).where(AiCallAttempt.worker_attempt == 1))
    first.validation_status = "invalid"
    db.commit()
    providers.error = None
    worker.process_next()
    assert [call[1] for call in providers.calls] == ["openai"] * 3


@pytest.mark.parametrize(
    "error_type,expected",
    [("insufficient_quota", "provider_billing_error"), ("unknown", "provider_rate_limit_unknown")],
)
def test_fallback_snapshot_rejects_billing_type_and_opaque_429(
    settings, image_bytes, error_type, expected
):
    import httpx
    from openai import OpenAI

    from garblo.ai_providers.openai_direct import OpenAIDirectProvider
    from garblo.jobs import new_snapshot

    setup_policy(settings)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429, json={"error": {"type": error_type, "message": "raw body không được lộ"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAIDirectProvider(
            settings, "test-vision", client=OpenAI(api_key="test-only", http_client=http)
        )
        provider.configure_catalogue(new_snapshot(settings, "openai", "catalogue"))
        with pytest.raises(AIProviderError) as error:
            provider.analyze(image_bytes, "image/png", lambda: None)
    assert error.value.code == expected
    assert not error.value.retryable
    assert len(calls) == 1
