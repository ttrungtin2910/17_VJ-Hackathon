from collections.abc import Generator

import mongomock
import pytest
from fastapi.testclient import TestClient
from pymongo.database import Database

from garblo.config import Settings, get_settings
from garblo.database import ensure_indexes, get_db
from garblo.main import app


@pytest.fixture()
def database() -> Database:
    database: Database = mongomock.MongoClient().garblo
    ensure_indexes(database)
    return database


@pytest.fixture()
def client(database: Database) -> Generator[TestClient, None, None]:

    def override_db() -> Database:
        return database

    def override_settings() -> Settings:
        return Settings(
            app_env="test",
            mongodb_uri="mongodb://unused",
            jwt_secret_key="test-secret-key-that-is-long-enough",
            enable_api_mock_ui=True,
            open_ai_api_key=None,
            fpt_api_key=None,
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def configured_client(database: Database) -> Generator[TestClient, None, None]:
    """API client with OpenAI configured; FPT remains optional for recommendation requests."""
    def override_db() -> Database:
        return database

    def override_settings() -> Settings:
        return Settings(
            app_env="test",
            mongodb_uri="mongodb://unused",
            jwt_secret_key="test-secret-key-that-is-long-enough",
            open_ai_api_key="test-openai-key",
            fpt_api_key=None,
            s3_endpoint_url="http://unused",
            s3_access_key="test",
            s3_secret_key="test-secret",
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
