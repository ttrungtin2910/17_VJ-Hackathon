"""Process test riêng: inference giả có nhãn, không cài fake-provider flag vào app."""

import io
import logging
import sys
import time

from PIL import Image
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from garblo.ai import AIProviderError
from garblo.ai_providers.contracts import Analysis
from garblo.catalogue.contracts import AnalysisResultV2
from garblo.config import get_settings
from garblo.database import SessionLocal
from garblo.local_runtime import write_heartbeat
from garblo.storage import ObjectStorage
from garblo.worker import JobWorker


def controlled_settings():
    settings = get_settings().model_copy(deep=True)
    if settings.app_env != "test" or not make_url(settings.database_url).database.endswith("_test"):
        raise RuntimeError("Harness chỉ cho phép database test riêng")
    # Credential tổng hợp chỉ để route chấp nhận enqueue trong harness. Registry
    # bên dưới không bao giờ khởi tạo SDK hoặc gọi inference ngoài máy.
    settings.openai_api_key = SecretStr("controlled-test-not-a-real-key")
    settings.openai_vlm_model = "controlled-vision-test"
    settings.catalogue_fpt_fallback_enabled = False
    settings.worker_retry_base_seconds = 0.1
    return settings


class ControlledProvider:
    def configure_catalogue(self, snapshot):
        self.snapshot = snapshot

    def analyze(self, image, mime_type, before_call):
        before_call()
        with Image.open(io.BytesIO(image)) as decoded:
            width = decoded.width
        if width == 32:
            raise AIProviderError(
                "Lỗi transient tổng hợp từ harness", "provider_transient_error", retryable=True
            )
        items = (
            []
            if width == 31
            else [
                {
                    "category": category,
                    "color": "xanh",
                    "material": None,
                    "season": None,
                    "tags": [],
                    "details": {"display_name": name},
                }
                for category, name in [
                    ("shirt", "Áo kiểm thử"),
                    ("pants", "Quần kiểm thử"),
                    ("shoes", "Giày kiểm thử"),
                ]
            ]
        )
        return Analysis(
            AnalysisResultV2(
                schema_version=2, image_type="outfit" if items else "unrelated", items=items
            )
        )


class ControlledRegistry:
    def resolve(self, provider, model, image_model=None):
        if provider != "openai" or model != "controlled-vision-test":
            raise AIProviderError(
                "Harness từ chối provider/model ngoài test", "provider_not_configured"
            )
        return ControlledProvider()


def run():
    settings = controlled_settings()
    logging.basicConfig(level=logging.INFO)
    logging.info("HARNESS: external inference MOCKED, không phải AI live")
    if sys.argv[1:] == ["api"]:
        import uvicorn

        from garblo.main import app

        app.dependency_overrides[get_settings] = lambda: settings
        app.add_api_route(
            "/__controlled-test",
            lambda: {"inference": "mocked", "database_test": True},
            methods=["GET"],
        )
        uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
        return
    if sys.argv[1:] != ["worker"]:
        raise RuntimeError("Chọn api hoặc worker")
    while True:
        write_heartbeat("busy")
        with SessionLocal() as db:
            processed = JobWorker(
                db, ObjectStorage(settings), ControlledRegistry(), settings
            ).process_next()
        write_heartbeat("idle")
        if not processed:
            time.sleep(1)


if __name__ == "__main__":
    run()
