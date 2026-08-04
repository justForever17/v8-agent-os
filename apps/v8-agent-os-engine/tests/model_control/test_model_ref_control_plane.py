import asyncio
import json

import pytest

from api import platform_routes
from core.llm_factory import llm_factory
from core.model_capability_registry import model_capability_registry
from core.model_control_plane import DEFAULT_ROLE_MAP, DEFAULT_ROUTING_POLICIES, MODULE_DEFINITIONS, ROLE_DEFINITIONS, model_control_plane
from core.model_provider_catalog import ModelProviderCatalog, model_provider_catalog
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from core.model_thinking_control import (
    reasoning_effort_request_patch,
    resolve_reasoning_effort_control_for_metadata,
    resolve_thinking_control_for_metadata,
)
from core.model_ref import make_model_ref, parse_model_ref

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:  # pragma: no cover - optional in partial dev environments
    ChatGoogleGenerativeAI = None


@pytest.fixture(autouse=True)
def _isolate_model_credentials(monkeypatch):
    monkeypatch.setattr(
        platform_routes.model_control_plane,
        "_credential_store",
        CredentialRefStore(MemoryCredentialBackend()),
    )


def test_model_ref_roundtrip():
    ref = make_model_ref("codex-oauth", "gpt-5.5")
    assert ref == "codex-oauth::gpt-5.5"
    assert parse_model_ref(ref) == ("codex-oauth", "gpt-5.5")


def test_global_planner_role_and_lane_are_removed():
    assert "planner" not in DEFAULT_ROLE_MAP
    assert "planner" not in DEFAULT_ROUTING_POLICIES
    assert "planner" not in ROLE_DEFINITIONS
    assert all(item["key"] != "planner_lane" for item in MODULE_DEFINITIONS)


def test_role_projection_ignores_historical_planner_and_constrains_active_dynamic_roles():
    definitions = model_control_plane.get_role_definitions(
        {"roles": {"planner": "provider::old", "custom_worker": "provider::current"}}
    )

    assert "planner" not in definitions
    assert definitions["custom_worker"]["capabilityClasses"] == [
        "chat_general",
        "chat_tool_calling",
        "chat_reasoning",
        "vision_multimodal",
    ]


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
    assert "codex" in provider_ids
    assert "gemini" not in provider_ids
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
    sensenova_flash = model_capability_registry.find("sensenova-6.7-flash-lite")
    gpt_56 = model_capability_registry.find("gpt-5.6")
    gpt_56_terra = model_capability_registry.find("gpt-5.6-terra")
    claude_opus_48 = model_capability_registry.find("claude-opus-4-8")
    claude_sonnet_5 = model_capability_registry.find("claude-sonnet-5")
    claude_mythos_5 = model_capability_registry.find("claude-mythos-5")
    deepseek_v4_flash = model_capability_registry.find("deepseek-v4-flash")
    assert sensenova_flash["contextWindowTokens"] == 262_144
    assert sensenova_flash["maxOutputTokens"] == 65_536
    assert gpt_56["canonicalModelId"] == "gpt-5.6-sol"
    assert gpt_56["contextWindowTokens"] == 1_050_000
    assert gpt_56["maxOutputTokens"] == 128_000
    assert gpt_56_terra["maxOutputTokens"] == 128_000
    assert claude_opus_48["contextWindowTokens"] == 1_000_000
    assert claude_sonnet_5["contextWindowTokens"] == 1_000_000
    assert claude_sonnet_5["maxOutputTokens"] == 128_000
    assert claude_mythos_5["availability"] == "limited_invitation_only"
    assert deepseek_v4_flash["maxOutputTokens"] == 65_536


def test_catalog_contains_bigmodel_layered_models():
    zhipu = model_provider_catalog.get_provider("zhipu")
    zai_coding = model_provider_catalog.get_provider("zai-coding")
    assert zhipu
    assert zai_coding
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

    zai_model_ids = {item["id"] for item in zai_coding["models"]}
    assert {"glm-5.2", "glm-5.2[1m]"}.issubset(zai_model_ids)
    glm_52 = model_provider_catalog.normalize_model(zai_coding, "glm-5.2")
    assert zai_coding["baseUrl"] == "https://api.z.ai/api/coding/paas/v4"
    assert glm_52["contextWindow"] == 1_000_000
    assert glm_52["maxTokens"] == 131_072
    assert glm_52["reasoningSurface"]["mode"] == "hidden"


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
        "zai-coding",
        "xiaomi-mimo",
        "minimax",
        "minimax-cn",
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
        "agnes_image": ("image", "IMAGE"),
        "agnes_video": ("video", "VIDEO"),
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
    assert "video.action_transfer" not in providers["aliyun_bailian_video"]["models"][0]["operationKinds"]
    animate_move = next(item for item in providers["aliyun_bailian_video"]["models"] if item["id"] == "wan2.2-animate-move")
    assert "video.action_transfer" in animate_move["operationKinds"]
    assert providers["zhipu_bigmodel_image"]["models"][0]["id"] == "glm-image"
    assert providers["openai_sora_video"]["models"][0]["mediaLimits"]["operationCapabilityProfiles"]["video.text_to_video"]["nativeAudio"] is True
    seedance_20 = next(
        item
        for item in providers["volcengine_seedance"]["models"]
        if item["id"] == "doubao-seedance-2-0-260128"
    )
    assert seedance_20["mediaLimits"]["operationCapabilityProfiles"]["video.first_last_frame"]["referenceInputs"]["image"]["maxCount"] == 9
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
    assert {
        "agnes_image",
        "agnes_video",
        "minimax_image",
        "minimax_video",
        "minimax_tts",
        "minimax_music",
    }.issubset(providers)
    assert providers["agnes_image"]["baseUrl"] == "https://apihub.agnes-ai.com/v1"
    assert providers["agnes_image"]["models"][0]["id"] == "agnes-image-2.1-flash"
    assert providers["agnes_video"]["baseUrl"] == "https://apihub.agnes-ai.com"
    assert providers["agnes_video"]["models"][0]["id"] == "agnes-video-v2.0"


def test_root_provider_accepts_prefixed_media_model_ids():
    agnes = model_provider_catalog.get_provider("agnes")
    minimax_cn = model_provider_catalog.get_provider("minimax-cn")
    dashscope = model_provider_catalog.get_provider("dashscope")
    volcengine_ark = model_provider_catalog.get_provider("volcengine-ark")
    assert agnes
    assert minimax_cn
    assert dashscope
    assert volcengine_ark

    agnes_image = model_provider_catalog.normalize_model(agnes, "images/generations/agnes-image-2.1-flash")
    assert agnes_image["type"] == "IMAGE"
    assert agnes_image["modelId"] == "images/generations/agnes-image-2.1-flash"
    assert agnes_image["mediaLimits"]["adapter"] == "agnes_images"
    assert agnes_image["mediaLimits"]["providerModelId"] == "agnes-image-2.1-flash"
    assert "image.generate" in agnes_image["mediaLimits"]["operationKinds"]

    agnes_video = model_provider_catalog.normalize_model(agnes, "videos/agnes-video-v2.0")
    assert agnes_video["type"] == "VIDEO"
    assert agnes_video["modelId"] == "videos/agnes-video-v2.0"
    assert agnes_video["mediaLimits"]["adapter"] == "agnes_video"
    assert agnes_video["mediaLimits"]["adapterProviderId"] == "agnes_video"
    assert agnes_video["mediaLimits"]["providerModelId"] == "agnes-video-v2.0"
    assert "video.first_last_frame" in agnes_video["mediaLimits"]["operationKinds"]

    minimax_image = model_provider_catalog.normalize_model(minimax_cn, "image_generation/image-01")
    assert minimax_image["type"] == "IMAGE"
    assert minimax_image["mediaLimits"]["adapterProviderId"] == "minimax_image"
    assert minimax_image["mediaLimits"]["providerModelId"] == "image-01"
    assert "image.generate" in minimax_image["mediaLimits"]["operationKinds"]

    minimax_video = model_provider_catalog.normalize_model(minimax_cn, "video_generation/MiniMax-Hailuo-02")
    assert minimax_video["type"] == "VIDEO"
    assert minimax_video["mediaLimits"]["adapterProviderId"] == "minimax_video"
    assert minimax_video["mediaLimits"]["providerModelId"] == "MiniMax-Hailuo-02"
    assert "video.text_to_video" in minimax_video["mediaLimits"]["operationKinds"]

    minimax_tts = model_provider_catalog.normalize_model(minimax_cn, "t2a_v2/speech-02-hd")
    assert minimax_tts["type"] == "VOICE"
    assert minimax_tts["mediaLimits"]["adapterProviderId"] == "minimax_tts"
    assert minimax_tts["mediaLimits"]["providerModelId"] == "speech-02-hd"
    assert "voice.tts" in minimax_tts["mediaLimits"]["operationKinds"]

    minimax_music = model_provider_catalog.normalize_model(minimax_cn, "music_generation/music-2.6")
    assert minimax_music["type"] == "MUSIC"
    assert minimax_music["mediaLimits"]["adapterProviderId"] == "minimax_music"
    assert minimax_music["mediaLimits"]["providerModelId"] == "music-2.6"
    assert "music.generate" in minimax_music["mediaLimits"]["operationKinds"]

    minimax_music_25 = model_provider_catalog.normalize_model(minimax_cn, "music_generation/minimax-music-2.5")
    assert minimax_music_25["type"] == "MUSIC"
    assert minimax_music_25["mediaLimits"]["adapterProviderId"] == "minimax_music"
    assert minimax_music_25["mediaLimits"]["providerModelId"] == "minimax-music-2.5"

    cosyvoice_tts = model_provider_catalog.normalize_model(
        dashscope,
        "services/audio/tts/SpeechSynthesizer/cosyvoice-v3-flash",
    )
    assert cosyvoice_tts["type"] == "VOICE"
    assert cosyvoice_tts["mediaLimits"]["adapterProviderId"] == "aliyun_bailian_cosyvoice"
    assert cosyvoice_tts["mediaLimits"]["providerModelId"] == "cosyvoice-v3-flash"
    assert "voice.tts" in cosyvoice_tts["mediaLimits"]["operationKinds"]

    doubao_voice_tts = model_provider_catalog.normalize_model(
        volcengine_ark,
        "audio/speech/doubao-voice-synthesis-2-0",
    )
    assert doubao_voice_tts["type"] == "VOICE"
    assert doubao_voice_tts["mediaLimits"]["adapterProviderId"] == "volcengine_doubao_voice"
    assert doubao_voice_tts["mediaLimits"]["providerModelId"] == "doubao-voice-synthesis-2-0"
    assert "voice.tts" in doubao_voice_tts["mediaLimits"]["operationKinds"]


def test_media_matrix_matches_by_model_id_across_custom_gateway_paths():
    custom_minimax = model_provider_catalog.build_custom_provider(
        name="My MiniMax Gateway",
        base_url="https://relay.example.com/openai-compatible",
        provider_id="custom-minimax-gateway",
        provider_kind="media_generation",
    )
    custom_music = model_provider_catalog.normalize_model(custom_minimax, "music_generation/minimax-music-2.5")
    assert custom_music["type"] == "MUSIC"
    assert custom_music["modelId"] == "music_generation/minimax-music-2.5"
    assert custom_music["mediaLimits"]["adapterProviderId"] == "minimax_music"
    assert custom_music["mediaLimits"]["providerModelId"] == "minimax-music-2.5"
    assert "music.generate" in custom_music["mediaLimits"]["operationKinds"]

    custom_video = model_provider_catalog.normalize_model(custom_minimax, "video_generation/MiniMax-Hailuo-02")
    assert custom_video["type"] == "VIDEO"
    assert custom_video["mediaLimits"]["adapterProviderId"] == "minimax_video"
    assert custom_video["mediaLimits"]["providerModelId"] == "MiniMax-Hailuo-02"

    custom_video_by_bare_model_id = model_provider_catalog.normalize_model(custom_minimax, "MiniMax-Hailuo-02")
    assert custom_video_by_bare_model_id["type"] == "VIDEO"
    assert custom_video_by_bare_model_id["mediaLimits"]["adapterProviderId"] == "minimax_video"
    assert custom_video_by_bare_model_id["mediaLimits"]["providerModelId"] == "MiniMax-Hailuo-02"

    custom_video_by_gateway_path = model_provider_catalog.normalize_model(custom_minimax, "proxy/media/video/MiniMax-Hailuo-2.3")
    assert custom_video_by_gateway_path["type"] == "VIDEO"
    assert custom_video_by_gateway_path["mediaLimits"]["adapterProviderId"] == "minimax_video"
    assert custom_video_by_gateway_path["mediaLimits"]["providerModelId"] == "MiniMax-Hailuo-2.3"

    custom_agnes = model_provider_catalog.build_custom_provider(
        name="My Agnes Gateway",
        base_url="https://relay.example.com/agnes",
        provider_id="custom-agnes-gateway",
        provider_kind="media_generation",
    )
    custom_agnes_video = model_provider_catalog.normalize_model(custom_agnes, "videos/agnes-video-v2.0")
    assert custom_agnes_video["type"] == "VIDEO"
    assert custom_agnes_video["mediaLimits"]["adapterProviderId"] == "agnes_video"
    assert custom_agnes_video["mediaLimits"]["providerModelId"] == "agnes-video-v2.0"

    custom_google = model_provider_catalog.build_custom_provider(
        name="My Google Media Gateway",
        base_url="https://relay.example.com/google",
        provider_id="custom-google-media",
        provider_kind="media_generation",
    )
    custom_google_image = model_provider_catalog.normalize_model(custom_google, "models/nano-banana-pro:generateContent")
    assert custom_google_image["type"] == "IMAGE"
    assert custom_google_image["mediaLimits"]["adapterProviderId"] == "google_gemini_image"
    assert custom_google_image["mediaLimits"]["providerModelId"] == "nano-banana-pro"

    custom_stability = model_provider_catalog.build_custom_provider(
        name="My Stability Gateway",
        base_url="https://relay.example.com/stability",
        provider_id="custom-stability-media",
        provider_kind="media_generation",
    )
    custom_stability_music = model_provider_catalog.normalize_model(
        custom_stability,
        "v2beta/audio/stable-audio-2/text-to-audio/stable-audio-2.5",
    )
    assert custom_stability_music["type"] == "MUSIC"
    assert custom_stability_music["mediaLimits"]["adapterProviderId"] == "stability_music"
    assert custom_stability_music["mediaLimits"]["providerModelId"] == "stable-audio-2.5"

    custom_aliyun = model_provider_catalog.build_custom_provider(
        name="My Aliyun Gateway",
        base_url="https://relay.example.com/aliyun",
        provider_id="custom-aliyun-media",
        provider_kind="media_generation",
    )
    custom_aliyun_3d = model_provider_catalog.normalize_model(
        custom_aliyun,
        "services/aigc/3d-generation/generation/motionshop-gen3d",
    )
    assert custom_aliyun_3d["type"] == "MODEL3D"
    assert custom_aliyun_3d["mediaLimits"]["adapterProviderId"] == "aliyun_bailian_3d"
    assert custom_aliyun_3d["mediaLimits"]["providerModelId"] == "motionshop-gen3d"


def test_catalog_only_probe_does_not_return_preset_models():
    result = model_provider_catalog.probe_provider("suno_placeholder", credential="")

    assert result["ok"] is False
    assert result["source"] == "catalog_metadata"
    assert result["reason"] == "catalog_only_provider"
    assert result["models"] == []
    assert result["rawCount"] >= 1


def test_minimax_providers_use_official_models_endpoint_and_reasoning_contract():
    provider = model_provider_catalog.get_provider("minimax")
    cn_provider = model_provider_catalog.get_provider("minimax-cn")
    assert provider
    assert cn_provider
    assert provider["probeStrategy"] == "openai_models"
    assert cn_provider["probeStrategy"] == "openai_models"
    assert provider["baseUrl"] == "https://api.minimax.io/v1"
    assert cn_provider["baseUrl"] == "https://api.minimaxi.com/v1"
    assert provider["anthropicCompatible"]["baseUrl"] == "https://api.minimax.io/anthropic"
    assert cn_provider["anthropicCompatible"]["baseUrl"] == "https://api.minimaxi.com/anthropic"
    assert provider["anthropicCompatible"]["modelsUrl"] == "https://api.minimax.io/anthropic/v1/models"
    assert cn_provider["anthropicCompatible"]["modelsUrl"] == "https://api.minimaxi.com/anthropic/v1/models"

    model_ids = {item["id"] for item in provider["models"]}
    assert {"MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.5", "M2-her"}.issubset(model_ids)

    normalized = model_provider_catalog.normalize_model(provider, "MiniMax-M3")
    assert normalized["contextWindow"] == 1_000_000
    assert normalized["capabilities"]["reasoning"] is True
    assert normalized["capabilities"]["vision"] is True
    assert normalized["reasoningSurface"]["mode"] == "provider_reasoning"
    assert normalized["reasoningSurface"]["trust"] == "official"
    assert "reasoning_details" in normalized["reasoningSurface"]["responseFields"]
    assert normalized["thinkingControl"]["supportsNoThink"] is True
    assert normalized["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert normalized["thinkingControl"]["disabled"] is False


def test_supported_no_think_models_expose_thinking_control():
    deepseek = model_provider_catalog.get_provider("deepseek")
    dashscope = model_provider_catalog.get_provider("dashscope")
    zhipu = model_provider_catalog.get_provider("zhipu")
    zai_coding = model_provider_catalog.get_provider("zai-coding")
    mimo = model_provider_catalog.get_provider("xiaomi-mimo")
    mimo_tokenplan = model_provider_catalog.get_provider("xiaomi-mimo-tokenplan")
    volcengine_ark = model_provider_catalog.get_provider("volcengine-ark")
    assert deepseek and dashscope and zhipu and zai_coding and mimo and mimo_tokenplan and volcengine_ark

    deepseek_v4 = model_provider_catalog.normalize_model(deepseek, "deepseek-v4-flash")
    qwen = model_provider_catalog.normalize_model(dashscope, "qwen-max")
    glm = model_provider_catalog.normalize_model(zhipu, "glm-5")
    glm_52 = model_provider_catalog.normalize_model(zai_coding, "glm-5.2")
    mimo_25_pro = model_provider_catalog.normalize_model(mimo, "mimo-v2.5-pro")
    mimo_25 = model_provider_catalog.normalize_model(mimo, "mimo-v2.5")
    mimo_25_flash = model_provider_catalog.normalize_model(mimo, "mimo-v2.5-flash")
    mimo_25_tts = model_provider_catalog.normalize_model(mimo, "mimo-v2.5-tts")
    mimo_tokenplan_25_pro = model_provider_catalog.normalize_model(mimo_tokenplan, "mimo-v2.5-pro")
    doubao_seed_21 = model_provider_catalog.normalize_model(volcengine_ark, "doubao-seed-2-1-pro-260628")
    doubao_seed = model_provider_catalog.normalize_model(volcengine_ark, "doubao-seed-2-0-pro-260215")
    doubao_code = model_provider_catalog.normalize_model(volcengine_ark, "doubao-seed-2-0-code-preview-260215")

    assert deepseek_v4["thinkingControl"]["requestStyle"] == "deepseek_thinking_disabled"
    assert qwen["thinkingControl"]["requestStyle"] == "dashscope_enable_thinking_false"
    assert glm["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert glm_52["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert mimo_25_pro["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert mimo_25["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert mimo_25_flash["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert not mimo_25_tts.get("thinkingControl")
    assert mimo_tokenplan_25_pro["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert doubao_seed_21["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert doubao_seed["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"
    assert doubao_code["thinkingControl"]["requestStyle"] == "openai_thinking_disabled"


def test_model_control_plane_surfaces_saved_no_think_state():
    config = model_control_plane.normalize_config(
        {
            "providers": {
                "deepseek": {
                    "provider": {
                        "name": "DeepSeek",
                        "base_url": "https://api.deepseek.com/v1",
                        "api_standard": "openai",
                    },
                    "models": {
                        "deepseek-v4-flash": {
                            "type": "TEXT",
                            "capabilities": {"chat": True, "reasoning": True},
                            "thinkingControl": {"disabled": True},
                        }
                    },
                }
            }
        }
    )
    rows = model_control_plane.list_models(config)
    row = next(item for item in rows if item["modelId"] == "deepseek-v4-flash")

    assert row["thinkingControl"]["supportsNoThink"] is True
    assert row["thinkingControl"]["disabled"] is True
    assert row["thinkingControl"]["requestStyle"] == "deepseek_thinking_disabled"


def test_minimax_model_list_probe_uses_site_specific_models_endpoint():
    international = model_provider_catalog.probe_provider("minimax", credential="")
    cn = model_provider_catalog.probe_provider("minimax-cn", credential="")

    assert international["ok"] is False
    assert international["reason"] == "credential_required"
    assert international["models"] == []
    assert international["resolvedModelsUrl"] == "https://api.minimax.io/v1/models"
    assert cn["ok"] is False
    assert cn["reason"] == "credential_required"
    assert cn["models"] == []
    assert cn["resolvedModelsUrl"] == "https://api.minimaxi.com/v1/models"


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


def test_custom_provider_persists_declared_capabilities_without_inventing_models(tmp_path):
    catalog_path = tmp_path / "provider_catalog.json"
    _write_catalog(catalog_path, [])
    custom_path = tmp_path / "custom.json"
    catalog = ModelProviderCatalog(path=catalog_path, custom_path=custom_path)

    provider = catalog.build_custom_provider(
        "Multimodal Gateway",
        "http://127.0.0.1:8317/v1",
        declared_capabilities=["text", "vision", "image", "video", "unknown", "image"],
    )
    saved = catalog.save_custom_provider(provider)
    loaded = catalog.get_provider(saved["id"])

    assert loaded is not None
    assert loaded["declaredCapabilities"] == ["text", "vision", "image", "video"]
    assert [item["mediaModality"] for item in loaded["capabilityEntries"]] == ["image", "video"]
    assert all(item["models"] == [] for item in loaded["capabilityEntries"])


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
        "id": "codex",
        "name": "Codex OAuth",
        "baseUrl": "https://chatgpt.com/backend-api",
        "apiStandard": "openai",
        "auth": {"type": "oauth_file", "path": "~/.codex/auth.json", "preset": "codex"},
        "models": [
            {
                "id": "gpt-5.5",
                "contextWindow": 1000000,
                "maxTokens": 100000,
                "capabilities": ["chat", "streaming"],
            }
        ],
    }

    monkeypatch.setattr(platform_routes.model_provider_catalog, "get_provider", lambda provider_id: provider if provider_id == "codex" else None)
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: saved_config.setdefault("value", config))

    result = asyncio.run(platform_routes.connect_model_provider({"providerId": "codex", "modelId": "gpt-5.5"}))

    model = result["config"]["providers"]["codex"]["models"]["gpt-5.5"]
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
    assert model["endpointBinding"]["providerModelId"] == "gpt-image-2"
    assert model["endpointBinding"]["provenance"] == {
        "source": "quick_connect",
        "confidence": "authoritative",
    }
    assert "temperature" not in model


def test_quick_connect_persists_the_visible_route_and_wire_model_binding(monkeypatch):
    provider = {
        "id": "openai_images",
        "name": "OpenAI-compatible Images",
        "baseUrl": "https://api.example.com/v1",
        "apiStandard": "openai_images",
        "providerKind": "media_generation",
        "mediaModality": "image",
        "auth": {"type": "api_key", "header": "Authorization", "scheme": "Bearer"},
        "models": [],
    }
    monkeypatch.setattr(platform_routes.model_provider_catalog, "get_provider", lambda provider_id: provider if provider_id == "openai_images" else None)
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: {"providers": {}})
    monkeypatch.setattr(platform_routes.model_control_plane, "save_config", lambda config: config)

    result = asyncio.run(
        platform_routes.connect_model_provider(
            {
                "providerId": "openai_images",
                "modelId": "images/generations/gpt-image-2",
                "apiKey": "sk-test",
                "modelType": "IMAGE",
                "endpointPath": "images/generations",
                "providerModelId": "gpt-image-2",
                "operationKind": "image.generate",
                "adapter": "openai_images",
            }
        )
    )

    binding = result["config"]["providers"]["openai_images"]["models"]["images/generations/gpt-image-2"]["endpointBinding"]
    assert binding["route"] == "images/generations/gpt-image-2"
    assert binding["endpointPath"] == "images/generations"
    assert binding["providerModelId"] == "gpt-image-2"
    assert binding["operationKind"] == "image.generate"
    assert binding["adapter"] == "openai_images"
    assert binding["provenance"] == {"source": "quick_connect", "confidence": "authoritative"}


def test_legacy_models_write_preserves_credential_omitted_by_public_read(monkeypatch):
    saved = {}
    current = {
        "providers": {
            "demo": {
                "provider": {"name": "Demo", "api_key": "sk-private"},
                "models": {},
            }
        }
    }
    monkeypatch.setattr(platform_routes.model_control_plane, "get_config", lambda: current)
    monkeypatch.setattr(
        platform_routes.model_control_plane,
        "save_config",
        lambda config: saved.setdefault("config", config),
    )

    result = asyncio.run(
        platform_routes.save_models_config(
            {
                "providers": {
                    "demo": {
                        "provider": {"name": "Demo", "credentialConfigured": True},
                        "models": {},
                    }
                }
            }
        )
    )

    assert saved["config"]["providers"]["demo"]["provider"]["api_key"] == "sk-private"
    assert "api_key" not in result["config"]["providers"]["demo"]["provider"]


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
    gemini = model_provider_catalog.get_provider("gemini-api")
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


def test_anthropic_sdk_uses_the_exact_user_configured_channel_base():
    versioned_channel_meta = {
        "api_key": "sk-test",
        "base_url": "https://provider.example.test/v1",
        "endpoint_binding": {
            "wireProtocol": "anthropic.messages",
            "requestUrlPreview": "https://provider.example.test/v1/messages",
        },
    }
    prefixed_channel_meta = {
        "api_key": "sk-test",
        "base_url": "https://provider.example.test/anthropic",
        "endpoint_binding": {
            "wireProtocol": "anthropic.messages",
            "requestUrlPreview": "https://provider.example.test/anthropic/v1/messages",
        },
    }

    assert llm_factory._build_anthropic_kwargs("claude-test", versioned_channel_meta)["base_url"] == (
        "https://provider.example.test/v1"
    )
    assert llm_factory._build_anthropic_kwargs("claude-test", prefixed_channel_meta)["base_url"] == (
        "https://provider.example.test/anthropic"
    )


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


def test_no_think_request_patch_for_openai_compatible_providers():
    cases = [
        ("deepseek", "deepseek-v4-flash", {"extra_body": {"thinking": {"type": "disabled"}}}),
        ("minimax-cn", "MiniMax-M3", {"extra_body": {"thinking": {"type": "disabled"}}}),
        ("xiaomi-mimo", "mimo-v2.5-pro", {"extra_body": {"thinking": {"type": "disabled"}}}),
        ("xiaomi-mimo-tokenplan", "mimo-v2.5", {"extra_body": {"thinking": {"type": "disabled"}}}),
        ("volcengine-ark", "doubao-seed-2-1-pro-260628", {"extra_body": {"thinking": {"type": "disabled"}}}),
        ("volcengine-coding", "doubao-seed-2.0-pro", {"extra_body": {"thinking": {"type": "disabled"}}}),
        ("zhipu", "glm-5", {"extra_body": {"thinking": {"type": "disabled"}}}),
        ("dashscope", "qwen-max", {"extra_body": {"enable_thinking": False}}),
        ("openai", "gpt-5.5", {"reasoning": {"effort": "none"}}),
        ("openrouter", "deepseek/deepseek-r1", {"reasoning": {"effort": "none"}}),
    ]

    for provider_id, model_id, expected_patch in cases:
        thinking_control = resolve_thinking_control_for_metadata(
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "model_record": {
                    "capabilities": {"chat": True, "reasoning": True},
                    "thinkingControl": {"disabled": True},
                },
            }
        )
        kwargs = llm_factory._build_openai_kwargs(
            model_id,
            {
                "api_key": "sk-test",
                "provider_id": provider_id,
                "model_id": model_id,
                "thinking_control": thinking_control,
            },
        )
        for key, value in expected_patch.items():
            assert kwargs[key] == value


def test_no_think_request_patch_merges_existing_extra_body():
    thinking_control = resolve_thinking_control_for_metadata(
        {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-pro",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "thinkingControl": {"disabled": True},
            },
        }
    )

    kwargs = llm_factory._build_openai_kwargs(
        "deepseek-v4-pro",
        {
            "api_key": "sk-test",
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-pro",
            "thinking_control": thinking_control,
        },
        extra_body={"caching": {"prefix": True}},
    )

    assert kwargs["extra_body"]["caching"] == {"prefix": True}
    assert kwargs["extra_body"]["thinking"] == {"type": "disabled"}


def test_deepseek_v4_projects_protocol_specific_effort_contracts():
    flash_responses = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-flash",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "endpointBinding": {"wireProtocol": "openai.responses"},
            },
        }
    )
    pro_chat = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-pro",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "endpointBinding": {"wireProtocol": "openai.chat_completions"},
            },
        }
    )
    pro_anthropic = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-pro",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "endpointBinding": {"wireProtocol": "anthropic.messages"},
            },
        }
    )

    responses_kwargs = llm_factory._build_openai_kwargs(
        "deepseek-v4-flash",
        {
            "api_key": "sk-test",
            "wire_protocol": "openai.responses",
            "reasoning_effort_control": flash_responses,
            "request_reasoning_effort": "low",
        },
    )
    chat_kwargs = llm_factory._build_openai_kwargs(
        "deepseek-v4-pro",
        {
            "api_key": "sk-test",
            "wire_protocol": "openai.chat_completions",
            "reasoning_effort_control": pro_chat,
            "request_reasoning_effort": "low",
        },
    )
    anthropic_kwargs = llm_factory._build_anthropic_kwargs(
        "deepseek-v4-pro",
        {
            "api_key": "sk-test",
            "reasoning_effort_control": pro_anthropic,
            "request_reasoning_effort": "xhigh",
        },
    )

    assert responses_kwargs["reasoning"] == {"effort": "low"}
    assert responses_kwargs["use_responses_api"] is True
    assert "extra_body" not in responses_kwargs
    assert chat_kwargs["reasoning_effort"] == "high"
    assert chat_kwargs["extra_body"]["thinking"] == {"type": "enabled"}
    assert anthropic_kwargs["effort"] == "max"
    assert anthropic_kwargs["thinking"] == {"type": "enabled"}


def test_single_no_think_control_reaches_anthropic_gemini_and_responses_requests():
    opus_thinking = resolve_thinking_control_for_metadata(
        {
            "provider_id": "anthropic",
            "model_id": "claude-opus-5",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "endpointBinding": {"wireProtocol": "anthropic.messages"},
                "thinkingControl": {"disabled": True},
            },
        }
    )
    gemini_thinking = resolve_thinking_control_for_metadata(
        {
            "provider_id": "gemini-api",
            "model_id": "gemini-2.5-flash",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "endpointBinding": {"wireProtocol": "gemini.generate_content"},
                "thinkingControl": {"disabled": True},
            },
        }
    )
    deepseek_thinking = resolve_thinking_control_for_metadata(
        {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-flash",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "endpointBinding": {"wireProtocol": "openai.responses"},
                "thinkingControl": {"disabled": True},
            },
        }
    )

    opus_kwargs = llm_factory._build_anthropic_kwargs(
        "claude-opus-5",
        {"api_key": "sk-test", "thinking_control": opus_thinking},
    )
    gemini_kwargs = llm_factory._build_gemini_kwargs(
        "gemini-2.5-flash",
        {"api_key": "sk-test", "thinking_control": gemini_thinking},
    )
    deepseek_kwargs = llm_factory._build_openai_kwargs(
        "deepseek-v4-flash",
        {
            "api_key": "sk-test",
            "wire_protocol": "openai.responses",
            "thinking_control": deepseek_thinking,
        },
    )

    assert opus_kwargs["thinking"] == {"type": "disabled"}
    assert gemini_kwargs["thinking_budget"] == 0
    assert deepseek_kwargs["reasoning"] == {"effort": "none"}
    assert deepseek_kwargs["use_responses_api"] is True


def test_reasoning_effort_control_is_limited_to_openai_reasoning_families():
    openai_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "openai",
            "model_id": "gpt-5.5",
            "model_record": {
                "capabilityClass": "chat_reasoning",
                "capabilities": {"chat": True, "reasoning": True},
            },
        }
    )
    openrouter_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "openrouter",
            "model_id": "deepseek/deepseek-r1",
            "model_record": {
                "capabilityClass": "chat_reasoning",
                "capabilities": {"chat": True, "reasoning": True},
            },
        }
    )
    minimax_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "minimax-cn",
            "model_id": "MiniMax-M3",
            "model_record": {
                "capabilityClass": "vision_multimodal",
                "capabilities": {"chat": True, "reasoning": True},
            },
        }
    )
    embedding_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "openai",
            "model_id": "text-embedding-3-large",
            "model_record": {
                "capabilityClass": "embedding",
                "capabilities": {"embedding": True, "reasoning": True},
            },
        }
    )

    assert openai_control["requestStyle"] == "openai_reasoning_effort"
    assert openai_control["levels"] == ["auto", "none", "low", "medium", "high", "xhigh"]
    assert openrouter_control["requestStyle"] == "openrouter_reasoning_effort"
    assert minimax_control == {}
    assert embedding_control == {}


def test_reasoning_effort_control_exposes_only_native_discrete_levels():
    legacy_anthropic_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "anthropic",
            "model_id": "claude-sonnet-4-5",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "reasoningSurface": {"requestStyle": "anthropic_thinking"},
            },
        }
    )
    anthropic_effort_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "anthropic",
            "model_id": "claude-opus-4-8",
            "model_record": {
                "capabilities": ["chat", "reasoning"],
                "reasoningSurface": {"requestStyle": "none"},
            },
        }
    )
    gemini_level_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "gemini-api",
            "model_id": "gemini-3.1-pro-preview",
            "model_record": {"capabilities": {"chat": True, "reasoning": True}},
        }
    )
    gemini_budget_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "gemini-api",
            "model_id": "gemini-2.5-pro",
            "model_record": {"capabilities": {"chat": True, "reasoning": True}},
        }
    )
    custom_gemini_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "my-gemini-key",
            "api_standard": "gemini",
            "model_id": "gemini-3-flash-preview",
            "capabilities": ["chat", "reasoning"],
        }
    )

    assert legacy_anthropic_control == {}
    assert anthropic_effort_control["requestStyle"] == "anthropic_effort"
    assert anthropic_effort_control["levels"] == ["auto", "low", "medium", "high", "xhigh", "max"]
    assert gemini_level_control["requestStyle"] == "gemini_thinking_level"
    assert gemini_budget_control == {}
    assert custom_gemini_control["requestStyle"] == "gemini_thinking_level"


def test_reasoning_effort_request_patch_uses_normalized_levels():
    control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "openai",
            "model_id": "gpt-5.5",
            "model_record": {
                "capabilityClass": "chat_reasoning",
                "capabilities": {"chat": True, "reasoning": True},
            },
        }
    )

    assert reasoning_effort_request_patch(control, "auto") == {}
    assert reasoning_effort_request_patch(control, "medium") == {"reasoning": {"effort": "medium"}}
    assert reasoning_effort_request_patch(control, "max") == {}


def test_reasoning_effort_request_patch_maps_vendor_specific_official_controls():
    anthropic_budget_control = {
        "supportsReasoningEffort": True,
        "requestStyle": "anthropic_thinking_budget",
    }
    anthropic_effort_control = {
        "supportsReasoningEffort": True,
        "requestStyle": "anthropic_effort",
    }
    gemini_level_control = {
        "supportsReasoningEffort": True,
        "requestStyle": "gemini_thinking_level",
    }
    gemini_budget_control = {
        "supportsReasoningEffort": True,
        "requestStyle": "gemini_thinking_budget",
    }

    assert reasoning_effort_request_patch(anthropic_budget_control, "low") == {
        "thinking": {"type": "enabled", "budget_tokens": 4096}
    }
    assert reasoning_effort_request_patch(anthropic_effort_control, "medium") == {
        "thinking": {"type": "adaptive"},
        "effort": "medium",
    }
    assert reasoning_effort_request_patch(gemini_level_control, "high") == {"thinking_level": "high"}
    assert reasoning_effort_request_patch(gemini_budget_control, "medium") == {"thinking_budget": 4096}


def test_openai_kwargs_apply_supervisor_reasoning_effort_after_no_think():
    thinking_control = resolve_thinking_control_for_metadata(
        {
            "provider_id": "openai",
            "model_id": "gpt-5.5",
            "model_record": {
                "capabilityClass": "chat_reasoning",
                "capabilities": {"chat": True, "reasoning": True},
                "thinkingControl": {"disabled": True},
            },
        }
    )
    reasoning_effort_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "openai",
            "model_id": "gpt-5.5",
            "model_record": {
                "capabilityClass": "chat_reasoning",
                "capabilities": {"chat": True, "reasoning": True},
            },
        }
    )

    kwargs = llm_factory._build_openai_kwargs(
        "gpt-5.5",
        {
            "api_key": "sk-test",
            "provider_id": "openai",
            "model_id": "gpt-5.5",
            "thinking_control": thinking_control,
            "reasoning_effort_control": reasoning_effort_control,
            "request_reasoning_effort": "high",
        },
    )

    assert kwargs["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in kwargs


def test_anthropic_kwargs_apply_reasoning_effort_controls_with_budget_headroom():
    budget_control = {
        "supportsReasoningEffort": True,
        "levels": ["auto", "low", "medium", "high"],
        "requestStyle": "anthropic_thinking_budget",
        "budgetByLevel": {"low": 4096, "medium": 8192, "high": 16000},
    }
    effort_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "anthropic",
            "model_id": "claude-opus-4-8",
            "model_record": {"capabilities": {"chat": True, "reasoning": True}},
        }
    )

    budget_kwargs = llm_factory._build_anthropic_kwargs(
        "claude-sonnet-4-5",
        {
            "api_key": "sk-test",
            "provider_id": "anthropic",
            "model_id": "claude-sonnet-4-5",
            "reasoning_effort_control": budget_control,
            "request_reasoning_effort": "low",
        },
        max_tokens=512,
    )
    effort_kwargs = llm_factory._build_anthropic_kwargs(
        "claude-opus-4-8",
        {
            "api_key": "sk-test",
            "provider_id": "anthropic",
            "model_id": "claude-opus-4-8",
            "reasoning_effort_control": effort_control,
            "request_reasoning_effort": "high",
        },
    )

    assert budget_kwargs["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert budget_kwargs["max_tokens_to_sample"] == 5120
    assert effort_kwargs["thinking"] == {"type": "adaptive"}
    assert effort_kwargs["effort"] == "high"


def test_gemini_kwargs_apply_reasoning_effort_controls_without_instantiating_optional_client():
    level_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "gemini-api",
            "model_id": "gemini-3.1-pro-preview",
            "model_record": {"capabilities": {"chat": True, "reasoning": True}},
        }
    )
    budget_control = {
        "supportsReasoningEffort": True,
        "levels": ["auto", "low", "medium", "high"],
        "requestStyle": "gemini_thinking_budget",
        "budgetByLevel": {"low": 1024, "medium": 8192, "high": 32768},
    }

    level_kwargs = llm_factory._build_gemini_kwargs(
        "gemini-3.1-pro-preview",
        {
            "api_key": "gemini-test",
            "provider_id": "gemini-api",
            "model_id": "gemini-3.1-pro-preview",
            "reasoning_effort_control": level_control,
            "request_reasoning_effort": "medium",
        },
    )
    budget_kwargs = llm_factory._build_gemini_kwargs(
        "gemini-2.5-pro",
        {
            "api_key": "gemini-test",
            "provider_id": "gemini-api",
            "model_id": "gemini-2.5-pro",
            "reasoning_effort_control": budget_control,
            "request_reasoning_effort": "high",
        },
    )

    assert level_kwargs["thinking_level"] == "medium"
    assert budget_kwargs["thinking_budget"] == 32768


def test_gemini_verified_summary_contract_enables_include_thoughts_without_opening_hidden_models():
    provider = model_provider_catalog.get_provider("gemini-api")
    assert provider
    verified_model = model_provider_catalog.normalize_model(provider, "gemini-3.1-pro-preview")
    verified_surface = verified_model["reasoningSurface"]

    assert verified_surface["mode"] == "reasoning_summary"
    assert verified_surface["trust"] == "adapter_verified"
    assert verified_surface["requestStyle"] == "gemini_include_thoughts"
    assert verified_surface["responseFields"] == ["content[type=thinking]"]
    assert verified_surface["displayKind"] == "summary"

    verified_kwargs = llm_factory._build_gemini_kwargs(
        "gemini-3.1-pro-preview",
        {
            "api_key": "dry-run-key",
            "reasoning_surface": verified_surface,
        },
    )
    hidden_kwargs = llm_factory._build_gemini_kwargs(
        "gemini-unverified-future-model",
        {
            "api_key": "dry-run-key",
            "reasoning_surface": {
                "mode": "hidden",
                "trust": "unknown",
                "requestStyle": "none",
                "responseFields": [],
                "displayKind": "hidden",
            },
        },
    )

    assert verified_kwargs["include_thoughts"] is True
    assert "include_thoughts" not in hidden_kwargs


@pytest.mark.skipif(ChatGoogleGenerativeAI is None, reason="langchain-google-genai is not installed")
def test_gemini_reasoning_effort_kwargs_are_accepted_by_langchain_google_genai():
    provider = model_provider_catalog.get_provider("gemini-api")
    assert provider
    level_surface = model_provider_catalog.normalize_model(provider, "gemini-3.1-pro-preview")["reasoningSurface"]
    budget_surface = model_provider_catalog.normalize_model(provider, "gemini-2.5-pro")["reasoningSurface"]
    level_control = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "gemini-api",
            "api_standard": "gemini",
            "model_id": "gemini-3.1-pro-preview",
            "model_record": {"capabilities": {"chat": True, "reasoning": True}},
        }
    )
    budget_control = {
        "supportsReasoningEffort": True,
        "levels": ["auto", "low", "medium", "high"],
        "requestStyle": "gemini_thinking_budget",
        "budgetByLevel": {"low": 1024, "medium": 8192, "high": 32768},
    }

    level_kwargs = llm_factory._build_gemini_kwargs(
        "gemini-3.1-pro-preview",
        {
            "api_key": "dry-run-key",
            "provider_id": "gemini-api",
            "api_standard": "gemini",
            "model_id": "gemini-3.1-pro-preview",
            "reasoning_effort_control": level_control,
            "request_reasoning_effort": "medium",
            "reasoning_surface": level_surface,
        },
        max_tokens=1234,
        timeout=12,
    )
    budget_kwargs = llm_factory._build_gemini_kwargs(
        "gemini-2.5-pro",
        {
            "api_key": "dry-run-key",
            "provider_id": "gemini-api",
            "api_standard": "gemini",
            "model_id": "gemini-2.5-pro",
            "reasoning_effort_control": budget_control,
            "request_reasoning_effort": "high",
            "reasoning_surface": budget_surface,
        },
        max_tokens=1234,
        timeout=12,
    )

    level_client = ChatGoogleGenerativeAI(**level_kwargs)
    budget_client = ChatGoogleGenerativeAI(**budget_kwargs)

    assert level_client.thinking_level == "medium"
    assert level_client.thinking_budget is None
    assert level_client.include_thoughts is True
    assert level_client.max_output_tokens == 1234
    assert level_client.timeout == 12
    assert budget_client.thinking_level is None
    assert budget_client.thinking_budget == 32768
    assert budget_client.include_thoughts is True


def test_reasoning_profiles_expose_exact_current_provider_levels_and_disable_rules():
    gpt_56 = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "openai",
            "model_id": "gpt-5.6-sol",
            "model_record": {"capabilities": {"chat": True, "reasoning": True}},
        }
    )
    gemini_35 = resolve_reasoning_effort_control_for_metadata(
        {
            "provider_id": "custom-cpm",
            "model_id": "gemini-3.5-flash-low",
            "model_record": {
                "capabilities": {"chat": True, "reasoning": True},
                "endpointBinding": {"wireProtocol": "gemini.generate_content"},
            },
        }
    )
    fable_5_thinking = resolve_thinking_control_for_metadata(
        {
            "provider_id": "anthropic",
            "model_id": "claude-fable-5",
            "model_record": {"capabilities": {"chat": True, "reasoning": True}},
        }
    )

    assert gpt_56["levels"] == ["auto", "none", "low", "medium", "high", "xhigh", "max"]
    assert gpt_56["profileId"] == "openai-gpt-5.6"
    assert gemini_35["requestStyle"] == "gemini_thinking_level"
    assert gemini_35["levels"] == ["auto", "minimal", "low", "medium", "high"]
    assert gemini_35["mandatory"] is True
    assert fable_5_thinking == {}
