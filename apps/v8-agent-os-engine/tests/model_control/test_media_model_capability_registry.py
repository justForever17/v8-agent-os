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

    seedance_lite = media_model_capability_registry.find("volcengine_seedance", "doubao-seed-2-0-lite-260428", "video.reference_to_video")
    assert seedance_lite
    assert seedance_lite["nativeAudio"] is True
    assert seedance_lite["referenceInputs"]["audio"]["maxCount"] == 3

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


def test_agnes_and_minimax_media_capabilities_keep_model_specific_operations():
    agnes_image = media_model_capability_registry.find("agnes_image", "agnes-image-2.1-flash", "image.edit")
    agnes_video = media_model_capability_registry.find("agnes_video", "agnes-video-v2.0", "video.first_last_frame")
    minimax_s2v = media_model_capability_registry.find("minimax_video", "S2V-01", "video.reference_to_video")
    minimax_fast = media_model_capability_registry.find("minimax_video", "MiniMax-Hailuo-2.3-Fast", "video.image_to_video")
    minimax_music = media_model_capability_registry.find("minimax_music", "music-2.6", "music.generate")

    assert agnes_image and agnes_image["inputModalities"] == ["text", "image"]
    assert agnes_video and agnes_video["duration"]["numFramesRule"] == "8n+1"
    assert minimax_s2v and minimax_s2v["operationKinds"] == ["video.reference_to_video"]
    assert minimax_fast and minimax_fast["operationKinds"] == ["video.image_to_video"]
    assert minimax_music and minimax_music["outputStreams"] == ["audio"]
