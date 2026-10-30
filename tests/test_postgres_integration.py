"""Chạy trên schema ngẫu nhiên của DB test; không xóa bảng/schema của ứng dụng khác."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from garblo.ai_providers.contracts import Analysis, AnalysisResult
from garblo.config import get_settings
from garblo.database import get_db
from garblo.main import app
from garblo.models import AiJob, ApprovalRequest, Garment, GarmentCandidate, Look, LookItem
from garblo.worker import Claim, JobWorker, utcnow
from tests.conftest import ATTRIBUTES
from tests.helpers import auth_headers, catalogue, upload

pytestmark = pytest.mark.integration


@pytest.fixture()
def postgres_runtime(monkeypatch, settings, storage):
    url = os.environ.get("GARBLO_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Chưa đặt GARBLO_TEST_DATABASE_URL cho PostgreSQL test riêng")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql" or not parsed.database.endswith("_test"):
        pytest.fail("Chỉ chấp nhận PostgreSQL database có tên kết thúc bằng _test")
    schema = "garblo_test_" + uuid.uuid4().hex
    admin = create_engine(parsed)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = parsed.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    get_settings.cache_clear()
    engine = create_engine(scoped)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_db():
        with factory() as db:
            yield db

    try:
        command.upgrade(Config("alembic.ini"), "head")
        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, factory=factory, schema=schema, engine=engine)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        # Chỉ schema UUID được fixture này tạo, không đụng public hoặc dữ liệu sẵn có.
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        get_settings.cache_clear()


def test_postgres_migration_flow_and_concurrent_commit(
    postgres_runtime, image_bytes, storage, providers, settings
):
    runtime = postgres_runtime
    client = runtime.client
    headers = auth_headers(client)
    asset_id = upload(client, headers, image_bytes)
    job_id = catalogue(client, headers, asset_id)
    with runtime.factory() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261030_0002"
        assert JobWorker(db, storage, providers, settings).process_next()
        candidate_id = db.scalar(select(GarmentCandidate.id))
        assert db.get(AiJob, job_id).status == "succeeded"

    barrier = Barrier(2)

    def commit():
        barrier.wait(timeout=10)
        response = client.post(
            f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": [candidate_id]}
        )
        assert response.status_code == 200, response.text
        return response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(commit) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0] == results[1]
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(Garment)) == 1
    assert len(client.get("/v1/garments?tags=casual&category=shirt", headers=headers).json()) == 1


def test_postgres_skip_locked_and_reclaim_fences_old_worker(
    postgres_runtime, image_bytes, storage, providers, settings
):
    runtime = postgres_runtime
    client = runtime.client
    headers = auth_headers(client)
    job_id = catalogue(client, headers, upload(client, headers, image_bytes))
    with runtime.factory() as locker, runtime.factory() as second:
        locker.scalar(select(AiJob).where(AiJob.id == job_id).with_for_update())
        assert JobWorker(second, storage, providers, settings).claim_next() is None
        locker.rollback()
        first_worker = JobWorker(locker, storage, providers, settings)
        job = first_worker.claim_next()
        old = Claim(
            job.id,
            job.user_id,
            job.job_type,
            job.model_provider,
            dict(job.input_ref),
            job.lock_token,
        )
        job.locked_at = utcnow() - timedelta(seconds=1000)
        locker.commit()
        second_worker = JobWorker(second, storage, providers, settings)
        assert second_worker.recover_expired() == 1
        assert second_worker.process_next()
        assert first_worker._owned_claim(old, lock=True) is None
        locker.rollback()
        locker.expire_all()
        assert locker.get(AiJob, job_id).status == "succeeded"
        assert locker.scalar(select(func.count()).select_from(GarmentCandidate)) == 1


def test_postgres_concurrent_providers_keep_separate_snapshots(
    postgres_runtime, image_bytes, storage, settings
):
    runtime = postgres_runtime
    headers = auth_headers(runtime.client)
    settings.fpt_vlm_api_key = SecretStr("fpt-test-only")
    catalogue(runtime.client, headers, upload(runtime.client, headers, image_bytes))
    catalogue(
        runtime.client,
        headers,
        upload(runtime.client, headers, image_bytes),
        {"vlm_provider": "fpt"},
    )
    barrier = Barrier(2)
    calls = []

    class Registry:
        def resolve(self, provider, model, image_model):
            class Analyzer:
                def analyze(self, image, mime_type, before_call):
                    before_call()
                    barrier.wait(timeout=10)
                    calls.append((provider, model))
                    return Analysis(AnalysisResult.model_validate({"items": [ATTRIBUTES]}))

            return Analyzer()

    def process():
        with runtime.factory() as db:
            return JobWorker(db, storage, Registry(), settings).process_next()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(process) for _ in range(2)]
        assert all(future.result(timeout=20) for future in futures)
    assert set(calls) == {("openai", "test-vision"), ("fpt", "Qwen2.5-VL-7B-Instruct")}
    with runtime.factory() as db:
        jobs = list(db.scalars(select(AiJob)))
        assert all(job.status == "succeeded" for job in jobs)
        assert all(job.output_ref["provider"] == job.model_provider for job in jobs)


def test_postgres_lookbook_and_concurrent_approval(
    postgres_runtime, image_bytes, storage, providers, settings
):
    runtime = postgres_runtime
    client = runtime.client
    headers = auth_headers(client)
    asset_id = upload(client, headers, image_bytes)
    catalogue(client, headers, asset_id)
    with runtime.factory() as db:
        JobWorker(db, storage, providers, settings).process_next()
        candidate_id = db.scalar(select(GarmentCandidate.id))
    response = client.post(
        f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": [candidate_id]}
    )
    garment_id = response.json()["garment_ids"][0]
    response = client.post(
        "/v1/lookbooks",
        headers=headers,
        json={"title": "Demo", "garment_ids": [garment_id], "preset_id": "office"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    with runtime.factory() as db:
        JobWorker(db, storage, providers, settings).process_next()
        job = db.get(AiJob, job_id)
        assert job.status == "succeeded"
        approval_id = job.output_ref["approval_id"]
        look_id = job.output_ref["look_id"]
        assert (
            db.scalar(select(LookItem.garment_id).where(LookItem.look_id == look_id)) == garment_id
        )
    barrier = Barrier(2)

    def decide(decision):
        barrier.wait(timeout=10)
        response = client.post(
            f"/v1/approvals/{approval_id}/decision", headers=headers, json={"decision": decision}
        )
        return response.status_code, decision

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(decide, decision) for decision in ("accepted", "rejected")]
        results = [future.result(timeout=20) for future in futures]
    assert sorted(status for status, _ in results) == [200, 409]
    winner = next(decision for status, decision in results if status == 200)
    with runtime.factory() as db:
        assert db.get(ApprovalRequest, approval_id).status == winner
        assert db.get(Look, look_id).status == winner
