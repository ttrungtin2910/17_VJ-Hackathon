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
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from garblo.ai_providers.contracts import Analysis
from garblo.catalogue.contracts import AnalysisResultV2
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
        assert (
            db.scalar(text("SELECT version_num FROM alembic_version"))
            == ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        )
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


def test_postgres_concurrent_upload_has_one_logical_asset(postgres_runtime, image_bytes):
    from garblo.models import MediaAsset, UploadOperation
    from tests.test_upload_recovery import keyed_upload

    runtime = postgres_runtime
    headers = auth_headers(runtime.client)
    barrier = Barrier(2)

    def send():
        barrier.wait(timeout=10)
        return keyed_upload(runtime.client, headers, image_bytes)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(send) for _ in range(2)]
        responses = [future.result(timeout=20) for future in futures]
    assert all(response.status_code in {201, 409} for response in responses)
    assert any(response.status_code == 201 for response in responses)
    replay = keyed_upload(runtime.client, headers, image_bytes)
    assert replay.status_code == 201
    assert all(
        response.json()["id"] == replay.json()["id"]
        for response in responses
        if response.status_code == 201
    )
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(MediaAsset)) == 1
        assert db.scalar(select(func.count()).select_from(UploadOperation)) == 1


def test_postgres_upload_recovers_storage_write_before_db_crash(
    postgres_runtime, image_bytes, storage, monkeypatch
):
    from garblo.models import MediaAsset, UploadOperation
    from tests.test_upload_recovery import KEY, keyed_upload

    runtime = postgres_runtime
    headers = auth_headers(runtime.client)
    put = storage.put_bytes

    def crash(key, body, mime):
        with runtime.factory() as db:
            assert db.scalar(select(func.count()).select_from(MediaAsset)) == 0
            # Đã commit reservation và không giữ khóa trong khi ghi S3.
            db.execute(text("SET LOCAL lock_timeout = '1s'"))
            op = db.scalar(select(UploadOperation).with_for_update())
            assert op.status == "pending" and op.asset_id is None
        put(key, body, mime)
        raise SystemExit("Mô phỏng process chết sau khi S3 ghi")

    monkeypatch.setattr(storage, "put_bytes", crash)
    # Gọi service trực tiếp để không làm chết event loop TestClient.
    from garblo.images import sanitize_image
    from garblo.uploads import upload_with_key

    body, mime = sanitize_image(image_bytes, 10 * 1024 * 1024, 25_000_000)
    with runtime.factory() as db:
        user_id = runtime.client.get("/v1/me", headers=headers).json()["id"]
        with pytest.raises(SystemExit):
            upload_with_key(db, user_id, KEY, body, mime, "garment_photo", storage)
    with runtime.factory() as db:
        op = db.scalar(select(UploadOperation))
        reserved_id = op.reserved_asset_id
        op.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(storage, "put_bytes", put)
    response = keyed_upload(runtime.client, headers, image_bytes)
    assert response.status_code == 201 and response.json()["id"] == reserved_id
    assert len(storage.objects) == 1


def test_postgres_upload_old_lease_cannot_publish(postgres_runtime, image_bytes, storage):
    from garblo.images import sanitize_image
    from garblo.models import MediaAsset, UploadOperation
    from garblo.uploads import upload_with_key
    from tests.test_upload_recovery import KEY

    runtime = postgres_runtime
    headers = auth_headers(runtime.client)
    user_id = runtime.client.get("/v1/me", headers=headers).json()["id"]
    body, mime = sanitize_image(image_bytes, 10 * 1024 * 1024, 25_000_000)

    class SlowWriter:
        def put_bytes(self, key, data, content_type):
            with runtime.factory() as second:
                op = second.scalar(select(UploadOperation))
                op.lease_expires_at = utcnow() - timedelta(seconds=1)
                second.commit()
                upload_with_key(second, user_id, KEY, body, mime, "garment_photo", storage)
            storage.put_bytes(key, data, content_type)

    from fastapi import HTTPException

    with runtime.factory() as first:
        with pytest.raises(HTTPException) as error:
            upload_with_key(first, user_id, KEY, body, mime, "garment_photo", SlowWriter())
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "upload_lease_lost"
    with runtime.factory() as db:
        assert db.scalar(select(func.count()).select_from(MediaAsset)) == 1
        assert db.scalar(select(UploadOperation)).status == "completed"


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
                    return Analysis(
                        AnalysisResultV2.model_validate(
                            {
                                "schema_version": 2,
                                "image_type": "single_garment",
                                "items": [ATTRIBUTES],
                            }
                        )
                    )

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


def test_postgres_multi_component_concurrent_partial_commit(
    postgres_runtime, image_bytes, storage, providers, settings
):
    from garblo.models import MediaAsset

    runtime = postgres_runtime
    headers = auth_headers(runtime.client)
    asset_id = upload(runtime.client, headers, image_bytes)
    providers.items = [dict(ATTRIBUTES), {**ATTRIBUTES, "category": "pants"}]
    job_id = catalogue(runtime.client, headers, asset_id)
    with runtime.factory() as db:
        assert JobWorker(db, storage, providers, settings).process_next()
        ids = db.get(AiJob, job_id).output_ref["candidate_ids"]
    barrier = Barrier(2)

    def commit(candidate_id):
        barrier.wait(timeout=10)
        response = runtime.client.post(
            f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": [candidate_id]}
        )
        assert response.status_code == 200, response.text
        return response.json()["garment_ids"][0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(commit, candidate_id) for candidate_id in ids]
        garment_ids = [future.result(timeout=20) for future in futures]
    assert len(set(garment_ids)) == 2
    with runtime.factory() as db:
        assert db.get(MediaAsset, asset_id).status == "catalogued"
        assert db.scalar(select(func.count()).select_from(Garment)) == 2


def test_postgres_upgrade_preserves_legacy_wardrobe_and_draft(postgres_runtime):
    runtime = postgres_runtime
    config = Config("alembic.ini")
    command.downgrade(config, "20261030_0002")
    with runtime.engine.begin() as connection:
        connection.execute(
            text("""
            INSERT INTO users (id,email,display_name,password_hash,style_profile,social_opt_in,created_at,updated_at)
            VALUES ('legacy-user','legacy@example.com','Cũ','not-a-real-password','{}',false,now(),now())
        """)
        )
        connection.execute(
            text("""
            INSERT INTO media_assets (id,user_id,object_key,asset_type,mime_type,checksum,byte_size,metadata,status,created_at,updated_at)
            VALUES ('legacy-asset','legacy-user','legacy/source.png','garment_photo','image/png','checksum',10,'{}','analyzed',now(),now())
        """)
        )
        connection.execute(
            text("""
            INSERT INTO ai_jobs (id,user_id,job_type,input_ref,output_ref,model_provider,status,attempts,created_at,updated_at)
            VALUES ('legacy-job','legacy-user','catalogue','{"asset_id":"legacy-asset","model":"legacy-model"}','{}','openai','succeeded',1,now(),now())
        """)
        )
        connection.execute(
            text("""
            INSERT INTO garments (id,user_id,source_asset_id,category,tags,wear_count,catalog_status,created_at,updated_at)
            VALUES ('legacy-garment','legacy-user','legacy-asset','shirt','[]',0,'active',now(),now())
        """)
        )
        connection.execute(
            text("""
            INSERT INTO garment_candidates (id,user_id,asset_id,job_id,candidate_index,attributes_json,original_attributes,status,created_at,updated_at)
            VALUES ('legacy-candidate','legacy-user','legacy-asset','legacy-job',0,'{}','{}','draft',now(),now())
        """)
        )
    command.upgrade(config, "head")
    with runtime.factory() as db:
        garment = db.get(Garment, "legacy-garment")
        candidate = db.get(GarmentCandidate, "legacy-candidate")
        assert garment.category == "shirt" and garment.details_json == {}
        assert candidate.schema_version == 1 and candidate.details_json == {}
        assert db.get(AiJob, "legacy-job").input_ref["model"] == "legacy-model"


def test_postgres_concurrent_garment_detail_patches_keep_both_fields(
    postgres_runtime, image_bytes, storage, providers, settings
):
    runtime = postgres_runtime
    headers = auth_headers(runtime.client)
    asset_id = upload(runtime.client, headers, image_bytes)
    catalogue(runtime.client, headers, asset_id)
    with runtime.factory() as db:
        JobWorker(db, storage, providers, settings).process_next()
        candidate_id = db.scalar(select(GarmentCandidate.id))
    garment_id = runtime.client.post(
        f"/v1/assets/{asset_id}/commit", headers=headers, json={"candidate_ids": [candidate_id]}
    ).json()["garment_ids"][0]
    barrier = Barrier(2)

    def patch(details):
        barrier.wait(timeout=10)
        response = runtime.client.patch(
            f"/v1/garments/{garment_id}", headers=headers, json={"details": details}
        )
        assert response.status_code == 200, response.text

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(patch, details)
            for details in ({"pattern": "solid"}, {"display_name": "Áo mới"})
        ]
        for future in futures:
            future.result(timeout=20)
    details = runtime.client.get(f"/v1/garments/{garment_id}", headers=headers).json()["details"]
    assert details["pattern"] == "solid" and details["display_name"] == "Áo mới"


@pytest.mark.parametrize("timezone_name", ["UTC", "Asia/Ho_Chi_Minh"])
def test_postgres_deadline_is_absolute_across_session_timezones(
    postgres_runtime, image_bytes, storage, providers, settings, timezone_name
):
    from garblo.ai import AIProviderError
    from garblo.catalogue.budget import CallLedger

    runtime = postgres_runtime
    headers = auth_headers(runtime.client)
    job_id = catalogue(runtime.client, headers, upload(runtime.client, headers, image_bytes))
    with runtime.factory() as db:
        worker = JobWorker(db, storage, providers, settings)
        job = worker.claim_next()
        assert job.id == job_id
        claim = Claim(
            job.id,
            job.user_id,
            job.job_type,
            job.model_provider,
            dict(job.input_ref),
            job.lock_token,
        )
        job.execution_deadline_at = utcnow() - timedelta(seconds=30)
        db.commit()
        db.execute(text("SELECT set_config('TimeZone', :zone, false)"), {"zone": timezone_name})
        with pytest.raises(AIProviderError) as error:
            CallLedger(worker, claim).ensure_deadline()
        assert error.value.code == "job_deadline_exceeded"


def test_two_workers_claim_different_jobs(
    postgres_runtime, image_bytes, storage, providers, settings
):
    runtime = postgres_runtime
    headers = auth_headers(runtime.client)
    ids = {
        catalogue(runtime.client, headers, upload(runtime.client, headers, image_bytes))
        for _ in range(2)
    }
    barrier = Barrier(2, timeout=10)

    def claim():
        with runtime.factory() as db:
            barrier.wait()
            job = JobWorker(db, storage, providers, settings).claim_next()
            return job.id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim) for _ in range(2)]
        assert {future.result() for future in futures} == ids
