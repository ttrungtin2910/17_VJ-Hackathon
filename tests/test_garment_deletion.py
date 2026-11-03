from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from garblo.asset_cleanup import cleanup_next
from garblo.models import Garment, GarmentCandidate, MediaAsset, new_id
from garblo.recommendations.models import (
    RecommendationFeedback,
    RecommendationSession,
    RecommendedItem,
    RecommendedOutfit,
)
from garblo.recommendations.service import garment_snapshot
from garblo.worker import JobWorker
from tests.conftest import ATTRIBUTES
from tests.helpers import auth_headers, committed_garment, upload
from tests.test_postgres_integration import postgres_runtime as _postgres_runtime

postgres_runtime = _postgres_runtime


@pytest.fixture(autouse=True)
def component_provider(providers, image_bytes):
    # Fixture chung chưa có image stage; các ca xóa dùng PNG giả, không gọi AI thật.
    resolve = providers.resolve

    def generate_component_image(*, before_call, **kwargs):
        before_call()
        return image_bytes

    def with_component(*args):
        provider = resolve(*args)
        provider.generate_component_image = generate_component_image
        return provider

    providers.resolve = with_component


def add_component_asset(db_factory, garment_id, storage):
    with db_factory() as db:
        garment = db.get(Garment, garment_id)
        component_id = new_id()
        component = MediaAsset(
            id=component_id,
            user_id=garment.user_id,
            object_key=f"users/{garment.user_id}/assets/{component_id}.png",
            asset_type="garment_component",
            mime_type="image/png",
            checksum=f"component-{component_id}",
            byte_size=4,
            status="processed",
            parent_asset_id=garment.source_asset_id,
        )
        db.add(component)
        db.flush()
        garment.component_asset_id = component_id
        db.commit()
        storage.objects[component.object_key] = b"test"
        return component_id


def test_delete_garment_removes_both_images_and_keeps_audit_rows(
    client, headers, asset_id, worker, storage, db_factory
):
    with db_factory() as db:
        db.get(MediaAsset, asset_id).metadata_json = {}
        db.commit()
    garment_id = committed_garment(client, headers, asset_id, worker)
    component_id = add_component_asset(db_factory, garment_id, storage)
    with db_factory() as db:
        garment = db.get(Garment, garment_id)
        source_id = garment.source_asset_id
        source = db.get(MediaAsset, source_id)
        component = db.get(MediaAsset, component_id)
        keys = [source.object_key, component.object_key]
        candidate = db.scalar(
            select(GarmentCandidate).where(GarmentCandidate.garment_id == garment_id)
        )
        candidate_id = candidate.id

    response = client.delete(f"/v1/garments/{garment_id}", headers=headers)

    assert response.status_code == 204
    assert all(key not in storage.objects for key in keys)
    assert client.get("/v1/garments", headers=headers).json() == []
    assert client.get(f"/v1/assets/{source_id}/download", headers=headers).status_code == 410
    with db_factory() as db:
        assert db.get(Garment, garment_id) is None
        assert db.get(MediaAsset, source_id).status == "deleted"
        assert db.get(MediaAsset, component_id).status == "deleted"
        candidate = db.get(GarmentCandidate, candidate_id)
        assert candidate.status == "deleted"
        assert candidate.garment_id is None
        assert candidate.details_json["deleted_garment_id"] == garment_id


def test_delete_garment_is_owner_scoped(client, headers, asset_id, worker):
    garment_id = committed_garment(client, headers, asset_id, worker)
    other = auth_headers(client, "delete-other@example.com")

    response = client.delete(f"/v1/garments/{garment_id}", headers=other)

    assert response.status_code == 404
    assert client.get(f"/v1/garments/{garment_id}", headers=headers).status_code == 200


def test_delete_garment_with_look_link_is_rejected(client, headers, asset_id, worker):
    garment_id = committed_garment(client, headers, asset_id, worker)
    create = client.post(
        "/v1/lookbooks",
        headers=headers,
        json={"title": "Bộ đã lưu", "garment_ids": [garment_id], "preset_id": "office"},
    )
    assert create.status_code == 202
    worker.process_next()

    response = client.delete(f"/v1/garments/{garment_id}", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "garment_in_look"
    assert client.get(f"/v1/garments/{garment_id}", headers=headers).status_code == 200


def test_delete_garment_with_shared_image_is_rejected(
    client, headers, asset_id, worker, db_factory
):
    garment_id = committed_garment(client, headers, asset_id, worker)
    with db_factory() as db:
        garment = db.get(Garment, garment_id)
        db.add(
            Garment(
                user_id=garment.user_id,
                source_asset_id=garment.source_asset_id,
                component_asset_id=garment.component_asset_id,
                category="shirt",
                tags=[],
                details_json={},
            )
        )
        db.commit()

    response = client.delete(f"/v1/garments/{garment_id}", headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "garment_asset_shared"


@pytest.mark.parametrize(
    "runtime_kind", ["sqlite", pytest.param("postgres", marks=pytest.mark.integration)]
)
@pytest.mark.parametrize("commit_other", [True, False])
def test_delete_one_component_preserves_shared_source_and_other_candidate(
    client,
    headers,
    asset_id,
    worker,
    providers,
    storage,
    db_factory,
    commit_other,
    runtime_kind,
    request,
    settings,
    image_bytes,
):
    if runtime_kind == "postgres":
        runtime = request.getfixturevalue("postgres_runtime")
        client, db_factory = runtime.client, runtime.factory
        headers = auth_headers(client)
        asset_id = upload(client, headers, image_bytes)
        worker = JobWorker(db_factory(), storage, providers, settings)
        request.addfinalizer(worker.db.close)
    from tests.test_catalogue_pipeline import v2_provider

    items = [
        dict(ATTRIBUTES),
        {**ATTRIBUTES, "category": "hat", "color": "red"},
    ]
    from tests.helpers import catalogue

    v2_provider(worker, items)
    job_id = catalogue(client, headers, asset_id)
    assert worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "succeeded", job
    candidate_ids = job["output_ref"]["candidate_ids"]
    response = client.post(
        f"/v1/assets/{asset_id}/commit",
        headers=headers,
        json={"candidate_ids": candidate_ids if commit_other else candidate_ids[:1]},
    )
    assert response.status_code == 200, response.text
    garment_ids = response.json()["garment_ids"]
    with db_factory() as db:
        source_key = db.get(MediaAsset, asset_id).object_key
        components = [db.get(GarmentCandidate, cid).component_asset_id for cid in candidate_ids]
        component_keys = [db.get(MediaAsset, cid).object_key for cid in components]

    deleted = client.delete(
        f"/v1/garments/{garment_ids[0]}?delete_recommended_outfits=true", headers=headers
    )

    assert deleted.status_code == 204, deleted.text
    assert source_key in storage.objects
    assert component_keys[0] not in storage.objects
    assert component_keys[1] in storage.objects
    assert client.get(f"/v1/assets/{asset_id}/download", headers=headers).status_code == 410
    with db_factory() as db:
        assert db.get(MediaAsset, asset_id).status != "deleted"
        assert db.get(MediaAsset, components[0]).status == "deleted"
        assert db.get(MediaAsset, components[1]).status != "deleted"
        assert db.get(GarmentCandidate, candidate_ids[0]).status == "deleted"
        other = db.get(GarmentCandidate, candidate_ids[1])
        assert other.status == ("committed" if commit_other else "draft")

    if not commit_other:
        response = client.post(
            f"/v1/assets/{asset_id}/commit",
            headers=headers,
            json={"candidate_ids": candidate_ids[1:]},
        )
        assert response.status_code == 200, response.text
        garment_ids.append(response.json()["garment_ids"][0])
    assert client.get(f"/v1/garments/{garment_ids[1]}", headers=headers).status_code == 200
    assert client.delete(f"/v1/garments/{garment_ids[1]}", headers=headers).status_code == 204
    with db_factory() as db:
        assert cleanup_next(db, storage)
    assert source_key not in storage.objects
    assert component_keys[1] not in storage.objects


def test_storage_failure_keeps_garment_and_retry_completes(
    client, headers, asset_id, worker, storage, db_factory
):
    with db_factory() as db:
        db.get(MediaAsset, asset_id).metadata_json = {}
        db.commit()
    garment_id = committed_garment(client, headers, asset_id, worker)
    add_component_asset(db_factory, garment_id, storage)
    original_delete = storage.delete
    calls = 0

    def fail_once(key):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("storage unavailable")
        original_delete(key)

    storage.delete = fail_once
    failed = client.delete(f"/v1/garments/{garment_id}", headers=headers)
    assert failed.status_code == 503
    assert failed.json()["detail"]["code"] == "storage_delete_failed"
    assert client.get(f"/v1/garments/{garment_id}", headers=headers).status_code == 200

    storage.delete = original_delete
    retried = client.delete(f"/v1/garments/{garment_id}", headers=headers)
    assert retried.status_code == 204
    assert client.get(f"/v1/garments/{garment_id}", headers=headers).status_code == 404


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.integration)])
def deletion_case(request, client, db_factory, image_bytes, storage, providers, settings):
    runtime = (
        request.getfixturevalue("postgres_runtime")
        if request.param == "postgres"
        else SimpleNamespace(client=client, factory=db_factory)
    )
    client = runtime.client
    headers = auth_headers(client, "recommendation-delete@example.com")
    asset_id = upload(client, headers, image_bytes)
    with runtime.factory() as db:
        db.get(MediaAsset, asset_id).metadata_json = {}
        db.commit()
        target_id = committed_garment(
            client, headers, asset_id, JobWorker(db, storage, providers, settings)
        )
    add_component_asset(runtime.factory, target_id, storage)
    with runtime.factory() as db:
        target = db.get(Garment, target_id)
        other_asset = MediaAsset(
            user_id=target.user_id,
            object_key=f"other/{new_id()}.png",
            asset_type="garment_photo",
            mime_type="image/png",
            checksum=new_id(),
            byte_size=4,
            status="catalogued",
        )
        db.add(other_asset)
        db.flush()
        other = Garment(
            user_id=target.user_id,
            source_asset_id=other_asset.id,
            category="pants",
            tags=[],
            details_json={},
        )
        db.add(other)
        db.flush()
        sessions, affected = [], []
        kept = None
        for index in range(2):
            session = RecommendationSession(
                user_id=target.user_id,
                idempotency_key=f"delete-{index}",
                request_hash="test",
                context_json={
                    "version": 1,
                    "event": {"id": "work"},
                    "style": {"id": "formal", "source": "manual"},
                    "weather": None,
                    "transports": [],
                },
                engine_version="test",
                weights_snapshot={},
                missing_categories=[],
            )
            db.add(session)
            db.flush()
            sessions.append(session.id)
            combinations = [[target, other], [other]] if index == 0 else [[target]]
            for rank, garments in enumerate(combinations, 1):
                outfit = RecommendedOutfit(
                    session_id=session.id,
                    rank=rank,
                    score=0,
                    breakdown=dict.fromkeys(
                        ["context", "style", "color", "weather", "repetition", "personalization"],
                        0,
                    ),
                    explanation=[],
                    features_snapshot=[],
                    creation_features=[],
                )
                db.add(outfit)
                db.flush()
                for garment in garments:
                    snapshot = garment_snapshot(garment)
                    db.add(
                        RecommendedItem(
                            outfit_id=outfit.id,
                            garment_id=garment.id,
                            role=snapshot.role,
                            snapshot=snapshot.model_dump(mode="json"),
                        )
                    )
                db.add(
                    RecommendationFeedback(
                        user_id=target.user_id,
                        outfit_id=outfit.id,
                        signal="like",
                        features_snapshot=[],
                    )
                )
                if target in garments:
                    affected.append(outfit.id)
                else:
                    kept = outfit.id
        db.commit()
    return SimpleNamespace(
        client=client,
        factory=runtime.factory,
        headers=headers,
        target=target_id,
        other=other.id,
        sessions=sessions,
        affected=affected,
        kept=kept,
        url=f"/v1/garments/{target_id}?delete_recommended_outfits=true",
    )


@pytest.mark.parametrize("query", ["", "?delete_recommended_outfits=false"])
def test_recommendation_delete_requires_confirmation(deletion_case, storage, monkeypatch, query):
    case = deletion_case
    before = dict(storage.objects)
    calls = []
    monkeypatch.setattr(storage, "delete", calls.append)
    response = case.client.delete(f"/v1/garments/{case.target}{query}", headers=case.headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "recommendation_delete_confirmation_required"
    assert storage.objects == before
    assert calls == []
    with case.factory() as db:
        assert db.get(Garment, case.target)
        assert len(list(db.scalars(select(RecommendedOutfit)))) == 3
        assert len(list(db.scalars(select(RecommendationFeedback)))) == 3


def test_delete_removes_whole_related_outfits_and_feedback_keeps_sessions(deletion_case):
    case = deletion_case
    response = case.client.delete(case.url, headers=case.headers)
    assert response.status_code == 204, response.text
    with case.factory() as db:
        assert db.get(Garment, case.target) is None
        assert db.get(Garment, case.other)
        assert list(db.scalars(select(RecommendedOutfit.id))) == [case.kept]
        assert list(db.scalars(select(RecommendationFeedback.outfit_id))) == [case.kept]
        assert list(db.scalars(select(RecommendedItem.outfit_id))) == [case.kept]
    first = case.client.get(f"/v1/recommendations/{case.sessions[0]}", headers=case.headers)
    assert [outfit["id"] for outfit in first.json()["outfits"]] == [case.kept]
    empty = case.client.get(f"/v1/recommendations/{case.sessions[1]}", headers=case.headers)
    assert empty.status_code == 200
    assert empty.json()["outfits"] == []
    assert len(case.client.get("/v1/recommendations", headers=case.headers).json()) == 2


def test_constraint_failure_on_flush_does_not_delete_storage(deletion_case, storage, monkeypatch):
    case = deletion_case
    with case.factory() as db:
        # Mô phỏng một liên kết RESTRICT ngoài các kiểm tra nghiệp vụ đã biết.
        db.execute(
            text("CREATE TABLE deletion_blocker (garment_id VARCHAR(36) REFERENCES garments(id))")
        )
        db.execute(
            text("INSERT INTO deletion_blocker (garment_id) VALUES (:id)"), {"id": case.target}
        )
        db.commit()
    calls = []
    monkeypatch.setattr(storage, "delete", calls.append)
    response = case.client.delete(case.url, headers=case.headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "garment_delete_conflict"
    assert calls == []
    with case.factory() as db:
        assert db.get(Garment, case.target)
        assert len(list(db.scalars(select(RecommendedOutfit)))) == 3
        assert len(list(db.scalars(select(RecommendationFeedback)))) == 3
        candidate = db.scalar(
            select(GarmentCandidate).where(GarmentCandidate.garment_id == case.target)
        )
        assert candidate.status == "committed"


def test_storage_failure_rolls_back_outfits_then_retry_with_missing_objects(
    deletion_case, storage, monkeypatch
):
    case = deletion_case
    original_delete = storage.delete
    calls = []

    def fail_second(key):
        calls.append(key)
        if len(calls) == 2:
            raise RuntimeError("storage unavailable")
        original_delete(key)

    monkeypatch.setattr(storage, "delete", fail_second)
    failed = case.client.delete(case.url, headers=case.headers)
    assert failed.status_code == 503
    assert len(calls) == 2
    assert calls[0] not in storage.objects
    with case.factory() as db:
        assert db.get(Garment, case.target)
        assert len(list(db.scalars(select(RecommendedOutfit)))) == 3
        assert len(list(db.scalars(select(RecommendationFeedback)))) == 3
        assert db.scalar(select(GarmentCandidate).where(GarmentCandidate.garment_id == case.target))
    # Bao gồm trường hợp mọi object đã mất sau lỗi của phiên bản cũ.
    storage.objects.clear()
    monkeypatch.setattr(storage, "delete", original_delete)
    assert case.client.delete(case.url, headers=case.headers).status_code == 204
