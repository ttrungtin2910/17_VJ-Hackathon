import io
import os
from collections.abc import Generator
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Không khởi tạo engine PostgreSQL mặc định hoặc đọc .env thật lúc import app.
os.environ.setdefault("DATABASE_URL", "sqlite://")

from garblo.ai import RenderedLook
from garblo.ai_providers.contracts import Analysis, AnalysisResult
from garblo.catalogue.contracts import AnalysisResultV2
from garblo.config import Settings, get_settings
from garblo.database import Base, get_db
from garblo.main import app
from garblo.worker import JobWorker

ATTRIBUTES = {
    "category": "shirt",
    "color": "blue",
    "material": None,
    "season": None,
    "tags": ["casual"],
}


@pytest.fixture()
def settings(monkeypatch) -> Settings:
    for name in list(os.environ):
        if name.startswith(("OPENAI_", "FPT_", "AZURE_OPENAI_", "LANGCHAIN_", "LANGSMITH_")):
            monkeypatch.delenv(name)
    return Settings(
        _env_file=None,
        app_env="test",
        database_url="sqlite://",
        jwt_secret_key="test-secret-key-that-is-long-enough",
        enable_api_mock_ui=True,
        openai_api_key="test-openai-key",
        openai_vlm_model="test-vision",
        openai_image_model="test-image",
        catalogue_component_image_concurrency=1,
    )


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    # DB chỉ nằm trong RAM của engine này; dispose xóa toàn bộ fixture.
    # DROP TABLE với self-FK RESTRICT (asset lineage) lỗi trên SQLite ngay cả khi
    # không còn reference từ bảng khác. Không tắt FK trong thời gian chạy test.
    engine.dispose()


@pytest.fixture()
def db(db_factory):
    with db_factory() as session:
        yield session


@pytest.fixture()
def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (24, 32), "blue").save(output, format="PNG")
    return output.getvalue()


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.downloads = []

    def put_bytes(self, key, body, mime):
        self.objects[key] = body

    def get_bytes(self, key):
        return self.objects[key]

    def presign_get(self, key):
        self.downloads.append(key)
        return "https://private-storage.invalid/signed-test-image"

    def delete(self, key):
        self.objects.pop(key, None)


@pytest.fixture()
def storage(monkeypatch):
    storage = MemoryStorage()
    monkeypatch.setattr("garblo.api.routes.storage_or_503", lambda _: storage)
    return storage


@pytest.fixture()
def client(db_factory, settings, storage) -> Generator[TestClient, None, None]:
    def override_db() -> Generator[Session, None, None]:
        with db_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def headers(client):
    from tests.helpers import auth_headers

    return auth_headers(client)


@pytest.fixture()
def asset_id(client, headers, image_bytes):
    from tests.helpers import upload

    return upload(client, headers, image_bytes)


@pytest.fixture()
def providers(image_bytes):
    class FakeProvider:
        def generate_component_image(self, *, before_call, **kwargs):
            before_call()
            self.on_result({}, 1, None)
            return image_bytes

        def configure_catalogue(self, snapshot):
            self.schema_version = snapshot.get("schema_version", 1)

        def analyze(self, image, mime_type, before_call):
            before_call()
            calls.append(("analyze", self.provider, self.model, mime_type))
            if state.error:
                raise state.error
            if getattr(self, "schema_version", 1) == 2:
                return Analysis(
                    AnalysisResultV2.model_validate(
                        {
                            "schema_version": 2,
                            "image_type": "single_garment" if state.items else "unrelated",
                            "items": state.items,
                        }
                    )
                )
            return Analysis(AnalysisResult.model_validate({"items": state.items}))

        def render_look(self, *, before_call, **kwargs):
            state.render_images = kwargs.get("images")
            before_call()
            calls.append(("render", self.provider, self.model, self.image_model))
            if state.error:
                raise state.error
            before_call()
            return RenderedLook(image_bytes, "image/png", "A test outfit", self.image_model)

    calls = []
    state = SimpleNamespace(error=None, items=[dict(ATTRIBUTES)], calls=calls)

    def resolve(provider, model, image_model=None):
        instance = FakeProvider()
        instance.provider, instance.model, instance.image_model = provider, model, image_model
        return instance

    state.resolve = resolve
    return state


@pytest.fixture()
def worker(db, storage, providers, settings):
    return JobWorker(db, storage, providers, settings)
