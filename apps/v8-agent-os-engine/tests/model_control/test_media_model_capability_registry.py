import json
from pathlib import Path

from core.media_model_capability_registry import media_model_capability_registry
from core.model_provider_catalog import model_provider_catalog

REPORT_PATH = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "model_catalog"
    / "media_model_capability_registry_unresolved_report.json"
)


def test_media_model_capability_registry_covers_matrix_and_doc_seed():
    payload = media_model_capability_registry.load()
    assert len(payload.get("providers") or []) >= 32
    assert len(payload.get("models") or []) >= 78
    report_models = {item["canonicalModelId"] for item in payload.get("models") or []}
    assert "doubao-seedance-2-0-260128" in report_models
    assert "nano-banana-pro" in report_models
    assert "flux.2" in report_models
    assert "stable-audio-2.5" in report_models


def test_media_registry_exact_native_audio_does_not_inherit_by_family_name():
    seedance_20 = media_model_capability_registry.find("volcengine_seedance", "doubao-seedance-2-0-260128", "video.text_to_video")
    assert seedance_20
    assert seedance_20["nativeAudio"] is True
    assert seedance_20["audioPreservationPolicy"] == "preserve_native_audio_by_default"

    seedance_lite = media_model_capability_registry.find("volcengine_seedance", "doubao-seedance-2-0-mini", "video.reference_to_video")
    assert seedance_lite
    assert seedance_lite["nativeAudio"] is True
    assert seedance_lite["referenceInputs"]["audio"]["maxCount"] == 3

    seedance_25 = media_model_capability_registry.find("volcengine_seedance", "doubao-seedance-2-5", "video.reference_to_video")
    assert seedance_25
    assert seedance_25["duration"]["secondsMax"] == 30

    minimax_h3 = media_model_capability_registry.find("minimax_video", "MiniMax-H3", "video.reference_to_video")
    assert minimax_h3
    assert minimax_h3["inputModalities"] == ["text", "image", "video", "audio"]
    assert minimax_h3["referenceInputs"]["video"]["maxCount"] == 3

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


def test_multimodal_reference_models_expose_both_reference_modes_in_model_hub():
    minimax = model_provider_catalog.get_provider("minimax_video")
    seedance = model_provider_catalog.get_provider("volcengine_seedance")
    assert minimax and seedance

    h3 = model_provider_catalog.normalize_model(minimax, "MiniMax-H3")
    seedance_20 = model_provider_catalog.normalize_model(seedance, "doubao-seedance-2-0-260128")

    expected = {"video.image_reference", "video.multimodal_reference"}
    assert expected.issubset(set(h3["mediaLimits"]["capabilityModes"]))
    assert expected.issubset(set(seedance_20["mediaLimits"]["capabilityModes"]))
    assert h3["mediaLimits"]["endpointPath"] == "/v2/video_generation"


def test_seedance_2x_catalog_models_share_the_multimodal_reference_contract():
    seedance = model_provider_catalog.get_provider("volcengine_seedance")
    assert seedance

    seedance_2x = [item for item in seedance["models"] if item["id"].startswith("doubao-seedance-2-")]
    assert seedance_2x
    for item in seedance_2x:
        modes = set(item["mediaLimits"]["capabilityModes"])
        assert {"video.image_reference", "video.multimodal_reference"}.issubset(modes), item["id"]

    assert "doubao-seedance-2-0" not in {item["id"] for item in seedance_2x}
    alias = media_model_capability_registry.find("volcengine_seedance", "doubao-seedance-2-0", "video.reference_to_video")
    assert alias and alias["canonicalModelId"] == "doubao-seedance-2-0-260128"


def test_existing_media_model_backfills_missing_capability_modes_without_overriding_user_values():
    base_model = {
        "id": "v2/video_generation/MiniMax-H3",
        "type": "VIDEO",
        "operationKinds": [
            "video.text_to_video",
            "video.image_to_video",
            "video.first_last_frame",
            "video.reference_to_video",
        ],
        "mediaLimits": {
            "adapter": "minimax_video",
            "providerModelId": "MiniMax-H3",
            "operationKinds": [
                "video.text_to_video",
                "video.image_to_video",
                "video.first_last_frame",
                "video.reference_to_video",
            ],
        },
    }
    provider = {
        "id": "minimax-cn",
        "providerKind": "media_generation",
        "baseUrl": "https://api.minimaxi.com/v1",
        "models": [base_model],
    }

    inferred = model_provider_catalog.normalize_model(provider, base_model["id"])
    assert "video.multimodal_reference" in inferred["mediaLimits"]["capabilityModes"]

    provider["models"][0]["mediaLimits"]["capabilityModes"] = ["video.text_to_video"]
    explicit = model_provider_catalog.normalize_model(provider, base_model["id"])
    assert explicit["mediaLimits"]["capabilityModes"] == ["video.text_to_video"]


def test_reference_media_models_are_matrix_backed_not_doc_only():
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    assert report["catalogOnlyModels"] == []
    assert report["entriesWithMissingFields"] == []

    expected = [
        ("black_forest_labs_image", "flux.2", "image.generate"),
        ("google_gemini_image", "nano-banana-pro", "image.generate"),
        ("3d_ai_studio", "trellis.2", "model3d.generate"),
        ("aliyun_bailian_3d", "motionshop-gen3d", "model3d.generate"),
        ("csm_3d", "csm-3d-generation", "model3d.generate"),
        ("hitem3d", "sparc3d-ultra3d", "model3d.generate"),
        ("hyper3d_rodin", "rodin-gen-2", "model3d.generate"),
        ("meshy_3d", "meshy-6", "model3d.generate"),
        ("stability_3d", "stable-fast-3d", "model3d.generate"),
        ("tripo3d_placeholder", "tripo-3d-v3", "model3d.generate"),
        ("volcengine_3d_generation", "doubao-seed3d-1-0-250928", "model3d.generate"),
        ("elevenlabs_music", "elevenlabs-music", "music.generate"),
        ("google_lyria_music", "lyria-realtime", "music.generate"),
        ("minimax_music", "minimax-music-2.5", "music.generate"),
        ("stability_music", "stable-audio-2.5", "music.generate"),
        ("elevenlabs_tts", "eleven-v3", "voice.tts"),
        ("fal_tts", "f5-tts", "voice.tts"),
        ("fish_audio_tts", "fish-speech-s2", "voice.tts"),
        ("minimax_tts", "minimax-speech-2.6", "voice.tts"),
    ]
    for provider_id, model_id, operation_kind in expected:
        entry = media_model_capability_registry.find(provider_id, model_id, operation_kind)
        assert entry, f"{provider_id}::{model_id} missing {operation_kind}"
        assert "providerMatrixEntry" not in set(entry.get("missingFields") or [])


def test_matrix_backed_catalog_models_are_visible_without_api_wire_claims():
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
