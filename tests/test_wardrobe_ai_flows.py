from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database

from garblo.application.wardrobe import RecommendationService
from garblo.config import Settings
from garblo.database import ensure_indexes
from garblo.domain.entities import DetectedGarment, VectorMatch
from garblo.infrastructure.fpt import FPTIntentProvider, FPTProviderError, json_object_from_text, text_from_fpt_message
from garblo.infrastructure.openai_provider import OpenAIWardrobeProvider
from garblo.infrastructure.qdrant import QdrantVectorStore
from garblo.models import document, job_document
from garblo.worker import JobWorker


PASSWORD = "correct-horse-battery-staple"


class MemoryStorage:
    def __init__(self, values: dict[str, bytes] | None = None):
        self.values = values or {}

    def get_bytes(self, key: str) -> bytes:
        return self.values[key]

    def put_bytes(self, key: str, body: bytes, content_type: str) -> None:
        self.values[key] = body


class OneGarmentDetector:
    def detect(self, image_bytes: bytes, mime_type: str) -> list[DetectedGarment]:
        return [
            DetectedGarment(
                category="top",
                confidence=0.93,
                bbox={"left": 2, "top": 3, "right": 30, "bottom": 40},
                crop_bytes=b"isolated-top-crop",
            )
        ]


class FakeOpenAI:
    def analyse_garment(self, image_bytes: bytes, mime_type: str) -> dict[str, Any]:
        return {
            "category": "top", "color": "red", "material": "cotton", "style": "casual",
            "season": "summer", "formality": "casual", "occasions": ["weekend"], "tags": ["t-shirt"],
            "confidence": 0.9,
        }

    def embed_text(self, text: str) -> list[float]:
        return [0.1] * 3072

    def render_look(self, **kwargs: Any) -> Any:  # pragma: no cover - injection completeness only
        raise AssertionError("render should not be called")


class FakeImageEmbedder:
    def embed_image(self, image_bytes: bytes, mime_type: str) -> list[float]:
        return [0.2] * 768


class CapturingVectors:
    def __init__(self, matches: list[VectorMatch] | None = None):
        self.calls: list[dict[str, Any]] = []
        self.matches = matches or []

    def upsert_garment(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)

    def search_metadata(self, *, user_id: str, vector: list[float], limit: int) -> list[VectorMatch]:
        self.last_user_id = user_id
        return self.matches


class FakeIntent:
    def parse_intent(self, prompt: str) -> dict[str, Any]:
        return {
            "occasion": "weekend", "style": "casual", "colors": [], "formality": "casual",
            "required_categories": [], "avoid_categories": ["outerwear"], "clarification_needed": False,
        }


def _settings() -> Settings:
    return Settings(
        app_env="test", mongodb_uri="mongodb://unused", jwt_secret_key="test-secret-key-that-is-long-enough",
        open_ai_api_key="test", fpt_api_key="test", s3_endpoint_url="http://unused", s3_access_key="test", s3_secret_key="test",
    )


def test_fpt_contract_uses_only_public_text_and_rejects_invalid_json() -> None:
    payload = {"content": [{"type": "thinking", "thinking": "private"}, {"type": "text", "text": '{"occasion":"work"}'}]}
    public = text_from_fpt_message(payload)
    assert public == '{"occasion":"work"}'
    assert "private" not in public
    assert json_object_from_text(public) == {"occasion": "work"}
    with pytest.raises(FPTProviderError):
        json_object_from_text("not-json")


def test_fpt_intent_request_uses_terra_and_discards_thinking() -> None:
    request = httpx.Request("POST", "https://token-api.fpt.ai/v1/messages")
    response = httpx.Response(
        200, request=request,
        json={"content": [{"type": "thinking", "thinking": "do not expose"}, {"type": "text", "text": '{"occasion":"work","colors":["black"]}'}]},
    )

    class Client:
        def post(self, path: str, **kwargs: Any) -> httpx.Response:
            self.path, self.body = path, kwargs["body"]
            return response

    client = Client()
    intent = FPTIntentProvider(_settings(), client=client).parse_intent("black work clothes")
    assert client.path == "/v1/messages"
    assert client.body["model"] == "Azure:gpt-5.6-terra" and client.body["stream"] is True
    assert intent["occasion"] == "work" and intent["colors"] == ["black"]


def test_fpt_http_402_is_safe_and_thinking_is_not_persisted() -> None:
    request = httpx.Request("POST", "https://token-api.fpt.ai/v1/messages")
    response = httpx.Response(402, request=request, json={"message": "payment required"})

    class Client:
        def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            return response

    provider = FPTIntentProvider(_settings(), client=Client())
    with pytest.raises(FPTProviderError, match="HTTP 402") as error:
        provider.parse_intent("smart casual")
    assert "test-fpt-key" not in str(error.value)


def test_ingestion_worker_creates_crop_metadata_and_vectors_idempotently() -> None:
    import mongomock

    database: Database = mongomock.MongoClient().garblo
    ensure_indexes(database)
    user_id = "user-1"
    asset = document(
        user_id=user_id, object_key="users/user-1/assets/source.png", asset_type="garment_photo", mime_type="image/png",
        checksum="source", byte_size=4, metadata_json={}, status="uploaded",
    )
    database.media_assets.insert_one(asset)
    job = job_document(user_id=user_id, job_type="ingest_asset", input_ref={"asset_id": asset["id"]})
    database.ai_jobs.insert_one(job)
    vectors = CapturingVectors()
    worker = JobWorker(
        database, MemoryStorage({asset["object_key"]: b"source"}), _settings(), detector=OneGarmentDetector(),
        image_embedder=FakeImageEmbedder(), openai_provider=FakeOpenAI(), vectors=vectors,
    )

    assert worker.process_next()
    garment = database.garments.find_one({"user_id": user_id})
    assert garment and garment["category"] == "top" and garment["metadata"]["color"] == "red"
    assert garment["vector_status"] == "indexed"
    assert database.media_assets.find_one({"id": garment["crop_asset_id"]})
    assert vectors.calls[0]["user_id"] == user_id
    assert len(vectors.calls[0]["metadata_vector"]) == 3072
    assert len(vectors.calls[0]["image_vector"]) == 768

    worker._catalogue(database.ai_jobs.find_one({"id": job["id"]}))
    assert database.garments.count_documents({"ingestion_job_id": job["id"]}) == 1


def test_recommendation_never_returns_a_foreign_or_excluded_garment() -> None:
    vectors = CapturingVectors([VectorMatch("foreign", 0.99), VectorMatch("top-1", 0.91), VectorMatch("coat-1", 0.9)])
    service = RecommendationService(FakeIntent(), FakeOpenAI(), vectors)
    result = service.recommend(
        user_id="user-1", prompt="weekend casual", garments=[
            {"id": "top-1", "category": "top", "catalog_status": "active"},
            {"id": "coat-1", "category": "outerwear", "catalog_status": "active"},
        ],
    )
    assert vectors.last_user_id == "user-1"
    assert result["outfit_garment_ids"] == ["top-1"]
    assert result["missing_categories"] == ["bottom", "shoes"]


def test_openai_provider_is_mockable_for_vision_embeddings_and_synthetic_render() -> None:
    image_bytes = b"rendered-image"

    class Client:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.chat_create))
            self.embeddings = SimpleNamespace(create=self.embedding_create)
            self.images = SimpleNamespace(edit=self.image_edit)

        @staticmethod
        def chat_create(**kwargs: Any) -> Any:
            content = '{"category":"top","color":"red","occasions":["weekend"],"tags":["tee"]}'
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

        @staticmethod
        def embedding_create(**kwargs: Any) -> Any:
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.25] * 3072)])

        @staticmethod
        def image_edit(**kwargs: Any) -> Any:
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(image_bytes).decode("ascii"))])

    provider = OpenAIWardrobeProvider(_settings(), client=Client())
    assert provider.analyse_garment(b"image", "image/png")["category"] == "top"
    assert len(provider.embed_text("red casual top")) == 3072
    rendered = provider.render_look(
        garments=[{"id": "top-1", "category": "top"}], images=[("top.png", b"image", "image/png")],
        theme=None, occasion="weekend", instruction=None,
    )
    assert rendered.image_bytes == image_bytes and rendered.model_reference == "gpt-image-2"


def test_qdrant_search_has_mandatory_user_payload_filter() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"result": {}})
        return httpx.Response(200, json={"result": {"points": [{"id": "garment-1", "score": 0.88}]}})

    client = httpx.Client(base_url="http://qdrant.test", transport=httpx.MockTransport(handler))
    results = QdrantVectorStore(_settings(), client=client).search_metadata(
        user_id="user-1", vector=[0.1] * 3072, limit=5
    )
    assert results == [VectorMatch("garment-1", 0.88)]
    query = json.loads(requests[-1].content)
    assert query["filter"]["must"][0]["match"]["value"] == "user-1"


def _register_and_consent(client: TestClient) -> str:
    assert client.post("/v1/auth/register", json={"email": "upload@example.com", "display_name": "Uploader", "password": PASSWORD}).status_code == 201
    token = client.post("/v1/auth/login", json={"email": "upload@example.com", "password": PASSWORD}).json()["access_token"]
    assert client.post("/v1/consents", json={"purpose": "ai_processing", "policy_version": "v1"}, headers={"Authorization": f"Bearer {token}"}).status_code == 201
    return token


def test_batch_upload_enqueues_one_ingestion_job_per_image(configured_client: TestClient, monkeypatch: pytest.MonkeyPatch, database: Database) -> None:
    storage = MemoryStorage()
    monkeypatch.setattr("garblo.api.routes.storage_or_503", lambda settings: storage)
    token = _register_and_consent(configured_client)
    response = configured_client.post(
        "/v1/uploads", headers={"Authorization": f"Bearer {token}"},
        files=[("files", ("red-top.png", b"fake-png-1", "image/png")), ("files", ("blue-jeans.png", b"fake-png-2", "image/png"))],
    )
    assert response.status_code == 202, response.text
    batch = response.json()
    assert batch["total_count"] == 2 and len(batch["job_ids"]) == 2
    assert database.ai_jobs.count_documents({"job_type": "ingest_asset"}) == 2
    status_response = configured_client.get(f"/v1/uploads/{batch['id']}", headers={"Authorization": f"Bearer {token}"})
    assert status_response.status_code == 200


def test_failed_job_can_be_retried_by_its_owner(configured_client: TestClient, database: Database) -> None:
    token = _register_and_consent(configured_client)
    user = database.users.find_one({"email": "upload@example.com"})
    assert user
    job = job_document(user_id=user["id"], job_type="ingest_asset", input_ref={"asset_id": "missing"})
    job.update({"status": "failed", "error_code": "provider_or_model_error", "error_message": "temporary"})
    database.ai_jobs.insert_one(job)
    response = configured_client.post(f"/v1/jobs/{job['id']}/retry", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 202
    retried = database.ai_jobs.find_one({"id": job["id"]})
    assert retried["status"] == "queued" and retried["error_message"] is None
