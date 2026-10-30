from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from garblo.ai import AIProviderError
from garblo.ai_providers.contracts import Analysis
from garblo.catalogue.contracts import AnalysisResultV2
from garblo.models import AiCallAttempt, AiJob, Garment, MediaAsset
from tests.conftest import ATTRIBUTES
from tests.helpers import auth_headers, catalogue, upload


def v2_provider(worker, items, callback=None):
    class Provider:
        def configure_catalogue(self, snapshot):
            self.snapshot = snapshot

        def analyze(self, image, mime_type, before_call):
            before_call()
            self.on_result(
                {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}, 5, None
            )
            if callback:
                callback()
            return Analysis(
                AnalysisResultV2.model_validate(
                    {
                        "schema_version": 2,
                        "image_type": "outfit",
                        "items": items,
                    }
                )
            )

    worker.providers = SimpleNamespace(resolve=lambda *args: Provider())


def test_multi_review_partial_commit_reject_restore_and_durable_ids(
    client, headers, asset_id, worker, db
):
    items = [dict(ATTRIBUTES), {**ATTRIBUTES, "category": "pants", "details": {"pattern": "solid"}}]
    v2_provider(worker, items)
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    result = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert result["status"] == "succeeded", result
    ids = result["output_ref"]["candidate_ids"]
    assert len(ids) == 2
    assert client.get("/v1/garments", headers=headers).json() == []
    first = client.post(
        f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": ids[:1]}
    )
    assert first.status_code == 200
    assert db.get(MediaAsset, asset_id).status == "partially_catalogued"
    assert (
        client.patch(
            f"/v1/candidates/{ids[1]}", headers=headers, json={"status": "rejected"}
        ).status_code
        == 200
    )
    assert db.get(MediaAsset, asset_id).status == "catalogued"
    assert (
        client.post(
            f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": ids}
        ).status_code
        == 409
    )
    assert db.scalar(select(func.count()).select_from(Garment)) == 1
    assert (
        client.patch(
            f"/v1/candidates/{ids[1]}",
            headers=headers,
            json={"status": "draft", "details": {"display_name": "Quần đi làm"}},
        ).status_code
        == 200
    )
    commit = client.post(
        f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": ids}
    )
    again = client.post(
        f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": ids}
    )
    assert commit.status_code == again.status_code == 200
    assert commit.json() == again.json()
    assert first.json()["garment_ids"][0] == commit.json()["garment_ids"][0]
    garments = client.get("/v1/garments", headers=headers).json()
    assert len(garments) == 2
    pants = next(g for g in garments if g["category"] == "pants")
    assert pants["details"]["display_name"] == "Quần đi làm"
    assert pants["details"]["pattern"] == "solid"
    calls = client.get(f"/v1/jobs/{job_id}/calls", headers=headers).json()
    assert len(calls) == 1 and calls[0]["usage"]["total_tokens"] == 30
    assert calls[0]["prompt_version"] == "catalogue-v2"
    other = auth_headers(client, "ledger-other@example.com")
    assert client.get(f"/v1/jobs/{job_id}/calls", headers=other).status_code == 404


def test_unknown_category_blocks_entire_selection_before_write(
    client, headers, asset_id, worker, db
):
    v2_provider(worker, [dict(ATTRIBUTES), {**ATTRIBUTES, "category": None}])
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    ids = client.get(f"/v1/jobs/{job_id}", headers=headers).json()["output_ref"]["candidate_ids"]
    assert (
        client.post(
            f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": ids}
        ).status_code
        == 422
    )
    assert db.scalar(select(func.count()).select_from(Garment)) == 0


def test_ledger_budget_survives_manual_retry(client, headers, asset_id, worker, db, settings):
    settings.catalogue_max_calls = 1

    class Provider:
        def analyze(self, image, mime_type, before_call):
            before_call()
            self.on_result({}, 1, "provider_auth_error")
            raise AIProviderError("Không xác thực", "provider_auth_error")

    worker.providers = SimpleNamespace(resolve=lambda *args: Provider())
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    assert client.post(f"/v1/jobs/{job_id}/retry", headers=headers).status_code == 202
    worker.process_next()
    job = db.get(AiJob, job_id)
    assert job.status == "failed" and job.error_code == "model_call_budget_exceeded"
    assert db.scalar(select(func.count()).select_from(AiCallAttempt)) == 1


def test_consent_revoked_after_inference_discards_all_candidates(
    client, headers, asset_id, worker, db
):
    from garblo.models import ConsentRecord, GarmentCandidate

    consent = db.scalar(select(ConsentRecord))
    v2_provider(
        worker,
        [dict(ATTRIBUTES)],
        lambda: client.delete(f"/v1/consents/{consent.id}", headers=headers),
    )
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    assert db.get(AiJob, job_id).error_code == "consent_revoked"
    assert db.scalar(select(func.count()).select_from(GarmentCandidate)) == 0
    assert db.scalar(select(func.count()).select_from(AiCallAttempt)) == 1


def test_optional_checksum_dedup_is_owner_scoped(client, headers, image_bytes, settings):
    settings.catalogue_deduplicate_uploads = True
    first = upload(client, headers, image_bytes)
    assert upload(client, headers, image_bytes) == first
    other = auth_headers(client, "checksum-other@example.com")
    assert upload(client, other, image_bytes) != first


def test_token_reservation_blocks_before_any_request(
    client, headers, asset_id, worker, providers, settings, db
):
    settings.catalogue_token_budget = 1024
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    assert db.get(AiJob, job_id).error_code == "token_budget_exceeded"
    assert providers.calls == []
    assert db.scalar(select(func.count()).select_from(AiCallAttempt)) == 0


def test_unknown_usage_keeps_reservation_across_retry(
    client, headers, asset_id, worker, providers, settings, db
):
    settings.catalogue_token_budget = settings.catalogue_call_token_reservation
    providers.error = AIProviderError("Lỗi provider", "provider_auth_error")
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    assert len(providers.calls) == 1
    assert client.post(f"/v1/jobs/{job_id}/retry", headers=headers).status_code == 202
    worker.process_next()
    assert db.get(AiJob, job_id).error_code == "token_budget_exceeded"
    assert len(providers.calls) == 1


def test_deadline_persists_across_retry(client, headers, asset_id, worker, providers, db):
    from datetime import datetime, timedelta, timezone

    providers.error = AIProviderError("Lỗi provider", "provider_auth_error")
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    job = db.get(AiJob, job_id)
    assert job.execution_deadline_at is not None
    job.execution_deadline_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert client.post(f"/v1/jobs/{job_id}/retry", headers=headers).status_code == 202
    worker.process_next()
    assert db.get(AiJob, job_id).error_code == "job_deadline_exceeded"
    assert len(providers.calls) == 1


def test_repair_audit_records_usage_validation_and_stage(
    client, headers, asset_id, worker, settings
):
    import json

    import httpx
    from openai import OpenAI

    from garblo.ai_providers.openai_direct import OpenAIDirectProvider
    from tests.test_openai_direct import completion

    responses = iter(
        [
            "not-json",
            json.dumps(
                {"schema_version": 2, "image_type": "single_garment", "items": [ATTRIBUTES]}
            ),
        ]
    )

    def handler(request):
        result = completion(next(responses))
        result["usage"] = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        return httpx.Response(200, json=result)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAIDirectProvider(
            settings, "test-vision", client=OpenAI(api_key="test", http_client=http, max_retries=0)
        )
        worker.providers = SimpleNamespace(resolve=lambda *args: provider)
        job_id = catalogue(client, headers, asset_id)
        worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "succeeded", job
    calls = client.get(f"/v1/jobs/{job_id}/calls", headers=headers).json()
    assert [call["stage"] for call in calls] == ["analysis", "analysis_repair"]
    assert [call["validation_status"] for call in calls] == ["invalid", "valid"]
    assert [call["usage"]["total_tokens"] for call in calls] == [30, 30]


def test_metrics_are_owner_scoped_and_do_not_invent_cost_or_accuracy(
    client, headers, asset_id, worker
):
    v2_provider(worker, [dict(ATTRIBUTES)])
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    candidate_id = client.get(f"/v1/jobs/{job_id}", headers=headers).json()["output_ref"][
        "candidate_ids"
    ][0]
    client.patch(f"/v1/candidates/{candidate_id}", headers=headers, json={"color": "red"})
    result = client.get("/v1/me/ai-metrics", headers=headers).json()
    assert result["window"]["jobs"] == 1 and result["calls"] == 1
    assert result["tokens"]["total_tokens"] == 30
    assert result["human_correction_rate"] == 1
    assert result["cost_usd"] is result["quality_accuracy"] is None
    other = auth_headers(client, "metrics-other@example.com")
    result = client.get("/v1/me/ai-metrics", headers=other).json()
    assert result["window"]["jobs"] == 0 and result["calls"] == 0


def test_optional_stages_persist_segment_and_mask_lineage(
    client, headers, asset_id, worker, settings, image_bytes, db, monkeypatch
):
    import base64
    import json

    import httpx

    from garblo.catalogue import stages
    from garblo.models import GarmentCandidate

    settings.catalogue_screening_model = "screen-test"
    settings.catalogue_escalation_model = "escalation-test"
    settings.catalogue_segmentation_url = "http://segmenter.test/analyze"
    settings.catalogue_segmentation_model = "segment-test"

    class Provider:
        def __init__(self, model):
            self.model = model

        def configure_catalogue(self, snapshot):
            pass

        def analyze(self, image, mime, before_call):
            before_call()
            self.on_result({"total_tokens": 30}, 5, None)
            self.on_validation(None)
            items = [dict(ATTRIBUTES), {**ATTRIBUTES, "category": "pants"}]
            if self.model == "test-vision":
                items[0]["category"] = None
            return Analysis(AnalysisResultV2(schema_version=2, image_type="outfit", items=items))

        def close(self):
            pass

    worker.providers = SimpleNamespace(resolve=lambda provider, model, image_model: Provider(model))
    real_http = httpx.Client

    def segment(request):
        payload = json.loads(request.content)
        assert payload["model"] == "segment-test"
        encoded = base64.b64encode(image_bytes).decode()
        return httpx.Response(
            200,
            json={
                "components": [
                    {"candidate_index": index, "image_base64": encoded, "mask_base64": encoded}
                    for index in range(2)
                ]
            },
        )

    monkeypatch.setattr(
        stages.httpx,
        "Client",
        lambda **kwargs: real_http(transport=httpx.MockTransport(segment), **kwargs),
    )
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "succeeded", job
    assert job["output_ref"]["analysis_model"] == "escalation-test"
    candidates = list(db.scalars(select(GarmentCandidate)))
    assert len(candidates) == 2
    for candidate in candidates:
        component = db.get(MediaAsset, candidate.component_asset_id)
        mask = db.get(MediaAsset, candidate.review_json["mask_asset_id"])
        assert component.parent_asset_id == mask.parent_asset_id == asset_id
        assert component.metadata_json["mask_asset_id"] == mask.id
        assert mask.asset_type == "mask" and component.asset_type == "component"
    calls = client.get(f"/v1/jobs/{job_id}/calls", headers=headers).json()
    assert [call["stage"] for call in calls] == [
        "screening",
        "analysis",
        "escalation",
        "segmentation",
    ]
    assert calls[-1]["provider"] == "segmentation_http"
    assert client.get("/v1/garments", headers=headers).json() == []


@pytest.mark.parametrize("provider_name", ["openai", "fpt"])
@pytest.mark.parametrize("refused", [False, True])
def test_truncation_is_audited_as_repair_but_refusal_is_not_retried(
    client, headers, asset_id, worker, settings, provider_name, refused
):
    import json

    import httpx
    from openai import OpenAI
    from pydantic import SecretStr

    from garblo.ai_providers.fpt_qwen import FPTQwenAnalyzer
    from garblo.ai_providers.openai_direct import OpenAIDirectProvider
    from tests.test_openai_direct import completion

    settings.fpt_vlm_api_key = SecretStr("test-fpt-key")
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        response = completion(
            json.dumps({"schema_version": 2, "image_type": "single_garment", "items": [ATTRIBUTES]})
        )
        response["usage"] = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        if count == 1:
            if refused:
                response["choices"][0]["message"]["refusal"] = "private refusal"
            else:
                response["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=response)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider = (
            OpenAIDirectProvider(
                settings,
                "test-vision",
                client=OpenAI(api_key="test", http_client=http, max_retries=0),
            )
            if provider_name == "openai"
            else FPTQwenAnalyzer(settings, settings.fpt_vlm_model, client=http)
        )
        worker.providers = SimpleNamespace(resolve=lambda *args: provider)
        job_id = catalogue(client, headers, asset_id, {"vlm_provider": provider_name})
        worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    calls = client.get(f"/v1/jobs/{job_id}/calls", headers=headers).json()
    if refused:
        assert job["status"] == "failed" and job["error_code"] == "provider_refused"
        assert count == len(calls) == 1
    else:
        assert job["status"] == "succeeded", job
        assert [call["stage"] for call in calls] == ["analysis", "analysis_repair"]
        assert [call["validation_status"] for call in calls] == ["invalid", "valid"]
