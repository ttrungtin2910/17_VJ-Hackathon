import pytest
from pydantic import SecretStr, ValidationError

from garblo.config import Settings
from garblo.jobs import new_snapshot


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
