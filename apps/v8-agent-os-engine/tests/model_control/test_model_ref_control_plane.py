import asyncio
import json

import pytest

from api import platform_routes
from core.llm_factory import llm_factory
from core.model_capability_registry import model_capability_registry
from core.model_control_plane import DEFAULT_ROLE_MAP, DEFAULT_ROUTING_POLICIES, MODULE_DEFINITIONS, ROLE_DEFINITIONS, model_control_plane
from core.model_provider_catalog import ModelProviderCatalog, model_provider_catalog
from core.model_ref import make_model_ref, parse_model_ref


def test_model_ref_roundtrip():
    ref = make_model_ref("codex-oauth", "gpt-5.5")
    assert ref == "codex-oauth::gpt-5.5"
    assert parse_model_ref(ref) == ("codex-oauth", "gpt-5.5")


def test_planner_role_is_first_class_but_unbound_by_default():
    assert DEFAULT_ROLE_MAP["planner"] == ""
    assert DEFAULT_ROUTING_POLICIES["planner"] == "planner"
    assert ROLE_DEFINITIONS["planner"]["label"] == "Planner 模型"
    assert "chat_tool_calling" in ROLE_DEFINITIONS["planner"]["capabilityClasses"]

    module = next(item for item in MODULE_DEFINITIONS if item["key"] == "planner_lane")
    assert module["roles"] == ["planner"]
    assert module["pagePath"] == "/admin/engineering-lane"


def test_duplicate_naked_model_id_is_ambiguous_but_model_ref_is_exact():
    config = model_control_plane.normalize_config(
        {
            "providers": {
                "codex-oauth": {"provider": {"name": "Codex"}, "models": {"gpt-5.5": {"name": "Codex GPT"}}},
                "custom-openai": {"provider": {"name": "Custom"}, "models": {"gpt-5.5": {"name": "Custom GPT"}}},
            },
            "roles": {"default": make_model_ref("codex-oauth", "gpt-5.5")},
        }
    )

    assert model_control_plane.get_model_record("gpt-5.5", config) is None
    record = model_control_plane.get_model_record(make_model_ref("codex-oauth", "gpt-5.5"), config)
    assert record
    assert record["provider_id"] == "codex-oauth"

    resolution = model_control_plane.resolve_model_for_role("default", config)
    assert resolution["resolvedModelId"] == "gpt-5.5"
    assert resolution["resolvedModelRef"] == "codex-oauth::gpt-5.5"


def test_catalog_contains_oauth_and_common_provider_entries():
    catalog = model_provider_catalog.load()
    provider_ids = {item["id"] for item in catalog["providers"]}
    assert "gemini" in provider_ids
    assert "codex" in provider_ids
    assert "openrouter" in provider_ids
    assert "siliconflow" in provider_ids


def test_model_capability_registry_contains_benchlm_scope_and_exact_aliases():
    payload = model_capability_registry.load()
    assert payload["stats"]["models"] >= 224
    gpt_55 = model_capability_registry.find("gpt-5.5")
    gpt_5_high = model_capability_registry.find("GPT-5 (high)")
    assert gpt_55
    assert gpt_55["contextWindowTokens"] == 1_000_000
    assert gpt_5_high
    assert gpt_5_high["canonicalModelId"] != gpt_55["canonicalModelId"]
    assert model_capability_registry.find("gpt-5") is None
    assert model_capability_registry.find("gpt-5.5-high") is None


def test_catalog_contains_bigmodel_layered_models():
    zhipu = model_provider_catalog.get_provider("zhipu")
    assert zhipu
    model_ids = {item["id"] for item in zhipu["models"]}
    assert {
        "glm-5.1",
        "glm-5",
        "glm-5-turbo",
        "glm-4.7",
        "glm-5v-turbo",
        "glm-ocr",
        "embedding-3",
        "embedding-2",
        "rerank",
    }.issubset(model_ids)
    assert zhipu["logoAsset"].endswith("zhipu-color.svg")

    vision = model_provider_catalog.normalize_model(zhipu, "glm-5v-turbo")
    embedding = model_provider_catalog.normalize_model(zhipu, "embedding-3")
    rerank = model_provider_catalog.normalize_model(zhipu, "rerank")

    assert vision["type"] == "MULTIMODAL"
    assert vision["capabilities"]["vision"] is True
    assert embedding["type"] == "EMBEDDING"
    assert rerank["type"] == "RERANK"


def test_catalog_exposes_credential_help_for_quick_connect():
    catalog = model_provider_catalog.load()
    providers = {item["id"]: item for item in catalog["providers"]}
    for provider_id in [
        "openai",
        "anthropic",
        "gemini-api",
        "openrouter",
        "deepseek",
        "dashscope",
        "siliconflow",
        "modelscope",
        "huggingface-router",
        "moonshot",
        "zhipu",
        "xiaomi-mimo",
        "minimax",
        "groq",
    ]:
        help_entry = providers[provider_id].get("credentialHelp") or {}
        assert help_entry.get("url", "").startswith("https://")
        assert help_entry.get("kind") in {"api_key", "console"}

    comfy_help = providers["comfyui"].get("credentialHelp") or {}
    assert comfy_help["kind"] == "local_ui"
    assert comfy_help["urlFrom"] == "baseUrl"


def test_catalog_bridges_creative_media_provider_matrix():
    catalog = model_provider_catalog.load()
    providers = {item["id"]: item for item in catalog["providers"]}

    expected = {
        "openai_images": ("image", "IMAGE"),
        "zhipu_bigmodel_image": ("image", "IMAGE"),
        "zhipu_bigmodel_video": ("video", "VIDEO"),
        "zhipu_bigmodel_voice": ("voice", "VOICE"),
        "openai_sora_video": ("video", "VIDEO"),
        "xiaomi_mimo_tts": ("voice", "VOICE"),
        "volcengine_seedance": ("video", "VIDEO"),
        "volcengine_doubao_voice": ("voice", "VOICE"),
        "volcengine_3d_generation": ("model3d", "MODEL3D"),
        "aliyun_bailian_image": ("image", "IMAGE"),
        "aliyun_bailian_video": ("video", "VIDEO"),
        "aliyun_bailian_cosyvoice": ("voice", "VOICE"),
        "mureka_music": ("music", "MUSIC"),
        "fal_3d": ("model3d", "MODEL3D"),
        "tencent_hunyuan_3d": ("model3d", "MODEL3D"),
    }
    for provider_id, (modality, model_type) in expected.items():
        provider = providers[provider_id]
        model_id = provider["models"][0]["id"]
        normalized = model_provider_catalog.normalize_model(provider, model_id)
        assert provider["providerKind"] == "media_generation"
        assert provider["mediaModality"] == modality
        assert provider["credentialHelp"]["url"]
        assert normalized["type"] == model_type
        assert normalized["capabilityClass"] == "media_generation"
        assert provider["models"][0]["contextWindow"] is None
        assert provider["models"][0]["maxTokens"] is None
        assert normalized["contextWindow"] is None
        assert normalized["maxTokens"] is None
        assert normalized["mediaLimits"]["operationKinds"]
    assert providers["aliyun_bailian_image"]["logoAsset"].endswith("alibabacloud-color.svg")
    assert "video.action_transfer" in providers["aliyun_bailian_video"]["models"][0]["operationKinds"]
    assert providers["zhipu_bigmodel_image"]["models"][0]["id"] == "glm-image"
    assert providers["openai_sora_video"]["models"][0]["mediaLimits"]["operationCapabilityProfiles"]["video.text_to_video"]["nativeAudio"] is True
    assert providers["volcengine_seedance"]["models"][0]["mediaLimits"]["operationCapabilityProfiles"]["video.first_last_frame"]["referenceInputs"]["image"]["maxCount"] == 9
    legacy_seedance = next(item for item in providers["volcengine_seedance"]["models"] if item["id"] == "doubao-seedance-1-0-pro-fast-251015")
    assert legacy_seedance["operationKinds"] == ["video.text_to_video", "video.image_to_video"]
    assert legacy_seedance["mediaLimits"]["operationKinds"] == ["video.text_to_video", "video.image_to_video"]
    assert set(legacy_seedance["mediaLimits"]["operationCapabilityProfiles"]) == {"video.text_to_video", "video.image_to_video"}
    assert "video.first_last_frame" not in legacy_seedance["mediaLimits"]["operationCapabilityProfiles"]
    assert providers["xiaomi-mimo"]["promptCachingProfileId"] == "xiaomi_mimo_implicit_prompt_cache"
    assert providers["xiaomi-mimo-tokenplan"]["apiStandard"] == "openai"
    assert providers["xiaomi-mimo-tokenplan"]["baseUrl"] == "https://token-plan-cn.xiaomimimo.com/v1"
    assert providers["xiaomi-mimo-tokenplan"]["probeStrategy"] == "openai_models"
    assert providers["xiaomi-mimo-tokenplan"]["anthropicCompatible"]["baseUrl"] == "https://token-plan-cn.xiaomimimo.com/anthropic"
    assert providers["xiaomi-mimo-tokenplan"]["promptCachingProfileId"] == "xiaomi_mimo_tokenplan_anthropic_observe_cache"
    assert "doubao-seed3d-2-0" in {item["id"] for item in providers["volcengine_3d_generation"]["models"]}
    hitem = next(item for item in providers["volcengine_3d_generation"]["models"] if item["id"] == "hitem3d-2-0")
    assert hitem["logoAsset"].endswith("hitem3d.svg")


def test_catalog_only_probe_does_not_return_preset_models():
    result = model_provider_catalog.probe_provider("suno_placeholder", credential="")

    assert result["ok"] is False
    assert result["source"] == "catalog_metadata"
    assert result["reason"] == "catalog_only_provider"
    assert result["models"] == []
    assert result["rawCount"] >= 1


def test_online_probe_strategy_requires_real_provider_list_not_catalog_fill():
    result = model_provider_catalog.probe_provider("openai_images", credential="")

    assert result["ok"] is False
    assert result["reason"] == "credential_required"
    assert result["models"] == []
    assert result["resolvedModelsUrl"] == "https://api.openai.com/v1/models"


def test_tokenplan_probe_uses_openai_models_endpoint_not_anthropic_models():
    result = model_provider_catalog.probe_provider("xiaomi-mimo-tokenplan", credential="")

    assert result["ok"] is False
    assert result["reason"] == "credential_required"
    assert result["models"] == []
    assert result["resolvedModelsUrl"] == "https://token-plan-cn.xiaomimimo.com/v1/models"


def test_tokenplan_tts_models_are_voice_not_chat():
    provider = model_provider_catalog.get_provider("xiaomi-mimo-tokenplan")
    assert provider

    expected_profiles = {
        "mimo-v2-tts": "voice_tts",
        "mimo-v2.5-tts": "voice_tts",
        "mimo-v2.5-tts-voiceclone": "voice_clone",
        "mimo-v2.5-tts-voicedesign": "voice_design",
    }
    for model_id, profile in expected_profiles.items():
        normalized = model_provider_catalog.normalize_model(provider, model_id, online_metadata={"id": model_id})

        assert normalized["type"] == "VOICE"
        assert normalized["capabilityClass"] == "media_generation"
        assert normalized["parameterProfile"] == profile
        assert normalized["contextWindow"] is None
        assert normalized["maxTokens"] is None
        assert normalized["capabilities"]["voice"] is True
        assert normalized["capabilities"]["audio"] is True
        assert normalized["capabilities"]["chat"] is False
        assert normalized["capabilityTags"] == ["audio", "voice"]


def test_model_control_normalizes_legacy_voice_chat_capability_to_false():
    config = model_control_plane.normalize_config(
        {
            "providers": {
                "xiaomi-mimo-tokenplan": {
                    "provider": {
                        "name": "Xiaomi MiMo TokenPlan",
                        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
                        "api_standard": "openai",
                    },
                    "models": {
                        "mimo-v2-tts": {
                            "type": "VOICE",
                            "capabilities": {"chat": True, "voice": True, "audio": True},
                            "parameterProfile": "voice_tts",
                        }
                    },
                }
            }
        }
    )

    model = config["providers"]["xiaomi-mimo-tokenplan"]["models"]["mimo-v2-tts"]
    assert model["type"] == "VOICE"
    assert model["capabilityClass"] == "media_generation"
    assert model["parameterProfile"] == "voice_tts"
    assert model["capabilities"]["chat"] is False
    assert model["capabilities"]["voice"] is True
    assert model["capabilities"]["audio"] is True


def _layered_default_model_config():
    return model_control_plane.normalize_config(
        {
            "providers": {
                "demo": {
                    "provider": {"name": "Demo"},
                    "models": {
                        "text-chat": {
                            "type": "TEXT",
                            "contextWindow": 128000,
                            "maxTokens": 4096,
                            "capabilityClass": "chat_general",
                            "capabilities": {"chat": True, "streaming": True},
                        },
                        "vision-chat": {
                            "type": "MULTIMODAL",
                            "contextWindow": 128000,
                            "maxTokens": 4096,
                            "capabilityClass": "vision_multimodal",
                            "capabilities": {"chat": True, "vision": True, "multimodal": True},
                        },
                        "embed": {
                            "type": "EMBEDDING",
                            "capabilityClass": "embedding",
                            "capabilities": {"embedding": True},
                        },
                        "rerank": {
                            "type": "RERANK",
                            "capabilityClass": "reranker",
                            "capabilities": {"rerank": True},
                        },
                        "image-gen": {
                            "type": "IMAGE",
                            "capabilityClass": "media_generation",
                            "parameterProfile": "media_generation",
                            "capabilities": {"image": True},
                        },
                    },
                }
            },
            "roles": {
                "default": make_model_ref("demo", "text-chat"),
                "vision": make_model_ref("demo", "vision-chat"),
                "embedding": make_model_ref("demo", "embed"),
                "reranker": make_model_ref("demo", "rerank"),
            },
        }
    )


def test_role_resolution_uses_layered_default_categories():
    config = _layered_default_model_config()

    summary = model_control_plane.resolve_model_for_role("summary", config)
    visual_judge = model_control_plane.resolve_model_for_role("computer_use_visual_judge", config)
    extension_reranker = model_control_plane.resolve_model_for_role("extensions_reranker", config)

    assert summary["resolvedModelRef"] == make_model_ref("demo", "text-chat")
    assert summary["defaultCategory"] == "text_generation"
    assert summary["defaultRole"] == "default"
    assert visual_judge["resolvedModelRef"] == make_model_ref("demo", "vision-chat")
    assert visual_judge["defaultCategory"] == "vision_multimodal"
    assert visual_judge["defaultRole"] == "vision"
    assert extension_reranker["resolvedModelRef"] == make_model_ref("demo", "rerank")
    assert extension_reranker["defaultCategory"] == "reranker"
    assert extension_reranker["defaultRole"] == "reranker"


def test_model_list_exposes_layered_default_categories():
    config = _layered_default_model_config()
    models = {item["modelRef"]: item for item in model_control_plane.list_models(config)}

    assert models[make_model_ref("demo", "text-chat")]["defaultCategories"] == [
        {"key": "text_generation", "label": "文本生成默认", "role": "default", "badge": "sky"}
    ]
    assert models[make_model_ref("demo", "vision-chat")]["defaultCategories"] == [
        {"key": "vision_multimodal", "label": "多模态视觉默认", "role": "vision", "badge": "violet"}
    ]
    assert models[make_model_ref("demo", "embed")]["defaultCategories"][0]["key"] == "embedding"
    assert models[make_model_ref("demo", "rerank")]["defaultCategories"][0]["key"] == "reranker"


def test_set_default_model_for_category_infers_type_and_rejects_media(monkeypatch):
    config = _layered_default_model_config()
    saved_config = {}
    monkeypatch.setattr(model_control_plane, "get_config", lambda: config)

    def save_config(next_config):
        saved_config["value"] = next_config
        return next_config

    monkeypatch.setattr(model_control_plane, "save_config", save_config)

    result = model_control_plane.set_default_model_for_category(model_ref=make_model_ref("demo", "embed"))

    assert result["category"] == "embedding"
    assert result["role"] == "embedding"
    assert saved_config["value"]["roles"]["embedding"] == make_model_ref("demo", "embed")
    with pytest.raises(ValueError, match="media_generation_models_do_not_support_default_binding"):
        model_control_plane.set_default_model_for_category(model_ref=make_model_ref("demo", "image-gen"))


def test_platform_model_defaults_endpoint_accepts_model_ref(monkeypatch):
    captured = {}

    def set_default_model_for_category(*, model_ref, category=None):
        captured["model_ref"] = model_ref
        captured["category"] = category
        return {"ok": True, "category": category, "role": "embedding", "modelRef": model_ref}

    monkeypatch.setattr(platform_routes.model_control_plane, "set_default_model_for_category", set_default_model_for_category)

    result = asyncio.run(
        platform_routes.set_model_default_category(
            {"modelRef": make_model_ref("demo", "embed"), "category": "embedding"}
        )
    )

    assert result["status"] == "success"
    assert captured == {"model_ref": make_model_ref("demo", "embed"), "category": "embedding"}


def test_role_cards_expose_role_doctor_readiness():
    config = model_control_plane.normalize_config(
        {
            "providers": {
                "custom-openai": {
                    "provider": {"name": "Custom OpenAI"},
                    "models": {
                        "tiny-chat": {
                            "name": "Tiny Chat",
                            "type": "CHAT",
                            "contextWindow": 8192,
                            "maxTokens": 1024,
                            "capabilityClass": "chat_general",
                            "capabilities": {"chat": True, "streaming": True},
                        }
                    },
                }
            },
            "roles": {"supervisor": make_model_ref("custom-openai", "tiny-chat")},
        }
    )

    cards = model_control_plane.get_role_cards(config)
    supervisor = next(item for item in cards if item["key"] == "supervisor")

    assert supervisor["bindingState"] == "explicit"
    assert supervisor["readiness"] == "blocked"
    assert supervisor["readinessReason"] == "below_min_context_window"
    assert supervisor["roleDoctor"]["role"] == "supervisor"
    assert supervisor["roleDoctor"]["blocking"] is True


def test_anthropic_official_probe_url_stays_v1_models():
    result = model_provider_catalog.probe_provider("anthropic", credential="")

    assert result["ok"] is False
    assert result["reason"] == "credential_required"
    assert result["resolvedModelsUrl"] == "https://api.anthropic.com/v1/models"


def _write_catalog(path, providers):
    path.write_text(json.dumps({"version": 1, "providers": providers}), encoding="utf-8")


def test_probe_requires_key_and_never_falls_back_to_catalog_models(tmp_path):
    catalog_path = tmp_path / "provider_catalog.json"
    _write_catalog(
        catalog_path,
        [
            {
                "id": "demo",
                "name": "Demo",
                "apiStandard": "openai",
                "baseUrl": "https://api.example.com/v1",
                "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
                "probeStrategy": "openai_models",
                "models": [{"id": "preset-model"}],
            }
        ],
    )
    catalog = ModelProviderCatalog(path=catalog_path, custom_path=tmp_path / "custom.json")

    result = catalog.probe_provider("demo", credential="")

    assert result["ok"] is False
    assert result["reason"] == "credential_required"
    assert result["models"] == []
    assert result["resolvedModelsUrl"] == "https://api.example.com/v1/models"


def test_probe_uses_base_url_models_and_parses_online_response(tmp_path, monkeypatch):
    catalog_path = tmp_path / "provider_catalog.json"
    _write_catalog(
        catalog_path,
        [
            {
                "id": "demo",
                "name": "Demo",
                "apiStandard": "openai",
                "baseUrl": "https://api.example.com/v1",
                "modelsEndpoint": "/not-used",
                "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
                "probeStrategy": "openai_models",
            }
        ],
    )
    catalog = ModelProviderCatalog(path=catalog_path, custom_path=tmp_path / "custom.json")
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"id": "online-a"}, {"id": "models/online-b"}]}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        captured["params"] = params or {}
        return FakeResponse()

    monkeypatch.setattr("core.model_provider_catalog.requests.get", fake_get)

    result = catalog.probe_provider("demo", credential="sk-test")

    assert captured["url"] == "https://api.example.com/v1/models"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert result["ok"] is True
    assert result["source"] == "online"
    assert [item["modelId"] for item in result["models"]] == ["online-a", "online-b"]


def test_probe_honors_explicit_models_path(tmp_path, monkeypatch):
    catalog_path = tmp_path / "provider_catalog.json"
    _write_catalog(
        catalog_path,
        [
            {
                "id": "demo",
                "name": "Demo",
                "apiStandard": "openai",
                "baseUrl": "https://api.example.com/v1",
                "modelsPath": "/open/models",
                "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
                "probeStrategy": "openai_models",
            }
        ],
    )
    catalog = ModelProviderCatalog(path=catalog_path, custom_path=tmp_path / "custom.json")
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"id": "online-a"}]}

    def fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        return FakeResponse()

    monkeypatch.setattr("core.model_provider_catalog.requests.get", fake_get)

    result = catalog.probe_provider("demo", credential="sk-test")

    assert captured["url"] == "https://api.example.com/v1/open/models"
    assert result["ok"] is True


def test_probe_classifies_tls_or_network_errors(tmp_path, monkeypatch):
    catalog_path = tmp_path / "provider_catalog.json"
    _write_catalog(
        catalog_path,
        [
            {
                "id": "demo",
                "name": "Demo",
                "apiStandard": "openai",
                "baseUrl": "https://api.example.com/v1",
                "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
                "probeStrategy": "openai_models",
            }
        ],
    )
    catalog = ModelProviderCatalog(path=catalog_path, custom_path=tmp_path / "custom.json")

    def fake_get(url, headers=None, params=None, timeout=None):
        raise Exception("SSLEOFError unexpected EOF while reading")

    monkeypatch.setattr("core.model_provider_catalog.requests.get", fake_get)

    result = catalog.probe_provider("demo", credential="sk-test")

    assert result["ok"] is False
    assert result["reason"] == "tls_or_network_error"
    assert result["models"] == []


def test_probe_reuses_saved_provider_credential_by_exact_provider(monkeypatch):
    monkeypatch.setattr(
        platform_routes.model_control_plane,
        "get_config",
        lambda: {
            "providers": {
                "deepseek": {
                    "provider": {
                        "name": "DeepSeek",
                        "base_url": "https://api.deepseek.com",
                        "api_key": "sk-deepseek",
                    }
                }
            }
        },
    )

    credential, source = platform_routes._stored_provider_credential(
        "deepseek",
        {"id": "deepseek", "name": "DeepSeek", "baseUrl": "https://api.deepseek.com"},
    )

    assert credential == "sk-deepseek"
    assert source == "stored_provider"


def test_probe_reuses_volcengine_credential_by_realm(monkeypatch):
    monkeypatch.setattr(
        platform_routes.model_control_plane,
        "get_config",
        lambda: {
            "providers": {
                "volcengine_seedance": {
                    "provider": {
                        "name": "Volcengine Ark Seedance",
                        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                        "api_key": "sk-ark",
                        "credentialRealm": "volcengine_ark",
                    }
                }
            }
        },
    )

    credential, source = platform_routes._stored_provider_credential(
        "volcengine-ark",
        {"id": "volcengine-ark", "name": "Volcengine Ark / Doubao", "credentialRealm": "volcengine_ark"},
    )

    assert credential == "sk-ark"
    assert source == "stored_provider_realm:volcengine_seedance"


def test_probe_does_not_reuse_credentials_without_matching_realm(monkeypatch):
    monkeypatch.setattr(
        platform_routes.model_control_plane,
        "get_config",
        lambda: {
            "providers": {
                "provider-a": {
                    "provider": {
                        "name": "Provider A",
                        "base_url": "https://api.a.example/v1",
                        "api_key": "sk-a",
                        "credentialRealm": "provider_a",
                    }
                }
            }
        },
    )

    credential, source = platform_routes._stored_provider_credential(
        "provider-b",
        {"id": "provider-b", "name": "Provider B", "baseUrl": "https://api.b.example/v1"},
    )

    assert credential == ""
    assert source == ""


def test_custom_provider_overlay_roundtrip(tmp_path):
    catalog_path = tmp_path / "provider_catalog.json"
    _write_catalog(catalog_path, [{"id": "builtin", "name": "Builtin", "baseUrl": "https://api.example.com/v1"}])
    custom_path = tmp_path / "custom.json"
    catalog = ModelProviderCatalog(path=catalog_path, custom_path=custom_path)

    provider = catalog.build_custom_provider("Local Gateway", "http://127.0.0.1:8317/v1")
    saved = catalog.save_custom_provider(provider)
    providers = catalog.load()["providers"]

    assert providers[0]["id"] == saved["id"]
    assert providers[0]["isCustom"] is True
    assert providers[0]["baseUrl"] == "http://127.0.0.1:8317/v1"
    assert catalog.delete_custom_provider(saved["id"]) is True
    assert all(item["id"] != saved["id"] for item in catalog.load()["providers"])


def test_connect_custom_provider_does_not_seed_runtime_budget_parameters(monkeypatch):
    saved_config = {}
    provider = {
        "id": "custom-demo",
        "name": "Custom Demo",
        "baseUrl": "http://127.0.0.1:8317/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
        "isCustom": True,
        "models": [
            {
                "id": "demo-model",
                "contextWindow": 128000,
                "maxTokens": 8192,
                "capabilities": ["chat"],
            }
        ],
    }

    monkeypatch.setattr(platform_routes.model_provider_catalog, "get_provider", lambda provider_id: provider if provider_id == "custom-demo" else None)
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: saved_config.setdefault("value", config))

    result = asyncio.run(platform_routes.connect_model_provider({"providerId": "custom-demo", "modelId": "demo-model", "apiKey": "sk-test"}))

    model = result["config"]["providers"]["custom-demo"]["models"]["demo-model"]
    assert model["contextWindow"] is None
    assert model["maxTokens"] is None
    assert "temperature" not in model


def test_connect_custom_provider_inherits_known_model_capability_budget(monkeypatch):
    saved_config = {}
    provider = {
        "id": "custom-openai-compatible",
        "name": "Custom OpenAI Compatible",
        "baseUrl": "http://127.0.0.1:8317/v1",
        "apiStandard": "openai",
        "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
        "isCustom": True,
        "models": [],
    }

    monkeypatch.setattr(
        platform_routes.model_provider_catalog,
        "get_provider",
        lambda provider_id: provider if provider_id == "custom-openai-compatible" else None,
    )
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: saved_config.setdefault("value", config))

    result = asyncio.run(platform_routes.connect_model_provider({"providerId": "custom-openai-compatible", "modelId": "gpt-5.2", "apiKey": "sk-test"}))

    model = result["config"]["providers"]["custom-openai-compatible"]["models"]["gpt-5.2"]
    assert model["contextWindow"] == 400_000
    assert model["capabilitySource"] == "model_capability_registry"
    assert model["capabilityRegistry"]["canonicalModelId"] == "gpt-5.2"
    assert model["pricing"]["inputPerMillionTokens"] == 1.75
    assert "temperature" not in model


def test_explicit_provider_capability_override_wins_over_registry():
    provider = {
        "id": "override-provider",
        "providerKind": "chat",
        "models": [
            {
                "id": "gpt-5.2",
                "contextWindow": 12345,
                "maxOutputTokens": 6789,
                "explicitCapabilityOverride": True,
                "capabilities": ["chat"],
            }
        ],
    }
    normalized = model_provider_catalog.normalize_model(provider, "gpt-5.2")
    assert normalized["contextWindow"] == 12345
    assert normalized["maxTokens"] == 6789


def test_connect_oauth_provider_does_not_seed_runtime_budget_parameters(monkeypatch):
    saved_config = {}
    provider = {
        "id": "gemini",
        "name": "Gemini CLI OAuth",
        "baseUrl": "https://cloudcode-pa.googleapis.com",
        "apiStandard": "gemini",
        "auth": {"type": "oauth_file", "path": "~/.gemini/oauth_creds.json", "preset": "geminiCli"},
        "models": [
            {
                "id": "gemini-3-flash-preview",
                "contextWindow": 1049000,
                "maxTokens": 65536,
                "capabilities": ["chat", "streaming"],
            }
        ],
    }

    monkeypatch.setattr(platform_routes.model_provider_catalog, "get_provider", lambda provider_id: provider if provider_id == "gemini" else None)
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: saved_config.setdefault("value", config))

    result = asyncio.run(platform_routes.connect_model_provider({"providerId": "gemini", "modelId": "gemini-3-flash-preview"}))

    model = result["config"]["providers"]["gemini"]["models"]["gemini-3-flash-preview"]
    assert model["contextWindow"] is None
    assert model["maxTokens"] is None
    assert "temperature" not in model


def test_connect_media_provider_does_not_seed_chat_budget_parameters(monkeypatch):
    saved_config = {}
    provider = {
        "id": "openai_images",
        "name": "OpenAI-compatible Images",
        "baseUrl": "https://api.example.com/v1",
        "apiStandard": "openai_images",
        "providerKind": "media_generation",
        "mediaModality": "image",
        "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
        "models": [
            {
                "id": "gpt-image-2",
                "type": "IMAGE",
                "logoAsset": "/model-assets/lobe/openai.svg",
                "capabilities": ["image"],
                "contextWindow": 128000,
                "maxTokens": 4096,
            }
        ],
    }

    monkeypatch.setattr(platform_routes.model_provider_catalog, "get_provider", lambda provider_id: provider if provider_id == "openai_images" else None)
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: saved_config.setdefault("value", config))

    result = asyncio.run(platform_routes.connect_model_provider({"providerId": "openai_images", "modelId": "gpt-image-2", "apiKey": "sk-test"}))

    provider_meta = result["config"]["providers"]["openai_images"]["provider"]
    model = result["config"]["providers"]["openai_images"]["models"]["gpt-image-2"]
    assert provider_meta["mediaModality"] == "image"
    assert model["type"] == "IMAGE"
    assert model["contextWindow"] is None
    assert model["maxTokens"] is None
    assert model["logoAsset"] == "/model-assets/lobe/openai.svg"
    assert model["capabilities"]["image"] is True
    assert "temperature" not in model


def test_connect_detected_voice_model_keeps_voice_type_when_chat_purpose_submits_text(monkeypatch):
    saved_config = {}
    provider = model_provider_catalog.get_provider("xiaomi-mimo-tokenplan")
    assert provider

    monkeypatch.setattr(platform_routes.model_provider_catalog, "get_provider", lambda provider_id: provider if provider_id == "xiaomi-mimo-tokenplan" else None)
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: saved_config.setdefault("value", config))

    result = asyncio.run(
        platform_routes.connect_model_provider(
            {
                "providerId": "xiaomi-mimo-tokenplan",
                "modelId": "mimo-v2-tts",
                "apiKey": "sk-test",
                "modelType": "TEXT",
            }
        )
    )

    provider_meta = result["config"]["providers"]["xiaomi-mimo-tokenplan"]["provider"]
    model = result["config"]["providers"]["xiaomi-mimo-tokenplan"]["models"]["mimo-v2-tts"]
    assert provider_meta["base_url"] == "https://token-plan-cn.xiaomimimo.com/v1"
    assert provider_meta["api_standard"] == "openai"
    assert model["type"] == "VOICE"
    assert model["capabilityClass"] == "media_generation"
    assert model["parameterProfile"] == "voice_tts"
    assert model["capabilities"]["chat"] is False
    assert model["capabilities"]["voice"] is True


def test_catalog_capabilities_do_not_depend_on_models_endpoint_metadata():
    openai = model_provider_catalog.get_provider("openai")
    gemini = model_provider_catalog.get_provider("gemini")
    comfy = model_provider_catalog.get_provider("comfyui")

    assert openai and gemini and comfy
    gpt = model_provider_catalog.normalize_model(openai, "gpt-5.5")
    gemini_model = model_provider_catalog.normalize_model(gemini, "gemini-3-flash-preview")
    comfy_model = model_provider_catalog.normalize_model(comfy, "comfyui-workflow")

    assert gpt["capabilities"]["vision"] is True
    assert gpt["capabilities"]["multimodal"] is True
    assert gemini_model["capabilities"]["vision"] is True
    assert gemini_model["capabilities"]["multimodal"] is True
    assert comfy_model["type"] == "MEDIA"
    assert comfy_model["capabilities"]["image"] is True
    assert comfy_model["capabilityClass"] == "media_generation"


def test_role_temperature_overrides_are_application_level_not_model_level():
    config = model_control_plane.normalize_config(
        {
            "providers": {
                "demo": {"provider": {"name": "Demo"}, "models": {"demo-model": {"temperature": 1.2}}},
            },
            "roles": {"supervisor": make_model_ref("demo", "demo-model")},
            "roleParameters": {
                "supervisor": {"temperature": 0.25},
                "subagent": {"temperature": ""},
            },
        }
    )

    model = config["providers"]["demo"]["models"]["demo-model"]
    assert "temperature" not in model
    assert model_control_plane.get_role_temperature("supervisor", config) == 0.25
    assert model_control_plane.get_role_temperature("agent:worker-1", config) is None


def test_user_role_temperature_zero_normalizes_to_unset():
    config = model_control_plane.normalize_config(
        {
            "roleParameters": {
                "supervisor": {"temperature": 0},
                "subagent": {"temperature": "0.0"},
                "agent:legacy": {"temperature": ""},
            },
        }
    )

    assert config["roleParameters"]["supervisor"]["temperature"] is None
    assert config["roleParameters"]["subagent"]["temperature"] is None
    assert model_control_plane.get_role_temperature("supervisor", config) is None
    assert model_control_plane.get_role_temperature("subagent", config) is None
    assert model_control_plane.get_role_temperature("agent:legacy", config) is None


def test_config_model_temperature_zero_is_not_sent_to_provider_kwargs():
    meta = {
        "api_key": "sk-test",
        "global_temperature": 0,
        "global_max_tokens": None,
    }

    assert "temperature" not in llm_factory._build_openai_kwargs("demo-model", meta)
    assert "temperature" not in llm_factory._build_anthropic_kwargs("demo-model", meta)
    assert "temperature" not in llm_factory._build_gemini_kwargs("demo-model", meta)
    assert llm_factory._build_openai_kwargs("demo-model", meta, temperature=0)["temperature"] == 0


def test_explicit_create_for_role_temperature_zero_is_preserved(monkeypatch):
    monkeypatch.setattr(
        "core.llm_factory.model_control_plane.resolve_model_for_role",
        lambda role: {"resolvedModelRef": "demo::model", "resolvedModelId": "model"},
    )
    monkeypatch.setattr(
        "core.llm_factory.LLMFactory._resolve_model_metadata",
        classmethod(lambda cls, model_id: {
            "is_found": True,
            "model_id": "model",
            "provider_id": "demo",
            "provider_name": "demo",
            "provider_record": {},
            "api_standard": "openai",
            "api_key": "sk-test",
            "runtime_ready": True,
            "global_temperature": 0.8,
            "global_max_tokens": None,
            "global_context_window": None,
            "effective_capability_matrix": {},
        }),
    )

    model = llm_factory.create_for_role("summary", temperature=0)

    assert model._model_kwargs["temperature"] == 0
