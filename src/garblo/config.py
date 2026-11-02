from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Garblo API"
    api_v1_prefix: str = "/v1"
    mongodb_uri: str = "mongodb://garblo:garblo@localhost:27017/garblo?authSource=admin"
    mongodb_database: str = "garblo"
    mongodb_server_selection_timeout_ms: int = 5_000

    jwt_secret_key: str = Field("unsafe-development-key-change-me", min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 60
    enable_api_mock_ui: bool = True
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    s3_endpoint_url: str | None = None
    s3_presign_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "garblo-assets"
    s3_region: str = "us-east-1"
    s3_presign_seconds: int = 900
    max_upload_bytes: int = 10 * 1024 * 1024
    max_upload_batch_files: int = 20
    max_upload_batch_bytes: int = 100 * 1024 * 1024

    fpt_api_key: str | None = None
    fpt_base_url: str = "https://token-api.fpt.ai"
    fpt_model: str = "Azure:gpt-5.6-terra"
    fpt_max_tokens: int = 10_000

    open_ai_api_key: str | None = None
    openai_vision_model: str = "gpt-4.1-mini"
    openai_intent_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-large"
    openai_image_model: str = "gpt-image-2"
    metadata_embedding_dimensions: int = 3_072

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "garblo_garments"
    qdrant_timeout_seconds: float = 10.0
    image_embedding_dimensions: int = 768

    detector_model: str = "IDEA-Research/grounding-dino-tiny"
    image_embedding_model: str = "google/siglip2-base-patch16-224"
    ml_device: str = "auto"
    worker_poll_seconds: float = 2.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ai_is_configured(self) -> bool:
        return self.openai_is_configured

    @property
    def recommendation_is_configured(self) -> bool:
        """OpenAI is the required provider; FPT is an optional primary intent provider."""
        return self.openai_is_configured

    @property
    def openai_is_configured(self) -> bool:
        return bool(self.open_ai_api_key)

    @property
    def fpt_is_configured(self) -> bool:
        return bool(self.fpt_api_key and self.fpt_base_url and self.fpt_model)

    @property
    def storage_is_configured(self) -> bool:
        return bool(self.s3_endpoint_url and self.s3_access_key and self.s3_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()

