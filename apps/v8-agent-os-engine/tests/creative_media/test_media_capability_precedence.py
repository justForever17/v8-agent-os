from core.model_endpoint_binding import persist_model_endpoint_binding, public_models_config
from runtimes.creative_media import runtime as creative_runtime


def _operation_kinds(
    monkeypatch,
    *,
    modality: str,
    adapter: str,
    explicit: list[str] | None,
    registry: list[str],
) -> list[str]:
    monkeypatch.setattr(
        creative_runtime,
        "_registry_operation_kinds_for_model",
        lambda **_: list(registry),
    )
    model_data = {"id": "manual-model"}
    if explicit is not None:
        model_data["operationKinds"] = list(explicit)
        model_data["mediaLimits"] = {"capabilityModes": ["manual-confirmed"]}
    return creative_runtime.CreativeMediaRuntime._operation_kinds_for_candidate(
        object(),
        modality=modality,
        provider_id="manual-provider",
        adapter=adapter,
        provider_meta={},
        model_data=model_data,
    )


def test_manual_image_capabilities_override_registry_without_expanding(monkeypatch):
    assert _operation_kinds(
        monkeypatch,
        modality="image",
        adapter="openai_images",
        explicit=["image.generate"],
        registry=["image.generate", "image.edit"],
    ) == ["image.generate"]


def test_manual_video_capabilities_override_registry(monkeypatch):
    assert _operation_kinds(
        monkeypatch,
        modality="video",
        adapter="agnes_video",
        explicit=["video.image_to_video"],
        registry=["video.text_to_video", "video.image_to_video"],
    ) == ["video.image_to_video"]


def test_manual_minimax_voice_capabilities_are_not_silently_broadened(monkeypatch):
    assert _operation_kinds(
        monkeypatch,
        modality="voice",
        adapter="minimax_tts",
        explicit=["voice.tts"],
        registry=["voice.tts", "voice.design"],
    ) == ["voice.tts"]


def test_registry_remains_the_fallback_when_no_manual_declaration_exists(monkeypatch):
    assert _operation_kinds(
        monkeypatch,
        modality="video",
        adapter="agnes_video",
        explicit=None,
        registry=["video.reference_to_video"],
    ) == ["video.reference_to_video"]


def test_explicitly_cleared_manual_image_capabilities_stay_empty(monkeypatch):
    assert _operation_kinds(
        monkeypatch,
        modality="image",
        adapter="openai_images",
        explicit=[],
        registry=["image.generate", "image.edit"],
    ) == []


def test_manual_capability_modes_survive_binding_persistence_and_public_projection():
    model = persist_model_endpoint_binding(
        "manual-provider",
        "images/generations/example-image",
        {"base_url": "https://example.test/v1"},
        {
            "type": "IMAGE",
            "operationKinds": ["image.generate"],
            "mediaLimits": {
                "capabilityModes": ["image.text_to_image"],
                "operationKinds": ["image.generate"],
            },
            "endpointBinding": {
                "endpointPath": "images/generations",
                "providerModelId": "example-image",
                "operationKind": "image.generate",
            },
        },
        source="manual",
    )

    assert model["mediaLimits"]["capabilityModes"] == ["image.text_to_image"]
    assert model["mediaLimits"]["operationKinds"] == ["image.generate"]
    projected = public_models_config(
        {
            "providers": {
                "manual-provider": {
                    "provider": {"base_url": "https://example.test/v1"},
                    "models": {"images/generations/example-image": model},
                }
            }
        }
    )
    visible = projected["providers"]["manual-provider"]["models"]["images/generations/example-image"]
    assert visible["mediaLimits"]["capabilityModes"] == ["image.text_to_image"]
