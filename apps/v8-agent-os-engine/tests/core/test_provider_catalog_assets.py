from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from core.model_provider_catalog import model_provider_catalog


REPO_ROOT = Path(__file__).resolve().parents[4]
PROVIDER_CATALOG = REPO_ROOT / "apps" / "v8-agent-os-engine" / "core" / "model_catalog" / "provider_catalog.json"
MEDIA_MATRIX = REPO_ROOT / "apps" / "v8-agent-os-engine" / "runtimes" / "creative_media" / "assets" / "media_provider_format_matrix.json"
TRUSTED_NETWORK_CATALOG = REPO_ROOT / "apps" / "v8-agent-os-engine" / "erc" / "assets" / "trusted_network_catalog.json"
RESEARCH_SOURCE_CATALOG = REPO_ROOT / "apps" / "v8-agent-os-engine" / "runtimes" / "research" / "assets" / "source_quality_catalog.json"
ASSET_ROOT = REPO_ROOT / "apps" / "v8-agent-os-admin" / "public"
MANIFEST = ASSET_ROOT / "model-assets" / "manifest.json"


def test_llm_provider_catalog_contains_new_model_providers():
    payload = json.loads(PROVIDER_CATALOG.read_text(encoding="utf-8"))
    provider_ids = {item["id"] for item in payload["providers"]}

    assert {
        "perplexity",
        "fireworks",
        "cerebras",
        "nvidia-nim",
        "cohere",
        "ai21",
        "agnes",
        "baidu-qianfan",
        "stepfun",
        "baichuan",
    }.issubset(provider_ids)

    loaded_ids = {item["id"] for item in model_provider_catalog.list_providers()}
    assert "perplexity" in loaded_ids
    assert "fireworks" in loaded_ids

    gemini = next(item for item in payload["providers"] if item["id"] == "gemini-api")
    gemini_model_ids = {item["id"] for item in gemini["models"]}
    assert {"gemini-3.5-flash", "gemini-3.1-flash-lite"}.issubset(gemini_model_ids)
    assert "gemini-3.1-flash-preview" not in gemini_model_ids
    assert "gemini-3.1-flash-lite-preview" not in gemini_model_ids

    agnes = next(item for item in payload["providers"] if item["id"] == "agnes")
    assert agnes["baseUrl"] == "https://apihub.agnes-ai.com/v1"
    assert agnes["probeStrategy"] == "openai_models"
    assert agnes["probeModelAllowlist"] == [
        "agnes-2.0-flash",
        "agnes-image-2.1-flash",
        "agnes-video-v2.0",
    ]
    assert agnes["capabilityEntries"] == [
        {"type": "image"},
        {"type": "video"},
    ]
    for provider_id in [
        "openai",
        "gemini-api",
        "dashscope",
        "zhipu",
        "xiaomi-mimo",
        "volcengine-ark",
        "tencent-hunyuan",
        "minimax",
        "minimax-cn",
    ]:
        provider = next(item for item in payload["providers"] if item["id"] == provider_id)
        assert provider.get("capabilityEntries"), provider_id
        assert all("sourceProviderId" not in item for item in provider["capabilityEntries"])

    loaded_agnes = model_provider_catalog.get_provider("agnes")
    loaded_capabilities = {
        (item["type"], item["sourceProviderId"]): item
        for item in loaded_agnes["capabilityEntries"]
    }
    assert loaded_capabilities[("image", "agnes_image")]["models"]
    assert loaded_capabilities[("video", "agnes_video")]["models"]
    assert loaded_capabilities[("image", "agnes_image")]["catalogVisibility"] == "internal_capability"
    loaded_dashscope = model_provider_catalog.get_provider("dashscope")
    loaded_dashscope_sources = {
        item["sourceProviderId"]
        for item in loaded_dashscope["capabilityEntries"]
        if item.get("sourceProviderId")
    }
    assert {"aliyun_bailian_image", "aliyun_bailian_video", "aliyun_bailian_cosyvoice", "aliyun_bailian_3d"}.issubset(
        loaded_dashscope_sources
    )
    assert "openai_images" not in loaded_dashscope_sources


def test_media_matrix_contains_requested_generation_providers():
    payload = json.loads(MEDIA_MATRIX.read_text(encoding="utf-8"))
    modality_ids = {
        modality: {item["id"] for item in items}
        for modality, items in payload["modalities"].items()
    }

    assert {"black_forest_labs_image", "ideogram_image", "leonardo_image"}.issubset(modality_ids["image"])
    assert "agnes_image" in modality_ids["image"]
    assert {
        "vidu_video",
        "pika_video",
        "haiper_video",
        "heygen_video",
        "synthesia_video",
        "d_id_video",
        "tavus_video",
        "hedra_video",
        "shotstack_video",
        "creatomate_video",
    }.issubset(modality_ids["video"])
    assert "agnes_video" in modality_ids["video"]
    assert {
        "fish_audio_tts",
        "cartesia_tts",
        "playht_tts",
        "azure_speech_tts",
        "google_cloud_tts",
        "google_gemini_tts",
        "google_gemini_live_audio",
        "amazon_polly_tts",
    }.issubset(modality_ids["voice"])
    assert {"minimax_music", "udio_music"}.issubset(modality_ids["music"])
    assert {"meshy_3d", "hitem3d", "hyper3d_rodin", "csm_3d", "3d_ai_studio"}.issubset(modality_ids["model3d"])


def test_new_local_provider_assets_match_manifest_hashes():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    providers = manifest["providers"]
    for provider_id in [
        "perplexity",
        "fireworks",
        "cerebras",
        "nvidia-nim",
        "ai21",
        "baidu-qianfan",
        "stepfun",
        "baichuan",
        "ideogram",
        "vidu",
        "pika",
        "cartesia",
        "udio",
    ]:
        entry = providers[provider_id]
        asset = ASSET_ROOT / str(entry["asset"]).lstrip("/")
        assert asset.exists(), provider_id
        digest = hashlib.sha256(asset.read_bytes()).hexdigest().upper()
        assert digest == entry["sha256"]


def test_new_provider_hosts_are_in_trusted_network_catalog():
    payload = json.loads(TRUSTED_NETWORK_CATALOG.read_text(encoding="utf-8"))
    entry_ids = {item["id"] for item in payload["entries"]}

    assert {
        "fireworks",
        "cerebras",
        "nvidia-nim",
        "ai21",
        "stepfun",
        "baichuan",
        "ideogram",
        "leonardo",
        "pika",
        "haiper",
        "hedra",
        "cartesia",
        "playht",
        "azure-speech",
        "amazon-polly",
        "udio",
        "agnes",
    }.issubset(entry_ids)


def test_agnes_online_probe_keeps_current_chat_image_and_video_models(monkeypatch):
    response = SimpleNamespace(
        ok=True,
        status_code=200,
        text="",
        json=lambda: {
            "data": [
                {"id": "agnes-2.0-flash"},
                {"id": "agnes-image-2.1-flash"},
                {"id": "agnes-video-v2.0"},
                {"id": "agnes-image-2.0-flash"},
            ]
        },
    )
    monkeypatch.setattr("core.model_provider_catalog.requests.get", lambda *args, **kwargs: response)

    result = model_provider_catalog.probe_provider("agnes", credential="sk-test")

    assert result["ok"] is True
    assert [item["id"] for item in result["models"]] == [
        "agnes-2.0-flash",
        "agnes-image-2.1-flash",
        "agnes-video-v2.0",
    ]
    assert [item["type"] for item in result["models"]] == ["MULTIMODAL", "IMAGE", "VIDEO"]


def test_research_source_quality_catalog_contains_authority_and_video_popularity_sources():
    payload = json.loads(RESEARCH_SOURCE_CATALOG.read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in payload["entries"]}

    assert "official_vendor_docs" in entries
    assert "video_platform_popularity" in entries
    assert "platform.openai.com" in entries["official_vendor_docs"]["hosts"]
    assert "youtube.com" in entries["video_platform_popularity"]["hosts"]
    assert {"views", "likes", "rank", "trending"} & set(entries["video_platform_popularity"]["popularitySignals"])
