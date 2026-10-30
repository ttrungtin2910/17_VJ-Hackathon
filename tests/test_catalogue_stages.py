import base64
import copy
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from garblo.ai import AIProviderError
from garblo.ai_providers.contracts import Analysis, AnalysisResult
from garblo.catalogue import stages
from garblo.catalogue.contracts import AnalysisResultV2, Component, ComponentDetails


def result(*, count=1, category="áo", color="xanh", uncertain=(), image_type=None):
    return Analysis(
        AnalysisResultV2(
            schema_version=2,
            image_type=image_type or ("outfit" if count > 1 else "single_garment"),
            items=[
                Component(
                    category=category,
                    color=color,
                    material=None,
                    season=None,
                    tags=[],
                    details=ComponentDetails(uncertain_fields=list(uncertain)),
                )
                for _ in range(count)
            ],
        ),
        {"usage": {"total_tokens": 7}},
    )


class Ledger:
    def __init__(self, provider="fpt"):
        self.provider = provider
        self.stage = "analysis"
        self.model = "main"
        self.reservations = []
        self.results = []
        self.validations = []
        self.error = None

    def before_call(self):
        if self.error:
            raise self.error
        self.reservations.append((self.stage, self.provider, self.model))

    def on_result(self, usage, latency, error_code=None):
        self.results.append((self.stage, self.provider, self.model, usage, latency, error_code))

    def on_validation(self, error_code=None):
        self.validations.append((self.stage, error_code))


class Provider:
    def __init__(self, outcome, *, configure_error=None):
        self.outcome = outcome
        self.configure_error = configure_error
        self.snapshots = []
        self.calls = []
        self.closed = False
        self.on_result = None
        self.on_validation = None

    def configure_catalogue(self, snapshot):
        self.snapshots.append(dict(snapshot))
        if self.configure_error:
            raise self.configure_error

    def analyze(self, image, mime, before_call):
        before_call()
        self.calls.append((image, mime))
        if isinstance(self.outcome, Exception):
            self.on_result({}, 3, self.outcome.code)
            raise self.outcome
        self.on_result({"total_tokens": 7}, 3, None)
        self.on_validation(None)
        return self.outcome

    def close(self):
        self.closed = True


class Registry:
    def __init__(self, providers):
        self.providers = providers
        self.calls = []

    def resolve(self, provider, model, image_model):
        self.calls.append((provider, model, image_model))
        value = self.providers[model]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture()
def context(image_bytes):
    snapshot = {"model": "main", "schema_version": 2, "prompt_version": "catalogue-v2"}
    claim = SimpleNamespace(provider="fpt", ref=snapshot)
    registry = Registry({})
    worker = SimpleNamespace(
        providers=registry,
        settings=SimpleNamespace(max_upload_bytes=1024 * 1024, max_image_pixels=1024 * 1024),
    )
    return SimpleNamespace(
        worker=worker,
        claim=claim,
        registry=registry,
        ledger=Ledger(),
        image=image_bytes,
        primary=Provider(result()),
    )


def run(context):
    context.primary.configure_catalogue(context.claim.ref)
    return stages.run_analysis_stages(
        context.worker, context.claim, context.image, "image/png", context.ledger, context.primary
    )


def test_main_only_preserves_snapshot_and_primary_lifecycle(context):
    snapshot = copy.deepcopy(context.claim.ref)
    analysis = run(context)
    assert analysis.result == context.primary.outcome.result
    assert analysis.metadata["human_review_required"] is True
    assert analysis.metadata["usage"] == {"total_tokens": 7}
    assert context.claim.ref == snapshot
    assert context.primary.snapshots == [snapshot]
    assert context.primary.on_result == context.ledger.on_result
    assert context.primary.on_validation == context.ledger.on_validation
    assert context.ledger.reservations == [("analysis", "fpt", "main")]
    assert not context.primary.closed
    assert not context.registry.calls


def test_screening_unrelated_shortcircuits_main_and_discards_items(context):
    context.claim.ref.update(screening_model="screen", escalation_model="expert")
    screening = Provider(result(image_type="unrelated"))
    context.registry.providers["screen"] = screening
    analysis = run(context)
    assert analysis.result == AnalysisResultV2(schema_version=2, image_type="unrelated", items=[])
    assert context.ledger.reservations == [("screening", "fpt", "screen")]
    assert context.registry.calls == [("fpt", "screen", None)]
    assert screening.snapshots[0] == {**context.claim.ref, "model": "screen"}
    assert screening.on_validation == context.ledger.on_validation
    assert screening.closed
    assert not context.primary.calls
    assert not context.primary.closed


def test_screening_classification_does_not_replace_main_components(context):
    context.claim.ref["screening_model"] = "screen"
    screening = Provider(result(count=2))
    context.registry.providers["screen"] = screening
    analysis = run(context)
    assert len(analysis.result.items) == 1
    assert analysis.metadata["screening_image_type"] == "outfit"
    assert [row[0] for row in context.ledger.reservations] == ["screening", "analysis"]
    assert screening.closed


@pytest.mark.parametrize(
    "ambiguous",
    [
        {"category": None},
        {"color": None},
        {"uncertain": ["category"]},
        {"uncertain": ["material"]},
        {"count": 0, "image_type": "unknown"},
    ],
)
def test_escalation_runs_once_same_provider_and_still_requires_review(context, ambiguous):
    context.claim.ref["escalation_model"] = "expert"
    context.primary.outcome = result(**ambiguous)
    expert = Provider(result(category=None))
    context.registry.providers["expert"] = expert
    analysis = run(context)
    assert analysis.result.items[0].category is None
    assert analysis.metadata["escalated"] is True
    assert analysis.metadata["human_review_required"] is True
    assert context.registry.calls == [("fpt", "expert", None)]
    assert context.ledger.reservations == [
        ("analysis", "fpt", "main"),
        ("escalation", "fpt", "expert"),
    ]
    assert expert.snapshots == [{**context.claim.ref, "model": "expert"}]
    assert expert.on_validation == context.ledger.on_validation
    assert len(expert.calls) == 1
    assert expert.closed
    assert not context.primary.closed


def test_missing_material_alone_does_not_trigger_escalation(context):
    context.claim.ref["escalation_model"] = "expert"
    run(context)
    assert not context.registry.calls


@pytest.mark.parametrize("stage", ["screening", "escalation"])
@pytest.mark.parametrize("failure_at", ["resolve", "configure", "analyze", "schema"])
def test_configured_secondary_failure_is_not_skipped_or_cross_provider_fallback(
    context, stage, failure_at
):
    context.claim.ref[f"{stage}_model"] = "secondary"
    context.primary.outcome = result(category=None)
    error = AIProviderError("Stage test lỗi", "provider_not_supported")
    secondary = Provider(result())
    context.registry.providers["secondary"] = secondary
    if failure_at == "resolve":
        context.registry.providers["secondary"] = error
    elif failure_at == "configure":
        secondary.configure_error = error
    elif failure_at == "analyze":
        secondary.outcome = error
    else:
        secondary.outcome = Analysis(AnalysisResult(items=[]))
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == (
        "invalid_model_output" if failure_at == "schema" else "provider_not_supported"
    )
    assert context.registry.calls == [("fpt", "secondary", None)]
    assert secondary.closed == (failure_at != "resolve")
    assert not context.primary.closed
    if failure_at == "schema":
        assert context.ledger.validations[-1] == (stage, "invalid_model_output")
    if stage == "screening":
        assert not context.primary.calls


@pytest.mark.parametrize("stage", ["screening", "escalation"])
def test_secondary_requires_v2_snapshot(context, stage):
    context.claim.ref.update(schema_version=1, prompt_version="catalogue-v1")
    context.claim.ref[f"{stage}_model"] = "secondary"
    with pytest.raises(AIProviderError, match="catalogue v2"):
        run(context)
    assert not context.registry.calls
    assert not context.primary.calls


def test_v1_main_without_optional_stages_remains_supported(context):
    context.claim.ref.update(schema_version=1, prompt_version="catalogue-v1")
    context.primary.outcome = Analysis(AnalysisResult(items=[]))
    assert run(context).result == AnalysisResult(items=[])


def image_data(size=(24, 32), fmt="PNG", color="white"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=fmt)
    return output.getvalue()


def encoded(body):
    return base64.b64encode(body).decode("ascii")


def segments_body():
    return {
        "components": [
            {
                "candidate_index": index,
                "image_base64": encoded(image_data(color=color)),
                "mask_base64": encoded(image_data(color="white")),
            }
            for index, color in enumerate(["red", "blue"])
        ]
    }


@pytest.fixture()
def segmentation(context, monkeypatch):
    context.primary.outcome = result(count=2)
    context.claim.ref.update(
        segmentation_url="http://segmentation.internal/v1/segment", segmentation_model="sam-pinned"
    )
    state = SimpleNamespace(
        body=segments_body(),
        status=200,
        calls=[],
        responses=[],
        clients=[],
        kwargs=[],
        error=None,
    )
    client_type = httpx.Client

    def handler(request):
        # Ledger phải giữ reservation trước khi service nhận request.
        assert context.ledger.reservations[-1] == (
            "segmentation",
            "segmentation_http",
            context.claim.ref.get("segmentation_model") or "service-default",
        )
        state.calls.append(request)
        if state.error:
            raise state.error
        response = httpx.Response(state.status, json=state.body)
        state.responses.append(response)
        return response

    def client_factory(**kwargs):
        state.kwargs.append(kwargs)
        client = client_type(transport=httpx.MockTransport(handler), **kwargs)
        state.clients.append(client)
        return client

    monkeypatch.setattr(stages.httpx, "Client", client_factory)
    return state


def test_segmentation_validates_all_indices_and_returns_internal_bytes(context, segmentation):
    segmentation.body["components"].reverse()
    analysis = run(context)
    request = segmentation.calls[0]
    assert request.method == "POST"
    assert str(request.url) == context.claim.ref["segmentation_url"]
    assert "authorization" not in request.headers
    assert json.loads(request.content) == {
        "model": "sam-pinned",
        "image_base64": encoded(context.image),
        "mime_type": "image/png",
        "components": [
            {"candidate_index": 0, "category": "áo"},
            {"candidate_index": 1, "category": "áo"},
        ],
    }
    segments = analysis.metadata["segments"]
    assert [segment["candidate_index"] for segment in segments] == [0, 1]
    for segment in segments:
        assert set(segment) == {"candidate_index", "image_bytes", "mime_type", "mask_bytes"}
        assert segment["mime_type"] == "image/png"
        with Image.open(io.BytesIO(segment["mask_bytes"])) as mask:
            assert mask.format == "PNG"
            assert mask.size == (24, 32)
    assert segmentation.kwargs == [{"timeout": 30, "follow_redirects": False}]
    assert all(response.is_closed for response in segmentation.responses)
    assert all(client.is_closed for client in segmentation.clients)
    assert context.ledger.provider == "fpt"
    assert context.ledger.results[-1][:4] == ("segmentation", "segmentation_http", "sam-pinned", {})
    assert context.ledger.results[-1][4] >= 0
    assert context.ledger.results[-1][5] is None


@pytest.mark.parametrize(
    "damage",
    [
        "missing_component",
        "duplicate",
        "negative",
        "outside",
        "bool_index",
        "str_index",
        "missing_mask",
        "corrupt_mask",
        "wrong_size_mask",
        "jpeg_mask",
        "corrupt_image",
        "output_url",
        "root_url",
        "extra_component",
        "missing_image",
        "invalid_base64",
    ],
)
def test_segmentation_rejects_invalid_mapping_images_and_masks(context, segmentation, damage):
    components = segmentation.body["components"]
    if damage == "missing_component":
        components.pop()
    elif damage == "duplicate":
        components[1]["candidate_index"] = 0
    elif damage in {"negative", "outside", "bool_index", "str_index"}:
        components[0]["candidate_index"] = {
            "negative": -1,
            "outside": 2,
            "bool_index": False,
            "str_index": "0",
        }[damage]
    elif damage == "missing_mask":
        del components[0]["mask_base64"]
    elif damage == "corrupt_mask":
        components[0]["mask_base64"] = encoded(b"not-an-image")
    elif damage == "wrong_size_mask":
        components[0]["mask_base64"] = encoded(image_data(size=(2, 2)))
    elif damage == "jpeg_mask":
        components[0]["mask_base64"] = encoded(image_data(fmt="JPEG"))
    elif damage == "corrupt_image":
        components[0]["image_base64"] = encoded(b"not-an-image")
    elif damage == "output_url":
        components[0]["image_url"] = "http://arbitrary.invalid/object"
    elif damage == "root_url":
        segmentation.body = {"url": "http://arbitrary.invalid/object"}
    elif damage == "extra_component":
        components.append(copy.deepcopy(components[0]))
    elif damage == "missing_image":
        del components[0]["image_base64"]
    else:
        components[0]["mask_base64"] = "%%%"
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == "invalid_segmentation_output"
    assert context.ledger.results[-1][5] == "invalid_segmentation_output"
    assert context.ledger.provider == "fpt"
    assert all(response.is_closed for response in segmentation.responses)
    assert all(client.is_closed for client in segmentation.clients)
    assert len(segmentation.calls) == 1  # Không tải URL hoặc fallback/crop cục bộ.


@pytest.mark.parametrize("count", [0, 1])
def test_segmentation_is_only_active_for_multiple_components(context, segmentation, count):
    context.primary.outcome = result(count=count)
    assert "segments" not in run(context).metadata
    assert not segmentation.calls
    assert context.ledger.reservations == [("analysis", "fpt", "main")]


def test_unconfigured_segmentation_does_not_invent_or_trust_provider_segments(context):
    context.primary.outcome = Analysis(
        result(count=2).result, {"segments": [{"image_bytes": b"fake"}]}
    )
    assert "segments" not in run(context).metadata


@pytest.mark.parametrize("status", [301, 400, 401, 429, 503])
def test_segmentation_http_errors_do_not_silently_skip(context, segmentation, status):
    segmentation.status = status
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == "segmentation_http_error"
    assert exc.value.retryable == (status in {429, 503})
    assert context.ledger.results[-1][5] == "segmentation_http_error"
    assert all(response.is_closed for response in segmentation.responses)


@pytest.mark.parametrize(
    "error, code",
    [
        (httpx.ReadTimeout("test timeout"), "segmentation_timeout"),
        (httpx.ConnectError("test network"), "segmentation_http_error"),
    ],
)
def test_segmentation_network_failure_records_ledger_and_closes(context, segmentation, error, code):
    segmentation.error = error
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == code
    assert exc.value.retryable
    assert context.ledger.results[-1][5] == code
    assert all(client.is_closed for client in segmentation.clients)
    assert context.ledger.provider == "fpt"


def test_segmentation_bounded_response_is_closed(context, segmentation, monkeypatch):
    monkeypatch.setattr(stages, "MAX_SEGMENTATION_RESPONSE_BYTES", 100)
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == "invalid_segmentation_output"
    assert all(response.is_closed for response in segmentation.responses)


@pytest.mark.parametrize(
    "key, value",
    [
        ("segmentation_url", "file:///tmp/not-allowed"),
        ("segmentation_url", "http://user:password@internal/segment"),
        ("segmentation_url", "http://internal/segment#fragment"),
        ("segmentation_model", ""),
        ("segmentation_timeout_seconds", 0),
        ("segmentation_timeout_seconds", float("nan")),
        ("segmentation_timeout_seconds", True),
    ],
)
def test_invalid_segmentation_config_is_explicit(context, segmentation, key, value):
    context.claim.ref[key] = value
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == "provider_request_error"
    assert not segmentation.calls


def test_segmentation_reservation_failure_prevents_http_and_restores_provider(
    context, segmentation
):
    original_before_call = context.ledger.before_call

    def before_call():
        if context.ledger.stage == "segmentation":
            raise AIProviderError("Hết ngân sách", "model_call_budget_exceeded")
        original_before_call()

    context.ledger.before_call = before_call
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == "model_call_budget_exceeded"
    assert not segmentation.calls
    assert [row[0] for row in context.ledger.results] == ["analysis"]
    assert context.ledger.provider == "fpt"


def test_segmentation_optional_model_and_operator_timeout(context, segmentation):
    context.claim.ref.pop("segmentation_model")
    context.claim.ref["segmentation_timeout_seconds"] = 12
    analysis = run(context)
    assert json.loads(segmentation.calls[0].content)["model"] is None
    assert context.ledger.results[-1][2] == "service-default"
    assert segmentation.kwargs == [{"timeout": 12, "follow_redirects": False}]
    assert len(analysis.metadata["segments"]) == 2


def test_secondary_reservation_failure_closes_secondary_without_main_call(context):
    context.claim.ref["screening_model"] = "screen"
    screening = Provider(result())
    context.registry.providers["screen"] = screening
    context.ledger.error = AIProviderError("Deadline test", "job_deadline_exceeded")
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == "job_deadline_exceeded"
    assert screening.closed
    assert not screening.calls
    assert not context.primary.calls
    assert not context.primary.closed


def test_registry_reusing_primary_fails_without_closing_primary(context):
    context.claim.ref["screening_model"] = "screen"
    context.registry.providers["screen"] = context.primary
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == "provider_request_error"
    assert not context.primary.closed


def test_segmentation_elapsed_timeout_closes_response(context, segmentation, monkeypatch):
    clock = iter([0, 31, 32])
    monkeypatch.setattr(stages, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(AIProviderError) as exc:
        run(context)
    assert exc.value.code == "segmentation_timeout"
    assert context.ledger.results[-1][5] == "segmentation_timeout"
    assert all(response.is_closed for response in segmentation.responses)
    assert context.ledger.provider == "fpt"


def test_full_pipeline_segments_final_escalation_candidates(context, segmentation):
    context.claim.ref.update(screening_model="screen", escalation_model="expert")
    screening = Provider(result(image_type="outfit"))
    expert = Provider(result(count=2, category="quần"))
    context.registry.providers.update(screen=screening, expert=expert)
    context.primary.outcome = result(category=None)
    analysis = run(context)
    assert [row[0] for row in context.ledger.reservations] == [
        "screening",
        "analysis",
        "escalation",
        "segmentation",
    ]
    payload = json.loads(segmentation.calls[0].content)
    assert payload["components"] == [
        {"candidate_index": 0, "category": "quần"},
        {"candidate_index": 1, "category": "quần"},
    ]
    assert analysis.metadata["escalated"] is True
    assert analysis.metadata["human_review_required"] is True
    assert screening.closed and expert.closed
    assert not context.primary.closed
