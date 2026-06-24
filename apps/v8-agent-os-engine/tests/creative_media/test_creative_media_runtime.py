from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from runtimes.creative_media.catalog import (
    capability_profile_for_model,
    load_audio_music_recipe_library,
    load_media_model_capability_overrides,
    load_provider_matrix,
    load_resolution_presets,
    load_video_recipe_library,
    load_visual_recipe_library,
    normalize_provider_status,
    resolve_image_size,
    resolve_video_resolution,
)
from runtimes.creative_media.recipe import CreativeRecipeCompiler, prepare_provider_prompt_policy
from runtimes.creative_media.runtime import (
    _build_agnes_image_payload,
    _build_agnes_video_payload,
    _build_openai_image_payload,
    _build_volcengine_image_payload,
    _build_volcengine_video_payload,
    creative_media_runtime,
)


class FakeJsonStorage:
    def __init__(self):
        self.payloads = {}
        self.base_dir = None

    def read_json(self, filename: str):
        return deepcopy(self.payloads.get(filename) or {})

    def write_json(self, filename: str, data):
        self.payloads[filename] = deepcopy(data)


def _compiler_with_fake_storage(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.recipe.storage", fake)
    return CreativeRecipeCompiler(), fake


def _contains_any_key(value, keys: set[str]) -> bool:
    if isinstance(value, dict):
        return any(key in value for key in keys) or any(_contains_any_key(item, keys) for item in value.values())
    if isinstance(value, list):
        return any(_contains_any_key(item, keys) for item in value)
    return False


def _candidate(model_id: str, *, operation_kind: str, modality: str, provider_id: str = "test_provider", priority: int = 10):
    return {
        "candidateId": f"{provider_id}-{model_id}-{operation_kind}",
        "modality": modality,
        "operationKind": operation_kind,
        "providerId": provider_id,
        "providerName": provider_id,
        "modelId": model_id,
        "modelRef": f"{provider_id}::{model_id}",
        "adapter": "volcengine_ark" if modality == "video" else "openai_images",
        "source": "model_control_plane",
        "available": True,
        "enabled": True,
        "priority": priority,
    }


def test_media_provider_matrix_has_required_contract_fields():
    matrix = load_provider_matrix()
    assert matrix["version"] == 1
    modalities = matrix["modalities"]
    assert {"agnes_image", "aliyun_bailian_image", "volcengine_seedream", "zhipu_bigmodel_image"}.issubset({entry["id"] for entry in modalities["image"]})
    assert {"agnes_video", "aliyun_bailian_video", "volcengine_seedance", "zhipu_bigmodel_video", "openai_sora_video", "happyhorse_video"}.issubset({entry["id"] for entry in modalities["video"]})
    assert {"aliyun_bailian_cosyvoice", "minimax_tts", "zhipu_bigmodel_voice", "volcengine_doubao_voice", "xiaomi_mimo_tts"}.issubset({entry["id"] for entry in modalities["voice"]})
    assert {"tencent_hunyuan_3d", "volcengine_3d_generation"}.issubset({entry["id"] for entry in modalities["model3d"]})
    for modality in ("image", "video", "voice", "music", "model3d"):
        assert modality in modalities
        assert modalities[modality]
        for entry in modalities[modality]:
            assert entry["id"]
            assert entry["apiStandard"]
            assert entry["adapter"]
            assert entry["credentialHelp"]["url"]
            assert entry["request"]["method"]
            assert "polling" in entry
            assert "result" in entry
            assert entry["confidence"] in matrix["statusLevels"]
    video_entries = {entry["id"]: entry for entry in modalities["video"]}
    image_entries = {entry["id"]: entry for entry in modalities["image"]}
    model3d_entries = {entry["id"]: entry for entry in modalities["model3d"]}
    assert "glm-image" in image_entries["zhipu_bigmodel_image"]["modelIds"]
    assert "hunyuan-3d-pro" in model3d_entries["tencent_hunyuan_3d"]["modelIds"]
    assert "video.action_transfer" in video_entries["aliyun_bailian_video"]["operationKinds"]
    assert "video.first_last_frame" in video_entries["volcengine_seedance"]["operationKinds"]
    assert video_entries["agnes_video"]["adapter"] == "agnes_video"
    assert video_entries["agnes_video"]["polling"]["statusPath"] == "/agnesapi?video_id={video_id}"
    assert "S2V-01" in video_entries["minimax_video"]["modelIds"]
    assert "subject_reference" in video_entries["minimax_video"]["request"]["bodyFields"]
    assert image_entries["agnes_image"]["adapter"] == "agnes_images"
    assert "data.image_base64[]" in image_entries["minimax_image"]["result"]["paths"]
    assert "capabilityProfile" not in video_entries["volcengine_seedance"]
    assert "capabilityProfile" not in video_entries["openai_sora_video"]


def test_media_model_capability_overrides_are_exact_model_versioned():
    overrides = load_media_model_capability_overrides()
    assert overrides["matchPolicy"].startswith("exact providerId")

    seedance_2 = capability_profile_for_model(
        provider_id="volcengine_seedance",
        model_id="doubao-seedance-2-0",
        operation_kind="video.first_last_frame",
    )
    seedance_old = capability_profile_for_model(
        provider_id="volcengine_seedance",
        model_id="doubao-seedance-1-0-pro-fast-251015",
        operation_kind="video.first_last_frame",
    )

    assert seedance_2["nativeAudio"] is True
    assert "audio" in seedance_2["inputModalities"]
    assert seedance_2["referenceInputs"]["image"]["maxCount"] == 9
    assert seedance_old == {}


def test_media_resolution_presets_cover_required_ratios():
    presets = load_resolution_presets()
    required_ratios = {"1:1", "2:3", "3:2", "4:3", "3:4", "16:9", "9:16", "21:9"}
    assert set(presets["ratios"]) == required_ratios
    for group in ("image", "video"):
        for preset_values in presets[group]["presets"].values():
            assert required_ratios.issubset(set(preset_values))
            assert all("x" in value for value in preset_values.values())
    assert resolve_image_size(ratio="16:9", adapter="openai_images") == "1536x1024"
    assert resolve_image_size(ratio="16:9", adapter="volcengine_ark") == "2560x1440"
    assert resolve_video_resolution(preset="720P") == "720p"


def test_creative_recipe_libraries_cover_p2a_modalities():
    visual = load_visual_recipe_library()
    video = load_video_recipe_library()
    audio_music = load_audio_music_recipe_library()

    assert {"ui_interface", "infographic", "poster", "product_commerce", "character_design", "narrative_scene"}.issubset(visual["templates"])
    assert {"timed_storyboard", "local_edit", "character_continuity"}.issubset(video["templates"])
    assert {"voice", "music"}.issubset(audio_music)
    assert "narration" in audio_music["voice"]["templates"]
    assert "background_score" in audio_music["music"]["templates"]


def test_media_payload_rendering_keeps_provider_specific_fields_separate():
    openai_payload = _build_openai_image_payload(
        model="gpt-image-2",
        prompt="a small red house",
        size="1024x1024",
        response_format="b64_json",
    )
    assert openai_payload == {
        "model": "gpt-image-2",
        "prompt": "a small red house",
        "size": "1024x1024",
        "response_format": "b64_json",
    }

    agnes_image_payload = _build_agnes_image_payload(
        model="agnes-image-2.1-flash",
        prompt="a small red house",
        size="1024x768",
        response_format="url",
        image_urls=["https://example.com/source.png"],
    )
    assert "response_format" not in agnes_image_payload
    assert agnes_image_payload["extra_body"] == {
        "image": ["https://example.com/source.png"],
        "response_format": "url",
    }

    agnes_video_payload = _build_agnes_video_payload(
        model="agnes-video-v2.0",
        prompt="a short camera move",
        operation_kind="video.first_last_frame",
        image_urls=["https://example.com/first.png", "https://example.com/last.png"],
        num_frames=120,
        frame_rate=24,
    )
    assert agnes_video_payload["num_frames"] == 113
    assert agnes_video_payload["extra_body"]["mode"] == "keyframes"
    assert agnes_video_payload["extra_body"]["image"] == [
        "https://example.com/first.png",
        "https://example.com/last.png",
    ]

    volc_image_payload = _build_volcengine_image_payload(
        model="doubao-seedream-4-0-250828",
        prompt="a small red house",
        size="2048x2048",
    )
    assert "size" in volc_image_payload
    assert "ratio" not in volc_image_payload

    volc_video_payload = _build_volcengine_video_payload(
        model="doubao-seedance-1-0-pro-fast-251015",
        prompt="a 5 second establishing shot",
        ratio="16:9",
        resolution="720p",
        duration=5,
    )
    assert {"ratio", "resolution", "duration", "content"}.issubset(volc_video_payload)
    assert "size" not in volc_video_payload


def test_simple_asset_work_order_prefers_gpt_image_2(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.recipe.storage", fake)
    monkeypatch.setattr(
        creative_media_runtime,
        "_preferred_model_candidates",
        lambda operation_kind: {
            "image.generate": [
                _candidate("legacy-image", operation_kind="image.generate", modality="image", priority=20),
                _candidate("gpt-image-2", operation_kind="image.generate", modality="image", provider_id="openai", priority=30),
            ]
        }.get(operation_kind, []),
    )

    work_order = creative_media_runtime.compile_work_order(
        {
            "intent": "simple_asset",
            "assetRole": "engineering_background",
            "brief": "A restrained dashboard background for an engineering proof page.",
            "aspectRatio": "16:9",
            "requestingRuntime": "engineering",
        }
    )

    assert work_order["workOrderKind"] == "simple_asset"
    assert work_order["requestingRuntime"] == "engineering"
    assert work_order["providerPlan"]["imageGeneration"]["primary"]["modelId"] == "gpt-image-2"
    assert work_order["recipeRefs"]
    assert work_order["dryRunOnly"] is True


def test_storyboard_work_order_prefers_seedance_2_over_seed_lite(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.recipe.storage", fake)
    monkeypatch.setattr(
        creative_media_runtime,
        "_preferred_model_candidates",
        lambda operation_kind: {
            "image.generate": [
                _candidate("gpt-image-2", operation_kind="image.generate", modality="image", provider_id="openai", priority=10),
            ],
            "video.first_last_frame": [
                _candidate("doubao-seed-2-0-lite-260428", operation_kind="video.first_last_frame", modality="video", provider_id="volcengine_seedance", priority=1),
                _candidate("doubao-seedance-2-0-260128", operation_kind="video.first_last_frame", modality="video", provider_id="volcengine_seedance", priority=20),
            ],
        }.get(operation_kind, []),
    )

    work_order = creative_media_runtime.compile_work_order(
        {
            "intent": "storyboard_to_video",
            "brief": "A three-shot product intro with a first frame and final frame.",
            "modality": "video",
            "aspectRatio": "16:9",
            "duration": 5,
            "referenceAssetIds": ["asset_first", "asset_last"],
            "requestingRuntime": "creative_media",
        }
    )

    video_plan = work_order["providerPlan"]["videoGeneration"]
    assert work_order["workOrderKind"] == "storyboard_to_video"
    assert video_plan["operationKind"] == "video.first_last_frame"
    assert video_plan["primary"]["modelId"] == "doubao-seedance-2-0-260128"
    assert video_plan["fallbacks"][0]["modelId"] == "doubao-seed-2-0-lite-260428"
    assert "doubao-seed-2-0-lite-260428" in work_order["providerPlan"]["directorOrReview"]["lowerTierExecutableVideoModels"]
    assert work_order["shotPlan"]
    assert work_order["storyboardAssets"]


def test_storyboard_work_order_can_execute_with_seed_lite_when_seedance_2_is_unavailable(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.recipe.storage", fake)
    monkeypatch.setattr(
        creative_media_runtime,
        "_preferred_model_candidates",
        lambda operation_kind: {
            "image.generate": [
                _candidate("gpt-image-2", operation_kind="image.generate", modality="image", provider_id="openai", priority=10),
            ],
            "video.reference_to_video": [
                _candidate("doubao-seed-2-0-lite-260428", operation_kind="video.reference_to_video", modality="video", provider_id="volcengine_seedance", priority=10),
            ],
        }.get(operation_kind, []),
    )

    work_order = creative_media_runtime.compile_work_order(
        {
            "intent": "storyboard_to_video",
            "brief": "A short music-driven scene using video and audio references.",
            "modality": "video",
            "aspectRatio": "16:9",
            "duration": 5,
            "referenceAssets": [{"id": "asset_reference_video", "modality": "video", "role": "camera_motion"}],
            "requestingRuntime": "creative_media",
        }
    )

    video_plan = work_order["providerPlan"]["videoGeneration"]
    assert video_plan["operationKind"] == "video.reference_to_video"
    assert video_plan["primary"]["modelId"] == "doubao-seed-2-0-lite-260428"
    assert video_plan["capabilityGap"] is None


def test_volcengine_status_is_normalized():
    assert normalize_provider_status("ordered", provider="volcengine_seedance") == "queued"
    assert normalize_provider_status("running", provider="volcengine_seedance") == "running"
    assert normalize_provider_status("succeeded", provider="volcengine_seedance") == "succeeded"
    assert normalize_provider_status("failed", provider="volcengine_seedance") == "failed"


def test_agnes_video_adapter_maps_submit_and_poll_contract(monkeypatch):
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "agnes": {
                    "provider": {
                        "name": "Agnes AI",
                        "base_url": "https://apihub.agnes-ai.com/v1",
                        "api_key": "sk-test",
                    },
                    "models": {
                        "agnes-2.0-flash": {"type": "MULTIMODAL"},
                        "agnes-image-2.1-flash": {"type": "IMAGE"},
                        "agnes-video-v2.0": {"type": "VIDEO"},
                    },
                }
            }
        },
    )
    responses = iter(
        [
            {
                "id": "task_123",
                "task_id": "task_123",
                "video_id": "video_123",
                "status": "queued",
                "seconds": "5.0",
                "size": "1152x768",
            },
            {
                "id": "task_123",
                "video_id": "video_123",
                "status": "completed",
                "progress": 100,
                "remixed_from_video_id": "https://example.com/result.mp4",
            },
        ]
    )
    requested_urls: list[str] = []

    async def fake_request_json(method, url, **kwargs):
        requested_urls.append(url)
        return next(responses)

    async def fake_artifact_from_url(url, **kwargs):
        return {"artifactId": "artifact_video", "url": url, "mimeType": "video/mp4"}

    monkeypatch.setattr(creative_media_runtime, "_request_json", fake_request_json)
    monkeypatch.setattr(creative_media_runtime, "_artifact_from_url", fake_artifact_from_url)
    monkeypatch.setattr(creative_media_runtime, "_save_job", lambda job: job)
    request = {
        "modality": "video",
        "operationKind": "video.text_to_video",
        "providerId": "agnes",
        "model": "agnes-video-v2.0",
        "prompt": "A short studio product shot",
        "duration": 5,
    }
    job = creative_media_runtime._new_job(modality="video", adapter="agnes_video", request=request)

    submitted = asyncio.run(creative_media_runtime._submit_agnes_video_job(job, request))
    completed = asyncio.run(creative_media_runtime._poll_agnes_video_job(submitted))

    assert submitted["providerResponse"]["videoId"] == "video_123"
    assert submitted["providerResponse"]["providerId"] == "agnes"
    assert completed["status"] == "succeeded"
    assert completed["artifacts"][0]["url"] == "https://example.com/result.mp4"
    assert requested_urls == [
        "https://apihub.agnes-ai.com/v1/videos",
        "https://apihub.agnes-ai.com/agnesapi?video_id=video_123",
    ]


def test_model_preferences_are_scoped_by_operation_kind(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "local2": {
                    "provider": {"name": "local2 images", "api_standard": "openai_images"},
                    "models": {"gpt-image-2": {"type": "IMAGE", "capabilities": {"image": True}}},
                },
                "motion-provider": {
                    "provider": {"name": "Motion Transfer"},
                    "models": {
                        "motion-v1": {
                            "type": "VIDEO",
                            "operationKinds": ["video.action_transfer"],
                            "mediaLimits": {"operationKinds": ["video.action_transfer"]},
                        }
                    },
                },
            }
        },
    )
    monkeypatch.setattr(
        creative_media_runtime,
        "_volc_credentials",
        lambda: {
            "apiKey": "",
            "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
            "imageModel": "seedream",
            "videoModel": "seedance",
        },
    )

    prefs = creative_media_runtime.get_model_preferences()

    assert "image.generate" in prefs["policies"]
    assert "video.first_last_frame" in prefs["policies"]
    assert "video.action_transfer" in prefs["policies"]
    assert any(item["source"] == "model_control_plane" for item in prefs["connectedOptions"])
    assert any(item["source"] == "mcp_or_env" for item in prefs["diagnosticCandidates"])
    assert all(item["operationKind"] == "video.first_last_frame" for item in prefs["policies"]["video.first_last_frame"]["models"])
    action_models = prefs["policies"]["video.action_transfer"]["models"]
    assert action_models
    assert all(item["operationKind"] == "video.action_transfer" for item in action_models)
    assert all(item["available"] is False for item in action_models)


def test_seedance_1_0_fast_uses_exact_registry_operations(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "volcengine_seedance": {
                    "provider": {"name": "Volcengine Seedance", "api_standard": "volcengine_ark"},
                    "models": {
                        "doubao-seedance-1-0-pro-fast-251015": {
                            "type": "VIDEO",
                            "mediaLimits": {
                                "operationKinds": [
                                    "video.text_to_video",
                                    "video.image_to_video",
                                    "video.first_last_frame",
                                    "video.reference_to_video",
                                ]
                            },
                        }
                    },
                },
            }
        },
    )
    monkeypatch.setattr(
        creative_media_runtime,
        "_volc_credentials",
        lambda: {"apiKey": "sk-test", "videoModel": "doubao-seedance-1-0-pro-fast-251015"},
    )

    prefs = creative_media_runtime.get_model_preferences()
    connected = [
        (item["operationKind"], item["source"], item["available"])
        for item in prefs["connectedOptions"]
        if item.get("modelRef") == "volcengine_seedance::doubao-seedance-1-0-pro-fast-251015"
    ]
    rows = {item["operationKind"]: item for item in prefs["operationRows"]}

    assert connected == [
        ("video.text_to_video", "model_control_plane", True),
        ("video.image_to_video", "model_control_plane", True),
    ]
    assert rows["video.reference_to_video"]["optionCount"] == 0
    assert rows["video.reference_to_video"]["selectedModelRefs"] == []
    assert rows["video.reference_to_video"]["enabled"] is False
    assert rows["video.first_last_frame"]["optionCount"] == 0


def test_media_candidate_exposes_native_audio_capability_profile(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
        "openai_sora_video": {
            "provider": {"name": "OpenAI Sora", "api_standard": "openai_video"},
            "models": {
                "sora-2": {
                    "type": "VIDEO",
                    "operationKinds": ["video.text_to_video"],
                    "mediaLimits": {
                        "operationKinds": ["video.text_to_video"],
                    },
                }
            },
                },
            }
        },
    )
    monkeypatch.setattr(creative_media_runtime, "_volc_credentials", lambda: {})

    candidates = creative_media_runtime.list_model_candidates()
    sora = next(item for item in candidates if item["modelId"] == "sora-2")

    assert sora["nativeAudio"] is True
    assert sora["capabilityProfile"]["audioPreservationPolicy"] == "preserve_native_audio_by_default"


def test_seedance_recipe_prompt_keeps_multimodal_reference_roles(monkeypatch):
    compiler, _fake = _compiler_with_fake_storage(monkeypatch)
    image = compiler.register_asset({"role": "first_frame", "modality": "image", "artifactId": "art_img"})
    video = compiler.register_asset({"role": "camera_motion", "modality": "video", "artifactId": "art_vid"})
    audio = compiler.register_asset({"role": "background_music", "modality": "music", "artifactId": "art_audio"})

    recipe = compiler.compile_recipe(
        {
            "modality": "video",
            "prompt": "做一个10秒产品广告，参考素材节奏和音乐",
            "durationSeconds": 10,
            "assetIds": [image["assetId"], video["assetId"], audio["assetId"]],
        }
    )

    prompt = recipe["providerPrompts"]["volcengine_seedance"]
    assert "@image1 as first_frame" in prompt
    assert "@video2 as camera_motion" in prompt
    assert "@audio3 as background_music" in prompt
    assert "Seedance 2.0 reference discipline" in prompt


def test_prompt_policy_defaults_to_english_and_rewrites_protected_ip():
    policy = prepare_provider_prompt_policy("生成钢铁侠海报，标题必须显示「未来战甲」", modality="image")

    assert policy["providerPromptLanguage"] == "en"
    assert "钢铁侠" not in policy["translatedPrompt"]
    assert "powered exoskeleton" in policy["translatedPrompt"]
    assert "未来战甲" in policy["preservedTextTokens"]
    assert policy["safetyTransform"]["applied"] is True


def test_dashscope_builtin_candidates_are_grouped_by_operation_kind(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.runtime.model_control_plane.get_config", lambda: {"providers": {}})
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    prefs = creative_media_runtime.get_model_preferences()

    assert "image.edit" in prefs["policies"]
    assert "video.lipsync" in prefs["policies"]
    assert "video.action_transfer" in prefs["policies"]
    assert all(item["source"] != "env_builtin" for item in prefs["connectedOptions"])
    assert any(item["source"] == "env_builtin" for item in prefs["diagnosticCandidates"])
    assert all(item["adapter"] == "dashscope" for item in prefs["policies"]["video.action_transfer"]["models"])
    assert all(item["operationKind"] == "video.lipsync" for item in prefs["policies"]["video.lipsync"]["models"])


def test_model_preferences_save_uses_operation_model_refs_without_secrets(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "local2": {
                    "provider": {"name": "local2 images", "api_standard": "openai_images"},
                    "models": {"gpt-image-2": {"type": "IMAGE", "capabilities": {"image": True}}},
                },
            }
        },
    )
    monkeypatch.setattr(creative_media_runtime, "_volc_credentials", lambda: {})

    result = creative_media_runtime.save_model_preferences(
        {
            "selections": [
                {
                    "operationKind": "image.generate",
                    "modelRefs": ["local2::gpt-image-2"],
                    "enabled": True,
                    "priority": 7,
                }
            ]
        }
    )

    stored = fake.payloads["creative_media/model_preferences.json"]
    assert stored["selections"][0]["modelRefs"] == ["local2::gpt-image-2"]
    assert stored["models"][0]["modelRef"] == "local2::gpt-image-2"
    assert not _contains_any_key(stored, {"apiKey", "api_key", "Authorization", "authorization"})
    assert result["operationRows"]
    assert result["connectedOptions"][0]["modelRef"] == "local2::gpt-image-2"


def test_music_brief_can_bind_connected_catalog_model(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "mureka_music": {
                    "provider": {
                        "name": "Mureka Music API",
                        "api_standard": "mureka_song_task",
                        "mediaModality": "music",
                    },
                    "models": {"auto": {"type": "MUSIC", "capabilities": {"music": True}}},
                },
            }
        },
    )
    monkeypatch.setattr(creative_media_runtime, "_volc_credentials", lambda: {})

    prefs = creative_media_runtime.get_model_preferences()
    music_options = [
        item for item in prefs["connectedOptions"]
        if item.get("operationKind") == "music.brief"
    ]
    music_row = next(item for item in prefs["operationRows"] if item["operationKind"] == "music.brief")

    assert music_options
    assert music_options[0]["modelRef"] == "mureka_music::auto"
    assert music_options[0]["available"] is False
    assert music_options[0]["briefOnly"] is True
    assert music_row["optionCount"] == 1

    result = creative_media_runtime.save_model_preferences(
        {
            "selections": [
                {
                    "operationKind": "music.brief",
                    "modelRefs": ["mureka_music::auto"],
                    "enabled": True,
                    "priority": 5,
                }
            ]
        }
    )

    stored = fake.payloads["creative_media/model_preferences.json"]
    assert stored["selections"][0]["modelRefs"] == ["mureka_music::auto"]
    assert result["operationRows"]
    assert next(item for item in result["operationRows"] if item["operationKind"] == "music.brief")["selectedModelRefs"] == ["mureka_music::auto"]


def test_minimax_music_job_decodes_hex_artifact(monkeypatch, tmp_path: Path):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "minimax-cn": {
                    "provider": {
                        "name": "MiniMax 中国站",
                        "api_standard": "minimax",
                        "base_url": "https://api.minimaxi.com/v1",
                        "api_key": "sk-test",
                    },
                    "models": {
                        "music_generation/music-2.6": {
                            "type": "MUSIC",
                            "mediaLimits": {
                                "providerModelId": "music-2.6",
                                "operationKinds": ["music.generate"],
                            },
                        }
                    },
                },
            }
        },
    )
    requested_urls = []

    async def fake_request_json(method, url, *, headers=None, json=None, timeout=120.0):
        requested_urls.append(url)
        assert json["model"] == "music-2.6"
        return {"data": {"audio": "00010203", "status": 2}, "trace_id": "trace_1", "base_resp": {"status_code": 0}}

    def fake_record_local_artifact(*, file_path, job, kind, mime_type, metadata):
        return {"artifactId": f"artifact_{kind}", "kind": kind, "mimeType": mime_type, "sourcePath": str(file_path), "metadata": metadata}

    monkeypatch.setattr(creative_media_runtime, "_request_json", fake_request_json)
    monkeypatch.setattr(creative_media_runtime, "_record_local_artifact", fake_record_local_artifact)

    job = asyncio.run(
        creative_media_runtime.create_job(
            {
                "modality": "music",
                "operationKind": "music.generate",
                "providerId": "minimax-cn",
                "modelId": "music_generation/music-2.6",
                "prompt": "short cinematic cue",
                "is_instrumental": True,
                "workspacePath": str(tmp_path),
            }
        )
    )

    assert job["modality"] == "music"
    assert job["operationKind"] == "music.generate"
    assert job["adapter"] == "minimax_music"
    assert job["status"] == "succeeded"
    assert job["artifacts"][0]["kind"] == "audio"
    assert Path(job["artifacts"][0]["sourcePath"]).read_bytes() == b"\x00\x01\x02\x03"
    assert requested_urls == ["https://api.minimaxi.com/v1/music_generation"]


def test_mureka_music_job_polls_and_downloads_audio(monkeypatch, tmp_path: Path):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "mureka_music": {
                    "provider": {
                        "name": "Mureka Music API",
                        "api_standard": "mureka_song_task",
                        "base_url": "https://api.mureka.ai",
                        "api_key": "sk-test",
                    },
                    "models": {"auto": {"type": "MUSIC", "capabilities": {"music": True}}},
                }
            }
        },
    )
    requested_urls = []

    async def fake_request_json(method, url, *, headers=None, json=None, timeout=120.0):
        requested_urls.append((method, url))
        if url.endswith("/v1/song/generate"):
            assert json["model"] == "mureka-o1"
            return {"task_id": "task_1", "status": "queued"}
        return {"status": "completed", "data": {"audio_url": "https://cdn.example.test/song.mp3"}}

    async def fake_artifact_from_url(url, *, job, kind, provider, mime_hint, metadata=None):
        return {"artifactId": "artifact_song", "kind": kind, "sourceUrl": url, "metadata": metadata or {}}

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(creative_media_runtime, "_request_json", fake_request_json)
    monkeypatch.setattr(creative_media_runtime, "_artifact_from_url", fake_artifact_from_url)
    monkeypatch.setattr("runtimes.creative_media.runtime.asyncio.sleep", fake_sleep)

    job = asyncio.run(
        creative_media_runtime.create_job(
            {
                "modality": "music",
                "operationKind": "music.generate",
                "providerId": "mureka_music",
                "modelId": "auto",
                "prompt": "short cheerful loop",
                "wait": True,
                "workspacePath": str(tmp_path),
            }
        )
    )

    assert job["adapter"] == "mureka_music"
    assert job["status"] == "succeeded"
    assert job["providerTaskId"] == "task_1"
    assert job["artifacts"][0]["sourceUrl"] == "https://cdn.example.test/song.mp3"
    assert requested_urls == [
        ("POST", "https://api.mureka.ai/v1/song/generate"),
        ("GET", "https://api.mureka.ai/v1/song/query/task_1"),
    ]


def test_tencent_hunyuan_3d_uses_tokenhub_and_downloads_model(monkeypatch, tmp_path: Path):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.model_control_plane.get_config",
        lambda: {
            "providers": {
                "tencent_hunyuan_3d": {
                    "provider": {
                        "name": "Tencent Hunyuan 3D",
                        "api_standard": "tencentcloud_ai3d",
                        "base_url": "https://ai3d.tencentcloudapi.com",
                        "api_key": "sk-test",
                    },
                    "models": {"hy-3d-3.0": {"type": "MODEL3D", "capabilities": {"model3d": True}}},
                }
            }
        },
    )
    requested_urls = []

    async def fake_request_json(method, url, *, headers=None, json=None, timeout=120.0):
        requested_urls.append((method, url, dict(json or {})))
        assert url.startswith("https://tokenhub.tencentmaas.com/v1/api/3d/")
        if url.endswith("/submit"):
            return {"id": "task_3d", "status": "queued"}
        return {"status": "completed", "data": [{"type": "obj", "url": "https://cdn.example.test/model.obj"}, {"type": "glb", "url": "https://cdn.example.test/model.glb"}]}

    async def fake_artifact_from_url(url, *, job, kind, provider, mime_hint, metadata=None):
        return {"artifactId": "artifact_model", "kind": kind, "sourceUrl": url, "metadata": metadata or {}}

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(creative_media_runtime, "_request_json", fake_request_json)
    monkeypatch.setattr(creative_media_runtime, "_artifact_from_url", fake_artifact_from_url)
    monkeypatch.setattr("runtimes.creative_media.runtime.asyncio.sleep", fake_sleep)

    job = asyncio.run(
        creative_media_runtime.create_job(
            {
                "modality": "model3d",
                "operationKind": "model3d.generate",
                "providerId": "tencent_hunyuan_3d",
                "modelId": "hy-3d-3.0",
                "prompt": "low-poly treasure chest",
                "wait": True,
                "workspacePath": str(tmp_path),
            }
        )
    )

    assert job["adapter"] == "tencent_hunyuan_3d"
    assert job["status"] == "succeeded"
    assert job["artifacts"][0]["kind"] == "model3d"
    assert job["artifacts"][0]["sourceUrl"] == "https://cdn.example.test/model.glb"
    assert job["providerResponse"]["endpointOverride"] == "legacy_tencentcloud_ai3d_preset_uses_tokenhub_bearer"
    assert requested_urls[0][1].endswith("/submit")
    assert requested_urls[1][1].endswith("/query")


def test_scope_fields_registers_explicit_project_workspace(monkeypatch, tmp_path: Path):
    saved_payloads = []

    monkeypatch.setattr(
        "runtimes.memory.project_registry.project_registry_service.find_project_for_workspace",
        lambda **kwargs: None,
    )

    def fake_save_project(payload):
        saved_payloads.append(payload)
        return SimpleNamespace(
            project_id=payload["id"],
            workspace_id=payload["workspaceId"],
            workspace_path=payload["workspacePath"],
        )

    monkeypatch.setattr(
        "runtimes.memory.project_registry.project_registry_service.save_project",
        fake_save_project,
    )

    scope = creative_media_runtime._scope_fields(
        {
            "projectId": "test2",
            "workspaceId": "test2",
            "workspacePath": str(tmp_path),
        }
    )

    assert scope["projectId"] == "test2"
    assert saved_payloads[0]["workspacePath"] == str(tmp_path)


def test_unsupported_video_operation_does_not_fallback_to_generic_video(monkeypatch):
    fake = FakeJsonStorage()
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.runtime.model_control_plane.get_config", lambda: {"providers": {}})
    monkeypatch.setattr(
        creative_media_runtime,
        "_volc_credentials",
        lambda: {
            "apiKey": "sk-test",
            "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
            "imageModel": "seedream",
            "videoModel": "seedance",
        },
    )

    job = asyncio.run(
        creative_media_runtime.create_job(
            {
                "modality": "video",
                "operationKind": "video.action_transfer",
                "prompt": "把参考动作迁移到目标角色",
            }
        )
    )

    assert job["status"] == "failed"
    assert "video.action_transfer" in job["error"]
    assert job["adapter"] in {"operation_unsupported", "operation_unavailable"}


def test_creative_media_runtime_can_record_fake_result(monkeypatch, tmp_path: Path):
    fake_file = tmp_path / "fake.png"
    fake_file.write_bytes(b"fake-image")
    captured = {}

    def fake_record_artifact(**kwargs):
        captured.update(kwargs)
        return {"artifactId": "art_fake", "kind": kwargs["artifact_kind"], "contentUrl": "/v1/artifacts/art_fake/content"}

    monkeypatch.setattr("runtimes.creative_media.runtime.artifact_store.record_artifact", fake_record_artifact)
    artifact = creative_media_runtime._record_local_artifact(
        file_path=fake_file,
        job={"jobId": "cm_fake", "modality": "image"},
        kind="image",
        mime_type="image/png",
        metadata={"provider": "fake", "origin": "provider_result"},
    )

    assert artifact["artifactId"] == "art_fake"
    assert captured["metadata"]["creativeMediaJobId"] == "cm_fake"
    assert captured["metadata"]["origin"] == "provider_result"


def test_project_scope_is_persisted_in_recipe_asset_and_artifact(monkeypatch, tmp_path: Path):
    compiler, _fake = _compiler_with_fake_storage(monkeypatch)
    workspace_path = str(tmp_path / "fresh-project")

    recipe = compiler.compile_recipe(
        {
            "modality": "image",
            "prompt": "生成一张项目级 smoke 测试图",
            "projectId": "project-smoke",
            "workspaceId": "workspace-smoke",
            "workspacePath": workspace_path,
        }
    )
    asset = compiler.register_asset(
        {
            "role": "reference",
            "modality": "image",
            "artifactId": "art_project",
            "projectId": "project-smoke",
            "workspaceId": "workspace-smoke",
            "workspacePath": workspace_path,
        }
    )

    output_path = creative_media_runtime._output_path(
        {"jobId": "cm_project", "projectId": "project-smoke", "workspaceId": "workspace-smoke", "workspacePath": workspace_path},
        "image",
        ".png",
    )
    captured = {}

    def fake_record_artifact(**kwargs):
        captured.update(kwargs)
        return {"artifactId": "art_project_scoped", "kind": kwargs["artifact_kind"]}

    monkeypatch.setattr("runtimes.creative_media.runtime.artifact_store.record_artifact", fake_record_artifact)
    artifact = creative_media_runtime._record_local_artifact(
        file_path=output_path,
        job={"jobId": "cm_project", "modality": "image", "projectId": "project-smoke", "workspaceId": "workspace-smoke", "workspacePath": workspace_path},
        kind="image",
        mime_type="image/png",
        metadata={"provider": "fake"},
    )

    assert recipe["projectId"] == "project-smoke"
    assert asset["workspacePath"] == workspace_path
    assert str(output_path).startswith(str(Path(workspace_path) / "creative_media" / "cm_project"))
    assert artifact["artifactId"] == "art_project_scoped"
    assert captured["metadata"]["projectId"] == "project-smoke"
    assert captured["metadata"]["workspaceId"] == "workspace-smoke"
    assert captured["metadata"]["workspacePath"] == workspace_path


def test_image_recipe_compilation_preserves_hard_requirements(monkeypatch):
    compiler, fake = _compiler_with_fake_storage(monkeypatch)

    recipe = compiler.compile_recipe(
        {
            "modality": "image",
            "prompt": "给新品耳机做一张电商海报，标题必须显示「Quiet Nova」，突出蓝色金属材质",
            "ratio": "1:1",
            "negativeConstraints": ["不要乱码"],
        }
    )

    assert recipe["modality"] == "image"
    assert recipe["recipeKind"] == "poster"
    assert "openai_images" in recipe["providerPrompts"]
    assert "volcengine_seedream" in recipe["providerPrompts"]
    assert "Quiet Nova" in recipe["providerPrompts"]["openai_images"]
    assert recipe["constraintCheck"]["ok"] is True
    assert recipe["recipeId"] in fake.payloads["creative_media/recipes.json"]["recipes"]


def test_video_recipe_compilation_uses_timed_segments_and_asset_refs(monkeypatch):
    compiler, _fake = _compiler_with_fake_storage(monkeypatch)
    asset = compiler.register_asset(
        {
            "role": "first_frame",
            "modality": "image",
            "artifactId": "art_first_frame",
            "title": "first frame",
        }
    )

    recipe = compiler.compile_recipe(
        {
            "modality": "video",
            "prompt": "5秒产品视频，耳机旋转，然后爆炸拆解，随后切到佩戴场景，再出现品牌字",
            "durationSeconds": 5,
            "ratio": "16:9",
            "assetIds": [asset["assetId"]],
        }
    )

    assert recipe["modality"] == "video"
    assert recipe["controls"]["durationSeconds"] == 5
    assert recipe["providerNeutralRecipe"]["timedSegments"][0]["start"] == 0
    assert recipe["providerNeutralRecipe"]["timedSegments"][0]["end"] == 5
    assert "One clear action" in recipe["providerNeutralRecipe"]["timedSegments"][0]["description"]
    assert "@image1 as first_frame" in recipe["providerPrompts"]["volcengine_seedance"]
    assert "5秒产品视频" not in recipe["providerPrompts"]["volcengine_seedance"]
    assert recipe["constraintCheck"]["warnings"]


def test_voice_and_music_recipes_have_separate_execution_semantics(monkeypatch):
    compiler, _fake = _compiler_with_fake_storage(monkeypatch)

    voice = compiler.compile_recipe({"modality": "voice", "prompt": "温柔旁白：欢迎来到春天的花园"})
    music = compiler.compile_recipe({"modality": "music", "prompt": "轻快BGM，适合30秒产品广告", "durationSeconds": 30})

    assert voice["executionStatus"] == "compiled"
    assert "v8_audio_tts" in voice["providerPrompts"]
    assert voice["providerPrompts"]["v8_audio_tts"]["text"] == "温柔旁白：欢迎来到春天的花园"
    assert music["executionStatus"] == "catalog_only"
    assert music["modality"] == "music"
    assert music["musicKind"] in {"cue_sheet", "score_brief", "music_reference", "future_generation"}
    assert "creative_music_brief" in music["providerPrompts"]
    assert not _contains_any_key(music, {"url", "audioUrl", "musicUrl", "tracks"})
    assert "catalog_only" in " ".join(music["constraintCheck"]["warnings"])


def test_asset_ledger_filters_and_versions(monkeypatch):
    compiler, _fake = _compiler_with_fake_storage(monkeypatch)

    first = compiler.register_asset({"assetId": "asset-1", "role": "character", "modality": "image", "artifactId": "art_1"})
    second = compiler.register_asset({"assetId": "asset-1", "role": "character", "modality": "image", "artifactId": "art_1", "metadata": {"note": "updated"}})
    compiler.register_asset({"assetId": "asset-2", "role": "music_ref", "modality": "music", "sourcePath": "E:/tmp/demo.mp3"})

    assert first["version"] == 1
    assert second["version"] == 2
    assert second["metadata"]["note"] == "updated"
    image_assets = compiler.list_assets(modality="image")
    character_assets = compiler.list_assets(role="character")
    assert [item["assetId"] for item in image_assets] == ["asset-1"]
    assert [item["assetId"] for item in character_assets] == ["asset-1"]


def test_character_bible_keyframe_and_recipe_lineage_are_persisted(monkeypatch):
    compiler, fake = _compiler_with_fake_storage(monkeypatch)

    bible = compiler.create_character_bible(
        {
            "characterBibleId": "hero-a",
            "name": "Hero A",
            "identityAnchors": ["短发", "银色夹克"],
            "visualAnchors": ["蓝色眼睛"],
            "negativeConstraints": ["不要换衣服"],
        }
    )
    keyframe = compiler.register_keyframe(
        {
            "keyframeId": "kf-opening",
            "recipeId": "recipe-parent",
            "role": "first_frame",
            "artifactId": "art_keyframe",
            "characterBibleIds": [bible["characterBibleId"]],
        }
    )
    recipe = compiler.compile_recipe(
        {
            "modality": "video",
            "recipeId": "recipe-child",
            "prompt": "10秒角色走进未来城市，保持衣服不变",
            "durationSeconds": 10,
            "characterBibleIds": [bible["characterBibleId"]],
            "keyframeIds": [keyframe["keyframeId"]],
            "parentRecipeId": "recipe-parent",
            "supersedesRecipeId": "recipe-old",
        }
    )

    assert recipe["lineage"]["parentRecipeId"] == "recipe-parent"
    assert recipe["lineage"]["supersedesRecipeId"] == "recipe-old"
    assert recipe["hardRequirements"]["characterBibleIds"] == ["hero-a"]
    assert recipe["hardRequirements"]["keyframeIds"] == ["kf-opening"]
    assert "hero-a" in recipe["sourceRefs"]
    assert "kf-opening" in recipe["sourceRefs"]
    assert "Character continuity" in recipe["providerPrompts"]["volcengine_seedance"]
    assert "Keyframe constraints" in recipe["providerPrompts"]["volcengine_seedance"]
    assert "hero-a" in fake.payloads["creative_media/character_bibles.json"]["characterBibles"]
    assert "kf-opening" in fake.payloads["creative_media/keyframes.json"]["keyframes"]


def test_local_edit_recipe_compiles_intent_without_creating_job(monkeypatch):
    compiler, _fake = _compiler_with_fake_storage(monkeypatch)

    recipe = compiler.compile_recipe(
        {
            "modality": "video",
            "prompt": "把源视频里第二个镜头的天空换成傍晚，其他都保留",
            "durationSeconds": 8,
            "editIntent": True,
            "sourceRefs": ["art_source_video"],
            "preserve": ["人物动作", "镜头节奏"],
            "modify": ["天空颜色"],
        }
    )

    assert recipe["recipeKind"] == "local_edit"
    assert recipe["editIntent"]["status"] == "compiled_only"
    assert recipe["editIntent"]["sourceRefs"] == ["art_source_video"]
    assert "compiles the edit intent" in recipe["editIntent"]["riskNotes"][0]


def test_p3_edit_plan_references_assets_and_lineage(monkeypatch, tmp_path: Path):
    fake = FakeJsonStorage()
    fake.base_dir = tmp_path
    monkeypatch.setattr("runtimes.creative_media.recipe.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)

    video_path = tmp_path / "clip.mp4"
    audio_path = tmp_path / "voice.mp3"
    video_path.write_bytes(b"fake-video")
    audio_path.write_bytes(b"fake-audio")

    creative_media_runtime.register_asset(
        {"assetId": "asset-video", "role": "clip", "modality": "video", "sourcePath": str(video_path)}
    )
    creative_media_runtime.register_asset(
        {"assetId": "asset-voice", "role": "voiceover", "modality": "voice", "sourcePath": str(audio_path)}
    )
    recipe = creative_media_runtime.compile_recipe({"recipeId": "recipe-video", "modality": "video", "prompt": "5秒开场", "durationSeconds": 5})
    plan = creative_media_runtime.create_edit_plan(
        {
            "recipeId": recipe["recipeId"],
            "assetIds": ["asset-video", "asset-voice"],
            "subtitleText": "欢迎来到 V8OS",
        }
    )

    assert plan["planId"] in fake.payloads["creative_media/edit_plans.json"]["editPlans"]
    assert plan["lineage"]["recipeId"] == "recipe-video"
    assert plan["tracks"]["video"][0]["assetId"] == "asset-video"
    assert plan["tracks"]["audio"][0]["assetId"] == "asset-voice"
    assert plan["tracks"]["subtitles"][0]["text"] == "欢迎来到 V8OS"
    assert "not used" in plan["qualityGates"]["musicBoundary"]


def test_p3_render_records_video_srt_and_edl_artifacts(monkeypatch, tmp_path: Path):
    fake = FakeJsonStorage()
    fake.base_dir = tmp_path
    monkeypatch.setattr("runtimes.creative_media.recipe.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.runtime.shutil.which", lambda name: name)

    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-video")
    creative_media_runtime.register_asset({"assetId": "asset-video", "role": "clip", "modality": "video", "sourcePath": str(video_path)})

    def fake_run(command, capture_output=True, text=True, timeout=30, check=False):
        if "ffprobe" in str(command[0]):
            return SimpleNamespace(returncode=0, stdout='{"format":{"duration":"5.0"}}', stderr="")
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0, stdout="", stderr="ok")

    recorded = []

    def fake_record_artifact(**kwargs):
        artifact = {
            "artifactId": f"art_{len(recorded) + 1}",
            "kind": kwargs["artifact_kind"],
            "sourcePath": kwargs["source_path"],
            "metadata": kwargs["metadata"],
            "contentUrl": f"/v1/artifacts/art_{len(recorded) + 1}/content",
        }
        recorded.append(artifact)
        return artifact

    monkeypatch.setattr("runtimes.creative_media.runtime.subprocess.run", fake_run)
    monkeypatch.setattr("runtimes.creative_media.runtime.artifact_store.record_artifact", fake_record_artifact)

    plan = creative_media_runtime.create_edit_plan({"assetIds": ["asset-video"], "subtitleText": "字幕测试"})
    render = creative_media_runtime.render_edit_plan({"planId": plan["planId"]})

    assert render["status"] == "succeeded"
    assert {artifact["kind"] for artifact in render["artifacts"]} == {"video", "subtitle", "report"}
    assert all(artifact["metadata"]["origin"] == "creative_media_post_production" for artifact in recorded)
    assert "creative_media/render_jobs.json" in fake.payloads


def test_native_audio_video_asset_is_preserved_without_extra_mix(monkeypatch, tmp_path: Path):
    fake = FakeJsonStorage()
    fake.base_dir = tmp_path
    monkeypatch.setattr("runtimes.creative_media.recipe.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.runtime.shutil.which", lambda name: name)

    video_path = tmp_path / "native-audio.mp4"
    video_path.write_bytes(b"fake-video")
    creative_media_runtime.register_asset(
        {
            "assetId": "asset-video-native-audio",
            "role": "clip",
            "modality": "video",
            "sourcePath": str(video_path),
            "metadata": {"nativeAudio": True},
        }
    )

    commands = []

    def fake_run(command, capture_output=True, text=True, timeout=30, check=False):
        commands.append(command)
        if "ffprobe" in str(command[0]):
            return SimpleNamespace(returncode=0, stdout='{"format":{"duration":"5.0"}}', stderr="")
        Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(command[-1]).write_bytes(b"rendered-video")
        return SimpleNamespace(returncode=0, stdout="", stderr="ok")

    monkeypatch.setattr("runtimes.creative_media.runtime.subprocess.run", fake_run)
    monkeypatch.setattr(
        "runtimes.creative_media.runtime.artifact_store.record_artifact",
        lambda **kwargs: {"artifactId": "art", "kind": kwargs["artifact_kind"], "metadata": kwargs["metadata"], "sourcePath": kwargs["source_path"]},
    )

    plan = creative_media_runtime.create_edit_plan({"assetIds": ["asset-video-native-audio"]})
    render = creative_media_runtime.render_edit_plan({"planId": plan["planId"]})
    ffmpeg_command = commands[-1]

    assert plan["audioPolicy"]["preserveNativeAudio"] is True
    assert render["diagnostics"]["preserveNativeAudio"] is True
    assert "-an" not in ffmpeg_command
    assert ["-map", "0:a?"] == ffmpeg_command[ffmpeg_command.index("-map", ffmpeg_command.index("-map") + 1): ffmpeg_command.index("-map", ffmpeg_command.index("-map") + 1) + 2]


def test_p4_quality_cost_and_safety_stores_are_written(monkeypatch, tmp_path: Path):
    fake = FakeJsonStorage()
    fake.base_dir = tmp_path
    monkeypatch.setattr("runtimes.creative_media.runtime.storage", fake)
    monkeypatch.setattr("runtimes.creative_media.runtime.db.get_runtime_artifact", lambda artifact_id: {})
    image_path = tmp_path / "quality.png"
    from PIL import Image

    Image.new("RGB", (1, 1), (255, 255, 255)).save(image_path)
    job = {
        "jobId": "cm_p4",
        "modality": "image",
        "operationKind": "image.generate",
        "adapter": "fake",
        "status": "succeeded",
        "request": {"ratio": "1:1", "prompt": "生成钢铁侠海报"},
        "providerResponse": {"providerId": "fake", "model": "fake-image"},
        "artifacts": [{"artifactId": "art_p4", "kind": "image", "sourcePath": str(image_path)}],
    }

    creative_media_runtime._record_safety_event(
        source="test",
        job=job,
        transform=prepare_provider_prompt_policy("生成钢铁侠海报", modality="image")["safetyTransform"],
    )
    creative_media_runtime._record_terminal_job_observations(job)

    assert job["qualityStatus"] in {"passed", "warning"}
    assert "creative_media/quality_jobs.json" in fake.payloads
    assert "creative_media/cost_ledger.json" in fake.payloads
    assert "creative_media/safety_events.json" in fake.payloads
