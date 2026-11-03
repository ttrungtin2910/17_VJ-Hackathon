import pytest
from pydantic import SecretStr, ValidationError

from garblo.config import Settings
from garblo.jobs import new_snapshot


@pytest.mark.parametrize("version", [1, 2])
def test_catalogue_schema_version_from_environment(monkeypatch, version):
    monkeypatch.setenv("CATALOGUE_SCHEMA_VERSION", str(version))

    configured = Settings(_env_file=None)

    assert configured.catalogue_schema_version == version
    assert type(configured.catalogue_schema_version) is int


@pytest.mark.parametrize("version", [1, 2])
def test_catalogue_schema_version_from_dotenv(monkeypatch, tmp_path, version):
    monkeypatch.delenv("CATALOGUE_SCHEMA_VERSION", raising=False)
    env_file = tmp_path / "schema.env"
    env_file.write_text(f"CATALOGUE_SCHEMA_VERSION={version}\n", encoding="utf-8")

    configured = Settings(_env_file=env_file)

    assert configured.catalogue_schema_version == version
    assert type(configured.catalogue_schema_version) is int


@pytest.mark.parametrize("version", [1, 2])
def test_catalogue_schema_version_accepts_integers(monkeypatch, version):
    monkeypatch.delenv("CATALOGUE_SCHEMA_VERSION", raising=False)

    configured = Settings(_env_file=None, catalogue_schema_version=version)

    assert configured.catalogue_schema_version == version
    assert type(configured.catalogue_schema_version) is int


def test_catalogue_schema_version_defaults_to_two(monkeypatch):
    monkeypatch.delenv("CATALOGUE_SCHEMA_VERSION", raising=False)

    configured = Settings(_env_file=None)

    assert configured.catalogue_schema_version == 2
    assert type(configured.catalogue_schema_version) is int


@pytest.mark.parametrize("version", ["0", "3", "abc", "2.0", ""])
def test_catalogue_schema_version_rejects_invalid_environment(monkeypatch, version):
    monkeypatch.setenv("CATALOGUE_SCHEMA_VERSION", version)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert error.value.errors()[0]["loc"] == ("catalogue_schema_version",)
    assert error.value.errors()[0]["type"] == "literal_error"


def test_optional_empty_env_models_are_disabled_not_invalid_stage(settings):
    configured = Settings(
        _env_file=None,
        catalogue_screening_model="",
        catalogue_escalation_model=" ",
        fpt_screening_model="",
        fpt_escalation_model="",
        catalogue_segmentation_url="",
        catalogue_segmentation_model="",
        stylist_intent_model="",
    )
    assert configured.catalogue_screening_model is None
    assert configured.catalogue_escalation_model is None
    assert configured.fpt_screening_model is None
    assert configured.stylist_intent_model is None


def test_provider_specific_stage_models_never_cross_route(settings):
    settings.fpt_vlm_api_key = SecretStr("test-fpt")
    settings.catalogue_screening_model = "openai-screen-test"
    settings.catalogue_escalation_model = "openai-expert-test"
    settings.catalogue_native_structured_output = True
    openai = new_snapshot(settings, "openai", "catalogue")
    fpt = new_snapshot(settings, "fpt", "catalogue")
    assert openai["screening_model"] == "openai-screen-test"
    assert fpt["screening_model"] is fpt["escalation_model"] is None
    assert not fpt["native_structured_output"]
    settings.fpt_screening_model = "fpt-screen-test"
    settings.fpt_escalation_model = "fpt-expert-test"
    fpt = new_snapshot(settings, "fpt", "catalogue")
    assert fpt["screening_model"] == "fpt-screen-test"
    assert fpt["escalation_model"] == "fpt-expert-test"


@pytest.mark.parametrize(
    "url",
    [
        "file:///private",
        "http://user:password@internal/segment",
        "http://internal/segment?secret=key",
        "http://internal/segment#fragment",
        "not-a-url",
    ],
)
def test_segmentation_endpoint_only_operator_http_without_credentials(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, catalogue_segmentation_url=url)


def test_catalogue_default_capacity_and_snapshot(monkeypatch):
    for key in (
        "CATALOGUE_MAX_CALLS",
        "CATALOGUE_TOKEN_BUDGET",
        "CATALOGUE_COMPONENT_IMAGE_CONCURRENCY",
    ):
        monkeypatch.delenv(key, raising=False)
    configured = Settings(
        _env_file=None,
        openai_api_key="test",
        openai_vlm_model="test",
        openai_image_model="test-image",
    )
    snapshot = new_snapshot(configured, "openai", "catalogue")
    assert snapshot["component_image_concurrency"] == 5
    assert snapshot["max_calls"] == 12
    assert snapshot["token_budget"] == 200000
