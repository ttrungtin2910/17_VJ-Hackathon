import base64
import json

import httpx
import pytest
from pydantic import SecretStr

from garblo.ai import AIProviderError
from garblo.ai_providers.fpt_qwen import FPTQwenAnalyzer, normalize_response, parse_sse
from tests.conftest import ATTRIBUTES


@pytest.mark.parametrize("envelope", [True, False])
@pytest.mark.parametrize("parts", [True, False])
def test_wire_protocol_and_normalization(settings, image_bytes, envelope, parts):
    calls = []
    content = "```json\n" + json.dumps({"items": [ATTRIBUTES]}) + "\n```"
    if parts:
        content = [{"type": "text", "text": content}]

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://mkp-api.fptcloud.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-fpt-key"
        payload = json.loads(request.content)
        assert payload["model"] == "Qwen2.5-VL-7B-Instruct"
        assert payload["stream"] is False
        uri = payload["messages"][0]["content"][1]["image_url"]["url"]
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",")[1]) == image_bytes
        root = {"choices": [{"message": {"content": content}}]}
        return httpx.Response(200, json={"code": 200, "data": root} if envelope else root)

    settings.fpt_vlm_api_key = SecretStr("test-fpt-key")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = FPTQwenAnalyzer(settings, settings.fpt_vlm_model, client).analyze(
            image_bytes, "image/png", lambda: None
        )
    assert result.result.items[0].category == "shirt"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,code,retry",
    [
        (401, "provider_auth_error", False),
        (403, "provider_auth_error", False),
        (429, "provider_transient_error", True),
        (503, "provider_transient_error", True),
    ],
)
def test_redacted_upstream_errors(settings, image_bytes, status, code, retry):
    settings.fpt_vlm_api_key = SecretStr("secret-key")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="secret-key private-image")
        )
    ) as client:
        with pytest.raises(AIProviderError) as error:
            FPTQwenAnalyzer(settings, settings.fpt_vlm_model, client).analyze(
                image_bytes, "image/png", lambda: None
            )
    assert error.value.code == code
    assert error.value.retryable is retry
    assert "secret-key" not in str(error.value)


def test_envelope_app_error():
    with pytest.raises(AIProviderError) as error:
        normalize_response({"code": 401, "message": "private data", "data": {"choices": []}})
    assert error.value.code == "provider_auth_error"


@pytest.mark.parametrize(
    "content", ["not json", '{"items":[{"category":"shirt"}]}', '{"items":[{"category":12}]}']
)
def test_one_repair_then_fail_without_fake_result(settings, image_bytes, content):
    settings.fpt_vlm_api_key = SecretStr("test")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AIProviderError) as error:
            FPTQwenAnalyzer(settings, settings.fpt_vlm_model, client).analyze(
                image_bytes, "image/png", lambda: None
            )
    assert error.value.code == "invalid_model_output"
    assert len(calls) == 2


def test_timeout_is_transient(settings, image_bytes):
    settings.fpt_vlm_api_key = SecretStr("test")

    def handler(request):
        raise httpx.ReadTimeout("sensitive response", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AIProviderError) as error:
            FPTQwenAnalyzer(settings, settings.fpt_vlm_model, client).analyze(
                image_bytes, "image/png", lambda: None
            )
    assert error.value.retryable
    assert "sensitive" not in str(error.value)


def test_sse_reassembles_content_and_rejects_truncation():
    chunks = ['{"items":', "[]}"]
    stream = ": comment\n\n" + "".join(
        "data: " + json.dumps({"choices": [{"delta": {"content": part}}]}) + "\n\n"
        for part in chunks
    )
    assert parse_sse(stream + "data: [DONE]\n\n")[0] == '{"items":[]}'
    with pytest.raises(AIProviderError):
        parse_sse(stream)


def test_fpt_sse_response_even_when_nonstream_requested(settings, image_bytes):
    settings.fpt_vlm_api_key = SecretStr("test")
    event = {
        "code": 200,
        "data": {"choices": [{"delta": {"content": '{"items":[]}'}, "finish_reason": "stop"}]},
    }
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
            )
        )
    ) as client:
        result = FPTQwenAnalyzer(settings, settings.fpt_vlm_model, client).analyze(
            image_bytes, "image/png", lambda: None
        )
    assert result.result.items == []
