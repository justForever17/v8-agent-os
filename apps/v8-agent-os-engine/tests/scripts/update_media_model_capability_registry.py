from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List


ENGINE_ROOT = Path(__file__).resolve().parents[2]
V8OS_ROOT = ENGINE_ROOT.parents[1]
MATRIX_PATH = ENGINE_ROOT / "runtimes" / "creative_media" / "assets" / "media_provider_format_matrix.json"
OVERRIDES_PATH = ENGINE_ROOT / "runtimes" / "creative_media" / "assets" / "media_model_capability_overrides.json"
MULTIMEDIA_DOC_PATH = V8OS_ROOT / "docs" / "creative-runtime" / "多媒体.md"
OUTPUT_PATH = ENGINE_ROOT / "core" / "model_catalog" / "media_model_capability_registry.json"
REPORT_PATH = ENGINE_ROOT / "core" / "model_catalog" / "media_model_capability_registry_unresolved_report.json"
ADMIN_PUBLIC_PATH = V8OS_ROOT / "apps" / "v8-agent-os-admin" / "public"


LOGO_FALLBACKS = {
    "openai": "/model-assets/lobe/openai.svg",
    "google": "/model-assets/lobe/google-color.svg",
    "veo": "/model-assets/lobe/google-color.svg",
    "v8": "/model-assets/providers/v8-audio.svg",
    "zhipu": "/model-assets/lobe/zhipu-color.svg",
    "bigmodel": "/model-assets/lobe/zhipu-color.svg",
    "volcengine": "/model-assets/lobe/volcengine-color.svg",
    "doubao": "/model-assets/lobe/doubao-color.svg",
    "seedance": "/model-assets/lobe/doubao-color.svg",
    "seedream": "/model-assets/lobe/doubao-color.svg",
    "seed3d": "/model-assets/lobe/doubao-color.svg",
    "aliyun": "/model-assets/lobe/alibabacloud-color.svg",
    "alibaba": "/model-assets/lobe/alibabacloud-color.svg",
    "qwen": "/model-assets/lobe/qwen-color.svg",
    "wan": "/model-assets/lobe/qwen-color.svg",
    "stability": "/model-assets/lobe/stability-color.svg",
    "stable": "/model-assets/lobe/stability-color.svg",
    "fal": "/model-assets/lobe/fal-color.svg",
    "replicate": "/model-assets/lobe/replicate.svg",
    "runway": "/model-assets/lobe/runway.svg",
    "luma": "/model-assets/lobe/luma-color.svg",
    "minimax": "/model-assets/lobe/minimax-color.svg",
    "kling": "/model-assets/lobe/kling-color.svg",
    "happyhorse": "/model-assets/providers/happyhorse.svg",
    "xiaomi": "/model-assets/providers/xiaomi-mimo.svg",
    "mimo": "/model-assets/providers/xiaomi-mimo.svg",
    "eleven": "/model-assets/lobe/elevenlabs.svg",
    "mureka": "/model-assets/providers/mureka.svg",
    "suno": "/model-assets/lobe/suno.svg",
    "hunyuan": "/model-assets/lobe/hunyuan-color.svg",
    "tencent": "/model-assets/lobe/tencentcloud-color.svg",
    "tripo": "/model-assets/lobe/tripo-color.svg",
    "hyper3d": "/model-assets/providers/hyper3d.svg",
    "hitem3d": "/model-assets/providers/hitem3d.svg",
    "fish": "/model-assets/providers/fish-audio.svg",
    "black-forest": "/model-assets/providers/black-forest-labs.svg",
    "flux": "/model-assets/providers/black-forest-labs.svg",
    "meshy": "/model-assets/providers/meshy.svg",
    "deemos": "/model-assets/providers/hyper3d.svg",
    "csm": "/model-assets/providers/csm.svg",
    "3d-ai-studio": "/model-assets/providers/3d-ai-studio.ico",
}


DOC_MODEL_MAP = {
    "seedance-2.0": ("volcengine_seedance", "doubao-seedance-2-0"),
    "veo-3.1": ("google_veo", "veo-3.1-generate-preview"),
    "sora-2": ("openai_sora_video", "sora-2"),
    "gen-4.5": ("runway_video", "gen4_turbo"),
    "nano-banana-pro-gemini-3-pro-image": ("google_gemini_image", "nano-banana-pro"),
    "qwen-image-2.0-pro": ("aliyun_bailian_image", "qwen-image-2.0-pro"),
    "seedream-5.0": ("volcengine_seedream", "doubao-seedream-5-0-lite"),
    "flux.2": ("black_forest_labs_image", "flux.2"),
    "eleven-v3": ("elevenlabs_tts", "eleven-v3"),
    "fish-speech-s2": ("fish_audio_tts", "fish-speech-s2"),
    "minimax-speech-2.6": ("minimax_tts", "minimax-speech-2.6"),
    "f5-tts": ("fal_tts", "f5-tts"),
    "elevenlabs-music": ("elevenlabs_music", "elevenlabs-music"),
    "minimax-music-2.5": ("minimax_music", "minimax-music-2.5"),
    "stable-audio-2.5": ("stability_music", "stable-audio-2.5"),
    "lyria-realtime": ("google_lyria_music", "lyria-realtime"),
    "3d-hunyuan-3d-3.0-3.1-3.5": ("tencent_hunyuan_3d", "hunyuan-3d"),
    "hunyuan-3d-3.0-3.1-3.5": ("tencent_hunyuan_3d", "hunyuan-3d"),
    "seed3d-doubao-seed3d-1-0-250928": ("volcengine_3d_generation", "doubao-seed3d-1-0-250928"),
    "meshy-6": ("meshy_3d", "meshy-6"),
    "tripo-3d-v3.0-v3.1": ("tripo3d_placeholder", "tripo-3d-v3"),
    "stable-fast-3d-sf3d": ("stability_3d", "stable-fast-3d"),
    "rodin-gen-2": ("hyper3d_rodin", "rodin-gen-2"),
    "motionshop-gen3d": ("aliyun_bailian_3d", "motionshop-gen3d"),
    "sparc3d-ultra3d": ("hitem3d", "sparc3d-ultra3d"),
    "trellis.2-microsoft-hunyuan-3d": ("3d_ai_studio", "trellis.2"),
    "trellis-2-microsoft-hunyuan-3d": ("3d_ai_studio", "trellis.2"),
    "csm-3d-generation": ("csm_3d", "csm-3d-generation"),
}


def slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"\([^)]*\)", lambda m: " " + m.group(0).strip("()") + " ", text)
    text = re.sub(r"[^a-z0-9.]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def strip_markdown(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*", "", text)
    return text.strip()


def read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def infer_logo(*values: Any) -> str:
    haystack = " ".join(str(value or "") for value in values).lower().replace("_", "-")
    for key, asset in LOGO_FALLBACKS.items():
        if key in haystack:
            return asset
    return ""


def public_asset_exists(asset: str) -> bool:
    if not asset:
        return False
    return (ADMIN_PUBLIC_PATH / asset.lstrip("/")).exists()


def normalize_modality(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"audio", "speech", "tts"}:
        return "voice"
    if raw in {"3d", "model-3d", "model_3d"}:
        return "model3d"
    return raw


def operation_kinds(entry: Dict[str, Any], modality: str) -> List[str]:
    explicit = entry.get("operationKinds") or entry.get("operations") or []
    if isinstance(explicit, list) and explicit:
        return [str(item) for item in explicit if str(item).strip()]
    normalized = normalize_modality(modality)
    if normalized == "image":
        return ["image.generate"]
    if normalized == "video":
        return ["video.text_to_video"]
    if normalized == "voice":
        return ["voice.tts"]
    if normalized == "music":
        return ["music.brief"]
    if normalized == "model3d":
        return ["model3d.generate"]
    return []


def default_io(modality: str, profile: Dict[str, Any]) -> tuple[List[str], List[str]]:
    if profile.get("inputModalities") or profile.get("outputStreams"):
        return list(profile.get("inputModalities") or []), list(profile.get("outputStreams") or [])
    if modality == "image":
        return ["text", "image"], ["image"]
    if modality == "video":
        return ["text", "image", "video"], ["video"]
    if modality == "voice":
        return ["text", "audio"], ["audio"]
    if modality == "music":
        return ["text", "audio"], ["audio"]
    if modality == "model3d":
        return ["text", "image"], ["model3d"]
    return ["text"], [modality]


def operation_profile(overrides: List[Dict[str, Any]], provider_id: str, model_id: str, operation_kind: str) -> Dict[str, Any]:
    for item in overrides:
        if str(item.get("providerId") or "").strip() != provider_id:
            continue
        model_ids = item.get("modelIds") if isinstance(item.get("modelIds"), list) else [item.get("modelId")]
        if model_id not in {str(value or "").strip() for value in model_ids}:
            continue
        operations = item.get("operationKinds")
        if isinstance(operations, list) and operation_kind not in {str(value).strip() for value in operations}:
            continue
        profile = dict(item.get("capabilityProfile") or {})
        if item.get("sourceUrl"):
            profile.setdefault("sourceUrl", item.get("sourceUrl"))
        if item.get("confidence"):
            profile.setdefault("confidence", item.get("confidence"))
        return profile
    return {}


def overridden_operation_kinds(overrides: List[Dict[str, Any]], provider_id: str, model_id: str) -> List[str]:
    for item in overrides:
        if str(item.get("providerId") or "").strip() != provider_id:
            continue
        model_ids = item.get("modelIds") if isinstance(item.get("modelIds"), list) else [item.get("modelId")]
        if model_id not in {str(value or "").strip() for value in model_ids}:
            continue
        operations = [str(value or "").strip() for value in as_list(item.get("operationKinds")) if str(value or "").strip()]
        if operations:
            return operations
    return []


def merge_source_refs(existing: List[Dict[str, Any]], new_refs: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = {(ref.get("source"), ref.get("url")) for ref in existing}
    result = list(existing)
    for ref in new_refs:
        key = (ref.get("source"), ref.get("url"))
        if key not in seen:
            result.append(ref)
            seen.add(key)
    return result


def parse_markdown_doc() -> List[Dict[str, Any]]:
    if not MULTIMEDIA_DOC_PATH.exists():
        return []
    text = MULTIMEDIA_DOC_PATH.read_text(encoding="utf-8")
    current_modality = ""
    entries: List[Dict[str, Any]] = []
    for line in text.splitlines():
        heading = re.match(r"###\s+\S+[、.]\s*(.+)", line)
        if heading:
            title = heading.group(1)
            if "视频" in title:
                current_modality = "video"
            elif "图像" in title:
                current_modality = "image"
            elif "音频" in title or "语音" in title:
                current_modality = "voice"
            elif "音乐" in title:
                current_modality = "music"
            elif "3D" in title or "3d" in title:
                current_modality = "model3d"
            continue
        if not current_modality or not line.startswith("|") or "---" in line or ":--" in line or "供应商" in line:
            continue
        cells = [strip_markdown(re.sub(r"<br\\s*/?>", " ", item)).strip() for item in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        provider = re.sub(r"\*\*", "", cells[0]).strip()
        model_name = re.sub(r"\*\*", "", cells[1]).strip()
        if current_modality == "model3d" and len(cells) >= 3:
            model_name = re.sub(r"\*\*", "", cells[2]).strip()
        if not provider or not model_name:
            continue
        normalized = slug(model_name)
        provider_id, model_id = DOC_MODEL_MAP.get(normalized, (slug(provider).replace("-", "_"), normalized))
        entries.append(
            {
                "providerId": provider_id,
                "providerName": provider,
                "modelId": model_id,
                "displayName": model_name,
                "modality": current_modality,
                "rawInput": cells[2] if len(cells) > 2 else "",
                "rawOutput": cells[3] if len(cells) > 3 else "",
                "sourceRefs": [{"source": "multimedia_doc", "url": "docs/creative-runtime/多媒体.md"}],
            }
        )
    return entries


def build_registry() -> Dict[str, Any]:
    matrix = read_json(MATRIX_PATH)
    overrides_payload = read_json(OVERRIDES_PATH) if OVERRIDES_PATH.exists() else {"capabilityProfiles": []}
    overrides = [dict(item) for item in as_list(overrides_payload.get("capabilityProfiles")) if isinstance(item, dict)]
    providers: Dict[str, Dict[str, Any]] = {}
    models: Dict[tuple[str, str], Dict[str, Any]] = {}

    for modality, entries in (matrix.get("modalities") or {}).items():
        for entry in as_list(entries):
            if not isinstance(entry, dict):
                continue
            provider_id = str(entry.get("id") or "").strip()
            if not provider_id:
                continue
            logo_asset = str(entry.get("logoAsset") or "").strip() or infer_logo(provider_id, entry.get("displayName"))
            provider = providers.setdefault(
                provider_id,
                {
                    "providerId": provider_id,
                    "displayName": entry.get("displayName") or provider_id,
                    "modalities": [],
                    "logoAsset": logo_asset,
                    "sourceRefs": [],
                    "confidence": entry.get("confidence") or "provider_docs",
                    "missingFields": [],
                },
            )
            provider["modalities"] = sorted(set(provider.get("modalities") or []) | {normalize_modality(modality)})
            if logo_asset and not provider.get("logoAsset"):
                provider["logoAsset"] = logo_asset
            provider["sourceRefs"] = merge_source_refs(
                provider.get("sourceRefs") or [],
                [{"source": "media_provider_format_matrix", "url": str(entry.get("sourceUrl") or MATRIX_PATH.as_posix())}],
            )
            if not provider.get("logoAsset"):
                provider["missingFields"].append("providerLogoAsset")
            model_logo_assets = entry.get("modelLogoAssets") if isinstance(entry.get("modelLogoAssets"), dict) else {}
            for model_id in [str(item).strip() for item in as_list(entry.get("modelIds")) if str(item).strip()]:
                operations = overridden_operation_kinds(overrides, provider_id, model_id) or operation_kinds(entry, str(modality))
                operation_capability_profiles = {
                    operation: operation_profile(overrides, provider_id, model_id, operation)
                    for operation in operations
                }
                operation_capability_profiles = {key: value for key, value in operation_capability_profiles.items() if value}
                profile = next(iter(operation_capability_profiles.values()), {})
                input_modalities, output_streams = default_io(normalize_modality(modality), profile)
                model_logo = str(model_logo_assets.get(model_id) or "").strip() or infer_logo(model_id, provider_id, entry.get("displayName")) or provider.get("logoAsset") or ""
                missing = []
                if not operations:
                    missing.append("operationKinds")
                if not model_logo:
                    missing.append("modelLogoAsset")
                if not entry.get("sourceUrl") and not profile.get("sourceUrl"):
                    missing.append("sourceRefs")
                models[(provider_id, model_id)] = {
                    "canonicalModelId": model_id,
                    "displayName": model_id,
                    "creator": entry.get("displayName") or provider_id,
                    "providerIds": [provider_id],
                    "aliases": sorted({model_id}),
                    "modality": normalize_modality(modality),
                    "operationKinds": operations,
                    "inputModalities": input_modalities,
                    "outputStreams": output_streams,
                    "referenceInputs": profile.get("referenceInputs") or {},
                    "nativeAudio": bool(profile.get("nativeAudio")),
                    "audioModes": profile.get("audioModes") or [],
                    "audioPreservationPolicy": profile.get("audioPreservationPolicy") or "",
                    "resolution": profile.get("resolution") or {},
                    "duration": profile.get("duration") or {},
                    "formats": profile.get("formats") or {},
                    "logoAsset": model_logo,
                    "operationCapabilityProfiles": operation_capability_profiles,
                    "sourceRefs": merge_source_refs(
                        [],
                        [
                            {"source": "media_provider_format_matrix", "url": str(entry.get("sourceUrl") or MATRIX_PATH.as_posix())},
                            *(
                                [{"source": "media_model_capability_overrides", "url": str(profile.get("sourceUrl"))}]
                                if profile.get("sourceUrl")
                                else []
                            ),
                        ],
                    ),
                    "confidence": profile.get("confidence") or entry.get("confidence") or "provider_docs",
                    "missingFields": sorted(set(missing)),
                }

    for doc in parse_markdown_doc():
        provider_id = doc["providerId"]
        model_id = doc["modelId"]
        provider_logo = infer_logo(provider_id, doc.get("providerName"))
        provider = providers.setdefault(
            provider_id,
            {
                "providerId": provider_id,
                "displayName": doc.get("providerName") or provider_id,
                "modalities": [],
                "logoAsset": provider_logo,
                "sourceRefs": [],
                "confidence": "community_or_inferred",
                "missingFields": [],
                "catalogOnly": True,
            },
        )
        provider["modalities"] = sorted(set(provider.get("modalities") or []) | {doc["modality"]})
        provider["sourceRefs"] = merge_source_refs(provider.get("sourceRefs") or [], doc.get("sourceRefs") or [])
        if not provider.get("logoAsset"):
            provider["logoAsset"] = provider_logo
        key = (provider_id, model_id)
        if key in models:
            item = models[key]
            item["displayName"] = item.get("displayName") if item.get("displayName") != model_id else doc.get("displayName") or model_id
            item["aliases"] = sorted(set(item.get("aliases") or []) | {doc.get("displayName") or model_id, slug(doc.get("displayName"))})
            item["sourceRefs"] = merge_source_refs(item.get("sourceRefs") or [], doc.get("sourceRefs") or [])
            item.setdefault("docNotes", {})
            item["docNotes"].update({"input": doc.get("rawInput") or "", "output": doc.get("rawOutput") or ""})
            continue
        operations = operation_kinds({}, doc["modality"])
        model_logo = infer_logo(model_id, provider_id, doc.get("providerName")) or provider.get("logoAsset") or ""
        models[key] = {
            "canonicalModelId": model_id,
            "displayName": doc.get("displayName") or model_id,
            "creator": doc.get("providerName") or provider_id,
            "providerIds": [provider_id],
            "aliases": sorted({model_id, doc.get("displayName") or model_id, slug(doc.get("displayName"))}),
            "modality": doc["modality"],
            "operationKinds": operations,
            "inputModalities": default_io(doc["modality"], {})[0],
            "outputStreams": default_io(doc["modality"], {})[1],
            "referenceInputs": {},
            "nativeAudio": False,
            "audioModes": [],
            "audioPreservationPolicy": "",
            "resolution": {},
            "duration": {},
            "formats": {},
            "logoAsset": model_logo,
            "operationCapabilityProfiles": {},
            "sourceRefs": doc.get("sourceRefs") or [],
            "confidence": "community_or_inferred",
            "missingFields": sorted({"providerMatrixEntry", *([] if model_logo else ["modelLogoAsset"])}),
            "catalogOnly": True,
            "docNotes": {"input": doc.get("rawInput") or "", "output": doc.get("rawOutput") or ""},
        }

    for provider in providers.values():
        provider["missingFields"] = sorted(set(provider.get("missingFields") or []))

    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "notes": [
            "Media model facts are exact providerId + modelId records. Do not infer advanced capability from provider or family names.",
            "Provider/API wire format remains in media_provider_format_matrix.json.",
            "Prices are intentionally omitted.",
        ],
        "providers": sorted(providers.values(), key=lambda item: item["providerId"]),
        "models": sorted(models.values(), key=lambda item: (item["modality"], item["providerIds"][0], item["canonicalModelId"])),
    }


def build_report(payload: Dict[str, Any]) -> Dict[str, Any]:
    missing_provider_logos = [item["providerId"] for item in payload["providers"] if not item.get("logoAsset")]
    missing_model_logos = [f"{item['providerIds'][0]}::{item['canonicalModelId']}" for item in payload["models"] if not item.get("logoAsset")]
    missing_asset_files = []
    for item in [*payload["providers"], *payload["models"]]:
        asset = item.get("logoAsset")
        if asset and not public_asset_exists(asset):
            missing_asset_files.append({"id": item.get("providerId") or item.get("canonicalModelId"), "logoAsset": asset})
    conflicts = []
    by_model = defaultdict(list)
    for item in payload["models"]:
        by_model[item["canonicalModelId"]].append(item["providerIds"][0])
    for model_id, provider_ids in by_model.items():
        if len(set(provider_ids)) > 1:
            conflicts.append({"modelId": model_id, "providerIds": sorted(set(provider_ids))})
    return {
        "generatedAt": payload["generatedAt"],
        "stats": {
            "providers": len(payload["providers"]),
            "models": len(payload["models"]),
            "byModality": dict(Counter(item["modality"] for item in payload["models"])),
        },
        "missingProviderLogos": missing_provider_logos,
        "missingModelLogos": missing_model_logos,
        "missingAssetFiles": missing_asset_files,
        "sameModelIdAcrossProviders": conflicts,
        "catalogOnlyModels": [
            f"{item['providerIds'][0]}::{item['canonicalModelId']}"
            for item in payload["models"]
            if item.get("catalogOnly")
        ],
        "entriesWithMissingFields": [
            {"model": f"{item['providerIds'][0]}::{item['canonicalModelId']}", "missingFields": item.get("missingFields") or []}
            for item in payload["models"]
            if item.get("missingFields")
        ],
    }


def main() -> None:
    payload = build_registry()
    report = build_report(payload)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "providers": len(payload["providers"]), "models": len(payload["models"]), "report": str(REPORT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
