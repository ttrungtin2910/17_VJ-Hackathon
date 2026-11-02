import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from garblo.config import Settings
from garblo.models import AiJob, MediaAsset
from tests.helpers import catalogue, upload


@pytest.mark.parametrize("with_fpt", [False, True])
@pytest.mark.parametrize("body", [None, {}, {"vlm_provider": "openai"}])
def test_openai_always_default(
    client, headers, asset_id, settings, worker, providers, with_fpt, body
):
    settings.fpt_vlm_api_key = SecretStr("test-fpt") if with_fpt else None
    job_id = catalogue(client, headers, asset_id, body)
    assert worker.process_next()
    response = client.get(f"/v1/jobs/{job_id}", headers=headers).json()
    assert response["status"] == "succeeded", response
    assert response["model_provider"] == "openai"
    assert providers.calls == [("analyze", "openai", "test-vision", "image/png")]


def test_fpt_explicit_and_provider_is_pinned(
    client, headers, asset_id, settings, worker, providers, db
):
    settings.fpt_vlm_api_key = SecretStr("test-fpt")
    job_id = catalogue(client, headers, asset_id, {"vlm_provider": "fpt"})
    job = db.get(AiJob, job_id)
    assert job.input_ref["provider"] == "fpt"
    assert job.input_ref["model"] == "Qwen2.5-VL-7B-Instruct"
    assert job.input_ref["requested_at"]
    settings.fpt_vlm_model = "changed-after-enqueue"
    assert worker.process_next()
    assert providers.calls[0][1:3] == ("fpt", "Qwen2.5-VL-7B-Instruct")


@pytest.mark.parametrize(
    "payload,status", [({"vlm_provider": "fpt"}, 503), ({"vlm_provider": "x"}, 422)]
)
def test_invalid_or_missing_config_never_enqueues(client, headers, asset_id, db, payload, status):
    response = client.post(f"/v1/assets/{asset_id}/catalogize", headers=headers, json=payload)
    assert response.status_code == status
    assert db.scalar(select(func.count()).select_from(AiJob)) == 0


def test_legacy_catalogue_does_not_require_image_model(client, headers, asset_id, settings):
    settings.catalogue_schema_version = 1
    settings.openai_image_model = None
    catalogue(client, headers, asset_id)


def test_catalogue_v2_missing_image_model_can_resume_same_asset(
    client, headers, asset_id, settings, db
):
    settings.openai_image_model = None
    response = client.post(f"/v1/assets/{asset_id}/catalogize", headers=headers, json={})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "provider_not_configured"
    assert db.scalar(select(func.count()).select_from(AiJob)) == 0
    assert db.get(MediaAsset, asset_id).status == "uploaded"

    settings.openai_image_model = "test-image"
    job_id = catalogue(client, headers, asset_id, {})
    assert db.get(AiJob, job_id).input_ref["image_model"] == "test-image"
    assert db.scalar(select(func.count()).select_from(AiJob)) == 1


def test_both_providers_do_not_share_selector(
    client, headers, asset_id, image_bytes, settings, worker, providers
):
    settings.fpt_vlm_api_key = SecretStr("test-fpt")
    catalogue(client, headers, asset_id)
    second = upload(client, headers, image_bytes)
    catalogue(client, headers, second, {"vlm_provider": "fpt"})
    assert worker.process_next()
    assert worker.process_next()
    assert [call[1] for call in providers.calls] == ["openai", "fpt"]


def test_fpt_key_only_after_settings_reload(tmp_path, monkeypatch):
    for name in ("FPT_VLM_API_KEY", "FPT_VLM_BASE_URL", "FPT_VLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    assert not Settings(_env_file=env).analysis_is_configured("fpt")
    env.write_text("FPT_VLM_API_KEY=test-only-key\n", encoding="utf-8")
    restarted = Settings(_env_file=env)
    assert restarted.analysis_is_configured("fpt")
    assert restarted.fpt_vlm_model == "Qwen2.5-VL-7B-Instruct"
    assert restarted.fpt_vlm_base_url == "https://mkp-api.fptcloud.com/v1"


@pytest.mark.parametrize("key", ["", "   "])
def test_empty_fpt_key_is_unavailable(key):
    assert Settings(_env_file=None, fpt_vlm_api_key=key).fpt_vlm_api_key is None


def test_cached_settings_need_process_reload_to_receive_fpt_key(tmp_path, monkeypatch):
    from garblo.config import get_settings

    for key in ("FPT_VLM_API_KEY", "FPT_VLM_BASE_URL", "FPT_VLM_MODEL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    get_settings.cache_clear()
    try:
        assert get_settings().fpt_vlm_api_key is None
        env.write_text("FPT_VLM_API_KEY=test-key-only\n", encoding="utf-8")
        assert get_settings().fpt_vlm_api_key is None
        get_settings.cache_clear()  # Tương đương process mới sau restart.
        assert get_settings().analysis_is_configured("fpt")
    finally:
        get_settings.cache_clear()
