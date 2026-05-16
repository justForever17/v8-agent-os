from core.media_model_capability_registry import media_model_capability_registry
from core.model_provider_catalog import model_provider_catalog


def test_media_model_capability_registry_covers_matrix_and_doc_seed():
    payload = media_model_capability_registry.load()
    assert len(payload.get("providers") or []) >= 32
    assert len(payload.get("models") or []) >= 78
    report_models = {item["canonicalModelId"] for item in payload.get("models") or []}
    assert "doubao-seedance-2-0" in report_models
    assert "nano-banana-pro" in report_models
    assert "flux.2" in report_models
    assert "stable-audio-2.5" in report_models


def test_media_registry_exact_native_audio_does_not_inherit_by_family_name():
    seedance_20 = media_model_capability_registry.find("volcengine_seedance", "doubao-seedance-2-0", "video.text_to_video")
    assert seedance_20
    assert seedance_20["nativeAudio"] is True
    assert seedance_20["audioPreservationPolicy"] == "preserve_native_audio_by_default"

    seedance_15 = media_model_capability_registry.find("volcengine_seedance", "doubao-seedance-1-5-pro", "video.text_to_video")
    assert seedance_15
    assert seedance_15["nativeAudio"] is False


def test_media_catalog_uses_registry_logo_and_keeps_chat_budget_empty():
    provider = model_provider_catalog.get_provider("openai_images")
    assert provider
    assert provider["logoAsset"] == "/model-assets/lobe/openai.svg"
    model = model_provider_catalog.normalize_model(provider, "gpt-image-2")
    assert model["contextWindow"] is None
    assert model["maxTokens"] is None
    assert model["logoAsset"] == "/model-assets/lobe/openai.svg"
    assert model["capabilitySource"] == "media_model_capability_registry"
    assert model["mediaLimits"]["mediaCapabilityRegistry"]["canonicalModelId"] == "gpt-image-2"


def test_catalog_only_doc_models_are_visible_without_api_wire_claims():
    provider = model_provider_catalog.get_provider("black_forest_labs_image")
    assert provider
    assert provider["adapter"] == "catalog_only"
    assert provider["probeStrategy"] == "catalog_only"
    model_ids = {item["id"] for item in provider["models"]}
    assert "flux.2" in model_ids
