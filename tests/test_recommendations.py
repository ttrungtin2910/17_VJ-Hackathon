import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Barrier, local

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from garblo.database import Base
from garblo.models import Garment, Look, User
from garblo.recommendations import engine, schemas, service
from garblo.recommendations.models import (
    RecommendationFeedback,
    RecommendationSession,
    RecommendedItem,
    RecommendedOutfit,
)
from tests.helpers import auth_headers, catalogue
from tests.test_catalogue_pipeline import v2_provider

CONTEXT = {
    "version": 1,
    "event": {"id": "work"},
    "style": {"id": "smart-casual", "source": "manual"},
    "weather": None,
    "transports": [],
}


def create(client, headers, key="first", context=None, **kwargs):
    response = client.post(
        "/v1/recommendations",
        headers={**headers, "Idempotency-Key": key},
        json={"context": context or deepcopy(CONTEXT), **kwargs},
    )
    assert response.status_code == 201, response.text
    return response.json()


def outfit_path(session, outfit=None):
    outfit = outfit or session["outfits"][0]
    return f"/v1/recommendations/{session['id']}/outfits/{outfit['id']}"


def edit_payload(outfit, **overrides):
    return {
        "expected_revision": outfit["revision"],
        "operation": "replace",
        "target_garment_id": outfit["items"][0]["garment_id"],
        "locked_garment_ids": [],
        "excluded_garment_ids": [],
        **overrides,
    }


@pytest.fixture()
def wardrobe(client, headers, db, asset_id):
    user_id = client.get("/v1/me", headers=headers).json()["id"]

    def add(categories=("shirt", "shirt", "shirt", "pants", "shoes"), **kwargs):
        result = []
        for category in categories:
            garment = Garment(
                user_id=user_id,
                source_asset_id=asset_id,
                category=category,
                color="black",
                material="cotton",
                tags=["smart-casual"],
                details_json={"styles": ["smart-casual"]},
                **kwargs,
            )
            db.add(garment)
            result.append(garment)
        db.commit()
        return result

    return add


def test_v2_commit_top3_edit_feedback_new_session(client, headers, asset_id, worker, db, providers):
    items = [
        {
            "category": category,
            "color": "đen",
            "material": "cotton",
            "season": None,
            "tags": [],
            "details": {"styles": ["smart-casual"], "display_name": category},
        }
        for category in ("áo", "shirt", "top", "quần", "giày")
    ]
    v2_provider(worker, items)
    job_id = catalogue(client, headers, asset_id)
    worker.process_next()
    job = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert job["status"] == "succeeded", job
    committed = client.post(
        f"/v1/assets/{asset_id}/commit",
        headers=headers,
        json={"candidate_ids": job["output_ref"]["candidate_ids"]},
    ).json()["garment_ids"]
    session = create(client, headers)
    assert len(session["outfits"]) == 3
    assert db.scalar(select(func.count()).select_from(Look)) == 0
    for outfit in session["outfits"]:
        assert {item["role"] for item in outfit["items"]} == {"top", "bottom", "shoes"}
        assert set(item["garment_id"] for item in outfit["items"]) <= set(committed)
        assert outfit["score"] == sum(outfit["breakdown"].values())
        assert "không phải" in outfit["explanation"][-1]
    outfit = session["outfits"][0]
    unchanged = outfit["items"][1:]
    response = client.post(
        outfit_path(session) + "/edits",
        headers=headers,
        json=edit_payload(outfit, locked_garment_ids=[i["garment_id"] for i in unchanged]),
    )
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["revision"] == 2
    assert edited["items"][1:] == unchanged
    assert edited["items"][0]["garment_id"] != outfit["items"][0]["garment_id"]
    feedback = client.put(
        outfit_path(session) + "/feedback", headers=headers, json={"signal": "like"}
    )
    assert feedback.status_code == 200
    following = create(client, headers, "following")
    assert following["weights_snapshot"]
    assert following["outfits"][0]["breakdown"]["personalization"] > 0
    old = client.get(f"/v1/recommendations/{session['id']}", headers=headers).json()
    assert old["weights_snapshot"] == {}
    assert old["outfits"][0]["breakdown"]["personalization"] == 0
    assert all(db.get(Garment, garment_id).wear_count == 0 for garment_id in committed)
    assert providers.calls == []  # Phối/sửa/feedback không gọi fake hay provider thật.


@pytest.mark.parametrize(
    "category,role",
    [
        ("top", "top"),
        ("shirt", "top"),
        ("áo", "top"),
        ("Áo sơ mi", "top"),
        ("bottom", "bottom"),
        ("pants", "bottom"),
        ("quần", "bottom"),
        ("giày", "shoes"),
        ("shoes", "shoes"),
        ("dress", "dress"),
        ("váy liền", "dress"),
        ("áo khoác", "outerwear"),
        ("phụ kiện", "accessory"),
        (None, None),
        ("không rõ", None),
    ],
)
def test_category_mapping(category, role):
    assert engine.category_role(category) == role


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.pop("event"),
        lambda c: c.pop("style"),
        lambda c: c.update(event=None),
        lambda c: c.update(version=1.0),
        lambda c: c.update(event={"id": "other", "custom": "   "}),
        lambda c: c.update(style={"id": "other", "custom": "", "source": "manual"}),
        lambda c: c.update(weather={"id": "other"}),
        lambda c: c.update(transports=["walk", "walk"]),
        lambda c: c.update(transports=["plane"]),
    ],
)
def test_context_validation(mutate):
    context = deepcopy(CONTEXT)
    mutate(context)
    with pytest.raises(ValidationError):
        schemas.RecommendationContext.model_validate(context)


def test_optional_weather_transport_location_no_invention(client, headers, wardrobe):
    wardrobe(("top", "bottom", "shoes"))
    context = deepcopy(CONTEXT)
    context.pop("weather")
    context.pop("transports")
    session = create(client, headers, context=context, location="Hà Nội")
    assert session["location"] == "Hà Nội"
    assert session["context"]["weather"] is None
    assert session["context"]["transports"] == []
    assert session["outfits"][0]["breakdown"]["weather"] == 0


@pytest.mark.parametrize(
    "categories,count",
    [
        ((), 0),
        (("shirt",), 0),
        (("shirt", "pants"), 0),
        (("shirt", "pants", "shoes"), 1),
        (("dress", "shoes"), 1),
        (("dress", "dress", "shoes"), 2),
        (("accessory", "shoes"), 0),
    ],
)
def test_insufficient_and_dress_no_fabricated_top3(client, headers, wardrobe, categories, count):
    wardrobe(categories)
    session = create(client, headers)
    assert len(session["outfits"]) == count
    assert session["reason"]
    if count == 0:
        assert session["missing_categories"]
    else:
        assert session["missing_categories"] == []


def test_idempotency_required_replay_conflict_and_owner_scope(client, headers, wardrobe, db):
    wardrobe()
    assert (
        client.post("/v1/recommendations", headers=headers, json={"context": CONTEXT}).status_code
        == 422
    )
    assert (
        client.post(
            "/v1/recommendations",
            headers={**headers, "Idempotency-Key": " "},
            json={"context": CONTEXT},
        ).status_code
        == 422
    )
    first = create(client, headers)
    assert create(client, headers) == first
    response = client.post(
        "/v1/recommendations",
        headers={**headers, "Idempotency-Key": "first"},
        json={"context": CONTEXT, "location": "Huế"},
    )
    assert response.status_code == 409
    other = auth_headers(client, "other-idempotency@example.com")
    assert create(client, other)["id"] != first["id"]
    assert db.scalar(select(func.count()).select_from(RecommendationSession)) == 2


def test_ownership_session_membership_active_only(client, headers, wardrobe, db, asset_id):
    owned = wardrobe()
    owned[0].catalog_status = "archived"
    db.commit()
    other = auth_headers(client, "attacker@example.com")
    other_id = client.get("/v1/me", headers=other).json()["id"]
    foreign = Garment(
        user_id=other_id, source_asset_id=asset_id, category="top", color="red", tags=[]
    )
    db.add(foreign)
    db.commit()
    first = create(client, headers)
    assert len(first["outfits"]) == 2
    for outfit in first["outfits"]:
        assert not {owned[0].id, foreign.id} & {i["garment_id"] for i in outfit["items"]}
    assert client.get(f"/v1/recommendations/{first['id']}", headers=other).status_code == 404
    assert client.get("/v1/recommendations", headers=other).json() == []
    assert (
        client.post(
            outfit_path(first) + "/edits", headers=other, json=edit_payload(first["outfits"][0])
        ).status_code
        == 404
    )
    assert (
        client.put(
            outfit_path(first) + "/feedback", headers=other, json={"signal": "like"}
        ).status_code
        == 404
    )
    second = create(client, headers, "second")
    wrong = f"/v1/recommendations/{second['id']}/outfits/{first['outfits'][0]['id']}"
    assert (
        client.post(
            wrong + "/edits", headers=headers, json=edit_payload(first["outfits"][0])
        ).status_code
        == 404
    )
    assert (
        client.put(wrong + "/feedback", headers=headers, json={"signal": "like"}).status_code == 404
    )
    payload = edit_payload(first["outfits"][0], replacement_garment_id=foreign.id)
    assert (
        client.post(outfit_path(first) + "/edits", headers=headers, json=payload).status_code == 422
    )


@pytest.mark.parametrize(
    "attack",
    [
        "locked",
        "unknown_locked",
        "duplicate",
        "wrong_role",
        "unknown",
        "exclude_other",
        "locked_excluded",
    ],
)
def test_edit_rejects_invalid_atomic(client, headers, wardrobe, attack):
    wardrobe()
    session = create(client, headers)
    outfit = session["outfits"][0]
    target, other = outfit["items"][:2]
    payload = edit_payload(outfit)
    if attack == "locked":
        payload["locked_garment_ids"] = [target["garment_id"]]
    elif attack == "unknown_locked":
        payload["locked_garment_ids"] = ["not-present"]
    elif attack in ("duplicate", "wrong_role"):
        payload["replacement_garment_id"] = other["garment_id"]
    elif attack == "unknown":
        payload["replacement_garment_id"] = "not-present"
    elif attack == "exclude_other":
        payload["excluded_garment_ids"] = [other["garment_id"]]
    else:
        payload["locked_garment_ids"] = payload["excluded_garment_ids"] = [other["garment_id"]]
    response = client.post(outfit_path(session) + "/edits", headers=headers, json=payload)
    assert response.status_code == 422, response.text
    assert client.get(f"/v1/recommendations/{session['id']}", headers=headers).json() == session


def test_explicit_constraints_exclusions_revision_and_item_ids(client, headers, wardrobe, db):
    garments = wardrobe()
    extra = wardrobe(("shirt",))[0]
    extra.color = "đỏ"
    db.commit()
    session = create(client, headers)
    outfit = session["outfits"][0]
    if outfit["items"][0]["garment_id"] == extra.id:
        outfit = next(o for o in session["outfits"] if o["items"][0]["garment_id"] != extra.id)
    old_rows = {
        row.role: row.id
        for row in db.scalars(
            select(RecommendedItem).where(RecommendedItem.outfit_id == outfit["id"])
        )
    }
    payload = edit_payload(
        outfit,
        replacement_garment_id=extra.id,
        constraints={"colors": ["red"], "styles": ["smart-casual"]},
    )
    response = client.post(outfit_path(session, outfit) + "/edits", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["items"][0]["garment_id"] == extra.id
    assert edited["items"][1:] == outfit["items"][1:]
    assert old_rows == {
        row.role: row.id
        for row in db.scalars(
            select(RecommendedItem).where(RecommendedItem.outfit_id == outfit["id"])
        )
    }
    assert (
        client.post(
            outfit_path(session, outfit) + "/edits", headers=headers, json=payload
        ).status_code
        == 409
    )
    excluded = [g.id for g in garments if g.category == "shirt"]
    payload = edit_payload(edited, excluded_garment_ids=excluded)
    assert (
        client.post(
            outfit_path(session, outfit) + "/edits", headers=headers, json=payload
        ).status_code
        == 422
    )


@pytest.mark.parametrize("change", ["archive", "role", "weather"])
def test_edit_revalidates_all_current_items(client, headers, wardrobe, db, change):
    wardrobe()
    context = deepcopy(CONTEXT)
    context["weather"] = {"id": "rainy"}
    session = create(client, headers, context=context)
    outfit = session["outfits"][0]
    other = db.get(Garment, outfit["items"][1]["garment_id"])
    if change == "archive":
        other.catalog_status = "archived"
    elif change == "role":
        other.category = "dress"
    else:
        other.material = "suede"
    db.commit()
    assert (
        client.post(
            outfit_path(session) + "/edits", headers=headers, json=edit_payload(outfit)
        ).status_code
        == 409
    )
    assert client.get(f"/v1/recommendations/{session['id']}", headers=headers).json() == session


def test_feedback_latest_upsert_bounded_snapshot_and_no_wear(client, headers, wardrobe, db):
    garments = wardrobe()
    session = create(client, headers)
    path = outfit_path(session) + "/feedback"
    for _ in range(20):
        assert client.put(path, headers=headers, json={"signal": "like"}).status_code == 200
    first = create(client, headers, "like-once")
    for _ in range(10):
        client.put(path, headers=headers, json={"signal": "dislike"})
        client.put(path, headers=headers, json={"signal": "like"})
    second = create(client, headers, "like-retry")
    assert first["weights_snapshot"] == second["weights_snapshot"]
    assert max(map(abs, second["weights_snapshot"].values())) <= engine.FEATURE_CAP
    assert db.scalar(select(func.count()).select_from(RecommendationFeedback)) == 1
    feedback = db.scalar(select(RecommendationFeedback))
    stored = deepcopy(feedback.features_snapshot)
    for garment in garments:
        garment.color = "red"
        garment.details_json = {"styles": ["sporty"]}
        garment.catalog_status = "archived"
    db.commit()
    assert client.put(path, headers=headers, json={"signal": "dislike"}).status_code == 200
    next_session = create(client, headers, "dislike")
    assert next_session["weights_snapshot"] == {k: -v for k, v in first["weights_snapshot"].items()}
    db.refresh(feedback)
    assert feedback.features_snapshot == stored
    assert all(db.get(Garment, g.id).wear_count == 0 for g in garments)
    before = deepcopy(session)
    after = client.get(f"/v1/recommendations/{session['id']}", headers=headers).json()
    after["outfits"][0]["feedback"] = None
    assert after == before


def test_apply_edit_flush_only_can_rollback(client, headers, wardrobe, db_factory):
    wardrobe()
    session = create(client, headers)
    outfit = session["outfits"][0]
    user_id = client.get("/v1/me", headers=headers).json()["id"]
    with db_factory() as transaction:
        result = service.apply_edit(
            transaction,
            user_id,
            session["id"],
            outfit["id"],
            schemas.EditRequest.model_validate(edit_payload(outfit)),
            commit=False,
        )
        assert result.revision == 2
        assert transaction.in_transaction()
        transaction.rollback()
    assert client.get(f"/v1/recommendations/{session['id']}", headers=headers).json() == session


def test_swapped_current_categories_cannot_evade_slot_validation(client, headers, wardrobe, db):
    wardrobe()
    session = create(client, headers)
    outfit = session["outfits"][0]
    top, bottom = outfit["items"][:2]
    db.get(Garment, top["garment_id"]).category = "bottom"
    db.get(Garment, bottom["garment_id"]).category = "top"
    db.commit()
    response = client.post(
        outfit_path(session) + "/edits", headers=headers, json=edit_payload(outfit)
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "wardrobe_changed"
    assert client.get(f"/v1/recommendations/{session['id']}", headers=headers).json() == session


def snapshot(id, category, **kwargs):
    return schemas.GarmentSnapshot(
        garment_id=id,
        role=engine.category_role(category),
        category=category,
        color="black",
        material=None,
        season=None,
        tags=[],
        details={},
        wear_count=0,
        last_worn_at=None,
        source_asset_id="asset",
        component_asset_id=None,
    ).model_copy(update=kwargs)


def test_weather_evidence_unknown_and_outerwear():
    context = schemas.RecommendationContext.model_validate({**CONTEXT, "weather": {"id": "cold"}})
    items = [
        snapshot("t", "top"),
        snapshot("b", "bottom"),
        snapshot("s", "shoes"),
        snapshot("j", "áo khoác", material="len"),
    ]
    result = engine.recommend(items, context, {})
    assert result.outfits[0].items[-1].role == "outerwear"
    assert engine.weather_allowed(items[0], context)  # Unknown != incompatible.
    assert not engine.weather_allowed(snapshot("bad", "top", tags=["summer only"]), context)
    uncertain = snapshot("u", "top", tags=["summer only"], details={"uncertain_fields": ["tags"]})
    assert engine.weather_allowed(uncertain, context)
    rainy = context.model_copy(update={"weather": schemas.WeatherChoice(id="rainy")})
    assert not engine.weather_allowed(snapshot("suede", "shoes", material="da lộn"), rainy)
    assert engine.weather_allowed(
        snapshot("u", "shoes", material="suede", details={"uncertain_fields": ["material"]}), rainy
    )


def test_uncertain_attributes_do_not_satisfy_explicit_edit_constraints():
    item = snapshot(
        "uncertain",
        "top",
        color="black",
        material="cotton",
        details={"uncertain_fields": ["color", "material"]},
    )
    assert not engine.matches_constraints(item, schemas.EditConstraints(colors=["black"]))
    assert not engine.matches_constraints(item, schemas.EditConstraints(materials=["cotton"]))


def test_deterministic_bounded_diverse_and_personalization_caps():
    context = schemas.RecommendationContext.model_validate({**CONTEXT, "weather": {"id": "cold"}})
    wardrobe = [
        snapshot(f"{role}-{i:03}", role)
        for role in ("top", "bottom", "shoes", "dress", "outerwear")
        for i in range(30)
    ]
    result = engine.recommend(wardrobe, context, {})
    reverse = engine.recommend(list(reversed(wardrobe)), context, {})
    assert [o.stable_key for o in result.outfits] == [o.stable_key for o in reverse.outfits]
    assert result.combinations_evaluated <= engine.MAX_COMBINATIONS
    assert len({o.stable_key for o in result.outfits}) == 3
    for outfit in result.outfits:
        assert engine.complete(outfit.items, context)
        assert all(
            int(item.garment_id.rsplit("-", 1)[1]) < engine.POOL_PER_ROLE for item in outfit.items
        )
    weights = engine.learn_weights([("like", ["style:formal", "style:formal"])] * 10000)
    assert 0 < weights["style:formal"] <= engine.FEATURE_CAP
    score = engine.score_outfit(
        wardrobe[:3], context, {f: 100 for f in engine.features(wardrobe[:3])}
    )
    assert score.breakdown.personalization == engine.PERSONALIZATION_CAP


def test_quality_fixture():
    fixture = json.loads((Path(__file__).parent / "fixtures" / "outfit-quality.json").read_text())
    for case in fixture:
        context = schemas.RecommendationContext.model_validate(case["context"])
        wardrobe = [
            snapshot(
                item["id"],
                item["category"],
                tags=item.get("tags", []),
                material=item.get("material"),
            )
            for item in case["wardrobe"]
        ]
        result = engine.recommend(wardrobe, context, case.get("weights", {}))
        assert result.outfits, case["name"]
        first = set(result.outfits[0].stable_key)
        assert set(case["must_include"]) <= first, case["name"]
        assert not set(case.get("must_exclude", [])) & first, case["name"]


def test_concurrent_edit_revision_compare_and_swap(tmp_path, monkeypatch):
    # DB file: hai connections/transactions độc lập, không dùng SQLite StaticPool chung.
    sql_engine = create_engine(
        f"sqlite:///{tmp_path / 'revision.db'}", connect_args={"timeout": 10}
    )
    Base.metadata.create_all(sql_engine)
    factory = sessionmaker(sql_engine, expire_on_commit=False)
    with factory() as db:
        user = User(
            email="concurrent@example.com", display_name="Concurrent", password_hash="unused"
        )
        db.add(user)
        db.flush()
        user_id = user.id
        # Test CAS không phụ thuộc media service; SQLite fixture này không bật FK.
        for category in ("top", "top", "top", "bottom", "shoes"):
            db.add(
                Garment(user_id=user_id, source_asset_id="test-asset", category=category, tags=[])
            )
        db.commit()
        session = service.create_session(
            db,
            user_id,
            schemas.CreateSessionRequest(
                context=schemas.RecommendationContext.model_validate(CONTEXT)
            ),
            "create",
        )
    outfit = session.outfits[0]
    payload = schemas.EditRequest(
        expected_revision=1, operation="replace", target_garment_id=outfit.items[0].garment_id
    )
    barrier, state = Barrier(2), local()
    original = engine.score_outfit

    def synchronized(*args, **kwargs):
        if not getattr(state, "waiting", False):
            state.waiting = True
            barrier.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(engine, "score_outfit", synchronized)

    def execute():
        with factory() as db:
            try:
                return service.apply_edit(db, user_id, session.id, outfit.id, payload).revision
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: execute(), range(2)))
        assert sorted(results) == [2, 409]
        with factory() as db:
            stored = db.get(RecommendedOutfit, outfit.id)
            assert stored.revision == 2
            assert (
                len(
                    list(
                        db.scalars(
                            select(RecommendedItem).where(RecommendedItem.outfit_id == outfit.id)
                        )
                    )
                )
                == 3
            )
    finally:
        sql_engine.dispose()
