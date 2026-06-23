from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote, urlparse

import httpx

from core.artifact_store import artifact_store
from core.audio.tts_provider import TTSManager
from core.database import db
from core.model_control_plane import model_control_plane
from core.storage import storage
from core.tools.tool_execution_envelope import ToolExecutionEnvelope
from erc.runtime_registry import runtime_registry

from .catalog import (
    capability_profile_for_model,
    load_audio_music_recipe_library,
    load_media_model_capability_registry,
    load_media_model_capability_overrides,
    load_provider_matrix,
    load_resolution_presets,
    load_video_recipe_library,
    load_visual_recipe_library,
    normalize_provider_status,
    resolve_image_size,
    resolve_video_resolution,
)
from .recipe import creative_recipe_compiler
from .recipe import prepare_provider_prompt_policy


JOB_STORE_FILE = "creative_media/jobs.json"
WORK_ORDER_STORE_FILE = "creative_media/work_orders.json"
EDIT_PLAN_STORE_FILE = "creative_media/edit_plans.json"
RENDER_JOB_STORE_FILE = "creative_media/render_jobs.json"
MODEL_PREFERENCES_STORE_FILE = "creative_media/model_preferences.json"
QUALITY_JOB_STORE_FILE = "creative_media/quality_jobs.json"
COST_LEDGER_STORE_FILE = "creative_media/cost_ledger.json"
SAFETY_EVENTS_STORE_FILE = "creative_media/safety_events.json"
SUPPORTED_MODALITIES = {"image", "video", "voice", "music", "model3d"}
MODEL_PREFERENCE_CONFIG_SOURCES = {"model_control_plane", "runtime_builtin"}
# music/model3d intentionally stay schema/catalog-only in P2; adapters can be added without changing the job envelope.
EXECUTABLE_MODALITIES = {"image", "video", "voice"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MEDIA_MODEL_TYPE_TO_MODALITY = {
    "IMAGE": "image",
    "VIDEO": "video",
    "VOICE": "voice",
    "AUDIO": "voice",
    "MUSIC": "music",
    "MODEL3D": "model3d",
    "WORKFLOW": "model3d",
}
DEFAULT_OPERATION_KINDS = {
    "image": ["image.generate"],
    "video": ["video.text_to_video"],
    "voice": ["voice.tts"],
    "music": ["music.brief"],
    "model3d": ["model3d.generate"],
}
EXECUTABLE_OPERATION_KINDS = {
    "image.generate",
    "image.edit",
    "video.text_to_video",
    "video.image_to_video",
    "video.first_last_frame",
    "video.reference_to_video",
    "video.video_edit",
    "video.style_repaint",
    "video.lipsync",
    "video.avatar",
    "video.action_transfer",
    "video.replacement",
    "voice.tts",
}
DASHSCOPE_VIDEO_OPERATION_KINDS = {
    "video.text_to_video",
    "video.image_to_video",
    "video.first_last_frame",
    "video.reference_to_video",
    "video.video_edit",
    "video.style_repaint",
    "video.lipsync",
    "video.avatar",
    "video.action_transfer",
    "video.replacement",
}
DASHSCOPE_BUILTIN_MODELS = {
    "image.generate": ["qwen-image-2.0-pro", "qwen-image-2.0", "wan2.7-image-pro"],
    "image.edit": ["qwen-image-2.0-pro", "qwen-image-2.0", "wan2.7-image-pro"],
    "video.text_to_video": ["wan2.7-t2v", "wan2.7-t2v-2026-04-25"],
    "video.image_to_video": ["wan2.7-i2v", "wan2.7-i2v-2026-04-25"],
    "video.first_last_frame": ["wan2.7-i2v", "wan2.7-i2v-2026-04-25"],
    "video.reference_to_video": ["wan2.7-r2v"],
    "video.video_edit": ["wan2.7-videoedit"],
    "video.style_repaint": ["wan2.7-videoedit"],
    "video.lipsync": ["wan2.2-s2v", "videoretalk"],
    "video.avatar": ["wan2.2-s2v"],
    "video.action_transfer": ["wan2.2-animate-move"],
    "video.replacement": ["wan2.2-animate-mix", "wan2.7-videoedit"],
}
POLICY_REJECT_MARKERS = (
    "IPInfringementSuspect",
    "DataInspectionFailed",
    "policy",
    "copyright",
    "infringement",
    "safety",
    "content security",
)
PREFERRED_IMAGE_MODEL_IDS = ("gpt-image-2", "gpt-image-1")
PREFERRED_SEEDANCE2_MODEL_IDS = (
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0",
    "doubao-seedance-2-0-fast",
    "doubao-seed-2-0-lite-260428",
)
LOWER_TIER_EXECUTABLE_VIDEO_MODELS = {"doubao-seed-2-0-lite-260428"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_filename(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip("-")
    return normalized[:80] or fallback


def _jsonable_request(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload or {})
    for key in ("apiKey", "api_key", "authorization", "Authorization"):
        safe.pop(key, None)
    return safe


def _list_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = [item.strip() for item in re.split(r"[,，;\n]", value)]
    else:
        raw = [str(item or "").strip() for item in list(value or [])]
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _exception_summary(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _candidate_id(*, modality: str, operation_kind: str, provider_id: str, model_id: str, adapter: str) -> str:
    raw = "|".join([modality, operation_kind, provider_id, model_id, adapter]).encode("utf-8")
    return f"cm_model_{hashlib.sha256(raw).hexdigest()[:16]}"


def _safe_priority(value: Any, default: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 999))


def _all_operation_kinds() -> list[str]:
    values: set[str] = set(EXECUTABLE_OPERATION_KINDS)
    for items in DEFAULT_OPERATION_KINDS.values():
        values.update(items)
    values.update(DASHSCOPE_BUILTIN_MODELS.keys())
    return sorted(values)


def _registry_operation_kinds_for_model(*, provider_id: str, model_id: str, modality: str) -> list[str]:
    registry = load_media_model_capability_registry()
    target_provider = str(provider_id or "").strip()
    target_model = str(model_id or "").strip()
    if not target_provider or not target_model:
        return []
    for item in list(registry.get("models") or []):
        if not isinstance(item, dict):
            continue
        provider_ids = {str(value or "").strip() for value in list(item.get("providerIds") or [])}
        aliases = {str(value or "").strip() for value in list(item.get("aliases") or [])}
        aliases.add(str(item.get("canonicalModelId") or "").strip())
        if target_provider not in provider_ids or target_model not in aliases:
            continue
        operations = [
            str(value or "").strip()
            for value in list(item.get("operationKinds") or [])
            if str(value or "").strip()
        ]
        return [
            operation
            for operation in operations
            if operation.startswith(f"{modality}.") or (modality == "voice" and operation.startswith("voice."))
        ]
    return []


def _modality_for_operation(operation_kind: str) -> str:
    prefix = str(operation_kind or "").split(".", 1)[0].strip().lower()
    return "voice" if prefix == "audio" else prefix


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _build_openai_image_payload(*, model: str, prompt: str, size: str, response_format: str = "b64_json") -> dict[str, Any]:
    return {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": response_format,
    }


def _build_agnes_image_payload(
    *,
    model: str,
    prompt: str,
    size: str,
    response_format: str = "url",
    image_urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    normalized_format = "b64_json" if response_format in {"base64", "b64_json"} else "url"
    payload: dict[str, Any] = {"model": model, "prompt": prompt, "size": size}
    if image_urls:
        payload["extra_body"] = {"image": image_urls, "response_format": normalized_format}
    elif normalized_format == "b64_json":
        payload["return_base64"] = True
    else:
        payload["extra_body"] = {"response_format": "url"}
    return payload


def _build_agnes_video_payload(
    *,
    model: str,
    prompt: str,
    operation_kind: str,
    image_urls: Optional[list[str]] = None,
    width: int = 1152,
    height: int = 768,
    num_frames: int = 121,
    frame_rate: int = 24,
    seed: int | None = None,
    negative_prompt: str = "",
    num_inference_steps: int | None = None,
) -> dict[str, Any]:
    safe_frames = max(1, min(int(num_frames), 441))
    if (safe_frames - 1) % 8:
        safe_frames = max(1, min(441, ((safe_frames - 1) // 8) * 8 + 1))
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "width": max(1, int(width)),
        "height": max(1, int(height)),
        "num_frames": safe_frames,
        "frame_rate": max(1, min(int(frame_rate), 60)),
    }
    references = [str(item).strip() for item in list(image_urls or []) if str(item).strip()]
    if operation_kind == "video.image_to_video" and references:
        payload["image"] = references[0]
    elif references:
        payload["extra_body"] = {"image": references}
        if operation_kind == "video.first_last_frame":
            payload["extra_body"]["mode"] = "keyframes"
    if seed is not None:
        payload["seed"] = int(seed)
    if negative_prompt:
        payload["negative_prompt"] = negative_prompt
    if num_inference_steps is not None:
        payload["num_inference_steps"] = int(num_inference_steps)
    return payload


def _build_volcengine_image_payload(
    *,
    model: str,
    prompt: str,
    size: str,
    response_format: str = "url",
    seed: int = -1,
    image_urls: Optional[list[str]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "seed": seed,
        "response_format": response_format,
    }
    if image_urls:
        payload["image"] = image_urls
    return payload


def _build_volcengine_video_payload(
    *,
    model: str,
    prompt: str,
    ratio: str,
    resolution: str,
    duration: int,
    seed: int = -1,
    image_urls: Optional[list[str]] = None,
    generate_audio: bool = True,
    watermark: bool = False,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if prompt:
        content.append({"type": "text", "text": prompt})
    for index, url in enumerate(image_urls or []):
        role = "first_frame" if index == 0 else "last_frame" if index == 1 else "reference_image"
        content.append({"type": "image_url", "image_url": {"url": url}, "role": role})
    return {
        "model": model,
        "content": content,
        "ratio": ratio,
        "resolution": resolution,
        "duration": duration,
        "seed": seed,
        "watermark": watermark,
        "generate_audio": generate_audio,
    }


class CreativeMediaRuntime:
    kind = "creative_media"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "CreativeMediaRuntime",
            "summary": "负责图片、视频、语音、音乐与未来 3D 媒体 job 的 provider 适配、轮询和 artifact 交付。",
            "responsibilities": [
                "归一化媒体 provider 请求格式。",
                "持久化媒体 job 状态。",
                "把生成结果登记为 runtime artifact。",
            ],
            "routingKeywords": ["image", "video", "voice", "music", "creative_media", "artifact"],
            "acceptedInputs": ["media job request"],
            "producedOutputs": ["image artifact", "video artifact", "audio artifact", "media job status"],
            "supportsResume": True,
            "supportsRepair": False,
            "visibility": "internal",
            "promptHints": [
                "用法入口：通过 runtime_broker(mode='route', need={'kind':'creative_media', ...}) 创建 episode；输入 brief、modality、assetRole、referenceAssetIds、qualityTier/costLimit，不要让 Supervisor 直接拼 provider raw request。",
                "执行流程：Creative Media 负责 recipe/work order 编译、provider 选择、job 轮询、artifact 登记、质量/安全摘要；明确 Seedance、Sora、图生视频、参考视频、首尾帧或参考音频/音乐时可作为主 runtime。",
                "支撑能力与边界：Engineering、Research、Admin 等 runtime 只需要背景图、图标、封面、角色图、配音、音乐或关键帧素材时，Creative Media 作为 CreativeAssetRequest 素材支持 runtime；科普、课程、讲解、产品介绍等可编辑代码视频由 Engineering 主导。",
                "回流要求：typed handoff 必须给 artifactRefs/jobIds/modelUsed/costEstimate/safetyStatus/limitations/detailRef；provider raw response、轮询日志和内部 recipe JSON 只进 Runtime Surface。",
                "科普、课程、讲解、产品介绍这类需要可编辑时间线的视频，默认由 Engineering 的代码视频链路主导，Creative Media 只提供素材和媒体 provider 子能力。",
            ],
            "metadata": {
                "p1": True,
                "p2": True,
                "p3": True,
                "supervisorToolSurface": False,
                "managedToolGroups": ["creative_media.core"],
                "managedToolNames": [
                    "creative_media_catalog",
                    "creative_media_resolutions",
                    "creative_media_create_job",
                    "creative_media_get_job",
                    "creative_media_list_jobs",
                    "creative_media_job_artifacts",
                    "creative_media_compile_recipe",
                    "creative_media_get_recipe",
                    "creative_media_list_recipes",
                    "creative_media_register_asset",
                    "creative_media_list_assets",
                    "creative_media_create_character_bible",
                    "creative_media_get_character_bible",
                    "creative_media_list_character_bibles",
                    "creative_media_register_keyframe",
                    "creative_media_get_keyframe",
                    "creative_media_list_keyframes",
                    "creative_media_create_edit_plan",
                    "creative_media_get_edit_plan",
                    "creative_media_list_edit_plans",
                    "creative_media_render_edit_plan",
                    "creative_media_get_render",
                    "creative_media_list_renders",
                    "creative_media_create_quality_job",
                    "creative_media_list_quality_jobs",
                    "creative_media_get_quality_job",
                    "creative_media_retry_job",
                    "creative_media_cost_ledger",
                    "creative_media_safety_events",
                    "creative_media_compile_work_order",
                    "creative_media_list_work_orders",
                ],
            },
        }

    def catalog(self) -> dict[str, Any]:
        matrix = load_provider_matrix()
        return {
            **matrix,
            "mediaModelCapabilityRegistry": load_media_model_capability_registry(),
            "modelCapabilityOverrides": load_media_model_capability_overrides(),
            "runtimeAdapters": [
                {"id": "openai_images", "modalities": ["image"], "executable": True},
                {"id": "agnes_images", "modalities": ["image"], "executable": True},
                {"id": "agnes_video", "modalities": ["video"], "executable": True},
                {"id": "volcengine_ark", "modalities": ["image", "video"], "executable": True},
                {"id": "dashscope", "modalities": ["image", "video"], "executable": True},
                {"id": "v8_audio_tts", "modalities": ["voice"], "executable": True},
                {"id": "catalog_only", "modalities": ["music", "model3d"], "executable": False},
            ],
        }

    def resolutions(self) -> dict[str, Any]:
        return load_resolution_presets()

    def recipe_libraries(self) -> dict[str, Any]:
        return {
            "visual": load_visual_recipe_library(),
            "video": load_video_recipe_library(),
            "audioMusic": load_audio_music_recipe_library(),
        }

    def _adapter_for_model_candidate(
        self,
        *,
        modality: str,
        provider_id: str,
        provider_meta: dict[str, Any],
        model_data: dict[str, Any],
    ) -> str:
        haystack = " ".join(
            [
                provider_id,
                str(provider_meta.get("name") or ""),
                str(provider_meta.get("apiStandard") or provider_meta.get("api_standard") or ""),
                str(model_data.get("adapter") or ""),
            ]
        ).lower()
        if modality in {"image", "video"} and any(token in haystack for token in ("volc", "seedream", "seedance", "jimeng")):
            return "volcengine_ark"
        if modality in {"image", "video"} and any(token in haystack for token in ("dashscope", "bailian", "aliyun", "alibaba", "qwen", "wan2", "wanx")):
            return "dashscope"
        if modality == "image" and "agnes" in haystack:
            return "agnes_images"
        if modality == "video" and "agnes" in haystack:
            return "agnes_video"
        if modality == "image":
            return "openai_images"
        if modality == "voice":
            return "v8_audio_tts" if provider_id == "v8_audio_tts" else str(model_data.get("adapter") or "v8_audio_tts")
        return str(model_data.get("adapter") or "catalog_only")

    def _operation_kinds_for_candidate(
        self,
        *,
        modality: str,
        provider_id: str,
        adapter: str,
        provider_meta: dict[str, Any],
        model_data: dict[str, Any],
    ) -> list[str]:
        registry_operations = _registry_operation_kinds_for_model(
            provider_id=provider_id,
            model_id=str(model_data.get("id") or ""),
            modality=modality,
        )
        if registry_operations:
            return registry_operations
        media_limits = dict(model_data.get("mediaLimits") or {})
        explicit = _list_of_strings(
            model_data.get("operationKinds")
            or model_data.get("operations")
            or media_limits.get("operationKinds")
            or provider_meta.get("operationKinds")
        )
        if explicit:
            return [item for item in explicit if item.startswith(f"{modality}.") or item.startswith("voice.")]
        haystack = " ".join([provider_id, str(provider_meta.get("name") or ""), str(model_data.get("id") or "")]).lower()
        if modality == "image":
            if adapter == "dashscope":
                return ["image.generate", "image.edit"]
            if adapter == "openai_images":
                return ["image.generate", "image.edit"]
        if modality == "video":
            if any(token in haystack for token in ("lipsync", "lip-sync", "retalk", "对口型")):
                return ["video.lipsync"]
            if any(token in haystack for token in ("action", "motion", "动作迁移")):
                return ["video.action_transfer"]
            if any(token in haystack for token in ("avatar", "digital-human", "数字人")):
                return ["video.avatar"]
            if any(token in haystack for token in ("replace", "replacement", "换人")):
                return ["video.replacement"]
            if adapter == "volcengine_ark":
                return ["video.text_to_video", "video.image_to_video", "video.first_last_frame"]
            if adapter == "dashscope":
                return ["video.text_to_video", "video.image_to_video"]
        return list(DEFAULT_OPERATION_KINDS.get(modality, []))

    def _operation_kind_for_request(self, modality: str, request: dict[str, Any]) -> str:
        explicit = str(
            request.get("operationKind")
            or request.get("operation_kind")
            or request.get("operation")
            or request.get("taskType")
            or request.get("task_type")
            or ""
        ).strip()
        if explicit:
            return explicit.replace("-", "_")
        if modality == "image":
            if request.get("editIntent") or request.get("edit_intent") or request.get("mask") or request.get("maskUrl"):
                return "image.edit"
            return "image.generate"
        if modality == "video":
            if request.get("actionVideoUrl") or request.get("action_video_url") or request.get("motionReferenceUrl") or request.get("motion_reference_url"):
                return "video.action_transfer"
            if request.get("audioUrl") or request.get("audio_url") or request.get("voiceAssetId") or request.get("voice_asset_id"):
                return "video.lipsync"
            if request.get("referenceVideoUrl") or request.get("reference_video_url") or request.get("referenceImageUrl") or request.get("reference_image_url"):
                return "video.reference_to_video"
            if request.get("editIntent") or request.get("edit_intent") or request.get("videoUrl") or request.get("video_url"):
                return "video.video_edit"
            image_urls = request.get("imageUrls") or request.get("image_urls") or []
            if request.get("firstFrame") or request.get("lastFrame") or (isinstance(image_urls, list) and len(image_urls) >= 2):
                return "video.first_last_frame"
            if image_urls:
                return "video.image_to_video"
            return "video.text_to_video"
        if modality == "voice":
            return "voice.tts"
        if modality == "music":
            return "music.brief"
        if modality == "model3d":
            return "model3d.generate"
        return f"{modality}.generate"

    def _is_operation_executable(self, *, adapter: str, operation_kind: str) -> bool:
        if operation_kind == "image.generate" and adapter in {"openai_images", "agnes_images", "volcengine_ark", "dashscope"}:
            return True
        if operation_kind == "image.edit" and adapter in {"openai_images", "agnes_images", "dashscope"}:
            return True
        if operation_kind in {"video.text_to_video", "video.image_to_video", "video.first_last_frame"} and adapter == "volcengine_ark":
            return True
        if operation_kind in DASHSCOPE_VIDEO_OPERATION_KINDS and adapter == "dashscope":
            return True
        if operation_kind in {"video.text_to_video", "video.image_to_video", "video.first_last_frame", "video.reference_to_video"} and adapter == "agnes_video":
            return True
        if operation_kind == "voice.tts" and adapter == "v8_audio_tts":
            return True
        return False

    def _is_brief_only_operation(self, *, adapter: str, operation_kind: str) -> bool:
        return operation_kind == "music.brief" and adapter == "catalog_only"

    def list_model_candidates(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        config = model_control_plane.get_config()
        providers = dict(config.get("providers") or {})
        for provider_id, provider_data in providers.items():
            provider_meta = dict((provider_data or {}).get("provider") or {})
            provider_name = str(provider_meta.get("name") or provider_id).strip() or provider_id
            provider_logo_asset = str(provider_meta.get("logoAsset") or provider_meta.get("logo_asset") or provider_meta.get("icon") or "").strip()
            for model_id, model_data_raw in dict((provider_data or {}).get("models") or {}).items():
                model_data = dict(model_data_raw or {})
                media_limits = dict(model_data.get("mediaLimits") or {})
                model_type = str(model_data.get("type") or "").strip().upper()
                modality = MEDIA_MODEL_TYPE_TO_MODALITY.get(model_type)
                capabilities = dict(model_data.get("capabilities") or {})
                if not modality:
                    if capabilities.get("image"):
                        modality = "image"
                    elif capabilities.get("video"):
                        modality = "video"
                    elif capabilities.get("voice") or capabilities.get("audio"):
                        modality = "voice"
                    elif capabilities.get("music"):
                        modality = "music"
                if modality not in SUPPORTED_MODALITIES:
                    continue
                adapter = self._adapter_for_model_candidate(
                    modality=modality,
                    provider_id=str(provider_id),
                    provider_meta=provider_meta,
                    model_data=model_data,
                )
                operation_capability_profiles = dict(
                    media_limits.get("operationCapabilityProfiles")
                    or model_data.get("operationCapabilityProfiles")
                    or {}
                )
                model_id_str = str(model_id)
                for operation_kind in self._operation_kinds_for_candidate(
                    modality=modality,
                    provider_id=str(provider_id),
                    adapter=adapter,
                    provider_meta=provider_meta,
                    model_data={**model_data, "id": model_id_str},
                ):
                    capability_profile = dict(
                        operation_capability_profiles.get(operation_kind)
                        or capability_profile_for_model(
                            provider_id=str(provider_id),
                            model_id=model_id_str,
                            operation_kind=operation_kind,
                        )
                        or media_limits.get("capabilityProfile")
                        or model_data.get("capabilityProfile")
                        or {}
                    )
                    candidates.append(
                        {
                            "candidateId": _candidate_id(
                                modality=modality,
                                operation_kind=operation_kind,
                                provider_id=str(provider_id),
                                model_id=model_id_str,
                                adapter=adapter,
                            ),
                            "modality": modality,
                            "operationKind": operation_kind,
                            "providerId": str(provider_id),
                            "providerName": provider_name,
                            "modelId": model_id_str,
                            "modelRef": f"{provider_id}::{model_id_str}",
                            "providerLogoAsset": provider_logo_asset,
                            "modelLogoAsset": str(model_data.get("logoAsset") or model_data.get("logo_asset") or "").strip(),
                            "adapter": adapter,
                            "capabilityProfile": capability_profile,
                            "nativeAudio": bool(capability_profile.get("nativeAudio")),
                            "source": "model_control_plane",
                            "available": self._is_operation_executable(adapter=adapter, operation_kind=operation_kind),
                            "briefOnly": self._is_brief_only_operation(adapter=adapter, operation_kind=operation_kind),
                        }
                    )

        volc = self._volc_credentials()
        if volc.get("imageModel"):
            candidates.append(
                {
                    "candidateId": _candidate_id(
                        modality="image",
                        operation_kind="image.generate",
                        provider_id="volcengine_seedream",
                        model_id=volc["imageModel"],
                        adapter="volcengine_ark",
                    ),
                    "modality": "image",
                    "operationKind": "image.generate",
                    "providerId": "volcengine_seedream",
                    "providerName": "Volcengine Seedream",
                    "modelId": volc["imageModel"],
                    "modelRef": f"volcengine_seedream::{volc['imageModel']}",
                    "adapter": "volcengine_ark",
                    "source": "mcp_or_env",
                    "available": bool(volc.get("apiKey")),
                }
            )
        if volc.get("videoModel"):
            env_video_operations = _registry_operation_kinds_for_model(
                provider_id="volcengine_seedance",
                model_id=volc["videoModel"],
                modality="video",
            ) or ["video.text_to_video", "video.image_to_video", "video.first_last_frame"]
            for operation_kind in env_video_operations:
                capability_profile = capability_profile_for_model(
                    provider_id="volcengine_seedance",
                    model_id=volc["videoModel"],
                    operation_kind=operation_kind,
                )
                candidates.append(
                    {
                        "candidateId": _candidate_id(
                            modality="video",
                            operation_kind=operation_kind,
                            provider_id="volcengine_seedance",
                            model_id=volc["videoModel"],
                            adapter="volcengine_ark",
                        ),
                        "modality": "video",
                        "operationKind": operation_kind,
                        "providerId": "volcengine_seedance",
                        "providerName": "Volcengine Seedance",
                        "modelId": volc["videoModel"],
                        "modelRef": f"volcengine_seedance::{volc['videoModel']}",
                        "adapter": "volcengine_ark",
                        "capabilityProfile": capability_profile,
                        "nativeAudio": bool(capability_profile.get("nativeAudio")),
                        "source": "mcp_or_env",
                        "available": bool(volc.get("apiKey")),
                    }
                )
        dashscope = self._dashscope_credentials()
        for operation_kind, model_ids in DASHSCOPE_BUILTIN_MODELS.items():
            modality = operation_kind.split(".", 1)[0]
            for model_id in model_ids:
                candidates.append(
                    {
                        "candidateId": _candidate_id(
                            modality=modality,
                            operation_kind=operation_kind,
                            provider_id="aliyun_bailian_dashscope",
                            model_id=model_id,
                            adapter="dashscope",
                        ),
                        "modality": modality,
                        "operationKind": operation_kind,
                        "providerId": "aliyun_bailian_dashscope",
                        "providerName": "Alibaba Cloud Bailian / DashScope",
                        "modelId": model_id,
                        "modelRef": f"aliyun_bailian_dashscope::{model_id}",
                        "adapter": "dashscope",
                        "source": "env_builtin",
                        "available": bool(dashscope.get("apiKey")),
                        "liveReady": bool(dashscope.get("apiKey")),
                        "inputAssetTypes": self._input_asset_types_for_operation(operation_kind),
                        "capabilityProfile": {
                            "nativeAudio": operation_kind in {"video.lipsync", "video.avatar"},
                            "audioModes": ["input_audio_synchronization"] if operation_kind in {"video.lipsync", "video.avatar"} else [],
                            "audioPreservationPolicy": "preserve_native_audio_by_default" if operation_kind in {"video.lipsync", "video.avatar"} else "silent_or_external_audio",
                            "outputStreams": ["video", "audio"] if operation_kind in {"video.lipsync", "video.avatar"} else ["video"],
                        },
                        "nativeAudio": operation_kind in {"video.lipsync", "video.avatar"},
                    }
                )
        candidates.append(
            {
                "candidateId": _candidate_id(
                    modality="voice",
                    operation_kind="voice.tts",
                    provider_id="v8_audio_tts",
                    model_id="active-tts-provider",
                    adapter="v8_audio_tts",
                ),
                "modality": "voice",
                "operationKind": "voice.tts",
                "providerId": "v8_audio_tts",
                "providerName": "V8 Audio TTS",
                "modelId": "active-tts-provider",
                "modelRef": "v8_audio_tts::active-tts-provider",
                "adapter": "v8_audio_tts",
                "source": "runtime_builtin",
                "available": True,
            }
        )

        by_id: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            candidate_id = str(candidate["candidateId"])
            existing = by_id.get(candidate_id)
            if existing is None or self._candidate_source_rank(candidate) < self._candidate_source_rank(existing):
                by_id[candidate_id] = candidate
        result = list(by_id.values())
        result.sort(key=lambda item: (str(item.get("modality") or ""), str(item.get("providerName") or ""), str(item.get("modelId") or "")))
        return result

    def _is_configured_model_candidate(self, candidate: dict[str, Any]) -> bool:
        return str(candidate.get("source") or "") in MODEL_PREFERENCE_CONFIG_SOURCES

    def _candidate_source_rank(self, candidate: dict[str, Any]) -> int:
        source = str(candidate.get("source") or "")
        if source == "model_control_plane":
            return 0
        if source == "runtime_builtin":
            return 1
        if source in {"mcp_or_env", "env_builtin"}:
            return 2
        if source == "catalog_only":
            return 3
        return 4

    def _default_enabled_for_candidate(self, candidate: dict[str, Any], *, has_stored_preferences: bool) -> bool:
        if has_stored_preferences:
            return False
        return self._is_configured_model_candidate(candidate) and bool(candidate.get("available", True))

    def _stored_model_selection_refs(self, item: dict[str, Any]) -> list[str]:
        refs = item.get("modelRefs")
        if refs is None:
            refs = item.get("model_refs")
        if refs is None:
            refs = item.get("fallbackModelRefs")
        if refs is None:
            refs = item.get("modelRef")
        return _list_of_strings(refs)

    def _build_operation_rows(
        self,
        *,
        candidates: list[dict[str, Any]],
        connected_options: list[dict[str, Any]],
        stored_selections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        operation_kinds = set(_all_operation_kinds())
        operation_kinds.update(str(item.get("operationKind") or "") for item in candidates if item.get("operationKind"))
        operation_kinds.update(str(item.get("operationKind") or "") for item in stored_selections if item.get("operationKind"))
        selection_by_operation: dict[str, dict[str, Any]] = {}
        for item in stored_selections:
            operation_kind = str(item.get("operationKind") or "").strip()
            if operation_kind:
                selection_by_operation[operation_kind] = item

        rows: list[dict[str, Any]] = []
        for operation_kind in sorted(operation_kinds):
            modality = _modality_for_operation(operation_kind)
            options = [
                item
                for item in connected_options
                if str(item.get("operationKind") or "") == operation_kind
            ]
            valid_option_refs = {
                str(item.get("modelRef") or "").strip()
                for item in options
                if str(item.get("modelRef") or "").strip()
            }
            enabled_candidates = [
                item
                for item in candidates
                if str(item.get("operationKind") or "") == operation_kind and bool(item.get("enabled", False))
            ]
            enabled_candidates.sort(key=lambda item: (_safe_priority(item.get("priority"), 999), str(item.get("modelRef") or "")))
            stored_selection = selection_by_operation.get(operation_kind) or {}
            if stored_selection:
                selected_refs = self._stored_model_selection_refs(stored_selection)
            else:
                selected_refs = [
                    str(item.get("modelRef") or "").strip()
                    for item in enabled_candidates
                    if str(item.get("modelRef") or "").strip()
                ]
            selected_refs = [
                ref
                for ref in _list_of_strings(selected_refs)
                if ref in valid_option_refs
            ][:3]
            rows.append(
                {
                    "operationKind": operation_kind,
                    "modality": modality,
                    "enabled": bool(selected_refs) and bool(stored_selection.get("enabled", True)) if stored_selection else bool(selected_refs),
                    "selectedModelRefs": selected_refs,
                    "priority": _safe_priority(stored_selection.get("priority") if stored_selection else (enabled_candidates[0].get("priority") if enabled_candidates else 100), 100),
                    "optionCount": len(options),
                }
            )
        return rows

    def get_model_preferences(self) -> dict[str, Any]:
        stored = storage.read_json(MODEL_PREFERENCES_STORE_FILE)
        stored_models = {
            str(item.get("candidateId") or ""): dict(item)
            for item in list((stored or {}).get("models") or [])
            if isinstance(item, dict) and item.get("candidateId")
        } if isinstance(stored, dict) else {}
        stored_selections = [
            dict(item)
            for item in list((stored or {}).get("selections") or [])
            if isinstance(item, dict) and item.get("operationKind")
        ] if isinstance(stored, dict) else []
        has_stored_preferences = bool(stored_models or stored_selections)
        selection_by_operation = {
            str(item.get("operationKind") or ""): dict(item)
            for item in stored_selections
            if str(item.get("operationKind") or "").strip()
        }
        candidates: list[dict[str, Any]] = []
        for index, candidate in enumerate(self.list_model_candidates(), start=1):
            saved = stored_models.get(str(candidate.get("candidateId"))) or {}
            default_enabled = self._default_enabled_for_candidate(candidate, has_stored_preferences=has_stored_preferences)
            priority = _safe_priority(saved.get("priority"), index * 10)
            candidates.append(
                {
                    **candidate,
                    "priority": priority,
                    "enabled": bool(saved.get("enabled", default_enabled)),
                    "lastUpdatedAt": saved.get("updatedAt") or "",
                }
            )

        for selection in stored_selections:
            operation_kind = str(selection.get("operationKind") or "").strip()
            selected_refs = self._stored_model_selection_refs(selection)
            if not operation_kind or not selected_refs:
                continue
            base_priority = _safe_priority(selection.get("priority"), 100)
            for index, model_ref in enumerate(selected_refs):
                for candidate in candidates:
                    if str(candidate.get("operationKind") or "") == operation_kind and str(candidate.get("modelRef") or "") == model_ref:
                        candidate["enabled"] = bool(selection.get("enabled", True))
                        candidate["priority"] = _safe_priority(base_priority + index, base_priority + index)
                        candidate["lastUpdatedAt"] = selection.get("updatedAt") or candidate.get("lastUpdatedAt") or ""

        policies: dict[str, dict[str, Any]] = {}
        operation_kinds = sorted({str(item.get("operationKind") or "") for item in candidates if item.get("operationKind")})
        for operation_kind in operation_kinds:
            models = [item for item in candidates if item.get("operationKind") == operation_kind]
            models.sort(key=lambda item: (_safe_priority(item.get("priority"), 999), str(item.get("providerName") or "")))
            policies[operation_kind] = {
                "fallbackEnabled": True,
                "models": models,
            }
        connected_options = [
            dict(item)
            for item in candidates
            if self._is_configured_model_candidate(item) and (bool(item.get("available", True)) or bool(item.get("briefOnly", False)))
        ]
        diagnostic_candidates = [
            dict(item)
            for item in candidates
            if not self._is_configured_model_candidate(item) or (not bool(item.get("available", True)) and not bool(item.get("briefOnly", False)))
        ]
        return {
            "version": 1,
            "updatedAt": (stored or {}).get("updatedAt") if isinstance(stored, dict) else "",
            "candidates": candidates,
            "connectedOptions": connected_options,
            "diagnosticCandidates": diagnostic_candidates,
            "operationRows": self._build_operation_rows(
                candidates=candidates,
                connected_options=connected_options,
                stored_selections=list(selection_by_operation.values()),
            ),
            "policies": policies,
        }

    def save_model_preferences(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        known_candidates = self.list_model_candidates()
        known = {str(item.get("candidateId")): item for item in known_candidates}
        known_by_operation_ref = {
            (str(item.get("operationKind") or ""), str(item.get("modelRef") or "")): item
            for item in known_candidates
            if self._is_configured_model_candidate(item)
        }
        saved_models: list[dict[str, Any]] = []
        saved_selections: list[dict[str, Any]] = []
        incoming = list((payload or {}).get("selections") or (payload or {}).get("models") or [])
        for item in incoming:
            if not isinstance(item, dict):
                continue
            candidate_id = str(item.get("candidateId") or "").strip()
            operation_kind = str(item.get("operationKind") or item.get("operation_kind") or "").strip()
            selected_refs = self._stored_model_selection_refs(item)
            if operation_kind:
                saved_selections.append(
                    {
                        "operationKind": operation_kind,
                        "modelRefs": selected_refs[:3],
                        "enabled": bool(item.get("enabled", bool(selected_refs))),
                        "priority": _safe_priority(item.get("priority"), 100),
                        "updatedAt": now,
                    }
                )
            if candidate_id and candidate_id in known:
                base = known[candidate_id]
                if not self._is_configured_model_candidate(base):
                    continue
                saved_models.append(
                    {
                        "candidateId": candidate_id,
                        "modality": base.get("modality"),
                        "operationKind": base.get("operationKind"),
                        "providerId": base.get("providerId"),
                        "modelId": base.get("modelId"),
                        "modelRef": base.get("modelRef"),
                        "adapter": base.get("adapter"),
                        "enabled": bool(item.get("enabled", True)),
                        "priority": _safe_priority(item.get("priority"), 100),
                        "updatedAt": now,
                    }
                )
                continue
            if not operation_kind:
                continue
            base_priority = _safe_priority(item.get("priority"), 100)
            for index, model_ref in enumerate(selected_refs[:3]):
                base = known_by_operation_ref.get((operation_kind, model_ref))
                if not base:
                    continue
                saved_models.append(
                    {
                        "candidateId": base.get("candidateId"),
                        "modality": base.get("modality"),
                        "operationKind": base.get("operationKind"),
                        "providerId": base.get("providerId"),
                        "modelId": base.get("modelId"),
                        "modelRef": base.get("modelRef"),
                        "adapter": base.get("adapter"),
                        "enabled": bool(item.get("enabled", True)),
                        "priority": _safe_priority(base_priority + index, base_priority + index),
                        "updatedAt": now,
                    }
                )
        storage.write_json(
            MODEL_PREFERENCES_STORE_FILE,
                {
                    "version": 1,
                    "updatedAt": now,
                    "selections": saved_selections,
                    "models": saved_models,
                },
        )
        return self.get_model_preferences()

    def _has_explicit_model_selection(self, request: dict[str, Any]) -> bool:
        return any(
            key in request and str(request.get(key) or "").strip()
            for key in ("adapter", "provider", "providerId", "provider_id", "model", "modelId", "model_id")
        )

    def _preferred_model_candidates(self, operation_kind: str) -> list[dict[str, Any]]:
        prefs = self.get_model_preferences()
        candidates = [
            dict(item)
            for item in list((prefs.get("policies") or {}).get(operation_kind, {}).get("models") or [])
            if bool(item.get("enabled", True)) and bool(item.get("available", True))
        ]
        candidates.sort(key=lambda item: (_safe_priority(item.get("priority"), 999), str(item.get("providerName") or "")))
        return candidates

    def _all_model_candidates_for_operation(self, operation_kind: str) -> list[dict[str, Any]]:
        prefs = self.get_model_preferences()
        return [
            dict(item)
            for item in list((prefs.get("policies") or {}).get(operation_kind, {}).get("models") or [])
        ]

    def _request_for_candidate(self, request: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        next_request = dict(request or {})
        next_request["adapter"] = candidate.get("adapter")
        next_request["providerId"] = candidate.get("providerId")
        next_request["model"] = candidate.get("modelId")
        next_request.setdefault("modelId", candidate.get("modelId"))
        next_request["operationKind"] = candidate.get("operationKind")
        return next_request

    def _compact_model_candidate(self, candidate: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(candidate, dict) or not candidate:
            return None
        return {
            "candidateId": candidate.get("candidateId"),
            "providerId": candidate.get("providerId"),
            "providerName": candidate.get("providerName"),
            "modelId": candidate.get("modelId"),
            "modelRef": candidate.get("modelRef"),
            "adapter": candidate.get("adapter"),
            "operationKind": candidate.get("operationKind"),
            "modality": candidate.get("modality"),
            "source": candidate.get("source"),
            "available": bool(candidate.get("available", False)),
            "nativeAudio": bool(candidate.get("nativeAudio", False)),
            "capabilityProfile": candidate.get("capabilityProfile") or {},
        }

    def _is_incompatible_media_candidate(self, candidate: dict[str, Any], operation_kind: str) -> bool:
        modality = _modality_for_operation(operation_kind)
        if str(candidate.get("modality") or "") != modality:
            return True
        return False

    def _provider_plan_for_operation(
        self,
        operation_kind: str,
        *,
        preferred_model_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        preferred = tuple(str(item or "").strip() for item in preferred_model_ids if str(item or "").strip())
        candidates = [
            item
            for item in self._preferred_model_candidates(operation_kind)
            if not self._is_incompatible_media_candidate(item, operation_kind)
        ]
        if not candidates:
            candidates = [
                item
                for item in self._all_model_candidates_for_operation(operation_kind)
                if bool(item.get("available", False)) and not self._is_incompatible_media_candidate(item, operation_kind)
            ]
        candidates.sort(
            key=lambda item: (
                preferred.index(str(item.get("modelId") or ""))
                if str(item.get("modelId") or "") in preferred
                else len(preferred),
                _safe_priority(item.get("priority"), 999),
                str(item.get("providerName") or ""),
                str(item.get("modelId") or ""),
            )
        )
        primary = candidates[0] if candidates else None
        return {
            "operationKind": operation_kind,
            "selectionPolicy": {
                "preferredModelIds": list(preferred),
                "requiresExactCapability": True,
                "fallbackEnabled": True,
            },
            "primary": self._compact_model_candidate(primary),
            "fallbacks": [self._compact_model_candidate(item) for item in candidates[1:4]],
            "capabilityGap": None
            if primary
            else {
                "code": "creative_media_capability_gap",
                "message": f"No available model candidate for {operation_kind}.",
            },
        }

    def _work_order_kind_for_request(self, payload: dict[str, Any]) -> str:
        explicit = str(
            payload.get("workOrderKind")
            or payload.get("work_order_kind")
            or payload.get("workflow")
            or payload.get("intent")
            or ""
        ).strip().lower()
        modality = str(payload.get("modality") or "").strip().lower()
        if "storyboard" in explicit or "video" in explicit and "simple" not in explicit:
            return "storyboard_to_video"
        if modality == "video":
            return "storyboard_to_video"
        return "simple_asset"

    def _video_operation_for_work_order(self, payload: dict[str, Any]) -> str:
        reference_ids = _list_of_strings(payload.get("referenceAssetIds") or payload.get("reference_asset_ids"))
        image_urls = _list_of_strings(payload.get("imageUrls") or payload.get("image_urls"))
        reference_assets = payload.get("referenceAssets") or payload.get("reference_assets") or []
        if not isinstance(reference_assets, list):
            reference_assets = []
        reference_haystack = " ".join(
            " ".join(str(asset.get(key) or "") for key in ("modality", "assetRole", "role", "type"))
            for asset in reference_assets
            if isinstance(asset, dict)
        ).lower()
        if (
            payload.get("referenceVideoUrl")
            or payload.get("reference_video_url")
            or payload.get("referenceAudioUrl")
            or payload.get("reference_audio_url")
            or any(token in reference_haystack for token in ("video", "audio", "music", "sound", "camera_motion"))
        ):
            return "video.reference_to_video"
        if payload.get("firstFrame") or payload.get("lastFrame") or len(reference_ids) >= 2 or len(image_urls) >= 2:
            return "video.first_last_frame"
        if reference_ids or image_urls or payload.get("referenceImageUrl") or payload.get("reference_image_url"):
            return "video.image_to_video"
        return "video.text_to_video"

    def _save_work_order(self, work_order: dict[str, Any]) -> dict[str, Any]:
        store = self._read_versioned_store(WORK_ORDER_STORE_FILE, "workOrders")
        values = dict(store.get("workOrders") or {})
        values[str(work_order["workOrderId"])] = dict(work_order)
        self._write_versioned_store(WORK_ORDER_STORE_FILE, "workOrders", values)
        return dict(work_order)

    def list_work_orders(self, *, status: str | None = None, requesting_runtime: str | None = None) -> list[dict[str, Any]]:
        store = self._read_versioned_store(WORK_ORDER_STORE_FILE, "workOrders")
        items = list(dict(store.get("workOrders") or {}).values())
        if status:
            items = [item for item in items if str(item.get("status") or "") == status]
        if requesting_runtime:
            items = [
                item
                for item in items
                if str(item.get("requestingRuntime") or item.get("requesting_runtime") or "") == requesting_runtime
            ]
        items.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return items

    def compile_work_order(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        payload.update(self._scope_fields(payload))
        work_order_kind = self._work_order_kind_for_request(payload)
        if work_order_kind == "storyboard_to_video":
            return self._compile_storyboard_work_order(payload)
        return self._compile_simple_asset_work_order(payload)

    def _compile_simple_asset_work_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        brief = str(payload.get("brief") or payload.get("prompt") or "Create a supporting visual asset.").strip()
        asset_role = str(payload.get("assetRole") or payload.get("asset_role") or "supporting_visual").strip()
        aspect_ratio = str(payload.get("aspectRatio") or payload.get("ratio") or "16:9").strip()
        recipe = self.compile_recipe(
            {
                **payload,
                "modality": "image",
                "prompt": brief,
                "ratio": aspect_ratio,
                "recipeKind": "simple_asset",
                "hardRequirements": {
                    **dict(payload.get("hardRequirements") or {}),
                    "assetRole": asset_role,
                    "costMode": "low_cost_single_image",
                },
            }
        )
        provider_plan = self._provider_plan_for_operation("image.generate", preferred_model_ids=PREFERRED_IMAGE_MODEL_IDS)
        now = utc_now_iso()
        work_order = {
            "version": 1,
            "workOrderId": f"cmwo_{uuid.uuid4().hex[:16]}",
            "status": "planned",
            "workOrderKind": "simple_asset",
            "intent": str(payload.get("intent") or "simple_asset"),
            "modality": "image",
            "assetRole": asset_role,
            "brief": brief,
            "aspectRatio": aspect_ratio,
            "requestingRuntime": str(payload.get("requestingRuntime") or payload.get("requesting_runtime") or ""),
            "qualityTier": str(payload.get("qualityTier") or payload.get("quality_tier") or "draft"),
            "costLimit": payload.get("costLimit") or payload.get("cost_limit"),
            "shotPlan": [],
            "storyboardAssets": [],
            "providerPlan": {"imageGeneration": provider_plan},
            "jobIds": [],
            "artifactRefs": [],
            "recipeRefs": [recipe.get("recipeId")] if recipe.get("recipeId") else [],
            "qualityChecks": [
                {"id": "brief_retention", "status": "planned"},
                {"id": "aspect_ratio", "status": "planned", "value": aspect_ratio},
            ],
            "costEstimate": {
                "mode": "dry_run",
                "liveSmokeLimit": "max 1 image when explicitly requested",
                "costLimit": payload.get("costLimit") or payload.get("cost_limit"),
            },
            "safetyStatus": {
                "status": "planned_only",
                "defaultPolicy": "provider safety checks still apply during live generation",
            },
            "dryRunOnly": not _truthy(payload.get("live") or payload.get("execute")),
            "createdAt": now,
            "updatedAt": now,
        }
        return self._save_work_order(work_order)

    def _compile_storyboard_work_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        brief = str(payload.get("brief") or payload.get("prompt") or "Create a short storyboard-driven video.").strip()
        aspect_ratio = str(payload.get("aspectRatio") or payload.get("ratio") or "16:9").strip()
        duration = int(payload.get("duration") or payload.get("durationSeconds") or payload.get("duration_seconds") or 5)
        recipe = self.compile_recipe(
            {
                **payload,
                "modality": "video",
                "prompt": brief,
                "ratio": aspect_ratio,
                "duration": duration,
                "recipeKind": "storyboard_to_video",
            }
        )
        neutral = dict(recipe.get("providerNeutralRecipe") or {})
        timed_segments = list(neutral.get("timedSegments") or [])
        image_plan = self._provider_plan_for_operation("image.generate", preferred_model_ids=PREFERRED_IMAGE_MODEL_IDS)
        video_operation = self._video_operation_for_work_order(payload)
        video_plan = self._provider_plan_for_operation(video_operation, preferred_model_ids=PREFERRED_SEEDANCE2_MODEL_IDS)
        storyboard_assets: list[dict[str, Any]] = []
        for index, segment in enumerate(timed_segments[:8], start=1):
            role = "first_frame" if index == 1 else "last_frame" if index == len(timed_segments[:8]) else "reference_image"
            storyboard_assets.append(
                {
                    "shotId": segment.get("id") or f"shot_{index:02d}",
                    "assetRole": role,
                    "brief": segment.get("description") or segment.get("visual") or brief,
                    "plannedModality": "image",
                    "providerPlan": image_plan,
                }
            )
        now = utc_now_iso()
        work_order = {
            "version": 1,
            "workOrderId": f"cmwo_{uuid.uuid4().hex[:16]}",
            "status": "planned",
            "workOrderKind": "storyboard_to_video",
            "intent": str(payload.get("intent") or "storyboard_to_video"),
            "modality": "video",
            "assetRole": str(payload.get("assetRole") or payload.get("asset_role") or "short_video"),
            "brief": brief,
            "aspectRatio": aspect_ratio,
            "durationSeconds": duration,
            "referenceAssetIds": _list_of_strings(payload.get("referenceAssetIds") or payload.get("reference_asset_ids")),
            "requestingRuntime": str(payload.get("requestingRuntime") or payload.get("requesting_runtime") or ""),
            "qualityTier": str(payload.get("qualityTier") or payload.get("quality_tier") or "draft"),
            "costLimit": payload.get("costLimit") or payload.get("cost_limit"),
            "shotPlan": timed_segments,
            "storyboardAssets": storyboard_assets,
            "providerPlan": {
                "imageStoryboard": image_plan,
                "videoGeneration": video_plan,
                "directorOrReview": {
                    "allowedRoles": ["director", "prompt_compression", "quality_review"],
                    "lowerTierExecutableVideoModels": sorted(LOWER_TIER_EXECUTABLE_VIDEO_MODELS),
                    "note": "doubao-seed-2-0-lite-260428 is an executable video model with image/video/audio references; it is treated as a lower-tier fallback behind Seedance 2.0 exact profiles.",
                },
            },
            "jobIds": [],
            "artifactRefs": [],
            "recipeRefs": [recipe.get("recipeId")] if recipe.get("recipeId") else [],
            "qualityChecks": [
                {"id": "shot_continuity", "status": "planned"},
                {"id": "reference_roles", "status": "planned", "operationKind": video_operation},
                {"id": "hard_requirement_retention", "status": "planned"},
            ],
            "costEstimate": {
                "mode": "dry_run",
                "liveSmokeLimit": "max 1 storyboard image + 1 short 3-5s video when explicitly requested",
                "costLimit": payload.get("costLimit") or payload.get("cost_limit"),
            },
            "safetyStatus": {
                "status": "planned_only",
                "defaultPolicy": "provider safety checks still apply during live generation",
            },
            "dryRunOnly": not _truthy(payload.get("live") or payload.get("execute")),
            "createdAt": now,
            "updatedAt": now,
        }
        return self._save_work_order(work_order)

    def compile_recipe(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        payload.update(self._scope_fields(payload))
        recipe = creative_recipe_compiler.compile_recipe(payload)
        self._record_safety_event(
            source="recipe_compile",
            recipe=recipe,
            transform=dict((recipe.get("hardRequirements") or {}).get("safetyTransform") or {}),
        )
        return recipe

    def get_recipe(self, recipe_id: str) -> dict[str, Any] | None:
        return creative_recipe_compiler.get_recipe(recipe_id)

    def list_recipes(self, *, modality: str | None = None, recipe_kind: str | None = None) -> list[dict[str, Any]]:
        return creative_recipe_compiler.list_recipes(modality=modality, recipe_kind=recipe_kind)

    def register_asset(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        payload.update(self._scope_fields(payload))
        return creative_recipe_compiler.register_asset(payload)

    def list_assets(self, *, modality: str | None = None, role: str | None = None) -> list[dict[str, Any]]:
        return creative_recipe_compiler.list_assets(modality=modality, role=role)

    def create_character_bible(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        payload.update(self._scope_fields(payload))
        return creative_recipe_compiler.create_character_bible(payload)

    def get_character_bible(self, bible_id: str) -> dict[str, Any] | None:
        return creative_recipe_compiler.get_character_bible(bible_id)

    def list_character_bibles(self) -> list[dict[str, Any]]:
        return creative_recipe_compiler.list_character_bibles()

    def register_keyframe(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        payload.update(self._scope_fields(payload))
        return creative_recipe_compiler.register_keyframe(payload)

    def get_keyframe(self, keyframe_id: str) -> dict[str, Any] | None:
        return creative_recipe_compiler.get_keyframe(keyframe_id)

    def list_keyframes(
        self,
        *,
        recipe_id: str | None = None,
        role: str | None = None,
        character_bible_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return creative_recipe_compiler.list_keyframes(
            recipe_id=recipe_id,
            role=role,
            character_bible_id=character_bible_id,
        )

    def _read_versioned_store(self, filename: str, key: str) -> dict[str, Any]:
        payload = storage.read_json(filename)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return {"version": 1, key: {}}
        payload.setdefault(key, {})
        return payload

    def _write_versioned_store(self, filename: str, key: str, values: dict[str, Any]) -> None:
        storage.write_json(filename, {"version": 1, key: dict(values or {})})

    def _artifact_source_path(self, artifact_id: str) -> str:
        normalized = str(artifact_id or "").strip()
        if not normalized:
            return ""
        record = db.get_runtime_artifact(normalized)
        if not record:
            return ""
        return str(record.get("source_path") or record.get("sourcePath") or "").strip()

    def _resolve_media_path(self, ref: dict[str, Any]) -> tuple[str, bool]:
        raw_path = (
            str(ref.get("sourcePath") or ref.get("source_path") or "").strip()
            or self._artifact_source_path(str(ref.get("artifactId") or ref.get("artifact_id") or ""))
            or str(ref.get("workspacePath") or ref.get("workspace_path") or "").strip()
        )
        if not raw_path:
            return "", False
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = storage.base_dir / "workspace" / raw_path
        return str(path), path.exists() and path.is_file()

    def _probe_duration_seconds(self, path: str) -> float | None:
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            return None
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "json",
                    path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if result.returncode != 0:
                return None
            payload = json.loads(result.stdout or "{}")
            duration = float(((payload.get("format") or {}).get("duration") or 0))
            return duration if duration > 0 else None
        except Exception:
            return None

    def _asset_refs_by_ids(self, asset_ids: list[str]) -> list[dict[str, Any]]:
        if not asset_ids:
            return []
        assets = self.list_assets()
        by_id = {str(item.get("assetId") or ""): dict(item) for item in assets}
        return [by_id[item] for item in asset_ids if item in by_id]

    def _select_edit_plan_assets(self, request: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        explicit_asset_ids = _list_of_strings(request.get("assetIds") or request.get("asset_ids"))
        explicit_video_ids = _list_of_strings(request.get("videoAssetIds") or request.get("video_asset_ids"))
        explicit_audio_ids = _list_of_strings(
            request.get("audioAssetIds")
            or request.get("audio_asset_ids")
            or request.get("voiceAssetIds")
            or request.get("musicAssetIds")
        )
        explicit_assets = self._asset_refs_by_ids([*explicit_asset_ids, *explicit_video_ids, *explicit_audio_ids])
        inline_assets = [dict(item) for item in list(request.get("assets") or []) if isinstance(item, dict)]
        candidates = [*explicit_assets, *inline_assets]
        if not candidates:
            candidates = self.list_assets()

        video_refs: list[dict[str, Any]] = []
        audio_refs: list[dict[str, Any]] = []
        for asset in candidates:
            modality = str(asset.get("modality") or "").strip().lower()
            path, exists = self._resolve_media_path(asset)
            suffix = Path(path).suffix.lower() if path else ""
            enriched = {**asset, "resolvedPath": path, "pathExists": exists}
            if modality == "video" or suffix in VIDEO_EXTENSIONS:
                video_refs.append(enriched)
            elif modality in {"voice", "music", "audio"} or suffix in AUDIO_EXTENSIONS:
                audio_refs.append(enriched)
        return video_refs[:12], audio_refs[:6]

    def _asset_declares_native_audio(self, asset: dict[str, Any]) -> bool:
        metadata = dict(asset.get("metadata") or {})
        return _truthy(asset.get("nativeAudio")) or _truthy(metadata.get("nativeAudio"))

    def _native_audio_policy_for_plan(
        self,
        request: dict[str, Any],
        *,
        video_refs: list[dict[str, Any]],
        audio_refs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        explicit = request.get("preserveNativeAudio")
        if explicit is None:
            explicit = request.get("preserve_native_audio")
        has_native_video_audio = any(self._asset_declares_native_audio(asset) for asset in video_refs)
        has_external_audio = bool(audio_refs)
        if explicit is not None:
            preserve_native = _truthy(explicit)
            reason = "explicit_request"
        elif has_native_video_audio and not has_external_audio:
            preserve_native = True
            reason = "native_audio_video_asset"
        else:
            preserve_native = False
            reason = "external_audio_requested" if has_external_audio else "no_native_audio_evidence"
        return {
            "preserveNativeAudio": preserve_native,
            "hasNativeVideoAudio": has_native_video_audio,
            "hasExternalAudioRefs": has_external_audio,
            "audioPreservationPolicy": "preserve_native_audio_by_default" if preserve_native else "use_external_or_silent_track",
            "reason": reason,
        }

    def _plan_subtitle_segments(self, request: dict[str, Any], *, total_duration: float) -> list[dict[str, Any]]:
        raw_segments = request.get("subtitles") or request.get("subtitleSegments") or request.get("subtitle_segments")
        segments: list[dict[str, Any]] = []
        if isinstance(raw_segments, list):
            for index, item in enumerate(raw_segments):
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or item.get("caption") or "").strip()
                if not text:
                    continue
                start = max(0.0, float(item.get("start") or item.get("startSeconds") or index * 3))
                end = max(start + 0.5, float(item.get("end") or item.get("endSeconds") or min(total_duration, start + 3)))
                segments.append({"start": start, "end": min(end, max(total_duration, end)), "text": text})
        subtitle_text = str(request.get("subtitleText") or request.get("subtitle_text") or "").strip()
        if subtitle_text and not segments:
            segments.append({"start": 0.0, "end": max(1.0, total_duration), "text": subtitle_text})
        return segments

    def create_edit_plan(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        plan_id = str(payload.get("planId") or payload.get("editPlanId") or payload.get("id") or f"cm_edit_{uuid.uuid4().hex}").strip()
        recipe_id = str(payload.get("recipeId") or payload.get("recipe_id") or "").strip()
        recipe = self.get_recipe(recipe_id) if recipe_id else None
        scope = self._scope_fields(payload, recipe or {})
        video_refs, audio_refs = self._select_edit_plan_assets(payload)
        if not video_refs:
            raise ValueError("creative media edit plan requires at least one video asset")

        timeline: list[dict[str, Any]] = []
        cursor = 0.0
        for index, asset in enumerate(video_refs, start=1):
            duration = self._probe_duration_seconds(str(asset.get("resolvedPath") or "")) or float(payload.get("defaultClipDurationSeconds") or 5)
            clip = {
                "clipId": f"clip_{index:02d}",
                "assetId": asset.get("assetId"),
                "artifactId": asset.get("artifactId"),
                "sourcePath": asset.get("sourcePath"),
                "resolvedPath": asset.get("resolvedPath"),
                "pathExists": bool(asset.get("pathExists")),
                "start": round(cursor, 3),
                "end": round(cursor + duration, 3),
                "durationSeconds": round(duration, 3),
                "role": asset.get("role") or "clip",
                "title": asset.get("title") or asset.get("assetId") or f"clip-{index}",
            }
            timeline.append(clip)
            cursor += duration

        target_duration = float(payload.get("targetDurationSeconds") or payload.get("durationSeconds") or cursor)
        render_profile = {
            "format": "mp4",
            "width": int(payload.get("width") or (payload.get("renderProfile") or {}).get("width") or 1280),
            "height": int(payload.get("height") or (payload.get("renderProfile") or {}).get("height") or 720),
            "fps": int(payload.get("fps") or (payload.get("renderProfile") or {}).get("fps") or 30),
            "videoCodec": "libx264",
            "audioCodec": "aac",
        }
        audio_policy = self._native_audio_policy_for_plan(payload, video_refs=video_refs, audio_refs=audio_refs)
        now = utc_now_iso()
        plan = {
            "planId": plan_id,
            **scope,
            "kind": "creative_media_edit_plan",
            "recipeId": recipe_id,
            "status": "planned",
            "timeline": timeline,
            "tracks": {
                "video": timeline,
                "audio": [
                    {
                        "assetId": asset.get("assetId"),
                        "artifactId": asset.get("artifactId"),
                        "sourcePath": asset.get("sourcePath"),
                        "resolvedPath": asset.get("resolvedPath"),
                        "pathExists": bool(asset.get("pathExists")),
                        "modality": asset.get("modality"),
                        "role": asset.get("role") or "audio",
                        "musicKind": asset.get("musicKind"),
                    }
                    for asset in audio_refs
                ],
                "subtitles": self._plan_subtitle_segments(payload, total_duration=target_duration or cursor),
            },
            "renderProfile": render_profile,
            "audioPolicy": audio_policy,
            "lineage": {
                "recipeId": recipe_id,
                "assetIds": [str(asset.get("assetId") or "") for asset in [*video_refs, *audio_refs] if asset.get("assetId")],
                "sourceRefs": _list_of_strings(payload.get("sourceRefs") or payload.get("source_refs")),
                "parentPlanId": str(payload.get("parentPlanId") or payload.get("parent_plan_id") or "").strip(),
            },
            "qualityGates": {
                "missingVideoFiles": [clip for clip in timeline if not clip.get("pathExists")],
                "missingAudioFiles": [item for item in audio_refs if not item.get("pathExists")],
                "musicBoundary": "Creative Media audio/music tracks must be artifact or asset ledger refs; legacy MusicTrack is not used.",
                "nativeAudioPolicy": audio_policy,
                "estimatedDurationSeconds": round(cursor, 3),
                "targetDurationSeconds": round(target_duration, 3),
            },
            "recipeSnapshot": {
                "modality": (recipe or {}).get("modality"),
                "recipeKind": (recipe or {}).get("recipeKind"),
                "prompt": (recipe or {}).get("prompt"),
            } if recipe else {},
            "createdAt": now,
            "updatedAt": now,
        }
        store = self._read_versioned_store(EDIT_PLAN_STORE_FILE, "editPlans")
        plans = dict(store.get("editPlans") or {})
        previous = dict(plans.get(plan_id) or {})
        plan["version"] = int(previous.get("version") or 0) + 1
        plan["createdAt"] = previous.get("createdAt") or now
        plans[plan_id] = plan
        self._write_versioned_store(EDIT_PLAN_STORE_FILE, "editPlans", plans)
        return plan

    def get_edit_plan(self, plan_id: str) -> dict[str, Any] | None:
        return dict((self._read_versioned_store(EDIT_PLAN_STORE_FILE, "editPlans").get("editPlans") or {}).get(str(plan_id)) or {}) or None

    def list_edit_plans(self, *, recipe_id: str | None = None) -> list[dict[str, Any]]:
        plans = list((self._read_versioned_store(EDIT_PLAN_STORE_FILE, "editPlans").get("editPlans") or {}).values())
        normalized_recipe_id = str(recipe_id or "").strip()
        result = [dict(item) for item in plans if not normalized_recipe_id or str(item.get("recipeId") or "") == normalized_recipe_id]
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    def _save_render_job(self, job: dict[str, Any]) -> dict[str, Any]:
        store = self._read_versioned_store(RENDER_JOB_STORE_FILE, "renderJobs")
        jobs = dict(store.get("renderJobs") or {})
        job["updatedAt"] = utc_now_iso()
        jobs[str(job["renderJobId"])] = job
        self._write_versioned_store(RENDER_JOB_STORE_FILE, "renderJobs", jobs)
        return job

    def get_render(self, render_job_id: str) -> dict[str, Any] | None:
        return dict((self._read_versioned_store(RENDER_JOB_STORE_FILE, "renderJobs").get("renderJobs") or {}).get(str(render_job_id)) or {}) or None

    def list_renders(self, *, plan_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        jobs = list((self._read_versioned_store(RENDER_JOB_STORE_FILE, "renderJobs").get("renderJobs") or {}).values())
        normalized_plan_id = str(plan_id or "").strip()
        normalized_status = str(status or "").strip().lower()
        result = [
            dict(item)
            for item in jobs
            if (not normalized_plan_id or str(item.get("planId") or "") == normalized_plan_id)
            and (not normalized_status or str(item.get("status") or "").lower() == normalized_status)
        ]
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    def _srt_timestamp(self, seconds: float) -> str:
        total_ms = max(0, int(round(float(seconds) * 1000)))
        hours, remainder = divmod(total_ms, 3600_000)
        minutes, remainder = divmod(remainder, 60_000)
        secs, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def _write_srt(self, subtitles: list[dict[str, Any]], render_job: dict[str, Any]) -> Path | None:
        if not subtitles:
            return None
        path = self._output_path(render_job, "subtitles", ".srt")
        lines: list[str] = []
        for index, item in enumerate(subtitles, start=1):
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            lines.extend(
                [
                    str(index),
                    f"{self._srt_timestamp(float(item.get('start') or 0))} --> {self._srt_timestamp(float(item.get('end') or 0))}",
                    text,
                    "",
                ]
            )
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _record_post_artifact(self, *, file_path: Path, kind: str, mime_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return artifact_store.record_artifact(
            artifact_kind=kind,
            mime_type=mime_type,
            title=file_path.name,
            source_path=str(file_path),
            metadata={
                **metadata,
                "origin": "creative_media_post_production",
                "pathPlane": "runtime",
                "storageClass": "runtime_artifact",
                "surfaceVisible": True,
            },
            source_component="creative_media_runtime",
            node="creative_media_post_production",
        )

    def _build_ffmpeg_render_command(
        self,
        *,
        ffmpeg: str,
        video_paths: list[str],
        audio_paths: list[str],
        output_path: Path,
        profile: dict[str, Any],
        preserve_native_audio: bool = False,
    ) -> list[str]:
        width = max(64, int(profile.get("width") or 1280))
        height = max(64, int(profile.get("height") or 720))
        fps = max(1, int(profile.get("fps") or 30))
        command = [ffmpeg, "-y"]
        for path in video_paths:
            command.extend(["-i", path])
        for path in audio_paths:
            command.extend(["-i", path])

        filters: list[str] = []
        video_labels: list[str] = []
        for index in range(len(video_paths)):
            label = f"v{index}"
            filters.append(
                f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[{label}]"
            )
            video_labels.append(f"[{label}]")
        video_out = "[vout]"
        if len(video_labels) == 1:
            filters.append(f"{video_labels[0]}null{video_out}")
        else:
            filters.append(f"{''.join(video_labels)}concat=n={len(video_labels)}:v=1:a=0{video_out}")

        audio_out = ""
        if audio_paths:
            audio_labels: list[str] = []
            audio_offset = len(video_paths)
            for index in range(len(audio_paths)):
                label = f"a{index}"
                filters.append(f"[{audio_offset + index}:a]aresample=44100,volume=1.0[{label}]")
                audio_labels.append(f"[{label}]")
            audio_out = "[aout]"
            if len(audio_labels) == 1:
                filters.append(f"{audio_labels[0]}anull{audio_out}")
            else:
                filters.append(f"{''.join(audio_labels)}amix=inputs={len(audio_labels)}:duration=shortest:dropout_transition=0{audio_out}")

        command.extend(["-filter_complex", ";".join(filters), "-map", video_out])
        native_audio_mapped = False
        if audio_out:
            command.extend(["-map", audio_out, "-shortest"])
        elif preserve_native_audio and len(video_paths) == 1:
            command.extend(["-map", "0:a?", "-shortest"])
            native_audio_mapped = True
        else:
            command.append("-an")
        command.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart"])
        if audio_out or native_audio_mapped:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        command.append(str(output_path))
        return command

    def render_edit_plan(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        plan_id = str(payload.get("planId") or payload.get("editPlanId") or payload.get("id") or "").strip()
        plan = self.get_edit_plan(plan_id) if plan_id else None
        if not plan:
            if payload.get("plan") and isinstance(payload.get("plan"), dict):
                plan = dict(payload["plan"])
                plan_id = str(plan.get("planId") or f"cm_edit_{uuid.uuid4().hex}")
            else:
                raise ValueError("creative media render requires planId or plan")
        render_job_id = str(payload.get("renderJobId") or f"cm_render_{uuid.uuid4().hex}").strip()
        now = utc_now_iso()
        scope = self._scope_fields(payload, plan)
        job = {
            "renderJobId": render_job_id,
            **scope,
            "planId": plan_id,
            "status": "running",
            "artifacts": [],
            "diagnostics": {},
            "error": None,
            "createdAt": now,
            "updatedAt": now,
            "completedAt": None,
        }
        self._save_render_job(job)

        try:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("ffmpeg not found")
            video_paths = [
                str(item.get("resolvedPath") or "")
                for item in list((plan.get("tracks") or {}).get("video") or plan.get("timeline") or [])
                if str(item.get("resolvedPath") or "").strip()
            ]
            audio_paths = [
                str(item.get("resolvedPath") or "")
                for item in list((plan.get("tracks") or {}).get("audio") or [])
                if str(item.get("resolvedPath") or "").strip()
            ]
            video_paths = [path for path in video_paths if Path(path).exists()]
            audio_paths = [path for path in audio_paths if Path(path).exists()]
            if not video_paths:
                raise ValueError("creative media render requires at least one existing video file")
            audio_policy = dict(plan.get("audioPolicy") or {})
            preserve_native_audio = bool(audio_policy.get("preserveNativeAudio")) and not audio_paths

            output_path = self._output_path(job, "final-video", ".mp4")
            command = self._build_ffmpeg_render_command(
                ffmpeg=ffmpeg,
                video_paths=video_paths,
                audio_paths=audio_paths,
                output_path=output_path,
                profile=dict(plan.get("renderProfile") or {}),
                preserve_native_audio=preserve_native_audio,
            )
            result = subprocess.run(command, capture_output=True, text=True, timeout=int(payload.get("timeoutSeconds") or 900), check=False)
            job["diagnostics"] = {
                "ffmpegPath": ffmpeg,
                "ffmpegReturnCode": result.returncode,
                "ffmpegCommand": command,
                "stderrTail": (result.stderr or "")[-4000:],
                "videoInputs": video_paths,
                "audioInputs": audio_paths,
                "audioPolicy": audio_policy,
                "preserveNativeAudio": preserve_native_audio,
            }
            if result.returncode != 0 or not output_path.exists():
                raise RuntimeError(f"ffmpeg render failed with code {result.returncode}")

            artifacts = [
                self._record_post_artifact(
                    file_path=output_path,
                    kind="video",
                    mime_type="video/mp4",
                    metadata={**scope, "creativeMediaRenderJobId": render_job_id, "creativeMediaEditPlanId": plan_id, "modality": "video"},
                )
            ]
            srt_path = self._write_srt(list((plan.get("tracks") or {}).get("subtitles") or []), job)
            if srt_path:
                artifacts.append(
                    self._record_post_artifact(
                        file_path=srt_path,
                        kind="subtitle",
                        mime_type="application/x-subrip",
                        metadata={**scope, "creativeMediaRenderJobId": render_job_id, "creativeMediaEditPlanId": plan_id, "modality": "subtitle"},
                    )
                )
            edl_path = self._output_path(job, "edit-decision-list", ".json")
            edl_path.write_text(json.dumps({"plan": plan, "renderJobId": render_job_id}, ensure_ascii=False, indent=2), encoding="utf-8")
            artifacts.append(
                self._record_post_artifact(
                    file_path=edl_path,
                    kind="report",
                    mime_type="application/json",
                    metadata={**scope, "creativeMediaRenderJobId": render_job_id, "creativeMediaEditPlanId": plan_id, "modality": "metadata"},
                )
            )
            job["status"] = "succeeded"
            job["artifacts"] = artifacts
            job["completedAt"] = utc_now_iso()
            return self._save_render_job(job)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            return self._save_render_job(job)

    def _record_terminal_job_observations(self, job: dict[str, Any]) -> None:
        self._append_cost_entry(job)
        if job.get("status") == "succeeded" and job.get("artifacts"):
            quality = self.create_quality_job({"jobId": job.get("jobId"), "artifacts": list(job.get("artifacts") or []), "auto": True})
            job["qualityStatus"] = quality.get("status") or "not_run"
            job["qualityJobIds"] = [quality.get("qualityJobId")] if quality.get("qualityJobId") else []
        elif job.get("status") == "failed":
            job["qualityStatus"] = "not_run"

    def _append_cost_entry(self, job: dict[str, Any]) -> dict[str, Any]:
        store = self._read_versioned_store(COST_LEDGER_STORE_FILE, "entries")
        entries = dict(store.get("entries") or {})
        response = dict(job.get("providerResponse") or {})
        request = dict(job.get("request") or {})
        entry_id = f"cm_cost_{uuid.uuid4().hex}"
        artifacts = list(job.get("artifacts") or [])
        output_bytes = 0
        for artifact in artifacts:
            path = str(artifact.get("sourcePath") or artifact.get("source_path") or "").strip()
            if path and Path(path).exists():
                output_bytes += Path(path).stat().st_size
        entries[entry_id] = {
            "entryId": entry_id,
            "jobId": job.get("jobId"),
            "status": job.get("status"),
            "provider": response.get("providerId") or request.get("providerId") or job.get("adapter"),
            "model": response.get("model") or request.get("model") or request.get("modelId"),
            "operationKind": job.get("operationKind"),
            "modality": job.get("modality"),
            "resolution": request.get("resolution") or request.get("size"),
            "durationSeconds": request.get("duration") or request.get("durationSeconds"),
            "retryCount": len(list(job.get("fallbackAttempts") or [])),
            "usage": response.get("usage") or {},
            "estimatedCost": None,
            "outputBytes": output_bytes,
            "artifactCount": len(artifacts),
            "createdAt": utc_now_iso(),
        }
        self._write_versioned_store(COST_LEDGER_STORE_FILE, "entries", entries)
        return entries[entry_id]

    def list_cost_ledger(self) -> list[dict[str, Any]]:
        entries = list((self._read_versioned_store(COST_LEDGER_STORE_FILE, "entries").get("entries") or {}).values())
        entries.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return [dict(item) for item in entries]

    def create_quality_job(self, request: dict[str, Any]) -> dict[str, Any]:
        payload = dict(request or {})
        quality_job_id = str(payload.get("qualityJobId") or payload.get("id") or f"cm_quality_{uuid.uuid4().hex}").strip()
        job_id = str(payload.get("jobId") or payload.get("job_id") or "").strip()
        job = self.get_job(job_id, refresh=False) if job_id else None
        artifact_refs = list(payload.get("artifacts") or ((job or {}).get("artifacts") or []))
        if not artifact_refs and payload.get("artifactId"):
            artifact_refs = [{"artifactId": payload.get("artifactId"), "sourcePath": payload.get("sourcePath")}]
        checks: list[dict[str, Any]] = []
        warnings: list[str] = []
        failures: list[str] = []
        for artifact in artifact_refs:
            if not isinstance(artifact, dict):
                continue
            checks.extend(self._quality_checks_for_artifact(artifact, job or payload, warnings=warnings, failures=failures))
        if not artifact_refs:
            failures.append("no_artifacts")
            checks.append({"name": "artifact_present", "ok": False})
        status = "failed" if failures else "warning" if warnings else "passed"
        quality_job = {
            "qualityJobId": quality_job_id,
            "jobId": job_id,
            "status": status,
            "checks": checks,
            "warnings": warnings,
            "failures": failures,
            "retryRecommendation": self._retry_recommendation(status=status, failures=failures, warnings=warnings, job=job or payload),
            "createdAt": utc_now_iso(),
        }
        store = self._read_versioned_store(QUALITY_JOB_STORE_FILE, "qualityJobs")
        values = dict(store.get("qualityJobs") or {})
        values[quality_job_id] = quality_job
        self._write_versioned_store(QUALITY_JOB_STORE_FILE, "qualityJobs", values)
        return quality_job

    def get_quality_job(self, quality_job_id: str) -> dict[str, Any] | None:
        return dict((self._read_versioned_store(QUALITY_JOB_STORE_FILE, "qualityJobs").get("qualityJobs") or {}).get(str(quality_job_id)) or {}) or None

    def list_quality_jobs(self, *, status: str | None = None) -> list[dict[str, Any]]:
        jobs = list((self._read_versioned_store(QUALITY_JOB_STORE_FILE, "qualityJobs").get("qualityJobs") or {}).values())
        normalized_status = str(status or "").strip().lower()
        result = [dict(item) for item in jobs if not normalized_status or str(item.get("status") or "").lower() == normalized_status]
        result.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return result

    def _quality_checks_for_artifact(self, artifact: dict[str, Any], owner: dict[str, Any], *, warnings: list[str], failures: list[str]) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        path = str(artifact.get("sourcePath") or artifact.get("source_path") or "").strip()
        if not path and artifact.get("artifactId"):
            path = self._artifact_source_path(str(artifact.get("artifactId") or ""))
        exists = bool(path and Path(path).exists() and Path(path).is_file())
        checks.append({"name": "artifact_openable", "ok": exists, "path": path})
        if not exists:
            failures.append("artifact_not_openable")
            return checks
        size_bytes = Path(path).stat().st_size
        checks.append({"name": "artifact_non_empty", "ok": size_bytes > 0, "bytes": size_bytes})
        if size_bytes <= 0:
            failures.append("artifact_empty")
        kind = str(artifact.get("kind") or owner.get("modality") or "").lower()
        suffix = Path(path).suffix.lower()
        if kind == "image" or suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            checks.extend(self._quality_image_checks(path, owner, warnings=warnings, failures=failures))
        elif kind in {"video", "audio"} or suffix in VIDEO_EXTENSIONS or suffix in AUDIO_EXTENSIONS:
            checks.extend(self._quality_media_probe_checks(path, owner, warnings=warnings, failures=failures, kind=kind or ("video" if suffix in VIDEO_EXTENSIONS else "audio")))
        elif suffix == ".srt":
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            ok = "-->" in text and bool(text.strip())
            checks.append({"name": "subtitle_timeline_present", "ok": ok})
            if not ok:
                warnings.append("subtitle_timeline_missing")
        return checks

    def _quality_image_checks(self, path: str, owner: dict[str, Any], *, warnings: list[str], failures: list[str]) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
            checks.append({"name": "image_dimensions", "ok": width > 0 and height > 0, "width": width, "height": height})
            expected_ratio = str((owner.get("request") or owner).get("ratio") or (owner.get("request") or owner).get("aspectRatio") or "").strip()
            if expected_ratio and ":" in expected_ratio:
                left, right = expected_ratio.split(":", 1)
                expected = float(left) / max(1.0, float(right))
                actual = width / max(1, height)
                ok = abs(actual - expected) <= 0.08
                checks.append({"name": "image_aspect_ratio", "ok": ok, "expected": expected_ratio, "actual": round(actual, 3)})
                if not ok:
                    warnings.append("image_aspect_ratio_mismatch")
        except Exception as exc:
            checks.append({"name": "image_metadata_readable", "ok": False, "error": _exception_summary(exc)})
            warnings.append("image_metadata_unavailable")
        return checks

    def _quality_media_probe_checks(self, path: str, owner: dict[str, Any], *, warnings: list[str], failures: list[str], kind: str) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        ffprobe = shutil.which("ffprobe")
        if not ffprobe:
            checks.append({"name": "ffprobe_available", "ok": False})
            warnings.append("ffprobe_unavailable")
            return checks
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            ok = result.returncode == 0
            checks.append({"name": "ffprobe_readable", "ok": ok})
            if not ok:
                failures.append("ffprobe_failed")
                return checks
            payload = json.loads(result.stdout or "{}")
            duration = float(((payload.get("format") or {}).get("duration") or 0) or 0)
            checks.append({"name": "media_duration_positive", "ok": duration > 0, "durationSeconds": round(duration, 3)})
            if duration <= 0:
                failures.append("duration_missing")
            request = dict(owner.get("request") or owner)
            expected_duration = request.get("duration") or request.get("durationSeconds")
            if expected_duration:
                expected = float(expected_duration)
                tolerance = max(1.0, expected * 0.35)
                duration_ok = abs(duration - expected) <= tolerance
                checks.append({"name": "media_duration_close_to_request", "ok": duration_ok, "expected": expected, "actual": round(duration, 3)})
                if not duration_ok:
                    warnings.append("duration_mismatch")
            streams = list(payload.get("streams") or [])
            if kind == "video":
                video_stream = next((item for item in streams if item.get("codec_type") == "video"), {})
                width = int(video_stream.get("width") or 0)
                height = int(video_stream.get("height") or 0)
                checks.append({"name": "video_dimensions", "ok": width > 0 and height > 0, "width": width, "height": height})
                if width <= 0 or height <= 0:
                    failures.append("video_dimensions_missing")
            if kind == "audio":
                audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
                checks.append({"name": "audio_stream_present", "ok": bool(audio_stream)})
                if not audio_stream:
                    failures.append("audio_stream_missing")
        except Exception as exc:
            checks.append({"name": "ffprobe_exception", "ok": False, "error": _exception_summary(exc)})
            warnings.append("ffprobe_exception")
        return checks

    def _retry_recommendation(self, *, status: str, failures: list[str], warnings: list[str], job: dict[str, Any]) -> dict[str, Any]:
        if status == "passed":
            return {"action": "accept", "reason": "quality gates passed"}
        if "artifact_not_openable" in failures or "ffprobe_failed" in failures:
            return {"action": "retry_same_operation", "reason": "artifact fetch or media probe failed"}
        if "duration_mismatch" in warnings:
            return {"action": "adjust_parameters", "reason": "requested duration differs from output"}
        return {"action": "manual_review" if status == "warning" else "retry_same_operation", "reason": ",".join([*failures, *warnings])}

    def _read_jobs(self) -> dict[str, Any]:
        payload = storage.read_json(JOB_STORE_FILE)
        if not isinstance(payload, dict) or payload.get("version") != 1:
            return {"version": 1, "jobs": {}}
        payload.setdefault("jobs", {})
        return payload

    def _write_jobs(self, payload: dict[str, Any]) -> None:
        storage.write_json(JOB_STORE_FILE, {"version": 1, "jobs": dict(payload.get("jobs") or {})})

    def _save_job(self, job: dict[str, Any]) -> dict[str, Any]:
        if job.get("status") in {"succeeded", "failed", "cancelled"} and not job.get("p4RecordedAt"):
            try:
                self._record_terminal_job_observations(job)
            except Exception as exc:
                job["p4ObservationError"] = _exception_summary(exc)
            job["p4RecordedAt"] = utc_now_iso()
        payload = self._read_jobs()
        jobs = dict(payload.get("jobs") or {})
        job["updatedAt"] = utc_now_iso()
        jobs[str(job["jobId"])] = job
        payload["jobs"] = jobs
        self._write_jobs(payload)
        return job

    def _scope_fields(self, request: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, str]:
        fallback = fallback or {}
        scope = {
            "projectId": str(request.get("projectId") or request.get("project_id") or fallback.get("projectId") or "").strip(),
            "workspaceId": str(request.get("workspaceId") or request.get("workspace_id") or fallback.get("workspaceId") or "").strip(),
            "workspacePath": str(request.get("workspacePath") or request.get("workspace_path") or fallback.get("workspacePath") or "").strip(),
        }
        return self._ensure_project_workspace_registered(scope)

    def _ensure_project_workspace_registered(self, scope: dict[str, str]) -> dict[str, str]:
        workspace_path = str(scope.get("workspacePath") or "").strip()
        if not workspace_path or not (str(scope.get("projectId") or "").strip() or str(scope.get("workspaceId") or "").strip()):
            return scope
        project_id = str(scope.get("projectId") or "").strip() or _safe_filename(Path(workspace_path).name, "creative-media-project")
        workspace_id = str(scope.get("workspaceId") or "").strip() or project_id
        scope["projectId"] = project_id
        scope["workspaceId"] = workspace_id
        try:
            from runtimes.memory.project_registry import project_registry_service

            existing = project_registry_service.find_project_for_workspace(
                workspace_id=workspace_id,
                workspace_path=workspace_path,
            )
            if existing:
                return {
                    **scope,
                    "projectId": str(existing.project_id or project_id),
                    "workspaceId": str(existing.workspace_id or workspace_id),
                    "workspacePath": str(existing.workspace_path or workspace_path),
                }
            project_registry_service.save_project(
                {
                    "id": project_id,
                    "name": Path(workspace_path).name or project_id,
                    "workspaceId": workspace_id,
                    "workspacePath": workspace_path,
                    "tags": ["creative_media"],
                    "active": True,
                }
            )
        except Exception:
            return scope
        return scope

    def _input_asset_types_for_operation(self, operation_kind: str) -> list[str]:
        mapping = {
            "image.generate": [],
            "image.edit": ["image_url", "image_artifact_public_url"],
            "video.text_to_video": [],
            "video.image_to_video": ["image_url"],
            "video.first_last_frame": ["first_frame_url", "last_frame_url"],
            "video.reference_to_video": ["reference_image_url", "reference_video_url"],
            "video.video_edit": ["video_url", "image_url"],
            "video.style_repaint": ["video_url", "reference_image_url"],
            "video.lipsync": ["video_or_image_url", "audio_url"],
            "video.avatar": ["image_url", "audio_url"],
            "video.action_transfer": ["target_image_url", "reference_video_url"],
            "video.replacement": ["source_video_url", "target_image_url", "reference_video_url"],
        }
        return list(mapping.get(str(operation_kind or ""), []))

    def _prepare_prompt_for_provider(self, request: dict[str, Any], *, modality: str) -> tuple[str, dict[str, Any]]:
        prompt = str(request.get("prompt") or request.get("text") or request.get("brief") or "").strip()
        if not prompt:
            return "", {}
        policy = prepare_provider_prompt_policy(prompt, modality=modality)
        return str(policy.get("translatedPrompt") or prompt).strip(), policy

    def _provider_request_hash(self, payload: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(_jsonable_request(payload), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    def _record_safety_event(self, *, source: str, job: dict[str, Any] | None = None, recipe: dict[str, Any] | None = None, transform: dict[str, Any] | None = None) -> None:
        transform = dict(transform or {})
        events = list(transform.get("events") or [])
        if not events:
            return
        store = self._read_versioned_store(SAFETY_EVENTS_STORE_FILE, "events")
        values = dict(store.get("events") or {})
        now = utc_now_iso()
        event_id = f"cm_safety_{uuid.uuid4().hex}"
        values[event_id] = {
            "eventId": event_id,
            "source": source,
            "jobId": (job or {}).get("jobId"),
            "recipeId": (recipe or {}).get("recipeId"),
            "modality": (job or recipe or {}).get("modality"),
            "operationKind": (job or {}).get("operationKind"),
            "policy": transform.get("policy"),
            "rawPromptHash": hashlib.sha256(str(transform.get("rawPrompt") or "").encode("utf-8")).hexdigest(),
            "sanitizedPrompt": transform.get("sanitizedPrompt"),
            "events": events,
            "createdAt": now,
        }
        self._write_versioned_store(SAFETY_EVENTS_STORE_FILE, "events", values)

    def list_safety_events(self) -> list[dict[str, Any]]:
        events = list((self._read_versioned_store(SAFETY_EVENTS_STORE_FILE, "events").get("events") or {}).values())
        events.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return [dict(item) for item in events]

    def _looks_like_policy_reject(self, error: Any) -> bool:
        text = str(error or "").lower()
        return any(marker.lower() in text for marker in POLICY_REJECT_MARKERS)

    def _downgrade_policy_prompt(self, prompt: str) -> str:
        policy = prepare_provider_prompt_policy(prompt, modality="image")
        base = str(policy.get("translatedPrompt") or prompt)
        return (
            base
            + "\nMake the result clearly original and non-infringing. Remove any remaining franchise, brand, celebrity, or protected identity references. "
            "Lower similarity to any known character and use generic descriptive traits only."
        )

    def _new_job(self, *, modality: str, adapter: str, request: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        operation_kind = str(request.get("operationKind") or request.get("operation_kind") or self._operation_kind_for_request(modality, request)).strip()
        return {
            "jobId": f"cm_{uuid.uuid4().hex}",
            **self._scope_fields(request),
            "modality": modality,
            "adapter": adapter,
            "operationKind": operation_kind,
            "status": "queued",
            "request": _jsonable_request(request),
            "providerTaskId": None,
            "providerRequestHash": None,
            "fallbackAttempts": [],
            "retryReason": str(request.get("retryReason") or request.get("retry_reason") or "").strip(),
            "policyRejectReason": "",
            "qualityStatus": "not_run",
            "qualityJobIds": [],
            "artifacts": [],
            "error": None,
            "providerResponse": {},
            "createdAt": now,
            "updatedAt": now,
            "completedAt": None,
        }

    def get_job(self, job_id: str, *, refresh: bool = True) -> dict[str, Any] | None:
        job = (self._read_jobs().get("jobs") or {}).get(str(job_id))
        if not job:
            return None
        return job

    async def refresh_job(self, job_id: str) -> dict[str, Any] | None:
        job = self.get_job(job_id, refresh=False)
        if not job:
            return None
        if job.get("status") in {"succeeded", "failed", "cancelled"}:
            return job
        if job.get("adapter") == "volcengine_ark" and job.get("modality") == "video":
            return await self._poll_volcengine_video_job(job)
        if job.get("adapter") == "agnes_video" and job.get("providerTaskId"):
            return await self._poll_agnes_video_job(job)
        if job.get("adapter") == "dashscope" and job.get("providerTaskId"):
            return await self._poll_dashscope_task(job)
        return job

    def job_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        job = self.get_job(job_id, refresh=False) or {}
        return [dict(item) for item in list(job.get("artifacts") or []) if isinstance(item, dict)]

    def list_jobs(self, *, modality: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        jobs = list((self._read_jobs().get("jobs") or {}).values())
        normalized_modality = str(modality or "").strip().lower()
        normalized_status = str(status or "").strip().lower()
        result: list[dict[str, Any]] = []
        for job in jobs:
            if normalized_modality and str(job.get("modality") or "").strip().lower() != normalized_modality:
                continue
            if normalized_status and str(job.get("status") or "").strip().lower() != normalized_status:
                continue
            result.append(dict(job))
        result.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        return result

    async def create_job(self, request: dict[str, Any]) -> dict[str, Any]:
        modality = str(request.get("modality") or "").strip().lower()
        if modality not in SUPPORTED_MODALITIES:
            raise ValueError(f"Unsupported creative media modality: {modality or 'missing'}")
        if modality not in EXECUTABLE_MODALITIES:
            job = self._new_job(modality=modality, adapter="catalog_only", request=request)
            job["status"] = "failed"
            job["error"] = f"{modality} is catalog-only in P2; runtime execution is reserved for a later phase."
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        operation_kind = self._operation_kind_for_request(modality, request)
        if not self._has_explicit_model_selection(request):
            preferred = self._preferred_model_candidates(operation_kind)
            if preferred:
                return await self._create_job_with_model_fallback(modality, operation_kind, request, preferred)
            if self._all_model_candidates_for_operation(operation_kind):
                job = self._new_job(modality=modality, adapter="operation_unavailable", request={**request, "operationKind": operation_kind})
                job["status"] = "failed"
                job["error"] = f"No enabled executable Creative Media model candidate is available for operationKind={operation_kind}"
                job["completedAt"] = utc_now_iso()
                return self._save_job(job)
            if operation_kind not in EXECUTABLE_OPERATION_KINDS:
                job = self._new_job(modality=modality, adapter="operation_unsupported", request={**request, "operationKind": operation_kind})
                job["status"] = "failed"
                job["error"] = f"No enabled Creative Media model candidate supports operationKind={operation_kind}"
                job["completedAt"] = utc_now_iso()
                return self._save_job(job)
        if modality == "image":
            return await self._create_image_job(request)
        if modality == "video":
            return await self._create_video_job(request)
        if modality == "voice":
            return await self._create_voice_job(request)
        raise ValueError(f"Unsupported creative media modality: {modality}")

    async def retry_job(self, job_id: str, request: dict[str, Any] | None = None) -> dict[str, Any]:
        original = self.get_job(job_id, refresh=False)
        if not original:
            raise ValueError(f"Creative Media job not found: {job_id}")
        payload = {**dict(original.get("request") or {}), **dict(request or {})}
        payload["retryOfJobId"] = job_id
        payload["operationKind"] = original.get("operationKind") or payload.get("operationKind")
        payload["modality"] = original.get("modality") or payload.get("modality")
        if original.get("policyRejectReason") or self._looks_like_policy_reject(original.get("error")):
            payload["prompt"] = self._downgrade_policy_prompt(str(payload.get("prompt") or ""))
            payload["retryReason"] = "policy_reject_prompt_downgrade"
        else:
            payload["retryReason"] = str(payload.get("retryReason") or "quality_or_provider_retry")
        return await self.create_job(payload)

    async def _create_job_with_model_fallback(
        self,
        modality: str,
        operation_kind: str,
        request: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        attempts: list[dict[str, Any]] = []
        last_job: dict[str, Any] | None = None
        for candidate in candidates:
            attempt_request = self._request_for_candidate(request, candidate)
            try:
                if modality == "image":
                    job = await self._create_image_job(attempt_request)
                elif modality == "video":
                    job = await self._create_video_job(attempt_request)
                elif modality == "voice":
                    job = await self._create_voice_job(attempt_request)
                else:
                    raise ValueError(f"Unsupported fallback modality: {modality}")
            except Exception as exc:
                attempts.append(
                    {
                        "candidateId": candidate.get("candidateId"),
                        "operationKind": operation_kind,
                        "providerId": candidate.get("providerId"),
                        "modelId": candidate.get("modelId"),
                        "adapter": candidate.get("adapter"),
                        "status": "failed",
                        "error": _exception_summary(exc),
                    }
                )
                continue
            last_job = job
            attempts.append(
                {
                    "candidateId": candidate.get("candidateId"),
                    "operationKind": operation_kind,
                    "providerId": candidate.get("providerId"),
                    "modelId": candidate.get("modelId"),
                    "adapter": candidate.get("adapter"),
                    "status": job.get("status"),
                    "error": job.get("error"),
                }
            )
            if job.get("status") != "failed":
                job["selectedModelCandidate"] = {
                    "candidateId": candidate.get("candidateId"),
                    "operationKind": operation_kind,
                    "providerId": candidate.get("providerId"),
                    "modelId": candidate.get("modelId"),
                    "adapter": candidate.get("adapter"),
                }
                job["fallbackAttempts"] = attempts
                return self._save_job(job)
        if last_job:
            last_job["fallbackAttempts"] = attempts
            return self._save_job(last_job)
        job = self._new_job(modality=modality, adapter="fallback", request={**request, "operationKind": operation_kind})
        job["status"] = "failed"
        job["error"] = "No enabled Creative Media model candidate succeeded"
        job["fallbackAttempts"] = attempts
        job["completedAt"] = utc_now_iso()
        return self._save_job(job)

    async def _create_image_job(self, request: dict[str, Any]) -> dict[str, Any]:
        operation_kind = self._operation_kind_for_request("image", request)
        if operation_kind not in {"image.generate", "image.edit"}:
            job = self._new_job(modality="image", adapter="operation_unsupported", request={**request, "operationKind": operation_kind})
            job["status"] = "failed"
            job["error"] = f"Creative Media P4 has not implemented executable image operationKind={operation_kind}; compile an editIntent recipe first."
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        adapter = str(request.get("adapter") or "").strip().lower()
        provider_id = str(request.get("providerId") or request.get("provider_id") or "").strip()
        if not adapter:
            if "agnes" in provider_id.lower():
                adapter = "agnes_images"
            elif any(token in provider_id.lower() for token in ("dashscope", "aliyun", "bailian")):
                adapter = "dashscope"
            else:
                adapter = "volcengine_ark" if "volc" in provider_id.lower() or str(request.get("provider") or "").lower() in {"volcengine", "seedream"} else "openai_images"
        provider_prompt, prompt_policy = self._prepare_prompt_for_provider(request, modality="image")
        prepared_request = {**request, "prompt": provider_prompt, "operationKind": operation_kind}
        if prompt_policy:
            prepared_request["promptPolicy"] = prompt_policy
        job = self._new_job(modality="image", adapter=adapter, request=prepared_request)
        self._record_safety_event(source="job_create", job=job, transform=dict(prompt_policy.get("safetyTransform") or {}) if prompt_policy else {})
        self._save_job(job)
        try:
            if adapter == "volcengine_ark":
                return await self._run_volcengine_image_job(job, prepared_request)
            if adapter == "dashscope":
                return await self._run_dashscope_image_job(job, prepared_request)
            if adapter == "openai_images":
                if operation_kind == "image.edit":
                    return await self._run_openai_image_edit_job(job, prepared_request)
                return await self._run_openai_image_job(job, prepared_request)
            if adapter == "agnes_images":
                return await self._run_agnes_image_job(job, prepared_request)
            raise ValueError(f"Unsupported image adapter: {adapter}")
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            if self._looks_like_policy_reject(job["error"]):
                job["policyRejectReason"] = job["error"]
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)

    async def _create_video_job(self, request: dict[str, Any]) -> dict[str, Any]:
        operation_kind = self._operation_kind_for_request("video", request)
        if operation_kind not in EXECUTABLE_OPERATION_KINDS:
            job = self._new_job(modality="video", adapter="operation_unsupported", request={**request, "operationKind": operation_kind})
            job["status"] = "failed"
            job["error"] = f"Creative Media P4 has not implemented executable video operationKind={operation_kind}; keep it as recipe/catalog planning until an adapter is added."
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        requested_provider_id = str(request.get("providerId") or request.get("provider_id") or "").strip().lower()
        adapter = str(request.get("adapter") or ("agnes_video" if "agnes" in requested_provider_id else "volcengine_ark")).strip().lower()
        if operation_kind not in {"video.text_to_video", "video.image_to_video", "video.first_last_frame"} and adapter == "volcengine_ark":
            adapter = "dashscope"
        provider_prompt, prompt_policy = self._prepare_prompt_for_provider(request, modality="video")
        prepared_request = {**request, "prompt": provider_prompt, "operationKind": operation_kind}
        if prompt_policy:
            prepared_request["promptPolicy"] = prompt_policy
        job = self._new_job(modality="video", adapter=adapter, request=prepared_request)
        self._record_safety_event(source="job_create", job=job, transform=dict(prompt_policy.get("safetyTransform") or {}) if prompt_policy else {})
        self._save_job(job)
        try:
            if adapter == "volcengine_ark":
                if operation_kind not in {"video.text_to_video", "video.image_to_video", "video.first_last_frame"}:
                    raise ValueError(f"Volcengine adapter does not support operationKind={operation_kind}")
                job = await self._submit_volcengine_video_job(job, prepared_request)
            elif adapter == "dashscope":
                job = await self._submit_dashscope_video_job(job, prepared_request)
            elif adapter == "agnes_video":
                job = await self._submit_agnes_video_job(job, prepared_request)
            else:
                raise ValueError(f"Unsupported video adapter: {adapter}")
            if bool(request.get("wait", False)):
                timeout_seconds = max(15, min(int(request.get("timeoutSeconds") or request.get("timeout_seconds") or 240), 60))
                poll_interval = max(2, min(int(request.get("pollIntervalSeconds") or request.get("poll_interval_seconds") or 8), 30))
                deadline = asyncio.get_event_loop().time() + timeout_seconds
                while job.get("status") not in {"succeeded", "failed", "cancelled"} and asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(poll_interval)
                    refreshed = await self.refresh_job(str(job.get("jobId") or ""))
                    if refreshed:
                        job = refreshed
                if job.get("status") not in {"succeeded", "failed", "cancelled"}:
                    job["status"] = "running"
                    with ToolExecutionEnvelope(tool_name="creative_media_create_job", family="creative_media", deadline_ms=timeout_seconds * 1000, retry_limit=1) as envelope:
                        job["toolExecution"] = envelope.payload(
                            ok=False,
                            failure_class="deadline_exceeded",
                            retryable=False,
                            recommended_next_action="返回 running job；使用 creative_media_get_job 或 creative_media_list_jobs 观察后续状态。",
                        )
                    job["recommendedNextAction"] = "observe_job"
                    self._save_job(job)
            return job
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            if self._looks_like_policy_reject(job["error"]):
                job["policyRejectReason"] = job["error"]
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)

    async def _create_voice_job(self, request: dict[str, Any]) -> dict[str, Any]:
        operation_kind = self._operation_kind_for_request("voice", request)
        if operation_kind != "voice.tts":
            job = self._new_job(modality="voice", adapter="operation_unsupported", request={**request, "operationKind": operation_kind})
            job["status"] = "failed"
            job["error"] = f"Unsupported voice operationKind={operation_kind}"
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        job = self._new_job(modality="voice", adapter="v8_audio_tts", request=request)
        self._save_job(job)
        try:
            text = str(request.get("text") or request.get("prompt") or "").strip()
            if not text:
                raise ValueError("voice job requires text")
            provider = TTSManager.get_provider()
            audio = bytearray()
            async for chunk in provider.synthesize_stream(text):
                if chunk:
                    audio.extend(chunk)
            if not audio:
                raise RuntimeError("TTS provider returned no audio bytes")
            path = self._output_path(job, "voice", ".mp3")
            path.write_bytes(bytes(audio))
            artifact = self._record_local_artifact(
                file_path=path,
                job=job,
                kind="audio",
                mime_type="audio/mpeg",
                metadata={"provider": "v8_audio_tts", "origin": "provider_result", "textLength": len(text)},
            )
            job["status"] = "succeeded"
            job["artifacts"] = [artifact]
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)

    def _mcp_volc_env(self) -> dict[str, str]:
        config = storage.read_json("config.json")
        servers = (((config.get("mcp") or {}).get("mcpServers") or {}) if isinstance(config, dict) else {})
        env = dict(((servers.get("jimeng_visual_generation") or {}).get("env") or {}))
        return {str(k): str(v) for k, v in env.items() if v is not None}

    def _volc_credentials(self) -> dict[str, str]:
        env = self._mcp_volc_env()
        api_key = str(os.getenv("VOLC_API_KEY") or env.get("VOLC_API_KEY") or "").strip()
        return {
            "apiKey": api_key,
            "baseUrl": str(os.getenv("VOLC_BASE_URL") or "https://ark.cn-beijing.volces.com/api/v3").rstrip("/"),
            "imageModel": str(os.getenv("VOLC_IMAGE_MODEL") or env.get("VOLC_IMAGE_MODEL") or "doubao-seedream-4-0-250828"),
            "videoModel": str(os.getenv("VOLC_VIDEO_MODEL") or env.get("VOLC_VIDEO_MODEL") or "doubao-seedance-1-0-pro-fast-251015"),
        }

    def _dashscope_credentials(self) -> dict[str, str]:
        return {
            "apiKey": str(os.getenv("DASHSCOPE_API_KEY") or "").strip(),
            "baseUrl": str(os.getenv("DASHSCOPE_BASE_URL") or "https://dashscope.aliyuncs.com/api/v1").rstrip("/"),
        }

    def _public_url_or_error(self, value: Any, *, field_name: str) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        raise ValueError(f"{field_name} must be a public HTTP/HTTPS URL for live provider calls; register or upload the local artifact to an accessible URL first")

    def _image_urls_from_request(self, request: dict[str, Any]) -> list[str]:
        urls = request.get("imageUrls") or request.get("image_urls") or []
        if isinstance(urls, str):
            urls = [urls]
        result = [
            self._public_url_or_error(url, field_name="imageUrls")
            for url in list(urls or [])
            if str(url or "").strip()
        ]
        for key in ("imageUrl", "image_url", "sourceImageUrl", "source_image_url", "firstFrame", "first_frame", "lastFrame", "last_frame", "referenceImageUrl", "reference_image_url"):
            url = self._public_url_or_error(request.get(key), field_name=key)
            if url:
                result.append(url)
        seen: set[str] = set()
        unique: list[str] = []
        for url in result:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique

    def _video_url_from_request(self, request: dict[str, Any], *keys: str) -> str:
        for key in keys or ("videoUrl", "video_url"):
            url = self._public_url_or_error(request.get(key), field_name=key)
            if url:
                return url
        return ""

    def _dashscope_headers(self, api_key: str, *, async_task: bool = False) -> dict[str, str]:
        headers = self._bearer_headers(api_key)
        if async_task:
            headers["X-DashScope-Async"] = "enable"
        return headers

    def _dashscope_task_path_for_operation(self, operation_kind: str) -> str:
        if operation_kind == "video.action_transfer":
            return "/services/aigc/image2video/video-synthesis"
        if operation_kind in {"video.lipsync", "video.avatar"}:
            return "/services/aigc/video-generation/video-synthesis"
        if operation_kind in {"video.video_edit", "video.style_repaint", "video.replacement"}:
            return "/services/aigc/video-generation/video-synthesis"
        if operation_kind in {"video.image_to_video", "video.first_last_frame", "video.reference_to_video"}:
            return "/services/aigc/image2video/video-synthesis"
        return "/services/aigc/video-generation/video-synthesis"

    def _dashscope_result_url(self, response: dict[str, Any]) -> str:
        output = dict(response.get("output") or {})
        results = output.get("results")
        if isinstance(results, dict):
            for key in ("video_url", "url", "image_url"):
                if results.get(key):
                    return str(results[key])
        if isinstance(results, list) and results:
            first = dict(results[0] or {})
            for key in ("video_url", "url", "image_url"):
                if first.get(key):
                    return str(first[key])
        for key in ("video_url", "image_url", "url"):
            if output.get(key):
                return str(output[key])
        choices = output.get("choices")
        if isinstance(choices, list) and choices:
            content = (((choices[0] or {}).get("message") or {}).get("content") or [])
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and (item.get("image") or item.get("video")):
                        return str(item.get("image") or item.get("video"))
        return ""

    def _configured_provider_for_model(self, request: dict[str, Any], *, default_model: str) -> tuple[str, dict[str, Any], str]:
        config = model_control_plane.get_config()
        providers = dict(config.get("providers") or {})
        requested_provider = str(request.get("providerId") or request.get("provider_id") or "").strip()
        requested_model = str(request.get("model") or request.get("modelId") or request.get("model_id") or default_model).strip()
        candidates: Iterable[tuple[str, dict[str, Any]]] = providers.items()
        if requested_provider:
            provider = providers.get(requested_provider)
            if not provider:
                raise ValueError(f"Provider not found: {requested_provider}")
            candidates = [(requested_provider, provider)]
        preferred: list[tuple[str, dict[str, Any]]] = []
        fallback: list[tuple[str, dict[str, Any]]] = []
        for provider_id, provider_data in candidates:
            models = dict((provider_data or {}).get("models") or {})
            if requested_model not in models:
                continue
            provider_meta = dict((provider_data or {}).get("provider") or {})
            name = str(provider_meta.get("name") or provider_id).lower()
            target = preferred if "local2" in name or "local2" in provider_id.lower() else fallback
            target.append((provider_id, provider_data))
        selected = (preferred or fallback)
        if not selected:
            raise ValueError(f"No configured provider exposes model: {requested_model}")
        provider_id, provider_data = selected[0]
        model_data = dict(((provider_data or {}).get("models") or {}).get(requested_model) or {})
        return provider_id, dict((provider_data or {}).get("provider") or {}), self._provider_model_id(requested_model, model_data)

    @staticmethod
    def _provider_model_id(model_id: str, model_data: dict[str, Any] | None = None) -> str:
        media_limits = dict((model_data or {}).get("mediaLimits") or {})
        provider_model_id = str(media_limits.get("providerModelId") or "").strip()
        if provider_model_id:
            return provider_model_id
        return str(model_id or "")

    def _openai_image_provider(self, request: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        return self._configured_provider_for_model(request, default_model="gpt-image-2")

    async def _run_openai_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, model = self._openai_image_provider(request)
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("image job requires prompt")
        size = resolve_image_size(
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "1:1"),
            preset=str(request.get("preset") or "1K"),
            adapter="openai_images",
            explicit_size=request.get("size"),
        )
        response_format = str(request.get("responseFormat") or request.get("response_format") or "b64_json")
        payload = _build_openai_image_payload(model=model, prompt=prompt, size=size, response_format=response_format)
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            f"{base_url}/images/generations",
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=180,
        )
        artifact = await self._artifact_from_image_response(response, job=job, provider=provider_id, model=model, mime_hint="image/png")
        job.update({"status": "succeeded", "artifacts": [artifact], "providerResponse": {"providerId": provider_id, "model": model, "size": size, "usage": response.get("usage") or {}}, "completedAt": utc_now_iso()})
        return self._save_job(job)

    async def _run_agnes_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, model = self._configured_provider_for_model(
            request,
            default_model="agnes-image-2.1-flash",
        )
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Agnes image job requires prompt")
        operation_kind = str(job.get("operationKind") or self._operation_kind_for_request("image", request))
        image_urls = self._image_urls_from_request(request)
        if operation_kind == "image.edit" and not image_urls:
            raise ValueError("Agnes image.edit requires a public imageUrls/referenceImageUrl input")
        size = resolve_image_size(
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "1:1"),
            preset=str(request.get("preset") or "1K"),
            adapter="openai_images",
            explicit_size=request.get("size"),
        )
        response_format = str(request.get("responseFormat") or request.get("response_format") or "url").strip().lower()
        payload = _build_agnes_image_payload(
            model=model,
            prompt=prompt,
            size=size,
            response_format=response_format,
            image_urls=image_urls or None,
        )
        job["providerRequestHash"] = self._provider_request_hash(payload)
        endpoint = f"{base_url}/images/generations" if base_url.endswith("/v1") else f"{base_url}/v1/images/generations"
        response = await self._request_json(
            "POST",
            endpoint,
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=180,
        )
        artifact = await self._artifact_from_image_response(
            response,
            job=job,
            provider=provider_id,
            model=model,
            mime_hint="image/png",
        )
        job.update(
            {
                "status": "succeeded",
                "artifacts": [artifact],
                "providerResponse": {
                    "providerId": provider_id,
                    "model": model,
                    "size": size,
                    "operationKind": operation_kind,
                },
                "completedAt": utc_now_iso(),
            }
        )
        return self._save_job(job)

    async def _run_openai_image_edit_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, model = self._openai_image_provider(request)
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        prompt = str(request.get("prompt") or "").strip()
        image_path = str(request.get("imagePath") or request.get("image_path") or request.get("sourcePath") or request.get("source_path") or "").strip()
        if not image_path and request.get("artifactId"):
            image_path = self._artifact_source_path(str(request.get("artifactId") or ""))
        if not prompt:
            raise ValueError("image edit job requires prompt")
        if not image_path or not Path(image_path).exists():
            raise ValueError("OpenAI-compatible image edit requires a local imagePath/sourcePath or artifactId with a source file")
        size = resolve_image_size(
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "1:1"),
            preset=str(request.get("preset") or "1K"),
            adapter="openai_images",
            explicit_size=request.get("size"),
        )
        data = {"model": model, "prompt": prompt, "size": size}
        job["providerRequestHash"] = self._provider_request_hash(data)
        with open(image_path, "rb") as image_file:
            files = {"image": (Path(image_path).name, image_file, mimetypes.guess_type(image_path)[0] or "image/png")}
            response = await self._request_multipart_json(
                "POST",
                f"{base_url}/images/edits",
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                data=data,
                files=files,
                timeout=300,
            )
        artifact = await self._artifact_from_image_response(response, job=job, provider=provider_id, model=model, mime_hint="image/png")
        job.update({"status": "succeeded", "artifacts": [artifact], "providerResponse": {"providerId": provider_id, "model": model, "size": size}, "completedAt": utc_now_iso()})
        return self._save_job(job)

    async def _run_dashscope_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        creds = self._dashscope_credentials()
        if not creds["apiKey"]:
            raise ValueError("DASHSCOPE_API_KEY is required for Alibaba Cloud Bailian / DashScope image jobs")
        operation_kind = str(job.get("operationKind") or self._operation_kind_for_request("image", request))
        model = str(request.get("model") or request.get("modelId") or ("qwen-image-2.0-pro" if operation_kind == "image.edit" else "qwen-image-2.0-pro"))
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("DashScope image job requires prompt")
        if operation_kind == "image.edit":
            content: list[dict[str, Any]] = []
            for url in self._image_urls_from_request(request)[:3]:
                content.append({"image": url})
            if not content:
                raise ValueError("DashScope image.edit requires at least one public image URL")
            content.append({"text": prompt})
            payload = {"model": model, "input": {"messages": [{"role": "user", "content": content}]}, "parameters": {"n": int(request.get("n") or 1)}}
        else:
            size = str(request.get("size") or resolve_image_size(ratio=str(request.get("ratio") or "1:1"), preset=str(request.get("preset") or "2K"))).replace("x", "*")
            payload = {
                "model": model,
                "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
                "parameters": {"size": size, "n": int(request.get("n") or 1), "watermark": bool(request.get("watermark", False))},
            }
        path = "/services/aigc/multimodal-generation/generation"
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json("POST", f"{creds['baseUrl']}{path}", headers=self._dashscope_headers(creds["apiKey"]), json=payload, timeout=300)
        result_url = self._dashscope_result_url(response)
        if not result_url:
            raise RuntimeError(f"DashScope image response did not include an image URL: {response}")
        artifact = await self._artifact_from_url(result_url, job=job, kind="image", provider="aliyun_bailian_dashscope", mime_hint="image/png", metadata={"model": model})
        job.update(
            {
                "status": "succeeded",
                "artifacts": [artifact],
                "providerResponse": {"providerId": "aliyun_bailian_dashscope", "model": model, "usage": response.get("usage") or {}, "requestId": response.get("request_id")},
                "completedAt": utc_now_iso(),
            }
        )
        return self._save_job(job)

    async def _run_volcengine_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        creds = self._volc_credentials()
        if not creds["apiKey"]:
            raise ValueError("Volcengine API key not found in jimeng_visual_generation MCP env or VOLC_API_KEY")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("image job requires prompt")
        model = str(request.get("model") or creds["imageModel"])
        size = resolve_image_size(
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "1:1"),
            preset=str(request.get("preset") or "2K"),
            adapter="volcengine_ark",
            explicit_size=request.get("size"),
        )
        payload = _build_volcengine_image_payload(
            model=model,
            prompt=prompt,
            size=size,
            response_format=str(request.get("responseFormat") or request.get("response_format") or "url"),
            seed=int(request.get("seed", -1)),
            image_urls=request.get("imageUrls") or request.get("image_urls"),
        )
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            f"{creds['baseUrl']}/images/generations",
            headers=self._bearer_headers(creds["apiKey"]),
            json=payload,
            timeout=180,
        )
        artifact = await self._artifact_from_image_response(response, job=job, provider="volcengine_seedream", model=model, mime_hint="image/png")
        job.update({"status": "succeeded", "artifacts": [artifact], "providerResponse": {"providerId": "volcengine_seedream", "model": model, "size": size, "usage": response.get("usage") or {}}, "completedAt": utc_now_iso()})
        return self._save_job(job)

    async def _submit_agnes_video_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, model = self._configured_provider_for_model(
            request,
            default_model="agnes-video-v2.0",
        )
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        api_root = base_url[:-3] if base_url.endswith("/v1") else base_url
        operation_kind = str(job.get("operationKind") or self._operation_kind_for_request("video", request))
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Agnes video job requires prompt")
        image_urls = self._image_urls_from_request(request)
        if operation_kind == "video.image_to_video" and not image_urls:
            raise ValueError("Agnes video.image_to_video requires an image URL")
        if operation_kind == "video.first_last_frame" and len(image_urls) < 2:
            raise ValueError("Agnes video.first_last_frame requires at least two image URLs")
        if operation_kind == "video.reference_to_video" and not image_urls:
            raise ValueError("Agnes video.reference_to_video requires one or more image URLs")
        duration = float(request.get("duration") or request.get("durationSeconds") or request.get("duration_seconds") or 5)
        frame_rate = max(1, min(int(request.get("frameRate") or request.get("frame_rate") or 24), 60))
        requested_frames = request.get("numFrames") or request.get("num_frames")
        num_frames = int(requested_frames) if requested_frames is not None else min(441, max(1, int(round(duration * frame_rate))))
        payload = _build_agnes_video_payload(
            model=model,
            prompt=prompt,
            operation_kind=operation_kind,
            image_urls=image_urls or None,
            width=int(request.get("width") or 1152),
            height=int(request.get("height") or 768),
            num_frames=num_frames,
            frame_rate=frame_rate,
            seed=int(request["seed"]) if request.get("seed") is not None else None,
            negative_prompt=str(request.get("negativePrompt") or request.get("negative_prompt") or ""),
            num_inference_steps=int(request.get("numInferenceSteps") or request.get("num_inference_steps"))
            if request.get("numInferenceSteps") is not None or request.get("num_inference_steps") is not None
            else None,
        )
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            f"{api_root}/v1/videos",
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=180,
        )
        task_id = str(response.get("task_id") or response.get("id") or "").strip()
        video_id = str(response.get("video_id") or "").strip()
        if not task_id and not video_id:
            raise RuntimeError(f"Agnes video response did not include task_id or video_id: {response}")
        raw_status = str(response.get("status") or "queued").strip().lower()
        job["status"] = "running" if raw_status in {"processing", "running"} else "queued"
        job["providerTaskId"] = task_id or video_id
        job["providerResponse"] = {
            "providerId": provider_id,
            "taskId": task_id,
            "videoId": video_id,
            "model": model,
            "operationKind": operation_kind,
            "seconds": response.get("seconds"),
            "size": response.get("size"),
        }
        return self._save_job(job)

    async def _poll_agnes_video_job(self, job: dict[str, Any]) -> dict[str, Any]:
        request = dict(job.get("request") or {})
        provider_id, provider_meta, model = self._configured_provider_for_model(
            request,
            default_model=str((job.get("providerResponse") or {}).get("model") or "agnes-video-v2.0"),
        )
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        api_root = base_url[:-3] if base_url.endswith("/v1") else base_url
        provider_response = dict(job.get("providerResponse") or {})
        video_id = str(provider_response.get("videoId") or "").strip()
        task_id = str(provider_response.get("taskId") or job.get("providerTaskId") or "").strip()
        if video_id:
            response = await self._request_json(
                "GET",
                f"{api_root}/agnesapi?video_id={quote(video_id)}",
                headers=self._bearer_headers(api_key),
                timeout=60,
            )
        elif task_id:
            response = await self._request_json(
                "GET",
                f"{api_root}/v1/videos/{quote(task_id)}",
                headers=self._bearer_headers(api_key),
                timeout=60,
            )
        else:
            job["status"] = "failed"
            job["error"] = "Missing Agnes video_id/task_id"
            return self._save_job(job)
        raw_status = str(response.get("status") or "").strip().lower()
        status = {
            "queued": "queued",
            "pending": "queued",
            "processing": "running",
            "running": "running",
            "completed": "succeeded",
            "success": "succeeded",
            "succeeded": "succeeded",
            "failed": "failed",
            "error": "failed",
        }.get(raw_status, "running")
        job["status"] = status
        job["providerResponse"] = {
            **provider_response,
            "lastStatus": raw_status,
            "progress": response.get("progress"),
            "seconds": response.get("seconds") or provider_response.get("seconds"),
            "size": response.get("size") or provider_response.get("size"),
        }
        if status == "succeeded":
            video_url = str(response.get("video_url") or response.get("remixed_from_video_id") or "").strip()
            if not video_url:
                job["status"] = "failed"
                job["error"] = "Agnes video task completed without a video URL"
            else:
                artifact = await self._artifact_from_url(
                    video_url,
                    job=job,
                    kind="video",
                    provider=provider_id,
                    mime_hint="video/mp4",
                    metadata={"model": model, "nativeAudio": False, "audioMode": "silent_or_external_audio"},
                )
                job["artifacts"] = [artifact]
                job["completedAt"] = utc_now_iso()
        elif status == "failed":
            job["error"] = str(response.get("error") or "Agnes video task failed")
            job["completedAt"] = utc_now_iso()
        return self._save_job(job)

    async def _submit_volcengine_video_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        creds = self._volc_credentials()
        if not creds["apiKey"]:
            raise ValueError("Volcengine API key not found in jimeng_visual_generation MCP env or VOLC_API_KEY")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt and not (request.get("imageUrls") or request.get("image_urls")):
            raise ValueError("video job requires prompt or imageUrls")
        duration = max(1, min(int(request.get("duration") or request.get("durationSeconds") or request.get("duration_seconds") or 5), 30))
        model = str(request.get("model") or creds["videoModel"])
        operation_kind = str(job.get("operationKind") or self._operation_kind_for_request("video", request))
        capability_profile = capability_profile_for_model(
            provider_id="volcengine_seedance",
            model_id=model,
            operation_kind=operation_kind,
        )
        supports_native_audio = bool(capability_profile.get("nativeAudio"))
        generate_audio = bool(request.get("generateAudio", request.get("generate_audio", supports_native_audio))) and supports_native_audio
        payload = _build_volcengine_video_payload(
            model=model,
            prompt=prompt,
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "16:9"),
            resolution=resolve_video_resolution(preset=request.get("resolutionPreset"), explicit_resolution=request.get("resolution") or "720p"),
            duration=duration,
            seed=int(request.get("seed", -1)),
            image_urls=request.get("imageUrls") or request.get("image_urls"),
            generate_audio=generate_audio,
            watermark=bool(request.get("watermark", False)),
        )
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            f"{creds['baseUrl']}/contents/generations/tasks",
            headers=self._bearer_headers(creds["apiKey"]),
            json=payload,
            timeout=180,
        )
        task_id = str(response.get("id") or response.get("task_id") or response.get("taskId") or "").strip()
        if not task_id:
            raise RuntimeError(f"Volcengine video response did not include a task id: {response}")
        job["status"] = "running"
        job["providerTaskId"] = task_id
        job["providerResponse"] = {
            "providerId": "volcengine_seedance",
            "taskId": task_id,
            "model": payload["model"],
            "operationKind": operation_kind,
            "capabilityProfile": capability_profile,
        }
        return self._save_job(job)

    async def _poll_volcengine_video_job(self, job: dict[str, Any]) -> dict[str, Any]:
        creds = self._volc_credentials()
        task_id = str(job.get("providerTaskId") or "").strip()
        if not task_id:
            job["status"] = "failed"
            job["error"] = "Missing providerTaskId"
            return self._save_job(job)
        response = await self._request_json(
            "GET",
            f"{creds['baseUrl']}/contents/generations/tasks/{task_id}",
            headers=self._bearer_headers(creds["apiKey"]),
            timeout=60,
        )
        status = normalize_provider_status(response.get("status"), provider="volcengine_seedance")
        job["status"] = status
        job["providerResponse"] = {**dict(job.get("providerResponse") or {}), "lastStatus": response.get("status"), "taskId": task_id}
        if status == "succeeded":
            video_url = (((response.get("content") or {}) if isinstance(response, dict) else {}).get("video_url") or "")
            if not video_url:
                job["status"] = "failed"
                job["error"] = "Volcengine video task succeeded without content.video_url"
            else:
                request = dict(job.get("request") or {})
                provider_response = dict(job.get("providerResponse") or {})
                capability_profile = dict(provider_response.get("capabilityProfile") or {})
                native_audio = bool(capability_profile.get("nativeAudio")) and _truthy(request.get("generateAudio", request.get("generate_audio", True)))
                artifact = await self._artifact_from_url(
                    video_url,
                    job=job,
                    kind="video",
                    provider="volcengine_seedance",
                    mime_hint="video/mp4",
                    metadata={
                        "model": (job.get("providerResponse") or {}).get("model"),
                        "nativeAudio": native_audio,
                        "audioMode": "native_generation" if native_audio else "silent",
                        "audioPreservationPolicy": "preserve_native_audio_by_default" if native_audio else "silent_or_external_audio",
                    },
                )
                job["artifacts"] = [artifact]
                job["completedAt"] = utc_now_iso()
        elif status == "failed":
            job["error"] = str((response.get("error") or {}).get("message") or response.get("error") or "Volcengine video task failed")
            job["completedAt"] = utc_now_iso()
        return self._save_job(job)

    async def _submit_dashscope_video_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        creds = self._dashscope_credentials()
        if not creds["apiKey"]:
            raise ValueError("DASHSCOPE_API_KEY is required for Alibaba Cloud Bailian / DashScope video jobs")
        operation_kind = str(job.get("operationKind") or self._operation_kind_for_request("video", request))
        model = str(request.get("model") or request.get("modelId") or (DASHSCOPE_BUILTIN_MODELS.get(operation_kind) or ["wan2.7-t2v"])[0])
        prompt = str(request.get("prompt") or "").strip()
        duration = max(1, min(int(request.get("duration") or request.get("durationSeconds") or request.get("duration_seconds") or 5), 30))
        input_payload: dict[str, Any] = {}
        if prompt:
            input_payload["prompt"] = prompt
        image_urls = self._image_urls_from_request(request)
        video_url = self._video_url_from_request(request, "videoUrl", "video_url", "sourceVideoUrl", "source_video_url")
        audio_url = self._public_url_or_error(request.get("audioUrl") or request.get("audio_url"), field_name="audioUrl")
        if operation_kind in {"video.image_to_video", "video.first_last_frame"}:
            if not image_urls:
                raise ValueError(f"DashScope {operation_kind} requires public imageUrls")
            input_payload["img_url"] = image_urls[0]
            input_payload["image_url"] = image_urls[0]
            if len(image_urls) > 1:
                input_payload["end_image_url"] = image_urls[1]
        elif operation_kind == "video.reference_to_video":
            ref_image = self._public_url_or_error(request.get("referenceImageUrl") or request.get("reference_image_url"), field_name="referenceImageUrl")
            ref_video = self._public_url_or_error(request.get("referenceVideoUrl") or request.get("reference_video_url"), field_name="referenceVideoUrl")
            if ref_image:
                input_payload["image_url"] = ref_image
            if ref_video:
                input_payload["video_url"] = ref_video
            if not (ref_image or ref_video):
                raise ValueError("DashScope video.reference_to_video requires referenceImageUrl or referenceVideoUrl")
        elif operation_kind == "video.action_transfer":
            target_image = image_urls[0] if image_urls else self._public_url_or_error(request.get("targetImageUrl") or request.get("target_image_url"), field_name="targetImageUrl")
            reference_video = self._public_url_or_error(
                request.get("referenceVideoUrl") or request.get("reference_video_url") or request.get("actionVideoUrl") or request.get("action_video_url"),
                field_name="referenceVideoUrl",
            )
            if not target_image or not reference_video:
                raise ValueError("DashScope video.action_transfer requires target image URL and reference video URL")
            input_payload.update({"image_url": target_image, "video_url": reference_video, "watermark": bool(request.get("watermark", False))})
        elif operation_kind in {"video.lipsync", "video.avatar"}:
            subject_url = video_url or (image_urls[0] if image_urls else "")
            if not subject_url or not audio_url:
                raise ValueError(f"DashScope {operation_kind} requires subject video/image URL and audioUrl")
            input_payload.update({"video_url": subject_url, "audio_url": audio_url})
            if prompt:
                input_payload["text"] = prompt
        elif operation_kind in {"video.video_edit", "video.style_repaint", "video.replacement"}:
            if not video_url:
                raise ValueError(f"DashScope {operation_kind} requires videoUrl")
            input_payload["video_url"] = video_url
            if image_urls:
                input_payload["image_url"] = image_urls[0]
        parameters = {
            "resolution": str(request.get("resolution") or "720P"),
            "duration": duration,
            "ratio": str(request.get("ratio") or request.get("aspectRatio") or "16:9"),
        }
        if operation_kind == "video.action_transfer":
            parameters = {"mode": str(request.get("mode") or "wan-std")}
        payload = {"model": model, "input": input_payload, "parameters": parameters}
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            f"{creds['baseUrl']}{self._dashscope_task_path_for_operation(operation_kind)}",
            headers=self._dashscope_headers(creds["apiKey"], async_task=True),
            json=payload,
            timeout=180,
        )
        output = dict(response.get("output") or {})
        task_id = str(output.get("task_id") or response.get("task_id") or response.get("taskId") or "").strip()
        if not task_id:
            raise RuntimeError(f"DashScope video response did not include task_id: {response}")
        job["status"] = normalize_provider_status(output.get("task_status") or "PENDING", provider="dashscope")
        job["providerTaskId"] = task_id
        job["providerResponse"] = {"providerId": "aliyun_bailian_dashscope", "taskId": task_id, "model": model, "operationKind": operation_kind}
        return self._save_job(job)

    async def _poll_dashscope_task(self, job: dict[str, Any]) -> dict[str, Any]:
        creds = self._dashscope_credentials()
        task_id = str(job.get("providerTaskId") or "").strip()
        if not task_id:
            job["status"] = "failed"
            job["error"] = "Missing providerTaskId"
            return self._save_job(job)
        response = await self._request_json(
            "GET",
            f"{creds['baseUrl']}/tasks/{task_id}",
            headers=self._dashscope_headers(creds["apiKey"]),
            timeout=60,
        )
        output = dict(response.get("output") or {})
        status = normalize_provider_status(output.get("task_status") or response.get("task_status"), provider="dashscope")
        job["status"] = status
        job["providerResponse"] = {
            **dict(job.get("providerResponse") or {}),
            "lastStatus": output.get("task_status"),
            "taskId": task_id,
            "usage": response.get("usage") or {},
            "requestId": response.get("request_id"),
        }
        if status == "succeeded":
            result_url = self._dashscope_result_url(response)
            if not result_url:
                job["status"] = "failed"
                job["error"] = "DashScope task succeeded without result URL"
            else:
                operation_kind = str((job.get("providerResponse") or {}).get("operationKind") or job.get("operationKind") or "")
                native_audio = operation_kind in {"video.lipsync", "video.avatar"}
                artifact = await self._artifact_from_url(
                    result_url,
                    job=job,
                    kind="video",
                    provider="aliyun_bailian_dashscope",
                    mime_hint="video/mp4",
                    metadata={
                        "model": job["providerResponse"].get("model"),
                        "nativeAudio": native_audio,
                        "audioMode": "input_audio_synchronization" if native_audio else "silent_or_external_audio",
                        "audioPreservationPolicy": "preserve_native_audio_by_default" if native_audio else "silent_or_external_audio",
                    },
                )
                job["artifacts"] = [artifact]
                job["completedAt"] = utc_now_iso()
        elif status == "failed":
            job["error"] = str(output.get("message") or output.get("code") or "DashScope task failed")
            if self._looks_like_policy_reject(job["error"]):
                job["policyRejectReason"] = job["error"]
            job["completedAt"] = utc_now_iso()
        return self._save_job(job)

    async def _artifact_from_image_response(self, response: dict[str, Any], *, job: dict[str, Any], provider: str, model: str, mime_hint: str) -> dict[str, Any]:
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, list) or not data:
            raise RuntimeError(f"Image response did not contain data[]: {response}")
        first = dict(data[0] or {})
        if first.get("b64_json"):
            return self._artifact_from_b64(str(first["b64_json"]), job=job, kind="image", provider=provider, mime_type=mime_hint, extension=".png", metadata={"model": model})
        if first.get("url"):
            return await self._artifact_from_url(str(first["url"]), job=job, kind="image", provider=provider, mime_hint=mime_hint, metadata={"model": model})
        raise RuntimeError("Image response contained neither url nor b64_json")

    def _artifact_from_b64(self, payload: str, *, job: dict[str, Any], kind: str, provider: str, mime_type: str, extension: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        raw = payload.split(",", 1)[1] if payload.startswith("data:") and "," in payload else payload
        data = base64.b64decode(raw)
        path = self._output_path(job, kind, extension)
        path.write_bytes(data)
        return self._record_local_artifact(file_path=path, job=job, kind=kind, mime_type=mime_type, metadata={"provider": provider, "origin": "provider_result", **dict(metadata or {})})

    async def _artifact_from_url(self, url: str, *, job: dict[str, Any], kind: str, provider: str, mime_hint: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        headers = {"User-Agent": "V8-Agent-OS-CreativeMedia/1.0"}
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            request = client.build_request("GET", url, headers=headers)
            response = await client.send(request, stream=True)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or mime_hint
            extension = self._extension_for_url(url, content_type, kind)
            path = self._output_path(job, kind, extension)
            try:
                with open(path, "wb") as file:
                    async for chunk in response.aiter_bytes():
                        if chunk:
                            file.write(chunk)
            finally:
                await response.aclose()
        parsed = urlparse(url)
        return self._record_local_artifact(
            file_path=path,
            job=job,
            kind=kind,
            mime_type=content_type,
            metadata={
                "provider": provider,
                "origin": "provider_result",
                "sourceHost": parsed.netloc,
                "sourceUrlHash": hashlib.sha256(url.encode("utf-8")).hexdigest(),
                **dict(metadata or {}),
            },
        )

    def _record_local_artifact(self, *, file_path: Path, job: dict[str, Any], kind: str, mime_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
        artifact = artifact_store.record_artifact(
            artifact_kind=kind,
            mime_type=mime_type,
            title=file_path.name,
            source_path=str(file_path),
            metadata={
                **metadata,
                "creativeMediaJobId": job["jobId"],
                "modality": job["modality"],
                "projectId": job.get("projectId") or "",
                "workspaceId": job.get("workspaceId") or "",
                "workspacePath": job.get("workspacePath") or "",
                "pathPlane": "runtime",
                "storageClass": "runtime_artifact",
                "surfaceVisible": True,
            },
            source_component="creative_media_runtime",
            node="creative_media_runtime",
        )
        return artifact

    def _output_path(self, owner: dict[str, Any] | str, kind: str, extension: str) -> Path:
        if isinstance(owner, dict):
            owner_id = str(owner.get("jobId") or owner.get("renderJobId") or owner.get("planId") or "job")
            workspace_path = str(owner.get("workspacePath") or "").strip()
        else:
            owner_id = str(owner)
            workspace_path = ""
        if workspace_path:
            root = Path(workspace_path).expanduser() / "creative_media" / _safe_filename(owner_id, "job")
        else:
            root = storage.base_dir / "workspace" / "creative_media" / _safe_filename(owner_id, "job")
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{_safe_filename(kind, 'media')}-{uuid.uuid4().hex[:8]}{extension}"

    def _extension_for_url(self, url: str, content_type: str, kind: str) -> str:
        suffix = Path(urlparse(url).path).suffix
        if suffix and len(suffix) <= 8:
            return suffix
        guessed = mimetypes.guess_extension(content_type or "")
        if guessed:
            return guessed
        return {"image": ".png", "video": ".mp4", "audio": ".mp3"}.get(kind, ".bin")

    def _bearer_headers(self, api_key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    async def _request_json(self, method: str, url: str, *, headers: Optional[dict[str, str]] = None, json: Optional[dict[str, Any]] = None, timeout: float = 120.0) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=min(float(timeout or 60.0), 60.0)) as client:
            response = await client.request(method, url, headers=headers, json=json)
            if response.status_code >= 400:
                raise RuntimeError(f"Provider request failed ({response.status_code}) at {url}: {response.text[:500]}")
            return response.json()

    async def _request_multipart_json(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        data: Optional[dict[str, Any]] = None,
        files: Optional[dict[str, Any]] = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=min(float(timeout or 60.0), 60.0)) as client:
            response = await client.request(method, url, headers=headers, data=data, files=files)
            if response.status_code >= 400:
                raise RuntimeError(f"Provider request failed ({response.status_code}) at {url}: {response.text[:500]}")
            return response.json()


creative_media_runtime = runtime_registry.register(CreativeMediaRuntime())

__all__ = [
    "CreativeMediaRuntime",
    "creative_media_runtime",
    "_build_openai_image_payload",
    "_build_volcengine_image_payload",
    "_build_volcengine_video_payload",
    "normalize_provider_status",
]
