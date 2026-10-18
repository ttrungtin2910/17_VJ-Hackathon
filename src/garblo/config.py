from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Garblo API"
    api_v1_prefix: str = "/v1"
    database_url: str = "postgresql+psycopg://garblo:garblo@localhost:5432/garblo"

    jwt_secret_key: str = Field("unsafe-development-key-change-me", min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_access_token_minutes: int = 60
    enable_api_mock_ui: bool = True
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "garblo-assets"
    s3_region: str = "us-east-1"
    s3_presign_seconds: int = 900
    max_upload_bytes: int = 10 * 1024 * 1024

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_chat_deployment: str | None = None
    azure_openai_image_deployment: str | None = None

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ai_is_configured(self) -> bool:
        return all(
            (
                self.azure_openai_endpoint,
                self.azure_openai_api_key,
                self.azure_openai_api_version,
                self.azure_openai_chat_deployment,
                self.azure_openai_image_deployment,
            )
        )

    @property
    def storage_is_configured(self) -> bool:
        return bool(self.s3_endpoint_url and self.s3_access_key and self.s3_secret_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()

