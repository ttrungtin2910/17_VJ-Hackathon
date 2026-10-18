from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from garblo.config import Settings, get_settings
from garblo.database import Base, get_db
import garblo.models  # noqa: F401
from garblo.main import app


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)

    def override_db() -> Generator[Session, None, None]:
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    def override_settings() -> Settings:
        return Settings(
            app_env="test",
            database_url="sqlite://",
            jwt_secret_key="test-secret-key-that-is-long-enough",
            enable_api_mock_ui=True,
        )

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = override_settings
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)

