from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
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
    s3_public_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "garblo-assets"
    s3_region: str = "us-east-1"
    s3_presign_seconds: int = 900
    local_storage_cors_enabled: bool = False
    max_upload_bytes: int = 10 * 1024 * 1024
    max_image_pixels: int = 25_000_000

    openai_api_key: SecretStr | None = None
    openai_vlm_model: str | None = None
    openai_image_model: str | None = None
    openai_embedding_model: str | None = "text-embedding-3-small"
    fpt_vlm_api_key: SecretStr | None = None
    fpt_vlm_base_url: str = "https://mkp-api.fptcloud.com/v1"
    fpt_vlm_model: str = "Qwen2.5-VL-7B-Instruct"
    fpt_vlm_timeout_seconds: float = Field(default=60, gt=0, le=120)
    openai_timeout_seconds: float = Field(default=120, gt=0, le=180)
    worker_max_attempts: int = Field(default=3, ge=1, le=10)
    worker_lease_seconds: int = Field(default=900, ge=600)
    worker_retry_base_seconds: float = Field(default=5, gt=0)
    catalogue_schema_version: Literal[1, 2] = 2
    catalogue_component_image_concurrency: int = Field(default=5, ge=1, le=5)
    catalogue_max_calls: int = Field(default=12, ge=1, le=20)
    catalogue_max_output_tokens: int = Field(default=4096, ge=256, le=8192)
    catalogue_token_budget: int = Field(default=200_000, ge=1024)
    catalogue_call_token_reservation: int = Field(default=16384, ge=1024)
    catalogue_deadline_seconds: float = Field(default=300, gt=0, le=500)
    catalogue_native_structured_output: bool = False
    catalogue_supports_temperature: bool = True
    catalogue_deduplicate_uploads: bool = False
    catalogue_fpt_fallback_enabled: bool = False
    catalogue_screening_model: str | None = None
    catalogue_escalation_model: str | None = None
    fpt_screening_model: str | None = None
    fpt_escalation_model: str | None = None
    catalogue_segmentation_url: str | None = None
    catalogue_segmentation_model: str | None = None
    catalogue_segmentation_timeout_seconds: float = Field(default=30, gt=0, le=60)
    stylist_intent_model: str | None = None
    stylist_intent_max_calls: int = Field(default=3, ge=1, le=6)
    stylist_intent_native_structured_output: bool = False

    @field_validator("catalogue_schema_version", mode="before")
    @classmethod
    def parse_catalogue_schema_version(cls, value):
        # Environment/dotenv cung cấp chuỗi; Literal số không tự chuyển kiểu.
        return int(value) if isinstance(value, str) and value in {"1", "2"} else value

    @field_validator("catalogue_segmentation_url")
    @classmethod
    def segmentation_endpoint(cls, value):
        if not value:
            return None
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Segmentation URL phải là endpoint HTTP(S) cố định, không credential/query"
            )
        return value

    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_api_version: str | None = None
    azure_openai_chat_deployment: str | None = None
    azure_openai_image_deployment: str | None = None

    @field_validator(
        "openai_api_key",
        "fpt_vlm_api_key",
        "openai_vlm_model",
        "openai_image_model",
        "openai_embedding_model",
        "catalogue_screening_model",
        "catalogue_escalation_model",
        "fpt_screening_model",
        "fpt_escalation_model",
        "catalogue_segmentation_model",
        "stylist_intent_model",
        mode="before",
    )
    @classmethod
    def empty_to_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def ai_is_configured(self) -> bool:
        return self.analysis_is_configured("openai") and bool(self.openai_image_model)

    def analysis_is_configured(self, provider: str) -> bool:
        if provider == "openai":
            return bool(self.openai_api_key and self.openai_vlm_model)
        if provider == "fpt":
            return bool(self.fpt_vlm_api_key and self.fpt_vlm_model)
        if provider == "azure_openai":
            return self.azure_is_configured
        return False

    @property
    def azure_is_configured(self) -> bool:
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
