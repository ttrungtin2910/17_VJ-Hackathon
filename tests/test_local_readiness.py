import time
from contextlib import contextmanager

from garblo.local_runtime import check_runtime, heartbeat_ready, write_heartbeat


def test_readiness_requires_db_storage_and_schema(settings):
    @contextmanager
    def database():
        class DB:
            def scalar(self, statement):
                return "schema-head" if "alembic_version" in str(statement) else 1

        yield DB()

    class Storage:
        def head_bucket(self, **_):
            return {}

    def check(**kwargs):
        return check_runtime(
            settings, database, lambda _: Storage(), expected_head="schema-head", **kwargs
        )

    assert all(check().values())

    @contextmanager
    def failed_database():
        raise RuntimeError("Không được đưa connection string ra response")
        yield

    result = check_runtime(settings, failed_database, lambda _: Storage(), "schema-head")
    assert result == {"database": False, "schema": False, "storage": True}

    class FailedStorage:
        def head_bucket(self, **_):
            raise RuntimeError("Không được đưa credential ra response")

    result = check_runtime(settings, database, lambda _: FailedStorage(), "wrong-head")
    assert result == {"database": True, "schema": False, "storage": False}


def test_worker_dead_loop_is_not_ready(settings, tmp_path):
    path = tmp_path / "heartbeat"
    assert not heartbeat_ready(settings, path)
    write_heartbeat("idle", path)
    assert heartbeat_ready(settings, path)
    write_heartbeat("idle", path, now=time.time() - 30)
    assert not heartbeat_ready(settings, path)
    write_heartbeat("busy", path, now=time.time() - 30)
    assert heartbeat_ready(settings, path)
    write_heartbeat("busy", path, now=time.time() - settings.worker_lease_seconds - 60)
    assert not heartbeat_ready(settings, path)
    write_heartbeat("failed", path)
    assert not heartbeat_ready(settings, path)


def test_missing_fpt_does_not_block_local_configuration(settings):
    settings.fpt_vlm_api_key = None
    settings.openai_image_model = None
    assert settings.analysis_is_configured("openai")
    assert not settings.analysis_is_configured("fpt")


def test_ready_route_reports_dependency_failure_without_secrets(client, monkeypatch):
    for healthy in (False, True):
        checks = {"database": True, "schema": True, "storage": healthy}
        monkeypatch.setattr("garblo.main.check_runtime", lambda _: checks)
        response = client.get("/ready")
        assert response.status_code == (200 if healthy else 503)
        assert response.json()["checks"] == checks
