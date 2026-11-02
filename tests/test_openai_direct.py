import base64
import io
import json

import httpx
import pytest
from openai import OpenAI
from PIL import Image

from garblo.ai import AIProviderError
from garblo.ai_providers.openai_direct import OpenAIDirectProvider
from tests.conftest import ATTRIBUTES


def completion(content):
    return {
        "id": "test",
        "object": "chat.completion",
        "created": 1,
        "model": "test-vision",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": content},
            }
        ],
    }


def test_openai_direct_chat_and_images_wire(settings, image_bytes):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-openai-key"
        data = json.loads(request.content)
        if request.url.path == "/v1/images/generations":
            assert data["model"] == "gpt-image-test"
            assert "response_format" not in data
            return httpx.Response(
                200,
                json={"created": 1, "data": [{"b64_json": base64.b64encode(image_bytes).decode()}]},
            )
        assert request.url.path == "/v1/chat/completions"
        assert data["model"] == "test-vision"
        content = (
            json.dumps({"items": [ATTRIBUTES]})
            if len(calls) == 1
            else '{"image_prompt":"A real outfit"}'
        )
        return httpx.Response(200, json=completion(content))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = OpenAI(api_key="test-openai-key", http_client=http, max_retries=0)
        provider = OpenAIDirectProvider(settings, "test-vision", "gpt-image-test", client)
        assert (
            provider.analyze(image_bytes, "image/png", lambda: None).result.items[0].category
            == "shirt"
        )
        result = provider.render_look(
            garments=[ATTRIBUTES],
            theme="office",
            occasion="office",
            instruction=None,
            before_call=lambda: None,
        )
        assert result.image_bytes == image_bytes
    assert len(calls) == 3


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "provider_auth_error"),
        (429, "provider_transient_error"),
        (500, "provider_transient_error"),
    ],
)
def test_openai_error_classification_and_no_sdk_retry(settings, image_bytes, status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": {"message": "private raw error", "type": "test"}}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAIDirectProvider(
            settings, "test-vision", client=OpenAI(api_key="test", http_client=http, max_retries=0)
        )
        with pytest.raises(AIProviderError) as error:
            provider.analyze(image_bytes, "image/png", lambda: None)
    assert error.value.code == code
    assert "private raw error" not in str(error.value)
    assert len(calls) == 1


def test_consent_rechecked_between_planning_and_images(settings):
    calls, checks = [], []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=completion('{"image_prompt":"An outfit"}'))

    def consent():
        checks.append(True)
        if len(checks) == 2:
            raise AIProviderError("Đã rút consent", "consent_revoked")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAIDirectProvider(
            settings,
            "test-vision",
            "test-image",
            OpenAI(api_key="test", http_client=http, max_retries=0),
        )
        with pytest.raises(AIProviderError) as error:
            provider.render_look(
                garments=[ATTRIBUTES],
                theme=None,
                occasion=None,
                instruction=None,
                before_call=consent,
            )
    assert error.value.code == "consent_revoked"
    assert calls == ["/v1/chat/completions"]


def test_render_uses_multipart_images_edit(settings, image_bytes):
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path.endswith("/chat/completions"):
            return httpx.Response(200, json=completion('{"image_prompt":"An outfit"}'))
        assert request.url.path == "/v1/images/edits"
        assert "multipart/form-data" in request.headers["content-type"]
        assert image_bytes in request.content
        assert b"adult synthetic model" in request.content
        return httpx.Response(
            200, json={"created": 1, "data": [{"b64_json": base64.b64encode(image_bytes).decode()}]}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAIDirectProvider(
            settings, "test-vision", "gpt-image-test", OpenAI(api_key="test", http_client=http)
        )
        result = provider.render_look(
            garments=[ATTRIBUTES],
            images=[("item.png", image_bytes, "image/png")],
            theme="office",
            occasion=None,
            instruction=None,
            before_call=lambda: None,
        )
    assert result.image_bytes == image_bytes
    assert paths == ["/v1/chat/completions", "/v1/images/edits"]


def test_component_image_requests_transparent_png(settings, image_bytes):
    output = io.BytesIO()
    Image.new("RGBA", (8, 8), (10, 20, 30, 128)).save(output, format="PNG")
    component_png = output.getvalue()
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/v1/images/edits"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'"background"' in request.content and b"transparent" in request.content
        assert b'"output_format"' in request.content and b"png" in request.content
        assert image_bytes in request.content
        return httpx.Response(
            200,
            json={
                "created": 1,
                "data": [{"b64_json": base64.b64encode(component_png).decode()}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        provider = OpenAIDirectProvider(
            settings, "test-vision", "gpt-image-test", OpenAI(api_key="test", http_client=http)
        )
        result = provider.generate_component_image(
            image=image_bytes,
            mime_type="image/png",
            description=ATTRIBUTES,
            before_call=lambda: None,
        )
    assert result == component_png
    assert len(requests) == 1
