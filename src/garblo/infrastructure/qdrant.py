from __future__ import annotations

from typing import Any

import httpx

from garblo.application.wardrobe import ApplicationError
from garblo.config import Settings
from garblo.domain.entities import VectorMatch


class VectorStoreUnavailable(ApplicationError):
    pass


class QdrantVectorStore:
    """Small REST adapter to Qdrant; keeping the Qdrant SDK out of the API image."""

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.collection = settings.qdrant_collection
        self.client = client or httpx.Client(base_url=settings.qdrant_url.rstrip("/"), timeout=settings.qdrant_timeout_seconds)

    def ensure_collection(self) -> None:
        try:
            response = self.client.get(f"/collections/{self.collection}")
            if response.status_code == 200:
                return
            if response.status_code != 404:
                response.raise_for_status()
            created = self.client.put(
                f"/collections/{self.collection}",
                json={
                    "vectors": {
                        "metadata_text": {"size": self.settings.metadata_embedding_dimensions, "distance": "Cosine"},
                        "image": {"size": self.settings.image_embedding_dimensions, "distance": "Cosine"},
                    },
                },
            )
            created.raise_for_status()
        except httpx.HTTPError as exc:
            raise VectorStoreUnavailable("Qdrant is unavailable") from exc

    def upsert_garment(
        self,
        *,
        garment_id: str,
        user_id: str,
        metadata_vector: list[float],
        image_vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        self.ensure_collection()
        try:
            response = self.client.put(
                f"/collections/{self.collection}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {
                            "id": garment_id,
                            "vector": {"metadata_text": metadata_vector, "image": image_vector},
                            "payload": {"user_id": user_id, **payload},
                        }
                    ]
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise VectorStoreUnavailable("Could not index garment vectors in Qdrant") from exc

    def search_metadata(self, *, user_id: str, vector: list[float], limit: int) -> list[VectorMatch]:
        self.ensure_collection()
        try:
            response = self.client.post(
                f"/collections/{self.collection}/points/query",
                json={
                    "query": vector,
                    "using": "metadata_text",
                    "limit": limit,
                    "with_payload": False,
                    "filter": {"must": [{"key": "user_id", "match": {"value": user_id}}]},
                },
            )
            response.raise_for_status()
            result = response.json().get("result", {})
            points = result.get("points", result if isinstance(result, list) else [])
            if not isinstance(points, list):
                raise VectorStoreUnavailable("Qdrant returned an invalid search payload")
            return [
                VectorMatch(garment_id=str(point["id"]), score=float(point.get("score", 0.0)))
                for point in points
                if isinstance(point, dict) and "id" in point
            ]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise VectorStoreUnavailable("Could not search garment vectors in Qdrant") from exc
