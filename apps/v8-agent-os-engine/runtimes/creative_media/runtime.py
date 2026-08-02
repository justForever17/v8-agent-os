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
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import quote, urlencode, urlparse

import httpx

from core.artifact_store import artifact_store
from core.audio.tts_provider import TTSManager
from core.database import db
from core.model_control_plane import model_control_plane
from core.model_endpoint_binding import build_model_endpoint_binding
from core.model_ref import parse_model_ref
from core.process_launch import run_windowless
from core.storage import storage
from core.tools.tool_execution_envelope import ToolExecutionEnvelope
from core.workspace_media_library import workspace_media_library
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
from .image_analysis import (
    QUALITY_PROFILES,
    analyze_image,
    compare_image_analyses,
    create_transparent_derivative,
    evaluate_quality_profile,
)
from .media_quality import (
    consistency_observation,
    evaluate_cross_shot_consistency,
    image_visual_signature,
    inspect_media_quality,
)
from .governed_media import trim_exact as trim_governed_media_exact
from .motion_capture import MOTION_MIME_TYPE, extract_holistic_motion
from .gltf_rig import inspect_rigged_model
from .godot_retarget import retarget_motion_with_godot
from .comfyui_workflow import bind_comfyui_inputs, select_comfyui_output, validate_comfyui_workflow
from .model_routing import (
    configured_adapter,
    configured_operation_kinds,
    evaluate_candidate_readiness,
    suggested_adapter_for_model,
)


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
# Only providers with a concrete adapter become executable. Other music/model3d catalog entries stay catalog-only.
EXECUTABLE_MODALITIES = {"image", "video", "voice", "music", "model3d"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}
MODEL3D_EXTENSIONS = {".glb", ".obj", ".fbx", ".zip", ".gltf", ".usdz"}
MEDIA_MODEL_TYPE_TO_MODALITY = {
    "IMAGE": "image",
    "VIDEO": "video",
    "VOICE": "voice",
    "AUDIO": "voice",
    "TTS": "voice",
    "SPEECH": "voice",
    "MUSIC": "music",
    "MODEL3D": "model3d",
}
DEFAULT_OPERATION_KINDS = {
    "image": ["image.generate"],
    "video": ["video.text_to_video"],
    "voice": ["voice.tts"],
    "music": ["music.generate", "music.brief"],
    "model3d": ["model3d.generate"],
}

_PROVIDER_CREDENTIAL_OVERRIDES: ContextVar[dict[str, str]] = ContextVar(
    "creative_media_provider_credential_overrides",
    default={},
)


@contextmanager
def bind_creative_provider_credentials(values: dict[str, str] | None):
    """Bind Engine-internal provider credentials to one async execution context."""

    token = _PROVIDER_CREDENTIAL_OVERRIDES.set(
        {str(key): str(value) for key, value in dict(values or {}).items() if str(value)}
    )
    try:
        yield
    finally:
        _PROVIDER_CREDENTIAL_OVERRIDES.reset(token)
EXECUTABLE_OPERATION_KINDS = {
    "image.generate",
    "image.edit",
    "video.text_to_video",
    "video.image_to_video",
    "video.first_last_frame",
    "video.reference_to_video",
    "video.action_transfer",
    "voice.tts",
    "voice.design",
    "music.generate",
    "music.cover",
    "model3d.generate",
    "video.extract_frame_exact",
    "video.trim_exact",
    "audio.trim_exact",
    "image.compose_psd",
    "image.edit_psd_layers",
    "video.extract_holistic_motion",
    "model3d.inspect_rigged",
    "model3d.retarget_motion_godot",
}
GOVERNED_LOCAL_OPERATION_KINDS = {
    "video.extract_frame_exact",
    "video.trim_exact",
    "audio.trim_exact",
    "image.compose_psd",
    "image.edit_psd_layers",
    "video.extract_holistic_motion",
    "model3d.inspect_rigged",
    "model3d.retarget_motion_godot",
}
DASHSCOPE_VIDEO_OPERATION_KINDS = {
    "video.text_to_video",
    "video.image_to_video",
    "video.first_last_frame",
    "video.reference_to_video",
}
DASHSCOPE_BUILTIN_MODELS = {
    "image.generate": ["qwen-image-2.0-pro", "qwen-image-2.0", "wan2.7-image-pro"],
    "image.edit": ["qwen-image-2.0-pro", "qwen-image-2.0", "wan2.7-image-pro"],
    "video.text_to_video": ["wan2.7-t2v", "wan2.7-t2v-2026-06-12"],
    "video.image_to_video": ["wan2.7-i2v", "wan2.7-i2v-2026-04-25"],
    "video.first_last_frame": ["wan2.7-i2v", "wan2.7-i2v-2026-04-25"],
    "video.reference_to_video": ["wan2.7-r2v", "wan2.7-r2v-2026-06-12"],
}
PLUGIN_ONLY_OPERATION_KINDS = {
    "video.lipsync",
    "video.avatar",
    "video.replacement",
    "video.style_repaint",
    "video.video_edit",
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
MINIMAX_VIDEO_MODELS_BY_OPERATION = {
    "video.text_to_video": {
        "MiniMax-Hailuo-2.3",
        "MiniMax-Hailuo-02",
        "T2V-01-Director",
        "T2V-01",
    },
    "video.image_to_video": {
        "MiniMax-Hailuo-2.3",
        "MiniMax-Hailuo-2.3-Fast",
        "MiniMax-Hailuo-02",
        "I2V-01-Director",
        "I2V-01-live",
        "I2V-01",
    },
    "video.first_last_frame": {"MiniMax-Hailuo-02"},
    "video.reference_to_video": {"S2V-01"},
}
MINIMAX_HAILUO_VIDEO_MODELS = {
    "MiniMax-Hailuo-2.3",
    "MiniMax-Hailuo-2.3-Fast",
    "MiniMax-Hailuo-02",
}


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
    return sorted(values - PLUGIN_ONLY_OPERATION_KINDS)


def _model_identifier_variants(value: Any) -> set[str]:
    raw = str(value or "").strip()
    if not raw:
        return set()
    variants = {raw, raw.lower()}
    tail = raw.rsplit("/", 1)[-1].strip()
    if tail:
        variants.update({tail, tail.lower()})
    return {item for item in variants if item}


def _normalize_operation_kinds_for_modality(modality: str, operations: Iterable[str]) -> list[str]:
    filtered: list[str] = []
    for operation in operations:
        item = str(operation or "").strip()
        if item and item not in PLUGIN_ONLY_OPERATION_KINDS and (item.startswith(f"{modality}.") or (modality == "voice" and item.startswith("voice."))):
            filtered.append(item)
    return list(dict.fromkeys(filtered))


def _registry_operation_kinds_for_model(*, provider_id: str, model_id: str, modality: str) -> list[str]:
    registry = load_media_model_capability_registry()
    target_provider = str(provider_id or "").strip()
    target_model = str(model_id or "").strip()
    target_model_variants = _model_identifier_variants(target_model)
    if not target_model_variants:
        return []
    for item in list(registry.get("models") or []):
        if not isinstance(item, dict):
            continue
        provider_ids = {str(value or "").strip() for value in list(item.get("providerIds") or [])}
        aliases: set[str] = set()
        for value in list(item.get("aliases") or []):
            aliases.update(_model_identifier_variants(value))
        aliases.update(_model_identifier_variants(item.get("canonicalModelId")))
        if not aliases or not (target_model_variants & aliases):
            continue
        operations = [
            str(value or "").strip()
            for value in list(item.get("operationKinds") or [])
            if str(value or "").strip()
        ]
        return [
            item
            for item in _normalize_operation_kinds_for_modality(modality, operations)
            if item not in PLUGIN_ONLY_OPERATION_KINDS
        ]
    return []


def _modality_for_operation(operation_kind: str) -> str:
    prefix = str(operation_kind or "").split(".", 1)[0].strip().lower()
    return "voice" if prefix == "audio" else prefix


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _build_openai_image_payload(
    *,
    model: str,
    prompt: str,
    size: str,
    response_format: str | None = None,
) -> dict[str, Any]:
    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
    }
    normalized_format = str(response_format or "").strip()
    if normalized_format:
        if str(model or "").strip().lower().startswith("gpt-image-"):
            raise ValueError("GPT Image returns base64 image data and does not accept legacy response_format")
        payload["response_format"] = normalized_format
    return payload


def _build_dashscope_image_payload(
    *,
    model: str,
    prompt: str,
    operation_kind: str,
    image_urls: Optional[list[str]] = None,
    size: str | None = None,
    n: int = 1,
    negative_prompt: str = "",
    prompt_extend: bool = True,
    watermark: bool = False,
    seed: int | None = None,
) -> dict[str, Any]:
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise ValueError("DashScope image request requires prompt")
    if operation_kind not in {"image.generate", "image.edit"}:
        raise ValueError(f"DashScope image adapter does not support operationKind={operation_kind}")
    references = [str(item or "").strip() for item in list(image_urls or []) if str(item or "").strip()]
    if operation_kind == "image.edit" and not references:
        raise ValueError("DashScope image.edit requires at least one image URL")
    if len(references) > 3:
        raise ValueError("DashScope image.edit supports at most three image inputs")
    content = [{"image": url} for url in references]
    content.append({"text": normalized_prompt})
    parameters: dict[str, Any] = {
        "n": max(1, int(n)),
        "prompt_extend": _truthy(prompt_extend),
        "watermark": _truthy(watermark),
    }
    if size:
        parameters["size"] = str(size).replace("x", "*")
    if negative_prompt:
        parameters["negative_prompt"] = str(negative_prompt)
    if seed is not None:
        parameters["seed"] = int(seed)
    return {
        "model": str(model or "").strip(),
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }


def _build_dashscope_video_payload(
    *,
    model: str,
    prompt: str,
    operation_kind: str,
    image_urls: Optional[list[str]] = None,
    reference_image_url: str = "",
    reference_video_url: str = "",
    reference_image_urls: Optional[list[str]] = None,
    reference_video_urls: Optional[list[str]] = None,
    audio_url: str = "",
    resolution: str = "720P",
    ratio: str = "16:9",
    duration: int = 5,
    negative_prompt: str = "",
    prompt_extend: bool = True,
    watermark: bool = False,
) -> dict[str, Any]:
    if operation_kind not in DASHSCOPE_VIDEO_OPERATION_KINDS:
        raise ValueError(f"DashScope Wan 2.7 adapter does not support operationKind={operation_kind}")
    normalized_prompt = str(prompt or "").strip()
    if not normalized_prompt:
        raise ValueError(f"DashScope {operation_kind} requires prompt")
    references = [str(item or "").strip() for item in list(image_urls or []) if str(item or "").strip()]
    input_payload: dict[str, Any] = {"prompt": normalized_prompt}
    media: list[dict[str, Any]] = []
    ref_videos: list[str] = []
    if operation_kind == "video.text_to_video":
        if references or reference_image_url or reference_video_url:
            raise ValueError("DashScope video.text_to_video does not accept image or video references")
        if audio_url:
            input_payload["audio_url"] = str(audio_url)
    elif operation_kind == "video.image_to_video":
        if len(references) != 1:
            raise ValueError("DashScope video.image_to_video requires exactly one first-frame image")
        media.append({"type": "first_frame", "url": references[0]})
        if audio_url:
            media.append({"type": "driving_audio", "url": str(audio_url)})
    elif operation_kind == "video.first_last_frame":
        if len(references) != 2:
            raise ValueError("DashScope video.first_last_frame requires exactly two images")
        media.extend(
            [
                {"type": "first_frame", "url": references[0]},
                {"type": "last_frame", "url": references[1]},
            ]
        )
        if audio_url:
            media.append({"type": "driving_audio", "url": str(audio_url)})
    elif operation_kind == "video.reference_to_video":
        ref_images = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in [reference_image_url, *list(reference_image_urls or [])]
                if str(item or "").strip()
            )
        )
        ref_videos = list(
            dict.fromkeys(
                str(item or "").strip()
                for item in [reference_video_url, *list(reference_video_urls or [])]
                if str(item or "").strip()
            )
        )
        references_total = len(ref_images) + len(ref_videos)
        if references_total < 1 or references_total > 5:
            raise ValueError("DashScope video.reference_to_video requires one to five reference images or videos")
        for reference_url in ref_images:
            item: dict[str, Any] = {"type": "reference_image", "url": reference_url}
            if audio_url:
                item["reference_voice"] = str(audio_url)
            media.append(item)
        for reference_url in ref_videos:
            item = {"type": "reference_video", "url": reference_url}
            if audio_url:
                item["reference_voice"] = str(audio_url)
            media.append(item)
    if media:
        input_payload["media"] = media
    if negative_prompt:
        input_payload["negative_prompt"] = str(negative_prompt)
    parameters: dict[str, Any] = {
        "resolution": str(resolution or "720P").upper(),
        "duration": max(2, min(int(duration), 10 if operation_kind == "video.reference_to_video" and ref_videos else 15)),
        "prompt_extend": _truthy(prompt_extend),
        "watermark": _truthy(watermark),
    }
    if operation_kind in {"video.text_to_video", "video.reference_to_video"}:
        parameters["ratio"] = str(ratio or "16:9")
    return {"model": str(model or "").strip(), "input": input_payload, "parameters": parameters}


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
    supported_operations = {"video.text_to_video", "video.image_to_video", "video.first_last_frame"}
    if operation_kind not in supported_operations:
        raise ValueError(f"Agnes video adapter does not support operationKind={operation_kind}")
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
    if operation_kind == "video.text_to_video" and references:
        raise ValueError("Agnes video.text_to_video does not accept image inputs")
    if operation_kind == "video.image_to_video":
        if len(references) != 1:
            raise ValueError("Agnes video.image_to_video requires exactly one image")
        payload["image"] = references[0]
    elif operation_kind == "video.first_last_frame":
        if len(references) != 2:
            raise ValueError("Agnes video.first_last_frame requires exactly two images")
        payload["extra_body"] = {"image": references}
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
    operation_kind: str,
    ratio: str,
    resolution: str,
    duration: int,
    seed: int = -1,
    image_urls: Optional[list[str]] = None,
    generate_audio: bool = True,
    watermark: bool = False,
) -> dict[str, Any]:
    supported_operations = {
        "video.text_to_video",
        "video.image_to_video",
        "video.first_last_frame",
        "video.reference_to_video",
    }
    if operation_kind not in supported_operations:
        raise ValueError(f"Volcengine Seedance adapter does not support operationKind={operation_kind}")
    content: list[dict[str, Any]] = []
    if prompt:
        content.append({"type": "text", "text": prompt})
    references = [str(item or "").strip() for item in list(image_urls or []) if str(item or "").strip()]
    if operation_kind == "video.reference_to_video":
        if not references or len(references) > 5:
            raise ValueError("Volcengine video.reference_to_video requires one to five reference images")
        required_count = None
    else:
        required_count = {"video.text_to_video": 0, "video.image_to_video": 1, "video.first_last_frame": 2}[operation_kind]
        if len(references) != required_count:
            raise ValueError(f"Volcengine {operation_kind} requires exactly {required_count} image inputs")
    for index, url in enumerate(references):
        role = "reference_image" if operation_kind == "video.reference_to_video" else "first_frame" if index == 0 else "last_frame"
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


def _build_minimax_video_payload(
    *,
    model: str,
    prompt: str,
    operation_kind: str,
    image_references: Optional[list[str]] = None,
    duration_seconds: Any = None,
    resolution: Any = None,
    prompt_optimizer: bool = True,
    fast_pretreatment: Any = None,
    aigc_watermark: bool = False,
) -> dict[str, Any]:
    normalized_model = str(model or "").strip()
    supported_models = MINIMAX_VIDEO_MODELS_BY_OPERATION.get(operation_kind)
    if not supported_models:
        raise ValueError(f"MiniMax video adapter does not support operationKind={operation_kind}")
    if normalized_model not in supported_models:
        raise ValueError(
            f"MiniMax model {normalized_model or 'missing'} does not officially support operationKind={operation_kind}"
        )

    normalized_prompt = str(prompt or "").strip()
    if operation_kind == "video.text_to_video" and not normalized_prompt:
        raise ValueError("MiniMax video.text_to_video requires prompt")
    if len(normalized_prompt) > 2000:
        raise ValueError("MiniMax video prompt must not exceed 2000 characters")

    references = [str(item or "").strip() for item in list(image_references or []) if str(item or "").strip()]
    payload: dict[str, Any] = {
        "model": normalized_model,
        "prompt_optimizer": _truthy(prompt_optimizer),
        "aigc_watermark": _truthy(aigc_watermark),
    }
    if normalized_prompt:
        payload["prompt"] = normalized_prompt

    if operation_kind == "video.image_to_video":
        if len(references) != 1:
            raise ValueError("MiniMax video.image_to_video requires exactly one first-frame image")
        payload["first_frame_image"] = references[0]
    elif operation_kind == "video.first_last_frame":
        if len(references) != 2:
            raise ValueError("MiniMax video.first_last_frame requires exactly two images in first/last order")
        payload["first_frame_image"] = references[0]
        payload["last_frame_image"] = references[1]
    elif operation_kind == "video.reference_to_video":
        if len(references) != 1:
            raise ValueError("MiniMax video.reference_to_video requires exactly one character reference image")
        payload["subject_reference"] = [{"type": "character", "image": [references[0]]}]
        return payload
    elif references:
        raise ValueError("MiniMax video.text_to_video does not accept image references")

    duration = int(duration_seconds) if duration_seconds not in (None, "") else 6
    if normalized_model in MINIMAX_HAILUO_VIDEO_MODELS:
        if duration not in {6, 10}:
            raise ValueError("MiniMax Hailuo duration must be 6 or 10 seconds")
        normalized_resolution = str(resolution or "768P").strip().upper()
        if normalized_resolution not in {"768P", "1080P"}:
            raise ValueError("MiniMax Hailuo resolution must be 768P or 1080P")
        if duration == 10 and normalized_resolution != "768P":
            raise ValueError("MiniMax Hailuo 10-second video only supports 768P")
        if fast_pretreatment not in (None, ""):
            payload["fast_pretreatment"] = _truthy(fast_pretreatment)
    else:
        if duration != 6:
            raise ValueError("MiniMax legacy T2V/I2V models support a 6-second request")
        normalized_resolution = str(resolution or "720P").strip().upper()
        if normalized_resolution != "720P":
            raise ValueError("MiniMax legacy T2V/I2V models require 720P")
        if fast_pretreatment not in (None, ""):
            raise ValueError(f"MiniMax model {normalized_model} does not support fast_pretreatment")
    payload["duration"] = duration
    payload["resolution"] = normalized_resolution
    return payload


class CreativeMediaRuntime:
    kind = "creative_media"

    def __init__(self) -> None:
        # Provider result URLs may be short-lived or signed. Keep them inside the
        # current Engine process so a follow-up media job can transport the
        # artifact without exposing the provider URL on Human Surface.
        self._provider_transport_urls: dict[str, str] = {}

    def runtime_descriptor(self) -> dict[str, Any]:
        try:
            from runtimes.plugin_manager.service import plugin_manager_service

            plugin_items = [
                item
                for item in plugin_manager_service.status_summary().get("plugins", [])
                if item.get("pluginId") in {"aliyun-bailian", "hyperframes"}
            ]
        except Exception:
            plugin_items = []
        return {
            "kind": self.kind,
            "displayName": "多媒体创作",
            "summary": "负责通用图片、视频、配音/旁白、音乐与 3D 基础生成，以及 recipe、产物和质量闭环。供应商独有工作流由受治理的插件 task grant 提供。",
            "responsibilities": [
                "归一化媒体 provider 请求格式。",
                "持久化媒体 job 状态。",
                "把生成结果登记为 runtime artifact。",
            ],
            "routingKeywords": ["image", "video", "voice", "music", "creative_media", "artifact"],
            "acceptedInputs": ["prompt", "image reference", "video reference", "audio reference", "first frame", "last frame", "media asset"],
            "producedOutputs": ["image", "video", "audio", "music", "3D", "PSD", "recipe", "QA report"],
            "supportsResume": True,
            "supportsRepair": False,
            "visibility": "internal",
            "promptHints": [
                "用法入口：通过 runtime_broker(mode='route', need={'kind':'creative_media', ...}) 创建 episode；输入 brief、modality、assetRole、referenceAssetIds、qualityTier/costLimit，不要让 Supervisor 直接拼 provider raw request。",
                "执行流程：Creative Media 负责 recipe/work order 编译、provider 选择、job 轮询、artifact 登记、质量/安全摘要；绑定 Agent 只使用 capabilities/plan/assets/jobs/edit/quality 六个 facade，不猜测旧工具名或 supplier 私有工具。",
                "边界：科普/课程/产品介绍等可编辑代码视频由 Engineering 主导；Creative Media 只提供素材和媒体 provider 子能力。",
                "回流要求：typed handoff 必须给 artifactRefs/jobIds/modelUsed/costEstimate/safetyStatus/limitations/detailRef；provider raw response、轮询日志和内部 recipe JSON 只进 Runtime Surface。",
                "支撑能力与边界：Engineering、Research、Admin 等 runtime 只需要背景图、图标、封面、角色图、配音、音乐、3D 道具或关键帧素材时，Creative Media 作为 CreativeAssetRequest 素材支持 runtime；AI 生成拼接长视频可由 Creative Media 产出各类素材，由 Engineering 组装可编辑页面/时间线。",
                "语音边界：Creative Media 的 voice.tts / voice.design 生成项目媒体 artifact 与 reusable voice_id，不等同于聊天气泡 `<voice>text</voice>` 的系统 TTS 播放协议。",
                "画布执行纪律：节点、参数、连线和关系说明只形成可恢复执行图，绝不自动执行；只有用户触发“运行全部 / 运行到此”后，才按图级预检通过的计划创建 job。",
                "动作采集首版边界：video.extract_holistic_motion 仅接受单人视频或本地录制后上传的视频，依赖显式安装的动作采集能力包，产出带精确 ffprobe time base 与 QA 的 .v8motion；不要宣称实时骨架流、多人物或高精度面部动画。",
                "3D 重定向首版边界：model3d.inspect_rigged / model3d.retarget_motion_godot 仅接受已绑骨 GLB/GLTF；重定向只使用用户在插件管理中心配置且离线校验通过的 Godot 3D 场景，不覆盖配置，不自动绑骨，不生成 root motion。",
                "动作转移 Provider 边界：video.action_transfer 只按用户已配置的精确模型候选执行；内置 DashScope 适配器仅接受 wan2.2-animate-move 的角色图 + 公网动作视频合同，本地素材没有 provider 可访问 URL 时必须诚实失败，不得伪造上传或换用其他模型。",
            ],
            "metadata": {
                "p1": True,
                "p2": True,
                "p3": True,
                "supervisorToolSurface": False,
                "managedToolGroups": ["creative_media.core"],
                "managedToolNames": [
                    "creative_media_capabilities",
                    "creative_media_plan",
                    "creative_media_assets",
                    "creative_media_jobs",
                    "creative_media_edit",
                    "creative_media_quality",
                ],
                "baseOperationKinds": sorted(EXECUTABLE_OPERATION_KINDS),
                "optionalPluginCapabilities": plugin_items,
                "artifactRange": ["image", "video", "audio", "music", "3D", "PSD", "motion", "rig profile", "recipe", "QA"],
            },
        }

    def catalog(self) -> dict[str, Any]:
        matrix = load_provider_matrix()
        return {
            **matrix,
            "modelRoutingContract": {
                "configurationAuthority": "model_control_plane",
                "registryRole": "suggestion_only",
                "selectionAuthority": "creative_media_operation_preferences",
                "failurePolicy": "fail_closed_with_readiness_reasons",
            },
            "mediaModelCapabilityRegistry": load_media_model_capability_registry(),
            "modelCapabilityOverrides": load_media_model_capability_overrides(),
            "runtimeAdapters": [
                {"id": "openai_images", "modalities": ["image"], "executable": True},
                {"id": "agnes_images", "modalities": ["image"], "executable": True},
                {"id": "agnes_video", "modalities": ["video"], "executable": True},
                {"id": "minimax_video", "modalities": ["video"], "executable": True},
                {"id": "volcengine_ark", "modalities": ["image", "video"], "executable": True},
                {"id": "dashscope", "modalities": ["image", "video"], "executable": True},
                {"id": "comfyui_workflow", "modalities": ["video"], "executable": True},
                {"id": "v8_audio_tts", "modalities": ["voice"], "executable": True},
                {"id": "minimax_tts", "modalities": ["voice"], "executable": True},
                {"id": "minimax_music", "modalities": ["music"], "executable": True},
                {"id": "mureka_music", "modalities": ["music"], "executable": True},
                {"id": "tencent_hunyuan_3d", "modalities": ["model3d"], "executable": True},
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
        adapter, declared = configured_adapter(model_data)
        if declared:
            return adapter
        return suggested_adapter_for_model(
            modality=modality,
            model_id=str(model_data.get("id") or ""),
            provider_matrix=load_provider_matrix(),
        ) or "catalog_only"

    def _operation_kinds_for_candidate(
        self,
        *,
        modality: str,
        provider_id: str,
        adapter: str,
        provider_meta: dict[str, Any],
        model_data: dict[str, Any],
    ) -> list[str]:
        configured_operations, configured = configured_operation_kinds(
            provider_meta=provider_meta,
            model_data=model_data,
        )
        if configured:
            return _normalize_operation_kinds_for_modality(modality, configured_operations)
        registry_operations = _registry_operation_kinds_for_model(
            provider_id=provider_id,
            model_id=str(model_data.get("id") or ""),
            modality=modality,
        )
        if registry_operations:
            return _normalize_operation_kinds_for_modality(modality, registry_operations)
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
            model_hint = str(request.get("model") or request.get("modelId") or request.get("model_id") or "").lower()
            if (
                "cover" in model_hint
                or request.get("audioUrl")
                or request.get("audio_url")
                or request.get("audioBase64")
                or request.get("audio_base64")
                or request.get("coverFeatureId")
                or request.get("cover_feature_id")
            ):
                return "music.cover"
            return "music.generate"
        if modality == "model3d":
            return "model3d.generate"
        return f"{modality}.generate"

    def _is_brief_only_operation(self, *, adapter: str, operation_kind: str) -> bool:
        return operation_kind == "music.brief"

    def list_model_candidates(self) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        config = model_control_plane.get_config()
        providers = dict(config.get("providers") or {})
        provider_matrix = load_provider_matrix()
        for provider_id, provider_data in providers.items():
            provider_meta = dict((provider_data or {}).get("provider") or {})
            provider_name = str(provider_meta.get("name") or provider_id).strip() or provider_id
            provider_logo_asset = str(provider_meta.get("logoAsset") or provider_meta.get("logo_asset") or provider_meta.get("icon") or "").strip()
            for model_id, model_data_raw in dict((provider_data or {}).get("models") or {}).items():
                model_data = dict(model_data_raw or {})
                model_id_str = str(model_id)
                endpoint_binding = build_model_endpoint_binding(
                    str(provider_id),
                    str(model_id),
                    provider_meta,
                    model_data,
                )
                media_limits = dict(model_data.get("mediaLimits") or {})
                model_type = str(model_data.get("type") or "").strip().upper()
                modality = MEDIA_MODEL_TYPE_TO_MODALITY.get(model_type)
                if model_type == "WORKFLOW":
                    workflow_operations, _ = configured_operation_kinds(
                        provider_meta=provider_meta,
                        model_data=model_data,
                    )
                    workflow_modalities = {
                        "voice" if str(item).split(".", 1)[0].lower() == "audio" else str(item).split(".", 1)[0].lower()
                        for item in workflow_operations
                        if "." in str(item)
                    }
                    modality = next(iter(workflow_modalities)) if len(workflow_modalities) == 1 else None
                capabilities = dict(model_data.get("capabilities") or {})
                if not modality and model_type != "WORKFLOW":
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
                    model_data={**model_data, "id": model_id_str},
                )
                configured_operations, has_configured_operations = configured_operation_kinds(
                    provider_meta=provider_meta,
                    model_data=model_data,
                )
                configured_adapter_value, has_configured_adapter = configured_adapter(model_data)
                suggested_adapter = suggested_adapter_for_model(
                    modality=modality,
                    model_id=model_id_str,
                    provider_matrix=provider_matrix,
                )
                operation_capability_profiles = dict(
                    media_limits.get("operationCapabilityProfiles")
                    or model_data.get("operationCapabilityProfiles")
                    or {}
                )
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
                    operation_configured = has_configured_operations and operation_kind in configured_operations
                    readiness = evaluate_candidate_readiness(
                        provider_meta=provider_meta,
                        model_data=model_data,
                        endpoint_binding=endpoint_binding,
                        operation_kind=operation_kind,
                        adapter=adapter,
                        operation_configured=operation_configured,
                        adapter_configured=bool(configured_adapter_value) and has_configured_adapter and configured_adapter_value == adapter,
                    )
                    brief_only = self._is_brief_only_operation(adapter=adapter, operation_kind=operation_kind)
                    if brief_only:
                        readiness = {"executable": False, "planningOnly": True, "reasonCodes": [], "reasonMessages": []}
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
                            "adapterSource": "configuration" if has_configured_adapter else "registry_suggestion" if suggested_adapter else "none",
                            "suggestedAdapter": suggested_adapter,
                            "endpointBinding": endpoint_binding,
                            "operationSource": "configuration" if operation_configured else "registry_suggestion" if _registry_operation_kinds_for_model(
                                provider_id=str(provider_id),
                                model_id=model_id_str,
                                modality=modality,
                            ) else "default_suggestion",
                            "capabilityProfile": capability_profile,
                            "nativeAudio": bool(capability_profile.get("nativeAudio")),
                            "source": "model_control_plane",
                            "available": brief_only or bool(readiness.get("executable")),
                            "readiness": readiness,
                            "briefOnly": brief_only,
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

    def _is_model_preference_visible_candidate(self, candidate: dict[str, Any]) -> bool:
        return self._is_configured_model_candidate(candidate)

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
            if self._is_model_preference_visible_candidate(item)
        ]
        diagnostic_candidates = [
            dict(item)
            for item in candidates
            if not self._is_model_preference_visible_candidate(item)
        ]
        operation_rows = self._build_operation_rows(
            candidates=candidates,
            connected_options=connected_options,
            stored_selections=list(selection_by_operation.values()),
        )
        execution_projection: dict[str, dict[str, Any]] = {}
        candidates_by_operation: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            operation_kind = str(candidate.get("operationKind") or "").strip()
            if operation_kind:
                candidates_by_operation.setdefault(operation_kind, []).append(candidate)
        for row in operation_rows:
            operation_kind = str(row.get("operationKind") or "").strip()
            selected_refs = set(_list_of_strings(row.get("selectedModelRefs")))
            operation_candidates = candidates_by_operation.get(operation_kind, [])
            selected_candidates = [
                item
                for item in operation_candidates
                if str(item.get("modelRef") or "").strip() in selected_refs
            ]
            executable = [
                self._compact_model_candidate(item)
                for item in selected_candidates
                if bool(item.get("enabled", False)) and bool(item.get("available", False))
            ]
            blocked = [
                self._compact_model_candidate(item)
                for item in selected_candidates
                if not bool(item.get("enabled", False)) or not bool(item.get("available", False))
            ]
            status = "ready" if executable else "blocked" if selected_refs else "unconfigured"
            execution_projection[operation_kind] = {
                "status": status,
                "configuredModelRefs": list(row.get("selectedModelRefs") or []),
                "executableCandidates": executable,
                "blockedCandidates": blocked,
            }
        return {
            "version": 1,
            "updatedAt": (stored or {}).get("updatedAt") if isinstance(stored, dict) else "",
            "candidates": candidates,
            "connectedOptions": connected_options,
            "diagnosticCandidates": diagnostic_candidates,
            "operationRows": operation_rows,
            "executionProjection": execution_projection,
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
            for key in ("adapter", "provider", "providerId", "provider_id", "model", "modelId", "model_id", "modelRef", "model_ref")
        )

    def _has_explicit_provider_or_model_selection(self, request: dict[str, Any]) -> bool:
        return any(
            key in request and str(request.get(key) or "").strip()
            for key in (
                "provider",
                "providerId",
                "provider_id",
                "model",
                "modelId",
                "model_id",
                "modelRef",
                "model_ref",
            )
        )

    def _matching_explicit_model_candidates(
        self,
        request: dict[str, Any],
        *,
        modality: str,
        operation_kind: str,
    ) -> list[dict[str, Any]]:
        model_ref = parse_model_ref(str(request.get("modelRef") or request.get("model_ref") or ""))
        requested_provider = str(
            request.get("providerId")
            or request.get("provider_id")
            or request.get("provider")
            or (model_ref[0] if model_ref else "")
            or ""
        ).strip()
        requested_model = str(
            request.get("model")
            or request.get("modelId")
            or request.get("model_id")
            or (model_ref[1] if model_ref else "")
            or ""
        ).strip()
        requested_model_ref = str(request.get("modelRef") or request.get("model_ref") or "").strip()
        matches: list[dict[str, Any]] = []
        for candidate in self._all_model_candidates_for_operation(operation_kind):
            if str(candidate.get("operationKind") or "").strip() != operation_kind:
                continue
            if str(candidate.get("modality") or "").strip() != modality:
                continue
            endpoint_binding = dict(candidate.get("endpointBinding") or {})
            binding_operation_kind = str(endpoint_binding.get("operationKind") or "").strip()
            if binding_operation_kind and binding_operation_kind != operation_kind:
                continue
            if requested_provider and str(candidate.get("providerId") or "").strip() != requested_provider:
                continue
            if requested_model_ref and str(candidate.get("modelRef") or "").strip() != requested_model_ref:
                continue
            if requested_model:
                candidate_models = {
                    str(candidate.get("modelId") or "").strip(),
                    str(endpoint_binding.get("providerModelId") or "").strip(),
                }
                candidate_models.update(
                    self._strip_provider_model_prefix(value)
                    for value in list(candidate_models)
                    if value
                )
                if requested_model not in candidate_models and self._strip_provider_model_prefix(requested_model) not in candidate_models:
                    continue
            matches.append(dict(candidate))
        matches.sort(key=lambda item: (_safe_priority(item.get("priority"), 999), str(item.get("modelRef") or "")))
        return matches

    def _explicit_enabled_model_candidate(
        self,
        request: dict[str, Any],
        *,
        modality: str,
        operation_kind: str,
    ) -> dict[str, Any] | None:
        matches = self._matching_explicit_model_candidates(
            request,
            modality=modality,
            operation_kind=operation_kind,
        )
        return next(
            (
                candidate
                for candidate in matches
                if bool(candidate.get("enabled", False)) and bool(candidate.get("available", False))
            ),
            None,
        )

    def _preferred_model_candidates(self, operation_kind: str) -> list[dict[str, Any]]:
        prefs = self.get_model_preferences()
        candidates = [
            dict(item)
            for item in list((prefs.get("policies") or {}).get(operation_kind, {}).get("models") or [])
            if bool(item.get("enabled", True))
            and bool(item.get("available", False))
            and not self._is_incompatible_media_candidate(item, operation_kind)
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
        next_request["modelRef"] = candidate.get("modelRef")
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
            "endpointBinding": candidate.get("endpointBinding") or {},
            "operationKind": candidate.get("operationKind"),
            "modality": candidate.get("modality"),
            "source": candidate.get("source"),
            "available": bool(candidate.get("available", False)),
            "adapterSource": candidate.get("adapterSource"),
            "suggestedAdapter": candidate.get("suggestedAdapter"),
            "operationSource": candidate.get("operationSource"),
            "readiness": candidate.get("readiness") or {},
            "nativeAudio": bool(candidate.get("nativeAudio", False)),
            "capabilityProfile": candidate.get("capabilityProfile") or {},
        }

    def _is_incompatible_media_candidate(self, candidate: dict[str, Any], operation_kind: str) -> bool:
        modality = _modality_for_operation(operation_kind)
        if str(candidate.get("modality") or "") != modality:
            return True
        if str(candidate.get("operationKind") or "") != operation_kind:
            return True
        binding_operation_kind = str((candidate.get("endpointBinding") or {}).get("operationKind") or "").strip()
        if binding_operation_kind and binding_operation_kind != operation_kind:
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
        blocked_candidates = [
            item
            for item in self._all_model_candidates_for_operation(operation_kind)
            if bool(item.get("enabled", False)) and not bool(item.get("available", False))
        ]
        blocking_reason_codes = list(
            dict.fromkeys(
                str(code)
                for item in blocked_candidates
                for code in list((item.get("readiness") or {}).get("reasonCodes") or [])
                if str(code)
            )
        )
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
                "reasonCodes": blocking_reason_codes,
                "configuredModelRefs": [str(item.get("modelRef") or "") for item in blocked_candidates if item.get("modelRef")],
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
        else:
            items = [
                item
                for item in items
                if not item.get("archivedAt")
                and not item.get("deletedAt")
                and str(item.get("status") or "").lower() not in {"archived", "deleted"}
            ]
        if requesting_runtime:
            items = [
                item
                for item in items
                if str(item.get("requestingRuntime") or item.get("requesting_runtime") or "") == requesting_runtime
            ]
        items.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return items

    def get_work_order(self, work_order_id: str) -> dict[str, Any] | None:
        store = self._read_versioned_store(WORK_ORDER_STORE_FILE, "workOrders")
        item = dict((store.get("workOrders") or {}).get(str(work_order_id)) or {})
        return item or None

    def archive_work_order(self, work_order_id: str) -> dict[str, Any]:
        return self._update_work_order_lifecycle(work_order_id, action="archive")

    def delete_work_order(self, work_order_id: str) -> dict[str, Any]:
        return self._update_work_order_lifecycle(work_order_id, action="delete")

    def _work_order_recipe_ids(self, work_order: dict[str, Any]) -> list[str]:
        recipe_refs = _list_of_strings(work_order.get("recipeRefs"))
        recipe_ids = _list_of_strings(work_order.get("recipeIds"))
        return _list_of_strings(
            [
                work_order.get("recipeId"),
                *recipe_refs,
                *recipe_ids,
            ]
        )

    def _update_work_order_lifecycle(self, work_order_id: str, *, action: str) -> dict[str, Any]:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"archive", "delete"}:
            raise ValueError("creative media work order lifecycle action must be archive or delete")
        store = self._read_versioned_store(WORK_ORDER_STORE_FILE, "workOrders")
        values = dict(store.get("workOrders") or {})
        work_order = dict(values.get(str(work_order_id)) or {})
        if not work_order:
            raise ValueError("creative media work order not found")
        now = utc_now_iso()
        if normalized_action == "archive":
            work_order["archivedAt"] = work_order.get("archivedAt") or now
            work_order["status"] = "archived"
        else:
            work_order["deletedAt"] = work_order.get("deletedAt") or now
            work_order["status"] = "deleted"
        work_order["updatedAt"] = now
        recipe_ids = self._work_order_recipe_ids(work_order)
        related_jobs = self._mark_related_jobs_lifecycle(
            recipe_ids=recipe_ids,
            work_order_ids=[str(work_order_id)],
            action=normalized_action,
        )
        related_recipes = creative_recipe_compiler.mark_recipe_lifecycle(
            recipe_ids,
            action=normalized_action,
            reason=f"work_order:{work_order_id}",
        )
        related_assets = creative_recipe_compiler.mark_assets_lifecycle(
            recipe_ids=recipe_ids,
            work_order_ids=[str(work_order_id)],
            action=normalized_action,
            reason=f"work_order:{work_order_id}",
        )
        work_order["lifecycleScope"] = {
            "action": normalized_action,
            "recipeIds": recipe_ids,
            "jobIds": [str(item.get("jobId") or "") for item in related_jobs if item.get("jobId")],
            "assetIds": [str(item.get("assetId") or "") for item in related_assets if item.get("assetId")],
            "recipeCount": len(related_recipes),
            "assetCount": len(related_assets),
            "jobCount": len(related_jobs),
        }
        values[str(work_order_id)] = work_order
        self._write_versioned_store(WORK_ORDER_STORE_FILE, "workOrders", values)
        return dict(work_order)

    def _mark_related_jobs_lifecycle(
        self,
        *,
        recipe_ids: Iterable[str],
        work_order_ids: Iterable[str],
        action: str,
    ) -> list[dict[str, Any]]:
        recipe_id_set = {str(item).strip() for item in recipe_ids if str(item).strip()}
        work_order_id_set = {str(item).strip() for item in work_order_ids if str(item).strip()}
        if not recipe_id_set and not work_order_id_set:
            return []
        payload = self._read_jobs()
        jobs = dict(payload.get("jobs") or {})
        now = utc_now_iso()
        changed: list[dict[str, Any]] = []
        for job_id, raw_job in jobs.items():
            job = dict(raw_job or {})
            request = dict(job.get("request") or {})
            job_recipe_ids = {
                str(job.get("recipeId") or "").strip(),
                str(request.get("recipeId") or request.get("recipe_id") or "").strip(),
            }
            job_work_order_ids = {
                str(job.get("workOrderId") or "").strip(),
                str(request.get("workOrderId") or request.get("work_order_id") or "").strip(),
            }
            if not (recipe_id_set.intersection(job_recipe_ids) or work_order_id_set.intersection(job_work_order_ids)):
                continue
            if action == "archive":
                job["archivedAt"] = job.get("archivedAt") or now
                if str(job.get("status") or "").lower() not in {"succeeded", "failed", "cancelled"}:
                    job["status"] = "archived"
            else:
                job["deletedAt"] = job.get("deletedAt") or now
                job["status"] = "deleted"
            job["updatedAt"] = now
            jobs[str(job_id)] = job
            changed.append(dict(job))
        if changed:
            payload["jobs"] = jobs
            self._write_jobs(payload)
        return changed

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
            result = run_windowless(
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
            session_id=str(metadata.get("sessionId") or "") or None,
            run_id=str(metadata.get("runId") or "") or None,
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
            result = run_windowless(command, capture_output=True, text=True, timeout=int(payload.get("timeoutSeconds") or 900), check=False)
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
        if not artifact_refs:
            artifact_refs = [
                {"artifactId": artifact_id}
                for artifact_id in list(payload.get("artifactIds") or [])
                if str(artifact_id or "").strip()
            ]
        owner = dict(job or payload)
        request_payload = dict(owner.get("request") or owner)
        quality_profile = str(
            payload.get("qualityProfile")
            or payload.get("quality_profile")
            or request_payload.get("qualityProfile")
            or request_payload.get("quality_profile")
            or "storyboard_frame"
        ).strip()
        reference_report = self._quality_reference_report(payload)
        cross_shot_setting = payload.get(
            "crossShotConsistency",
            request_payload.get("crossShotConsistency", request_payload.get("cross_shot_consistency")),
        )
        cross_shot_config = dict(cross_shot_setting) if isinstance(cross_shot_setting, dict) else {}
        cross_shot_enabled_by_request = (
            bool(cross_shot_config.get("enabled", True))
            if isinstance(cross_shot_setting, dict)
            else cross_shot_setting is not False
        )
        required_kinds = {
            str(item or "").strip().lower()
            for item in list(payload.get("requiredKinds") or [])
            if str(item or "").strip()
        }

        def run_checks(refs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
            next_checks: list[dict[str, Any]] = []
            next_warnings: list[str] = []
            next_failures: list[str] = []
            visual_ref_count = 0
            for artifact in refs:
                if not isinstance(artifact, dict):
                    continue
                explicit_kind = str(artifact.get("kind") or artifact.get("modality") or "").strip().lower()
                source_path = str(
                    artifact.get("sourcePath")
                    or artifact.get("source_path")
                    or artifact.get("path")
                    or ""
                )
                suffix = Path(source_path).suffix.lower()
                if explicit_kind in {"image", "video"} or suffix in {
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                    ".psd",
                    *VIDEO_EXTENSIONS,
                }:
                    visual_ref_count += 1
            cross_shot_enabled = cross_shot_enabled_by_request and visual_ref_count >= 2
            consistency_observations: list[dict[str, Any]] = []
            for artifact in refs:
                if not isinstance(artifact, dict):
                    continue
                artifact_checks = self._quality_checks_for_artifact(
                    artifact,
                    owner,
                    warnings=next_warnings,
                    failures=next_failures,
                    quality_profile=quality_profile,
                    reference_report=reference_report,
                    analyze_visual=cross_shot_enabled,
                )
                next_checks.extend(artifact_checks)
                observation = consistency_observation(artifact, artifact_checks)
                if observation:
                    consistency_observations.append(observation)
            if cross_shot_enabled:
                consistency = evaluate_cross_shot_consistency(
                    consistency_observations,
                    config=cross_shot_config,
                )
                next_checks.extend(list(consistency.get("checks") or []))
                next_warnings.extend(
                    item for item in list(consistency.get("warnings") or []) if item not in next_warnings
                )
                next_failures.extend(
                    item for item in list(consistency.get("failures") or []) if item not in next_failures
                )
            if required_kinds:
                present_kinds: set[str] = set()
                for artifact in refs:
                    explicit_kind = str(artifact.get("kind") or artifact.get("modality") or "").strip().lower()
                    if explicit_kind:
                        present_kinds.add(explicit_kind)
                    source_path = str(artifact.get("sourcePath") or artifact.get("source_path") or artifact.get("path") or "")
                    suffix = Path(source_path).suffix.lower()
                    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".psd"}:
                        present_kinds.add("image")
                    elif suffix in VIDEO_EXTENSIONS:
                        present_kinds.add("video")
                    elif suffix in AUDIO_EXTENSIONS:
                        present_kinds.add("audio")
                missing_kinds = sorted(required_kinds - present_kinds)
                next_checks.append(
                    {
                        "name": "required_artifact_kinds",
                        "ok": not missing_kinds,
                        "requiredKinds": sorted(required_kinds),
                        "missingKinds": missing_kinds,
                    }
                )
                next_failures.extend(f"missing_required_kind:{kind}" for kind in missing_kinds)
            if not refs:
                next_failures.append("no_artifacts")
                next_checks.append({"name": "artifact_present", "ok": False})
            return next_checks, next_warnings, next_failures

        normalized_refs = [dict(item) for item in artifact_refs if isinstance(item, dict)]
        checks, warnings, failures = run_checks(normalized_refs)
        state_values = [
            str(check.get("qualityState") or "")
            for check in checks
            if str(check.get("qualityState") or "")
        ]
        status = (
            "failed"
            if failures
            else "repairable"
            if "repairable" in state_values
            else "review_required"
            if "review_required" in state_values
            else "warning"
            if warnings
            else "passed"
        )
        auto_repair = bool(payload.get("autoRepair", payload.get("auto", False)))
        max_repair_attempts = max(0, min(int(payload.get("maxRepairAttempts") or 2), 2))
        repair_attempts: list[dict[str, Any]] = []
        repaired_refs = normalized_refs
        while status == "repairable" and auto_repair and len(repair_attempts) < max_repair_attempts:
            repaired = self._repair_quality_artifacts(
                repaired_refs,
                owner=owner,
                attempt=len(repair_attempts) + 1,
            )
            if not repaired:
                break
            repaired_refs = repaired
            repair_attempts.append(
                {
                    "attempt": len(repair_attempts) + 1,
                    "action": "create_transparent_derivative",
                    "artifactRefs": [item.get("artifactId") for item in repaired_refs if item.get("artifactId")],
                    "createdAt": utc_now_iso(),
                }
            )
            checks, warnings, failures = run_checks(repaired_refs)
            state_values = [str(check.get("qualityState") or "") for check in checks if check.get("qualityState")]
            status = (
                "failed"
                if failures
                else "repairable"
                if "repairable" in state_values
                else "review_required"
                if "review_required" in state_values
                else "warning"
                if warnings
                else "passed"
            )
        required_feature_pack = next(
            (
                check.get("requiredFeaturePackId")
                for check in checks
                if check.get("requiredFeaturePackId")
            ),
            None,
        )
        quality_job = {
            "qualityJobId": quality_job_id,
            "jobId": job_id,
            "status": status,
            **self._scope_fields(payload, owner),
            "qualityProfile": quality_profile,
            "checks": checks,
            "warnings": warnings,
            "failures": failures,
            "repairAttempts": repair_attempts,
            "repairArtifactRefs": [item.get("artifactId") for item in repaired_refs if item.get("artifactId")],
            "requiredFeaturePackId": required_feature_pack,
            "summary": self._quality_human_summary(status, quality_profile, checks, failures, warnings),
            "retryRecommendation": self._retry_recommendation(status=status, failures=failures, warnings=warnings, job=job or payload),
            "createdAt": utc_now_iso(),
        }
        store = self._read_versioned_store(QUALITY_JOB_STORE_FILE, "qualityJobs")
        values = dict(store.get("qualityJobs") or {})
        values[quality_job_id] = quality_job
        self._write_versioned_store(QUALITY_JOB_STORE_FILE, "qualityJobs", values)
        return quality_job

    def _quality_reference_report(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        artifact_id = str(payload.get("referenceArtifactId") or payload.get("reference_artifact_id") or "").strip()
        if not artifact_id:
            return None
        path = self._artifact_source_path(artifact_id)
        if not path or not Path(path).is_file():
            return {
                "status": "review_required",
                "sourceFingerprint": None,
                "subject": {},
                "alpha": {},
                "requiredFeaturePackId": None,
                "diagnostics": {"reason": "reference_artifact_unavailable"},
            }
        try:
            return analyze_image(path)
        except Exception:
            return {
                "status": "review_required",
                "sourceFingerprint": None,
                "subject": {},
                "alpha": {},
                "requiredFeaturePackId": None,
                "diagnostics": {"reason": "reference_analysis_failed"},
            }

    def _repair_quality_artifacts(
        self,
        artifacts: list[dict[str, Any]],
        *,
        owner: dict[str, Any],
        attempt: int,
    ) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        for artifact in artifacts:
            path = str(artifact.get("sourcePath") or artifact.get("source_path") or "").strip()
            if not path and artifact.get("artifactId"):
                path = self._artifact_source_path(str(artifact.get("artifactId") or ""))
            source = Path(path).expanduser() if path else None
            if source is None or not source.is_file() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".psd"}:
                return []
            target = self._output_path(owner, f"quality-repair-{attempt}", ".png")
            try:
                derivative = create_transparent_derivative(source, target)
                recorded = self._record_local_artifact(
                    file_path=target,
                    job=owner,
                    kind="image",
                    mime_type="image/png",
                    metadata={
                        "origin": "quality_repair",
                        "repairAttempt": attempt,
                        "sourceArtifactId": artifact.get("artifactId"),
                        "sourceFingerprint": (derivative.get("sourceReport") or {}).get("sourceFingerprint"),
                    },
                )
                repaired.append(recorded)
            except Exception:
                return []
        return repaired

    def _quality_human_summary(
        self,
        status: str,
        quality_profile: str,
        checks: list[dict[str, Any]],
        failures: list[str],
        warnings: list[str],
    ) -> str:
        image_check = next((item for item in checks if item.get("name") == "image_subject_analysis"), {})
        video_check = next((item for item in checks if item.get("name") == "video_decode_integrity"), {})
        audio_check = next((item for item in checks if item.get("name") == "audio_decode_integrity"), {})
        cross_shot_check = next(
            (item for item in checks if item.get("name") == "cross_shot_semantic_identity_review"),
            {},
        )
        subject = dict(image_check.get("subject") or {})
        if status == "passed":
            if video_check:
                return "视频已通过解码完整性、画面连续性、音轨与技术参数门禁。"
            if audio_check:
                return "音频已通过解码完整性、音轨参数、响度与静音门禁。"
            return f"图像已通过 {quality_profile} 质量门禁；主体占比 {subject.get('areaRatio', '—')}，未发现需要处理的裁切或透明度问题。"
        if status == "repairable":
            return "检测到可安全修复的透明度问题；可生成非破坏性 PNG 衍生文件，原文件保持不变。"
        if status == "review_required":
            if cross_shot_check:
                return "跨镜头技术一致性已经检查；角色或主体身份一致性仍需结合样片人工确认。"
            return "复杂背景无法由基础规则可靠分离，需要安装图像分析增强包或交由用户复核。"
        reasons = [*failures, *warnings]
        return f"质量门禁未通过：{', '.join(reasons[:4]) or '需要人工复核'}。"

    def get_quality_job(self, quality_job_id: str) -> dict[str, Any] | None:
        return dict((self._read_versioned_store(QUALITY_JOB_STORE_FILE, "qualityJobs").get("qualityJobs") or {}).get(str(quality_job_id)) or {}) or None

    def list_quality_jobs(self, *, status: str | None = None) -> list[dict[str, Any]]:
        jobs = list((self._read_versioned_store(QUALITY_JOB_STORE_FILE, "qualityJobs").get("qualityJobs") or {}).values())
        normalized_status = str(status or "").strip().lower()
        result = [dict(item) for item in jobs if not normalized_status or str(item.get("status") or "").lower() == normalized_status]
        result.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return result

    def _quality_checks_for_artifact(
        self,
        artifact: dict[str, Any],
        owner: dict[str, Any],
        *,
        warnings: list[str],
        failures: list[str],
        quality_profile: str = "storyboard_frame",
        reference_report: dict[str, Any] | None = None,
        analyze_visual: bool = False,
    ) -> list[dict[str, Any]]:
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
        if kind in {"voice", "music", "speech", "tts"}:
            kind = "audio"
        suffix = Path(path).suffix.lower()
        if kind == "image" or suffix in {".png", ".jpg", ".jpeg", ".webp", ".psd"}:
            checks.extend(
                self._quality_image_checks(
                    path,
                    owner,
                    warnings=warnings,
                    failures=failures,
                    quality_profile=quality_profile,
                    reference_report=reference_report,
                    analyze_visual=analyze_visual,
                )
            )
        elif kind in {"video", "audio"} or suffix in VIDEO_EXTENSIONS or suffix in AUDIO_EXTENSIONS:
            media_kind = "video" if kind == "video" or suffix in VIDEO_EXTENSIONS else "audio"
            checks.extend(
                self._quality_media_probe_checks(
                    path,
                    owner,
                    warnings=warnings,
                    failures=failures,
                    kind=media_kind,
                    analyze_visual=analyze_visual,
                )
            )
        elif suffix == ".srt":
            text = Path(path).read_text(encoding="utf-8", errors="ignore")
            ok = "-->" in text and bool(text.strip())
            checks.append({"name": "subtitle_timeline_present", "ok": ok})
            if not ok:
                warnings.append("subtitle_timeline_missing")
        return checks

    def _quality_image_checks(
        self,
        path: str,
        owner: dict[str, Any],
        *,
        warnings: list[str],
        failures: list[str],
        quality_profile: str = "storyboard_frame",
        reference_report: dict[str, Any] | None = None,
        analyze_visual: bool = False,
    ) -> list[dict[str, Any]]:
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
            analysis = analyze_image(path)
            visual_signature = image_visual_signature(path) if analyze_visual else None
            comparison = compare_image_analyses(reference_report, analysis) if reference_report else None
            evaluation = evaluate_quality_profile(analysis, quality_profile, comparison=comparison)
            checks.append(
                {
                    "name": "image_subject_analysis",
                    "ok": evaluation.get("status") == "passed",
                    "qualityState": evaluation.get("status"),
                    "qualityProfile": evaluation.get("profile"),
                    "alpha": analysis.get("alpha"),
                    "subject": analysis.get("subject"),
                    "comparison": comparison,
                    "violations": evaluation.get("violations"),
                    "warnings": evaluation.get("warnings"),
                    "requiredFeaturePackId": evaluation.get("requiredFeaturePackId"),
                    "analysisVersion": analysis.get("analyzerVersion"),
                    "sourceFingerprint": analysis.get("sourceFingerprint"),
                    "visualSignature": visual_signature,
                }
            )
            if evaluation.get("status") == "failed":
                failures.extend(str(item) for item in list(evaluation.get("violations") or []))
            elif evaluation.get("status") == "review_required":
                warnings.extend(str(item) for item in list(evaluation.get("warnings") or []) if item)
        except Exception as exc:
            checks.append({"name": "image_metadata_readable", "ok": False, "error": _exception_summary(exc)})
            warnings.append("image_metadata_unavailable")
        return checks

    def _quality_media_probe_checks(
        self,
        path: str,
        owner: dict[str, Any],
        *,
        warnings: list[str],
        failures: list[str],
        kind: str,
        analyze_visual: bool = False,
    ) -> list[dict[str, Any]]:
        result = inspect_media_quality(
            path=path,
            kind=kind,
            request=dict(owner.get("request") or owner),
            runner=run_windowless,
            which=shutil.which,
            analyze_visual=analyze_visual,
        )
        for warning in list(result.get("warnings") or []):
            if warning not in warnings:
                warnings.append(str(warning))
        for failure in list(result.get("failures") or []):
            if failure not in failures:
                failures.append(str(failure))
        checks = list(result.get("checks") or [])
        checks.append(
            {
                "name": "media_technical_signature",
                "ok": not bool(result.get("failures")),
                "signature": dict(result.get("signature") or {}),
            }
        )
        return checks

    def _retry_recommendation(self, *, status: str, failures: list[str], warnings: list[str], job: dict[str, Any]) -> dict[str, Any]:
        if status == "passed":
            return {"action": "accept", "reason": "quality gates passed"}
        if status == "repairable":
            return {"action": "create_non_destructive_derivative", "reason": "a deterministic local repair is available"}
        if status == "review_required":
            return {"action": "manual_review", "reason": "the local analyzer cannot make a reliable determination"}
        if "artifact_not_openable" in failures or "ffprobe_failed" in failures:
            return {"action": "retry_same_operation", "reason": "artifact fetch or media probe failed"}
        if any(
            item in failures
            for item in {
                "video_decode_failed",
                "audio_decode_failed",
                "audio_effectively_silent",
                "video_mostly_black",
                "video_mostly_frozen",
            }
        ):
            return {"action": "regenerate_or_reencode", "reason": "the media payload failed a decode or content integrity gate"}
        if "cross_shot_technical_mismatch" in failures:
            return {"action": "normalize_shot_exports", "reason": "shots use incompatible resolution, frame-rate, or audio profiles"}
        if "cross_shot_visual_drift" in warnings:
            return {"action": "review_or_regenerate_shots", "reason": "subject scale, position, or palette drifted across shots"}
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
            "sessionId": str(request.get("sessionId") or request.get("session_id") or fallback.get("sessionId") or "").strip(),
            "runId": str(request.get("runId") or request.get("run_id") or fallback.get("runId") or "").strip(),
            "projectId": str(request.get("projectId") or request.get("project_id") or fallback.get("projectId") or "").strip(),
            "workspaceId": str(request.get("workspaceId") or request.get("workspace_id") or fallback.get("workspaceId") or "").strip(),
            "workspacePath": str(request.get("workspacePath") or request.get("workspace_path") or fallback.get("workspacePath") or "").strip(),
        }
        return self._ensure_project_workspace_registered(scope)

    def _resolve_current_session_source_path(
        self,
        request: dict[str, Any],
        *,
        source_id: str,
        role: str,
    ) -> str:
        session_id = str(request.get("sessionId") or request.get("session_id") or "").strip()
        workspace_path = str(request.get("workspacePath") or request.get("workspace_path") or "").strip()
        if not session_id or not workspace_path:
            raise ValueError(f"{role} requires current runtime session and workspace context")
        source = db.get_session_source(session_id=session_id, source_id=source_id)
        if not source:
            raise ValueError(f"{role} sourceId is not registered in the current session source ledger")
        ledger_path = str(source.get("workspacePath") or "").strip()
        if not ledger_path:
            raise ValueError(f"{role} source ledger entry has no local workspace path")
        workspace_root = Path(workspace_path).expanduser().resolve()
        candidate = Path(ledger_path).expanduser()
        candidate = (workspace_root / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        try:
            candidate.relative_to(workspace_root)
        except ValueError as exc:
            raise ValueError(f"{role} source path escapes the current runtime workspace") from exc
        if not candidate.is_file():
            raise ValueError(f"{role} source file is unavailable in the current runtime workspace")
        return str(candidate)

    def _prepare_governed_source_inputs(
        self,
        request: dict[str, Any],
        *,
        operation_kind: str,
    ) -> dict[str, Any]:
        prepared = dict(request or {})
        source_id = str(prepared.get("sourceId") or "").strip()
        mask_source_id = str(prepared.get("maskSourceId") or "").strip()
        if operation_kind in GOVERNED_LOCAL_OPERATION_KINDS:
            return prepared
        if operation_kind == "video.action_transfer" and isinstance(prepared.get("canvasInputs"), list):
            return prepared
        if not source_id and not mask_source_id:
            return prepared
        if operation_kind != "image.edit":
            raise ValueError("sourceId and maskSourceId are only executable for operationKind=image.edit")
        if not source_id:
            raise ValueError("maskSourceId requires sourceId for operationKind=image.edit")
        prepared["imagePath"] = self._resolve_current_session_source_path(
            prepared,
            source_id=source_id,
            role="image edit source",
        )
        if mask_source_id:
            prepared["maskPath"] = self._resolve_current_session_source_path(
                prepared,
                source_id=mask_source_id,
                role="image edit mask",
            )
        return prepared

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
            "video.extract_frame_exact": ["session_source_or_workspace_media_asset"],
            "video.trim_exact": ["session_source_or_workspace_media_asset"],
            "audio.trim_exact": ["session_source_or_workspace_media_asset"],
            "image.compose_psd": ["ordered_canvas_image_or_psd_inputs"],
            "image.edit_psd_layers": ["canvas_psd_input"],
            "video.extract_holistic_motion": ["session_source_or_workspace_media_asset"],
            "model3d.inspect_rigged": ["session_source_or_workspace_media_asset"],
            "model3d.retarget_motion_godot": ["canvas_motion_and_rigged_model_inputs"],
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
        normalized_request = dict(request)
        if modality == "image":
            quality_profile = str(request.get("qualityProfile") or request.get("quality_profile") or "").strip()
            if quality_profile not in QUALITY_PROFILES:
                asset_role = str(request.get("assetRole") or request.get("asset_role") or "").strip().lower()
                if any(marker in asset_role for marker in ("cutout", "layer", "transparent")):
                    quality_profile = "transparent_cutout"
                elif "icon" in asset_role:
                    quality_profile = "ui_icon"
                elif any(marker in asset_role for marker in ("character", "reference")):
                    quality_profile = "character_reference"
                elif any(marker in asset_role for marker in ("product", "packshot")):
                    quality_profile = "product_packshot"
                else:
                    quality_profile = "storyboard_frame"
            normalized_request["qualityProfile"] = quality_profile
        return {
            "jobId": f"cm_{uuid.uuid4().hex}",
            **self._scope_fields(request),
            "canvasOperationId": str(request.get("canvasOperationId") or "").strip(),
            "sourceId": str(request.get("sourceId") or "").strip(),
            "artifactId": str(request.get("artifactId") or "").strip(),
            "workspaceAssetId": str(request.get("workspaceAssetId") or "").strip(),
            "maskSourceId": str(request.get("maskSourceId") or "").strip(),
            "outputKind": str(request.get("outputKind") or "").strip(),
            "outputSlot": str(request.get("outputSlot") or "").strip(),
            "modality": modality,
            "adapter": adapter,
            "operationKind": operation_kind,
            "status": "queued",
            "request": _jsonable_request(normalized_request),
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
        if job.get("adapter") == "minimax_video" and job.get("providerTaskId"):
            return await self._poll_minimax_video_job(job)
        if job.get("adapter") == "dashscope" and job.get("providerTaskId"):
            return await self._poll_dashscope_task(job)
        if job.get("adapter") == "comfyui_workflow" and job.get("providerTaskId"):
            return await self._poll_comfyui_workflow_job(job)
        if job.get("adapter") == "mureka_music" and job.get("providerTaskId"):
            return await self._poll_mureka_music_job(job)
        if job.get("adapter") == "tencent_hunyuan_3d" and job.get("providerTaskId"):
            return await self._poll_tencent_hunyuan_3d_job(job)
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
        if str(request.get("operationKind") or "").strip() == "canvas.graph.execute":
            from core.creative_canvas_graph import creative_canvas_graph_service

            return await creative_canvas_graph_service.execute_as_creative_job(self, request)
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
        request = self._prepare_governed_source_inputs(request, operation_kind=operation_kind)
        if operation_kind in GOVERNED_LOCAL_OPERATION_KINDS:
            return await self._create_governed_local_media_job(
                modality=modality,
                operation_kind=operation_kind,
                request=request,
            )
        has_explicit_model_identity = self._has_explicit_provider_or_model_selection(request)
        requested_adapter = str(request.get("adapter") or "").strip().lower()
        if requested_adapter and not has_explicit_model_identity:
            job = self._new_job(
                modality=modality,
                adapter="operation_unavailable",
                request={**request, "operationKind": operation_kind},
            )
            job["status"] = "failed"
            job["error"] = (
                "Creative Media adapter cannot authorize execution without a configured provider/model; "
                "configurationErrors=adapter_without_configured_model"
            )
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        if has_explicit_model_identity:
            explicit_candidate = self._explicit_enabled_model_candidate(
                request,
                modality=modality,
                operation_kind=operation_kind,
            )
            if not explicit_candidate:
                matches = self._matching_explicit_model_candidates(
                    request,
                    modality=modality,
                    operation_kind=operation_kind,
                )
                reason_codes = list(
                    dict.fromkeys(
                        [
                            *("model_not_enabled_for_operation" for item in matches if not bool(item.get("enabled", False))),
                            *(
                                str(code)
                                for item in matches
                                for code in list((item.get("readiness") or {}).get("reasonCodes") or [])
                                if str(code)
                            ),
                        ]
                    )
                )
                job = self._new_job(
                    modality=modality,
                    adapter="operation_unavailable",
                    request={**request, "operationKind": operation_kind},
                )
                job["status"] = "failed"
                reason_text = (
                    "configured model is not enabled; "
                    if "model_not_enabled_for_operation" in reason_codes
                    else ""
                )
                job["error"] = (
                    "Explicit Creative Media provider/model cannot execute the exact "
                    f"operationKind={operation_kind}; {reason_text}configurationErrors="
                    f"{','.join(reason_codes or ['configured_candidate_not_found'])}"
                )
                job["completedAt"] = utc_now_iso()
                return self._save_job(job)
            configured_adapter_value = str(explicit_candidate.get("adapter") or "").strip().lower()
            if requested_adapter and requested_adapter != configured_adapter_value:
                job = self._new_job(
                    modality=modality,
                    adapter="operation_unavailable",
                    request={**request, "operationKind": operation_kind},
                )
                job["status"] = "failed"
                job["error"] = (
                    "Requested Creative Media adapter conflicts with the configured model binding; "
                    "configurationErrors=requested_adapter_mismatch"
                )
                job["completedAt"] = utc_now_iso()
                return self._save_job(job)
            request = self._request_for_candidate(request, explicit_candidate)
        if not has_explicit_model_identity:
            preferred = self._preferred_model_candidates(operation_kind)
            if preferred:
                return await self._create_job_with_model_fallback(modality, operation_kind, request, preferred)
            if self._all_model_candidates_for_operation(operation_kind):
                job = self._new_job(modality=modality, adapter="operation_unavailable", request={**request, "operationKind": operation_kind})
                job["status"] = "failed"
                job["error"] = f"No enabled executable Creative Media model candidate is available for operationKind={operation_kind}"
                job["completedAt"] = utc_now_iso()
                return self._save_job(job)
            job = self._new_job(modality=modality, adapter="operation_unavailable", request={**request, "operationKind": operation_kind})
            job["status"] = "failed"
            job["error"] = f"No configured Creative Media model candidate exists for operationKind={operation_kind}"
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        if modality == "image":
            return await self._create_image_job(request)
        if modality == "video":
            return await self._create_video_job(request)
        if modality == "voice":
            return await self._create_voice_job(request)
        if modality == "music":
            return await self._create_music_job(request)
        if modality == "model3d":
            return await self._create_model3d_job(request)
        raise ValueError(f"Unsupported creative media modality: {modality}")

    async def _create_governed_local_media_job(
        self,
        *,
        modality: str,
        operation_kind: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        expected_modality = (
            "image" if operation_kind.startswith("image.")
            else "video" if operation_kind.startswith("video.")
            else "model3d" if operation_kind.startswith("model3d.")
            else "voice"
        )
        if modality != expected_modality:
            raise ValueError(f"operationKind={operation_kind} requires modality={expected_modality}")
        if operation_kind in {"image.compose_psd", "image.edit_psd_layers"}:
            return await self._create_governed_psd_job(
                modality=modality,
                operation_kind=operation_kind,
                request=request,
            )
        if operation_kind == "video.extract_holistic_motion":
            return await self._create_holistic_motion_job(
                modality=modality,
                operation_kind=operation_kind,
                request=request,
            )
        if operation_kind == "model3d.inspect_rigged":
            return await self._create_rig_inspection_job(
                modality=modality,
                operation_kind=operation_kind,
                request=request,
            )
        if operation_kind == "model3d.retarget_motion_godot":
            return await self._create_godot_retarget_job(
                modality=modality,
                operation_kind=operation_kind,
                request=request,
            )
        job = self._new_job(
            modality=modality,
            adapter="governed_ffmpeg",
            request={**request, "operationKind": operation_kind},
        )
        job["status"] = "running"
        self._save_job(job)
        extension = ".png" if operation_kind == "video.extract_frame_exact" else ".mp4" if operation_kind == "video.trim_exact" else ".flac"
        artifact_kind = "image" if operation_kind == "video.extract_frame_exact" else "video" if operation_kind == "video.trim_exact" else "audio"
        mime_type = "image/png" if operation_kind == "video.extract_frame_exact" else "video/mp4" if operation_kind == "video.trim_exact" else "audio/flac"
        output_label = "image-exact-frame" if operation_kind == "video.extract_frame_exact" else f"{artifact_kind}-exact-trim"
        output_path = self._output_path(job, output_label, extension)
        try:
            proof = await asyncio.to_thread(
                trim_governed_media_exact,
                {**request, "operationKind": operation_kind},
                output_path=output_path,
            )
            artifact = self._record_local_artifact(
                file_path=output_path,
                job=job,
                kind=artifact_kind,
                mime_type=mime_type,
                metadata={
                    "origin": "governed_local_edit",
                    "governedMediaProof": proof,
                    "providerInvoked": False,
                },
            )
            job["artifacts"] = [artifact]
            job["providerResponse"] = {
                "adapter": "governed_ffmpeg",
                "proofSchema": proof.get("schema"),
                "providerInvoked": False,
            }
            job["status"] = "succeeded"
            job["qualityStatus"] = "passed"
            job["completedAt"] = utc_now_iso()
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
        return self._save_job(job)

    async def _create_holistic_motion_job(
        self,
        *,
        modality: str,
        operation_kind: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        job = self._new_job(
            modality=modality,
            adapter="mediapipe_holistic",
            request={**request, "operationKind": operation_kind},
        )
        job["status"] = "running"
        self._save_job(job)
        output_path = self._output_path(job, "single-person-motion", ".v8motion")
        try:
            proof = await asyncio.to_thread(
                extract_holistic_motion,
                {**request, "operationKind": operation_kind},
                output_path=output_path,
            )
            manifest = dict(proof.get("manifest") or {})
            qa = dict(manifest.get("qa") or {})
            artifact = self._record_local_artifact(
                file_path=output_path,
                job=job,
                kind="motion",
                mime_type=MOTION_MIME_TYPE,
                metadata={
                    "origin": "governed_local_motion_capture",
                    "motionCaptureProof": proof,
                    "motionManifest": manifest,
                    "providerInvoked": False,
                },
            )
            job["artifacts"] = [artifact]
            job["providerResponse"] = {
                "adapter": "mediapipe_holistic",
                "proofSchema": proof.get("schema"),
                "providerInvoked": False,
            }
            job["status"] = "succeeded"
            job["qualityStatus"] = str(qa.get("status") or "warning")
            job["completedAt"] = utc_now_iso()
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
        return self._save_job(job)

    async def _create_rig_inspection_job(
        self,
        *,
        modality: str,
        operation_kind: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        job = self._new_job(
            modality=modality,
            adapter="gltf_rig_inspector",
            request={**request, "operationKind": operation_kind},
        )
        job["status"] = "running"
        self._save_job(job)
        output_path = self._output_path(job, "rig-profile", ".json")
        try:
            profile = await asyncio.to_thread(inspect_rigged_model, request)
            output_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            artifact = self._record_local_artifact(
                file_path=output_path,
                job=job,
                kind="document",
                mime_type="application/json",
                metadata={
                    "origin": "governed_local_rig_inspection",
                    "rigProfile": profile,
                    "providerInvoked": False,
                },
            )
            job["artifacts"] = [artifact]
            job["providerResponse"] = {
                "adapter": "gltf_rig_inspector",
                "profileSchema": profile.get("schema"),
                "providerInvoked": False,
            }
            job["status"] = "succeeded"
            job["qualityStatus"] = "passed" if profile.get("readyForGodotRetarget") else "warning"
            job["completedAt"] = utc_now_iso()
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
        return self._save_job(job)

    async def _create_godot_retarget_job(
        self,
        *,
        modality: str,
        operation_kind: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        job = self._new_job(
            modality=modality,
            adapter="godot_humanoid_v1",
            request={**request, "operationKind": operation_kind},
        )
        job["status"] = "running"
        self._save_job(job)
        output_path = self._output_path(job, "retargeted-motion", ".glb")
        try:
            canvas_inputs = [dict(item) for item in list(request.get("canvasInputs") or []) if isinstance(item, dict)]
            motion_inputs = [item for item in canvas_inputs if str(item.get("portId") or "") == "motion"]
            model_inputs = [item for item in canvas_inputs if str(item.get("portId") or "") == "model"]
            if len(motion_inputs) != 1 or len(model_inputs) != 1:
                raise ValueError("Godot retarget requires exactly one motion package and one rigged model")
            session_id = str(request.get("sessionId") or "").strip()
            motion_path = self._canvas_input_path(session_id=session_id, item=motion_inputs[0])
            model_path = self._canvas_input_path(session_id=session_id, item=model_inputs[0])
            proof = await asyncio.to_thread(
                retarget_motion_with_godot,
                motion_path=motion_path,
                model_path=model_path,
                output_path=output_path,
                minimum_confidence=float(request.get("minimumConfidence") or 0.5),
            )
            artifact = self._record_local_artifact(
                file_path=output_path,
                job=job,
                kind="model3d",
                mime_type="model/gltf-binary",
                metadata={
                    "origin": "governed_local_godot_retarget",
                    "godotRetargetProof": proof,
                    "providerInvoked": False,
                },
            )
            job["artifacts"] = [artifact]
            job["providerResponse"] = {
                "adapter": "godot_humanoid_v1",
                "proofSchema": proof.get("schema"),
                "providerInvoked": False,
            }
            job["status"] = "succeeded"
            job["qualityStatus"] = "passed"
            job["completedAt"] = utc_now_iso()
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
        return self._save_job(job)

    @staticmethod
    def _canvas_input_path(*, session_id: str, item: dict[str, Any]) -> Path:
        origin = str(item.get("origin") or "").strip()
        resource_id = str(item.get("id") or "").strip()
        if not session_id or not origin or not resource_id:
            raise ValueError("Canvas input is missing its session-bound resource reference")
        if origin == "source":
            return workspace_media_library.resolve_source_path(session_id=session_id, source_id=resource_id)
        if origin == "artifact":
            return workspace_media_library.resolve_artifact_path(session_id=session_id, artifact_id=resource_id)
        if origin == "workspace_asset":
            return workspace_media_library.resolve_asset_path(
                session_id=session_id,
                asset_id=resource_id,
                require_session_use=True,
            )
        raise ValueError("Canvas input origin is not governed")

    async def _create_governed_psd_job(
        self,
        *,
        modality: str,
        operation_kind: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        from core.tools.native.creative_media_psd import compose_psd_document, edit_psd_document

        session_id = str(request.get("sessionId") or request.get("session_id") or "").strip()
        canvas_inputs = [dict(item) for item in list(request.get("canvasInputs") or []) if isinstance(item, dict)]
        job = self._new_job(
            modality=modality,
            adapter="governed_psd_tools",
            request={**request, "operationKind": operation_kind},
        )
        job["status"] = "running"
        self._save_job(job)
        output_path = self._output_path(job, "layered-document", ".psd")
        preview_path = output_path.with_name(f"{output_path.stem}-preview.png")
        try:
            if operation_kind == "image.compose_psd":
                layer_inputs = sorted(
                    (item for item in canvas_inputs if str(item.get("portId") or "") == "layers"),
                    key=lambda item: (int(item.get("order") or 0), str(item.get("sourceNodeId") or "")),
                )
                if not layer_inputs:
                    raise ValueError("PSD composition requires connected image or PSD layer inputs")
                configured_layers = [dict(item) for item in list(request.get("layers") or []) if isinstance(item, dict)]
                configured_by_node = {
                    str(item.get("sourceNodeId") or ""): item
                    for item in configured_layers
                    if str(item.get("sourceNodeId") or "")
                }
                resolved_layers: list[dict[str, Any]] = []
                for index, item in enumerate(layer_inputs):
                    source_path = self._canvas_input_path(session_id=session_id, item=item)
                    configured = dict(configured_by_node.get(str(item.get("sourceNodeId") or "")) or (configured_layers[index] if index < len(configured_layers) else {}))
                    resolved_layers.append({
                        **configured,
                        "source": source_path,
                        "name": str(configured.get("name") or source_path.stem),
                        "order": int(configured.get("order") if configured.get("order") is not None else index),
                    })
                resolved_layers.sort(key=lambda item: int(item.get("order") or 0))
                manifest = await asyncio.to_thread(
                    compose_psd_document,
                    output_path=output_path,
                    preview_path=preview_path,
                    canvas=dict(request.get("canvas") or {}),
                    layers=resolved_layers,
                )
            else:
                psd_inputs = [item for item in canvas_inputs if str(item.get("portId") or "") == "psd"]
                if len(psd_inputs) != 1:
                    raise ValueError("PSD layer editing requires exactly one connected PSD input")
                source_path = self._canvas_input_path(session_id=session_id, item=psd_inputs[0])
                if source_path.suffix.lower() != ".psd":
                    raise ValueError("PSD layer editing requires a .psd input")
                manifest = await asyncio.to_thread(
                    edit_psd_document,
                    source_path=source_path,
                    output_path=output_path,
                    preview_path=preview_path,
                    edits=[dict(item) for item in list(request.get("edits") or []) if isinstance(item, dict)],
                )
            artifact = self._record_local_artifact(
                file_path=output_path,
                job=job,
                kind="document",
                mime_type="image/vnd.adobe.photoshop",
                metadata={
                    "origin": "governed_local_psd",
                    "providerInvoked": False,
                    "psdManifestSchema": manifest.get("schema"),
                    "psdLayerCount": manifest.get("layerCount"),
                    "psdPreviewPath": str(preview_path),
                },
            )
            job["artifacts"] = [artifact]
            job["providerResponse"] = {
                "adapter": "governed_psd_tools",
                "manifestSchema": manifest.get("schema"),
                "layerCount": manifest.get("layerCount"),
                "providerInvoked": False,
            }
            job["status"] = "succeeded"
            job["qualityStatus"] = "passed"
            job["completedAt"] = utc_now_iso()
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            for path in (output_path, preview_path):
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass
        return self._save_job(job)

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
                elif modality == "music":
                    job = await self._create_music_job(attempt_request)
                elif modality == "model3d":
                    job = await self._create_model3d_job(attempt_request)
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
        endpoint_binding: dict[str, Any] = {}
        binding_error = ""
        if any(str(request.get(key) or "").strip() for key in ("providerId", "provider_id", "model", "modelId", "model_id", "modelRef", "model_ref")):
            try:
                endpoint_binding = self._configured_endpoint_binding(request, default_model="gpt-image-2")
            except ValueError as exc:
                if not str(request.get("adapter") or "").strip():
                    binding_error = str(exc)
        if endpoint_binding.get("operationKind") and not str(request.get("operationKind") or request.get("operation_kind") or "").strip():
            operation_kind = str(endpoint_binding["operationKind"])
        adapter = str(request.get("adapter") or "").strip().lower()
        provider_id = str(request.get("providerId") or request.get("provider_id") or "").strip()
        provider_prompt, prompt_policy = self._prepare_prompt_for_provider(request, modality="image")
        prepared_request = {**request, "prompt": provider_prompt, "operationKind": operation_kind}
        if endpoint_binding:
            prepared_request["endpointBinding"] = {
                key: value
                for key, value in endpoint_binding.items()
                if key not in {"providerMeta", "modelData"}
            }
            prepared_request["providerId"] = endpoint_binding.get("providerId") or prepared_request.get("providerId")
        if prompt_policy:
            prepared_request["promptPolicy"] = prompt_policy
        job = self._new_job(modality="image", adapter=adapter, request=prepared_request)
        self._record_safety_event(source="job_create", job=job, transform=dict(prompt_policy.get("safetyTransform") or {}) if prompt_policy else {})
        self._save_job(job)
        try:
            if binding_error:
                raise ValueError(binding_error)
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
        endpoint_binding: dict[str, Any] = {}
        binding_error = ""
        if any(str(request.get(key) or "").strip() for key in ("providerId", "provider_id", "model", "modelId", "model_id", "modelRef", "model_ref")):
            try:
                endpoint_binding = self._configured_endpoint_binding(request, default_model="")
            except ValueError as exc:
                if not str(request.get("adapter") or "").strip():
                    binding_error = str(exc)
        if endpoint_binding.get("operationKind") and not str(request.get("operationKind") or request.get("operation_kind") or "").strip():
            operation_kind = str(endpoint_binding["operationKind"])
        adapter = str(request.get("adapter") or "").strip().lower()
        provider_prompt, prompt_policy = self._prepare_prompt_for_provider(request, modality="video")
        prepared_request = {**request, "prompt": provider_prompt, "operationKind": operation_kind}
        if endpoint_binding:
            prepared_request["endpointBinding"] = {
                key: value
                for key, value in endpoint_binding.items()
                if key not in {"providerMeta", "modelData"}
            }
            prepared_request["providerId"] = endpoint_binding.get("providerId") or prepared_request.get("providerId")
        if prompt_policy:
            prepared_request["promptPolicy"] = prompt_policy
        job = self._new_job(modality="video", adapter=adapter, request=prepared_request)
        self._record_safety_event(source="job_create", job=job, transform=dict(prompt_policy.get("safetyTransform") or {}) if prompt_policy else {})
        self._save_job(job)
        try:
            if binding_error:
                raise ValueError(binding_error)
            if adapter == "volcengine_ark":
                if operation_kind not in {"video.text_to_video", "video.image_to_video", "video.first_last_frame"}:
                    raise ValueError(f"Volcengine adapter does not support operationKind={operation_kind}")
                job = await self._submit_volcengine_video_job(job, prepared_request)
            elif adapter == "dashscope":
                job = await self._submit_dashscope_video_job(job, prepared_request)
            elif adapter == "comfyui_workflow":
                job = await self._submit_comfyui_workflow_job(job, prepared_request)
            elif adapter == "agnes_video":
                job = await self._submit_agnes_video_job(job, prepared_request)
            elif adapter == "minimax_video":
                job = await self._submit_minimax_video_job(job, prepared_request)
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
                    with ToolExecutionEnvelope(tool_name="creative_media_jobs.create.internal", family="creative_media", deadline_ms=timeout_seconds * 1000, retry_limit=1) as envelope:
                        job["toolExecution"] = envelope.payload(
                            ok=False,
                            failure_class="deadline_exceeded",
                            retryable=False,
                            recommended_next_action="返回 running job；使用 creative_media_jobs 的 get 或 list action 观察后续状态。",
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
        adapter = str(request.get("adapter") or "").strip().lower()
        prepared_request = {**request, "operationKind": operation_kind}
        job = self._new_job(modality="voice", adapter=adapter, request=prepared_request)
        self._save_job(job)
        try:
            if adapter == "minimax_tts":
                if operation_kind == "voice.tts":
                    return await self._run_minimax_tts_job(job, prepared_request)
                if operation_kind == "voice.design":
                    return await self._run_minimax_voice_design_job(job, prepared_request)
                raise ValueError(f"Unsupported MiniMax voice operationKind={operation_kind}")
            if operation_kind != "voice.tts":
                raise ValueError(f"Unsupported voice operationKind={operation_kind}")
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

    async def _run_minimax_tts_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, model = self._configured_provider_for_model(request, default_model="t2a_v2/speech-2.8-hd")
        model = self._strip_provider_model_prefix(model)
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        text = str(request.get("text") or request.get("prompt") or "").strip()
        if not text:
            raise ValueError("MiniMax voice.tts job requires text")
        audio_format = str(request.get("format") or request.get("audioFormat") or request.get("audio_format") or "mp3").strip().lower() or "mp3"
        speed: float | str = 1
        if request.get("speed") not in (None, ""):
            try:
                speed = float(str(request.get("speed")))
            except ValueError:
                speed = str(request.get("speed"))
        try:
            volume = int(float(str(request.get("volume") or request.get("vol") or 1)))
        except ValueError:
            volume = 1
        try:
            pitch = int(float(str(request.get("pitch") or 0)))
        except ValueError:
            pitch = 0
        voice_id = str(
            request.get("voiceId")
            or request.get("voice_id")
            or request.get("voice")
            or (dict(request.get("voiceSetting") or request.get("voice_setting") or {}).get("voice_id"))
            or "male-qn-qingse"
        ).strip()
        payload: dict[str, Any] = {
            "model": model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": volume,
                "pitch": pitch,
            },
            "audio_setting": {
                "sample_rate": int(request.get("sampleRate") or request.get("sample_rate") or 32000),
                "bitrate": int(request.get("bitrate") or 128000),
                "format": audio_format,
                "channel": int(request.get("channel") or 1),
            },
            "output_format": "hex",
            "subtitle_enable": bool(request.get("subtitleEnable") or request.get("subtitle_enable") or False),
        }
        emotion = str(request.get("emotion") or "").strip()
        if emotion:
            payload["voice_setting"]["emotion"] = emotion
        pronunciation_dict = request.get("pronunciationDict") or request.get("pronunciation_dict")
        if isinstance(pronunciation_dict, dict) and pronunciation_dict:
            payload["pronunciation_dict"] = pronunciation_dict
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            self._join_api_path(base_url, "/v1/t2a_v2"),
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=180,
        )
        base_resp = dict(response.get("base_resp") or {})
        if base_resp and int(base_resp.get("status_code") or 0) != 0:
            raise RuntimeError(str(base_resp.get("status_msg") or "MiniMax TTS failed"))
        audio_hex = str(((response.get("data") or {}) or {}).get("audio") or "").strip()
        if not audio_hex:
            raise RuntimeError("MiniMax TTS response did not include data.audio")
        artifact = self._artifact_from_hex(
            audio_hex,
            job=job,
            kind="audio",
            provider=provider_id,
            mime_type=mimetypes.types_map.get(f".{audio_format}", "audio/mpeg"),
            extension=f".{audio_format.lstrip('.') or 'mp3'}",
            metadata={
                "model": model,
                "operationKind": job.get("operationKind"),
                "voiceId": voice_id,
                "traceId": response.get("trace_id"),
                "creativeMediaVoiceTts": True,
                "systemVoiceProtocol": False,
            },
        )
        job.update(
            {
                "status": "succeeded",
                "artifacts": [artifact],
                "providerResponse": {
                    "providerId": provider_id,
                    "model": model,
                    "voiceId": voice_id,
                    "traceId": response.get("trace_id"),
                    "extraInfo": response.get("extra_info") or {},
                    "operationKind": job.get("operationKind"),
                },
                "completedAt": utc_now_iso(),
            }
        )
        return self._save_job(job)

    async def _run_minimax_voice_design_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, _model = self._configured_provider_for_model(request, default_model="t2a_v2/speech-2.8-hd")
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        prompt = str(
            request.get("voicePrompt")
            or request.get("voice_prompt")
            or request.get("voiceDescription")
            or request.get("voice_description")
            or request.get("prompt")
            or ""
        ).strip()
        preview_text = str(
            request.get("previewText")
            or request.get("preview_text")
            or request.get("text")
            or "这是一段用于试听新音色的 V8 Agent OS 创意媒体旁白。"
        ).strip()
        if not prompt:
            raise ValueError("MiniMax voice.design job requires voicePrompt or prompt")
        if not preview_text:
            raise ValueError("MiniMax voice.design job requires previewText or text")
        payload: dict[str, Any] = {
            "prompt": prompt,
            "preview_text": preview_text[:500],
        }
        voice_id = str(request.get("voiceId") or request.get("voice_id") or "").strip()
        if voice_id:
            payload["voice_id"] = voice_id
        if "aigcWatermark" in request or "aigc_watermark" in request:
            payload["aigc_watermark"] = bool(request.get("aigcWatermark") if "aigcWatermark" in request else request.get("aigc_watermark"))
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            self._join_api_path(base_url, "/v1/voice_design"),
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=180,
        )
        base_resp = dict(response.get("base_resp") or {})
        if base_resp and int(base_resp.get("status_code") or 0) != 0:
            raise RuntimeError(str(base_resp.get("status_msg") or "MiniMax voice design failed"))
        designed_voice_id = str(response.get("voice_id") or voice_id or "").strip()
        trial_audio = str(response.get("trial_audio") or "").strip()
        if not designed_voice_id:
            raise RuntimeError("MiniMax voice design response did not include voice_id")
        artifacts: list[dict[str, Any]] = []
        if trial_audio:
            artifacts.append(
                self._artifact_from_hex(
                    trial_audio,
                    job=job,
                    kind="audio",
                    provider=provider_id,
                    mime_type="audio/mpeg",
                    extension=".mp3",
                    metadata={
                        "operationKind": job.get("operationKind"),
                        "voiceId": designed_voice_id,
                        "previewTextLength": len(preview_text),
                        "creativeMediaVoiceDesign": True,
                        "systemVoiceProtocol": False,
                    },
                )
            )
        job.update(
            {
                "status": "succeeded",
                "artifacts": artifacts,
                "providerResponse": {
                    "providerId": provider_id,
                    "voiceId": designed_voice_id,
                    "operationKind": job.get("operationKind"),
                    "trialAudioArtifactId": (artifacts[0] or {}).get("artifactId") if artifacts else "",
                },
                "completedAt": utc_now_iso(),
            }
        )
        return self._save_job(job)

    async def _create_music_job(self, request: dict[str, Any]) -> dict[str, Any]:
        operation_kind = self._operation_kind_for_request("music", request)
        if operation_kind not in {"music.generate", "music.cover"}:
            job = self._new_job(modality="music", adapter="operation_unsupported", request={**request, "operationKind": operation_kind})
            job["status"] = "failed"
            job["error"] = f"Unsupported music operationKind={operation_kind}; music.brief is planning-only."
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        adapter = str(request.get("adapter") or "").strip().lower()
        prepared_request = {**request, "operationKind": operation_kind}
        job = self._new_job(modality="music", adapter=adapter, request=prepared_request)
        self._save_job(job)
        try:
            if adapter == "minimax_music":
                return await self._run_minimax_music_job(job, prepared_request)
            if adapter == "mureka_music":
                job = await self._submit_mureka_music_job(job, prepared_request)
                if bool(request.get("wait", False)):
                    job = await self._wait_for_async_job(job, request)
                return job
            raise ValueError(f"Unsupported music adapter: {adapter}")
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)

    async def _create_model3d_job(self, request: dict[str, Any]) -> dict[str, Any]:
        operation_kind = self._operation_kind_for_request("model3d", request)
        if operation_kind != "model3d.generate":
            job = self._new_job(modality="model3d", adapter="operation_unsupported", request={**request, "operationKind": operation_kind})
            job["status"] = "failed"
            job["error"] = f"Unsupported model3d operationKind={operation_kind}"
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        adapter = str(request.get("adapter") or "").strip().lower()
        prepared_request = {**request, "operationKind": operation_kind}
        job = self._new_job(modality="model3d", adapter=adapter, request=prepared_request)
        self._save_job(job)
        try:
            if adapter == "tencent_hunyuan_3d":
                job = await self._submit_tencent_hunyuan_3d_job(job, prepared_request)
                if bool(request.get("wait", False)):
                    job = await self._wait_for_async_job(job, request)
                return job
            raise ValueError(f"Unsupported model3d adapter: {adapter}")
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = _exception_summary(exc)
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)

    async def _wait_for_async_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        timeout_seconds = max(15, min(int(request.get("timeoutSeconds") or request.get("timeout_seconds") or 300), 900))
        poll_interval = max(2, min(int(request.get("pollIntervalSeconds") or request.get("poll_interval_seconds") or 8), 60))
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while job.get("status") not in {"succeeded", "failed", "cancelled"} and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(poll_interval)
            refreshed = await self.refresh_job(str(job.get("jobId") or ""))
            if refreshed:
                job = refreshed
        if job.get("status") not in {"succeeded", "failed", "cancelled"}:
            job["status"] = "running"
            with ToolExecutionEnvelope(tool_name="creative_media_jobs.create.internal", family="creative_media", deadline_ms=timeout_seconds * 1000, retry_limit=1) as envelope:
                job["toolExecution"] = envelope.payload(
                    ok=False,
                    failure_class="deadline_exceeded",
                    retryable=False,
                    recommended_next_action="返回 running job；使用 creative_media_jobs 的 get 或 list action 观察后续状态。",
                )
            job["recommendedNextAction"] = "observe_job"
            return self._save_job(job)
        return job

    async def _run_minimax_music_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, model = self._configured_provider_for_model(request, default_model="music_generation/music-2.6")
        model = self._strip_provider_model_prefix(model)
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        prompt = str(request.get("prompt") or request.get("brief") or "").strip()
        if not prompt:
            raise ValueError("music job requires prompt")
        output_format = str(request.get("outputFormat") or request.get("output_format") or "hex").strip().lower() or "hex"
        audio_format = str(request.get("format") or request.get("audioFormat") or request.get("audio_format") or "mp3").strip().lower() or "mp3"
        audio_setting = {
            "sample_rate": int(request.get("sampleRate") or request.get("sample_rate") or 44100),
            "bitrate": int(request.get("bitrate") or 256000),
            "format": audio_format,
        }
        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "output_format": output_format,
            "audio_setting": audio_setting,
        }
        lyrics = str(request.get("lyrics") or "").strip()
        is_instrumental = _truthy(request.get("isInstrumental") if "isInstrumental" in request else request.get("is_instrumental"))
        if lyrics:
            payload["lyrics"] = lyrics
        if is_instrumental:
            payload["is_instrumental"] = True
        if str(job.get("operationKind") or "") == "music.cover":
            audio_url = self._public_url_or_error(request.get("audioUrl") or request.get("audio_url"), field_name="audioUrl")
            audio_base64 = str(request.get("audioBase64") or request.get("audio_base64") or "").strip()
            cover_feature_id = str(request.get("coverFeatureId") or request.get("cover_feature_id") or "").strip()
            if audio_url:
                payload["audio_url"] = audio_url
            if audio_base64:
                payload["audio_base64"] = audio_base64
            if cover_feature_id:
                payload["cover_feature_id"] = cover_feature_id
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            self._join_api_path(base_url, "/v1/music_generation"),
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=300,
        )
        base_resp = dict(response.get("base_resp") or {})
        if base_resp and int(base_resp.get("status_code") or 0) != 0:
            raise RuntimeError(str(base_resp.get("status_msg") or "MiniMax music generation failed"))
        data = dict(response.get("data") or {})
        if data.get("audio"):
            artifact = self._artifact_from_hex(
                str(data["audio"]),
                job=job,
                kind="audio",
                provider=provider_id,
                mime_type=mimetypes.types_map.get(f".{audio_format}", "audio/mpeg"),
                extension=f".{audio_format.lstrip('.') or 'mp3'}",
                metadata={"model": model, "operationKind": job.get("operationKind"), "traceId": response.get("trace_id")},
            )
        elif data.get("audio_url") or data.get("url"):
            artifact = await self._artifact_from_url(
                str(data.get("audio_url") or data.get("url")),
                job=job,
                kind="audio",
                provider=provider_id,
                mime_hint=mimetypes.types_map.get(f".{audio_format}", "audio/mpeg"),
                metadata={"model": model, "operationKind": job.get("operationKind"), "traceId": response.get("trace_id")},
            )
        else:
            raise RuntimeError("MiniMax music response did not include data.audio or data.audio_url")
        job.update(
            {
                "status": "succeeded",
                "artifacts": [artifact],
                "providerResponse": {
                    "providerId": provider_id,
                    "model": model,
                    "traceId": response.get("trace_id"),
                    "extraInfo": response.get("extra_info") or {},
                    "operationKind": job.get("operationKind"),
                },
                "completedAt": utc_now_iso(),
            }
        )
        return self._save_job(job)

    async def _submit_mureka_music_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, configured_model = self._configured_provider_for_model(request, default_model="auto")
        model = "mureka-o1" if configured_model in {"", "auto"} else self._strip_provider_model_prefix(configured_model)
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        prompt = str(request.get("prompt") or request.get("brief") or "").strip()
        lyrics = str(request.get("lyrics") or "").strip()
        if not prompt and not lyrics:
            raise ValueError("Mureka music job requires prompt or lyrics")
        payload: dict[str, Any] = {"model": model}
        if prompt:
            payload["prompt"] = prompt
        if lyrics:
            payload["lyrics"] = lyrics
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            self._join_api_path(base_url, "/v1/song/generate"),
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=120,
        )
        task_id = self._extract_task_id(response)
        if not task_id:
            raise RuntimeError(f"Mureka response did not include task_id: {self._compact_provider_response(response)}")
        job["status"] = normalize_provider_status(response.get("status") or response.get("state") or response.get("task_status") or "queued", provider="mureka")
        if job["status"] == "succeeded":
            job["status"] = "running"
        job["providerTaskId"] = task_id
        job["providerResponse"] = {"providerId": provider_id, "taskId": task_id, "model": model, "operationKind": job.get("operationKind")}
        return self._save_job(job)

    async def _poll_mureka_music_job(self, job: dict[str, Any]) -> dict[str, Any]:
        task_id = str(job.get("providerTaskId") or "").strip()
        if not task_id:
            job["status"] = "failed"
            job["error"] = "Missing providerTaskId"
            return self._save_job(job)
        request = dict(job.get("request") or {})
        provider_id, provider_meta, configured_model = self._configured_provider_for_model(request, default_model="auto")
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        response = await self._request_json(
            "GET",
            self._join_api_path(base_url, f"/v1/song/query/{task_id}"),
            headers=self._bearer_headers(api_key),
            timeout=60,
        )
        status = self._normalize_async_status(response.get("status") or response.get("state") or response.get("task_status") or response.get("code"))
        job["status"] = status
        job["providerResponse"] = {**dict(job.get("providerResponse") or {}), "lastStatus": response.get("status") or response.get("state"), "taskId": task_id}
        if status == "succeeded":
            result_url = self._find_first_url(response, preferred_extensions=AUDIO_EXTENSIONS)
            if not result_url:
                job["status"] = "failed"
                job["error"] = "Mureka task succeeded without audio URL"
            else:
                artifact = await self._artifact_from_url(
                    result_url,
                    job=job,
                    kind="audio",
                    provider=provider_id,
                    mime_hint="audio/mpeg",
                    metadata={"model": "mureka-o1" if configured_model in {"", "auto"} else configured_model, "taskId": task_id},
                )
                job["artifacts"] = [artifact]
                job["completedAt"] = utc_now_iso()
        elif status == "failed":
            job["error"] = str(response.get("error") or response.get("message") or "Mureka music task failed")
            job["completedAt"] = utc_now_iso()
        return self._save_job(job)

    async def _submit_tencent_hunyuan_3d_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        provider_id, provider_meta, model = self._configured_provider_for_model(request, default_model="hy-3d-3.0")
        model = self._strip_provider_model_prefix(model)
        endpoints = self._tencent_tokenhub_3d_endpoints(provider_meta)
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not api_key:
            raise ValueError(f"Provider {provider_id} requires api_key for Tencent TokenHub 3D")
        prompt = str(request.get("prompt") or request.get("brief") or "").strip()
        if not prompt and not (request.get("imageUrl") or request.get("image_url")):
            raise ValueError("model3d job requires prompt or imageUrl")
        payload: dict[str, Any] = {"model": model}
        if prompt:
            payload["prompt"] = prompt
        image_url = self._public_url_or_error(request.get("imageUrl") or request.get("image_url"), field_name="imageUrl")
        if image_url:
            payload["image_url"] = image_url
        result_format = str(request.get("resultFormat") or request.get("result_format") or "GLB").strip()
        if result_format:
            payload["result_format"] = result_format
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            endpoints["submit"],
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=120,
        )
        task_id = self._extract_task_id(response)
        if not task_id:
            raise RuntimeError(f"Tencent 3D response did not include id: {self._compact_provider_response(response)}")
        job["status"] = self._normalize_async_status(response.get("status") or "queued")
        if job["status"] == "succeeded":
            job["status"] = "running"
        job["providerTaskId"] = task_id
        job["providerResponse"] = {
            "providerId": provider_id,
            "taskId": task_id,
            "model": model,
            "operationKind": job.get("operationKind"),
            "endpointOverride": endpoints.get("overrideReason") or "",
        }
        return self._save_job(job)

    async def _poll_tencent_hunyuan_3d_job(self, job: dict[str, Any]) -> dict[str, Any]:
        task_id = str(job.get("providerTaskId") or "").strip()
        if not task_id:
            job["status"] = "failed"
            job["error"] = "Missing providerTaskId"
            return self._save_job(job)
        request = dict(job.get("request") or {})
        provider_id, provider_meta, model = self._configured_provider_for_model(request, default_model="hy-3d-3.0")
        model = self._strip_provider_model_prefix(model)
        endpoints = self._tencent_tokenhub_3d_endpoints(provider_meta)
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        response = await self._request_json(
            "POST",
            endpoints["query"],
            headers=self._bearer_headers(api_key),
            json={"model": model, "id": task_id},
            timeout=60,
        )
        status = self._normalize_async_status(response.get("status") or response.get("state"))
        job["status"] = status
        job["providerResponse"] = {**dict(job.get("providerResponse") or {}), "lastStatus": response.get("status") or response.get("state"), "taskId": task_id}
        if status == "succeeded":
            result_url = self._best_model3d_url(response)
            if not result_url:
                job["status"] = "failed"
                job["error"] = "Tencent 3D task succeeded without model file URL"
            else:
                artifact = await self._artifact_from_url(
                    result_url,
                    job=job,
                    kind="model3d",
                    provider=provider_id,
                    mime_hint="model/gltf-binary",
                    metadata={"model": model, "taskId": task_id},
                )
                job["artifacts"] = [artifact]
                job["completedAt"] = utc_now_iso()
        elif status == "failed":
            job["error"] = str(response.get("error") or response.get("message") or "Tencent Hunyuan 3D task failed")
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
        overrides = _PROVIDER_CREDENTIAL_OVERRIDES.get()
        return {
            "apiKey": str(overrides.get("DASHSCOPE_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "").strip(),
            "baseUrl": str(
                overrides.get("DASHSCOPE_BASE_URL")
                or os.getenv("DASHSCOPE_BASE_URL")
                or "https://dashscope.aliyuncs.com/api/v1"
            ).rstrip("/"),
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

    def _artifact_provider_transport_url(self, artifact_id: str) -> str:
        normalized = str(artifact_id or "").strip()
        if not normalized:
            return ""
        transient = str(self._provider_transport_urls.get(normalized) or "").strip()
        if transient:
            return transient
        record = db.get_runtime_artifact(normalized) or {}
        external_url = str(record.get("external_url") or record.get("externalUrl") or "").strip()
        return self._public_url_or_error(external_url, field_name="artifact externalUrl") if external_url else ""

    def _local_image_data_url(self, artifact_id: str) -> str:
        source_path = self._artifact_source_path(artifact_id)
        if not source_path:
            raise ValueError(f"Image artifact {artifact_id} has no local source file")
        path = Path(source_path).expanduser()
        if not path.is_file():
            raise ValueError(f"Image artifact {artifact_id} source file is unavailable")
        size_bytes = path.stat().st_size
        if size_bytes >= 20 * 1024 * 1024:
            raise ValueError(f"MiniMax image input {path.name} must be smaller than 20 MB")
        try:
            from PIL import Image

            with Image.open(path) as image:
                image_format = str(image.format or "").upper()
                width, height = image.size
        except Exception as exc:
            raise ValueError(f"MiniMax image input {path.name} could not be decoded: {_exception_summary(exc)}") from exc
        mime_type = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }.get(image_format)
        if not mime_type:
            raise ValueError("MiniMax image input must be JPG, JPEG, PNG, or WebP")
        if min(width, height) <= 300:
            raise ValueError("MiniMax image input short edge must be greater than 300 pixels")
        ratio = width / height
        if ratio < 0.4 or ratio > 2.5:
            raise ValueError("MiniMax image input aspect ratio must be between 2:5 and 5:2")
        return f"data:{mime_type};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    def _minimax_image_references_from_request(
        self,
        request: dict[str, Any],
        *,
        operation_kind: str,
    ) -> list[str]:
        references = self._image_urls_from_request(request)
        artifact_ids = request.get("referenceAssetIds") or request.get("reference_asset_ids") or []
        if isinstance(artifact_ids, str):
            artifact_ids = [artifact_ids]
        for artifact_id in list(artifact_ids or []):
            normalized_id = str(artifact_id or "").strip()
            if not normalized_id:
                continue
            provider_url = self._artifact_provider_transport_url(normalized_id)
            if provider_url:
                references.append(provider_url)
                continue
            if operation_kind == "video.reference_to_video":
                raise ValueError(
                    "MiniMax S2V requires a provider-accessible character image URL; "
                    "regenerate the source image through a URL-returning V8 image provider in the current Engine process "
                    "or provide a public imageUrls value"
                )
            references.append(self._local_image_data_url(normalized_id))
        return list(dict.fromkeys(references))

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
        if operation_kind in DASHSCOPE_VIDEO_OPERATION_KINDS:
            return "/services/aigc/video-generation/video-synthesis"
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

    def _configured_model_context(
        self,
        request: dict[str, Any],
        *,
        default_model: str,
    ) -> tuple[str, dict[str, Any], str, dict[str, Any], dict[str, Any]]:
        config = model_control_plane.get_config()
        providers = dict(config.get("providers") or {})
        model_ref = parse_model_ref(str(request.get("modelRef") or request.get("model_ref") or ""))
        requested_provider = str(request.get("providerId") or request.get("provider_id") or "").strip()
        requested_model = str(request.get("model") or request.get("modelId") or request.get("model_id") or default_model).strip()
        if model_ref:
            requested_provider = requested_provider or model_ref[0]
            if not any(str(request.get(key) or "").strip() for key in ("model", "modelId", "model_id")):
                requested_model = model_ref[1]
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
            model_key = requested_model if requested_model in models else ""
            if not model_key:
                for candidate_model_id, candidate_model_data in models.items():
                    provider_model_id = self._provider_model_id(str(candidate_model_id), dict(candidate_model_data or {}))
                    if requested_model in {provider_model_id, self._strip_provider_model_prefix(provider_model_id), self._strip_provider_model_prefix(str(candidate_model_id))}:
                        model_key = str(candidate_model_id)
                        break
            if not model_key:
                continue
            provider_meta = dict((provider_data or {}).get("provider") or {})
            name = str(provider_meta.get("name") or provider_id).lower()
            target = preferred if "local2" in name or "local2" in provider_id.lower() else fallback
            target.append((provider_id, {**dict(provider_data or {}), "_selectedModelKey": model_key}))
        selected = (preferred or fallback)
        if not selected:
            raise ValueError(f"No configured provider exposes model: {requested_model}")
        provider_id, provider_data = selected[0]
        selected_model_key = str(provider_data.get("_selectedModelKey") or requested_model)
        model_data = dict(((provider_data or {}).get("models") or {}).get(selected_model_key) or {})
        provider_meta = dict((provider_data or {}).get("provider") or {})
        binding = build_model_endpoint_binding(provider_id, selected_model_key, provider_meta, model_data)
        return provider_id, provider_meta, binding.get("providerModelId") or requested_model, model_data, binding

    def _configured_provider_for_model(self, request: dict[str, Any], *, default_model: str) -> tuple[str, dict[str, Any], str]:
        provider_id, provider_meta, model, _model_data, _binding = self._configured_model_context(
            request,
            default_model=default_model,
        )
        return provider_id, provider_meta, model

    def _configured_endpoint_binding(self, request: dict[str, Any], *, default_model: str) -> dict[str, Any]:
        provider_id, provider_meta, provider_model_id, model_data, binding = self._configured_model_context(
            request,
            default_model=default_model,
        )
        return {
            **binding,
            "providerId": provider_id,
            "providerMeta": provider_meta,
            "providerModelId": provider_model_id,
            "modelData": model_data,
        }

    @staticmethod
    def _provider_model_id(model_id: str, model_data: dict[str, Any] | None = None) -> str:
        endpoint_binding = dict((model_data or {}).get("endpointBinding") or {})
        binding_model_id = str(endpoint_binding.get("providerModelId") or "").strip()
        if binding_model_id:
            return binding_model_id
        media_limits = dict((model_data or {}).get("mediaLimits") or {})
        provider_model_id = str(media_limits.get("providerModelId") or "").strip()
        if provider_model_id:
            return provider_model_id
        raw_model_id = str(model_id or "").strip().lstrip("/")
        model_type = str((model_data or {}).get("type") or "").strip().upper()
        if model_type in {"IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D", "MEDIA"}:
            for route_prefix in (
                "images/generations/",
                "images/edits/",
                "image_generation/",
                "video_generation/",
                "music_generation/",
                "audio/speech/",
                "t2a_v2/",
            ):
                if raw_model_id.startswith(route_prefix):
                    return raw_model_id[len(route_prefix) :]
        return raw_model_id

    @staticmethod
    def _strip_provider_model_prefix(model_id: str) -> str:
        raw = str(model_id or "").strip()
        return raw.rsplit("/", 1)[-1] if "/" in raw else raw

    @staticmethod
    def _join_api_path(base_url: str, path: str) -> str:
        base = str(base_url or "").rstrip("/")
        suffix = str(path or "").strip()
        if not suffix.startswith("/"):
            suffix = f"/{suffix}"
        if base.endswith("/v1") and suffix.startswith("/v1/"):
            suffix = suffix[3:]
        return f"{base}{suffix}"

    async def _run_openai_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        binding = self._configured_endpoint_binding(request, default_model="gpt-image-2")
        provider_id = str(binding.get("providerId") or "")
        provider_meta = dict(binding.get("providerMeta") or {})
        model = str(binding.get("providerModelId") or "gpt-image-2")
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
        response_format = str(request.get("responseFormat") or request.get("response_format") or "").strip()
        payload = _build_openai_image_payload(model=model, prompt=prompt, size=size, response_format=response_format)
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            self._join_api_path(base_url, str(binding.get("endpointPath") or "images/generations")),
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=180,
        )
        artifact = await self._artifact_from_image_response(response, job=job, provider=provider_id, model=model, mime_hint="image/png")
        job.update({"status": "succeeded", "artifacts": [artifact], "providerResponse": {"providerId": provider_id, "model": model, "size": size, "usage": response.get("usage") or {}}, "completedAt": utc_now_iso()})
        return self._save_job(job)

    async def _run_agnes_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        binding = self._configured_endpoint_binding(request, default_model="agnes-image-2.1-flash")
        provider_id = str(binding.get("providerId") or "")
        provider_meta = dict(binding.get("providerMeta") or {})
        model = str(binding.get("providerModelId") or "agnes-image-2.1-flash")
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
        endpoint = self._join_api_path(base_url, str(binding.get("endpointPath") or "images/generations"))
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
        binding = self._configured_endpoint_binding(request, default_model="gpt-image-2")
        provider_id = str(binding.get("providerId") or "")
        provider_meta = dict(binding.get("providerMeta") or {})
        model = str(binding.get("providerModelId") or "gpt-image-2")
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "")
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        prompt = str(request.get("prompt") or "").strip()
        image_path = str(request.get("imagePath") or request.get("image_path") or request.get("sourcePath") or request.get("source_path") or "").strip()
        mask_path = str(request.get("maskPath") or request.get("mask_path") or "").strip()
        if not image_path and request.get("artifactId"):
            image_path = self._artifact_source_path(str(request.get("artifactId") or ""))
        if not prompt:
            raise ValueError("image edit job requires prompt")
        if not image_path or not Path(image_path).exists():
            raise ValueError("OpenAI-compatible image edit requires a local imagePath/sourcePath or artifactId with a source file")
        if mask_path and not Path(mask_path).is_file():
            raise ValueError("OpenAI-compatible image edit mask is unavailable")
        size = resolve_image_size(
            ratio=str(request.get("ratio") or request.get("aspectRatio") or request.get("aspect_ratio") or "1:1"),
            preset=str(request.get("preset") or "1K"),
            adapter="openai_images",
            explicit_size=request.get("size"),
        )
        data = {"model": model, "prompt": prompt, "size": size}
        job["providerRequestHash"] = self._provider_request_hash(
            {
                **data,
                "sourceId": request.get("sourceId") or "",
                "maskSourceId": request.get("maskSourceId") or "",
            }
        )
        with ExitStack() as stack:
            image_file = stack.enter_context(open(image_path, "rb"))
            files = {"image": (Path(image_path).name, image_file, mimetypes.guess_type(image_path)[0] or "image/png")}
            if mask_path:
                mask_file = stack.enter_context(open(mask_path, "rb"))
                files["mask"] = (
                    Path(mask_path).name,
                    mask_file,
                    mimetypes.guess_type(mask_path)[0] or "image/png",
                )
            response = await self._request_multipart_json(
                "POST",
                self._join_api_path(base_url, str(binding.get("endpointPath") or "images/edits")),
                headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
                data=data,
                files=files,
                timeout=300,
            )
        artifact = await self._artifact_from_image_response(response, job=job, provider=provider_id, model=model, mime_hint="image/png")
        job.update({"status": "succeeded", "artifacts": [artifact], "providerResponse": {"providerId": provider_id, "model": model, "size": size}, "completedAt": utc_now_iso()})
        return self._save_job(job)

    async def _run_dashscope_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        try:
            binding = self._configured_endpoint_binding(request, default_model="qwen-image-2.0-pro")
            provider_id = str(binding.get("providerId") or "aliyun_bailian_dashscope")
            provider_meta = dict(binding.get("providerMeta") or {})
            creds = {
                "apiKey": str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip(),
                "baseUrl": str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/"),
            }
            model = str(binding.get("providerModelId") or "qwen-image-2.0-pro")
            endpoint_path = str(binding.get("endpointPath") or "services/aigc/multimodal-generation/generation")
        except ValueError:
            if self._has_explicit_model_selection(request):
                raise
            binding = {}
            provider_id = "aliyun_bailian_dashscope"
            creds = self._dashscope_credentials()
            model = str(request.get("model") or request.get("modelId") or "qwen-image-2.0-pro")
            endpoint_path = "services/aigc/multimodal-generation/generation"
        if not creds["apiKey"]:
            raise ValueError("DashScope Provider credential is not configured")
        if not creds["baseUrl"]:
            raise ValueError("DashScope Provider base URL is not configured")
        operation_kind = str(job.get("operationKind") or self._operation_kind_for_request("image", request))
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("DashScope image job requires prompt")
        size = str(
            request.get("size")
            or resolve_image_size(
                ratio=str(request.get("ratio") or "1:1"),
                preset=str(request.get("preset") or "2K"),
            )
        )
        payload = _build_dashscope_image_payload(
            model=model,
            prompt=prompt,
            operation_kind=operation_kind,
            image_urls=self._image_urls_from_request(request) if operation_kind == "image.edit" else None,
            size=size,
            n=int(request.get("n") or 1),
            negative_prompt=str(request.get("negativePrompt") or request.get("negative_prompt") or ""),
            prompt_extend=request.get("promptExtend", request.get("prompt_extend", True)),
            watermark=request.get("watermark", False),
            seed=int(request["seed"]) if request.get("seed") is not None else None,
        )
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            self._join_api_path(creds["baseUrl"], endpoint_path),
            headers=self._dashscope_headers(creds["apiKey"]),
            json=payload,
            timeout=300,
        )
        result_url = self._dashscope_result_url(response)
        if not result_url:
            raise RuntimeError(f"DashScope image response did not include an image URL: {response}")
        artifact = await self._artifact_from_url(result_url, job=job, kind="image", provider=provider_id, mime_hint="image/png", metadata={"model": model})
        job.update(
            {
                "status": "succeeded",
                "artifacts": [artifact],
                "providerResponse": {"providerId": provider_id, "model": model, "usage": response.get("usage") or {}, "requestId": response.get("request_id")},
                "completedAt": utc_now_iso(),
            }
        )
        return self._save_job(job)

    async def _run_volcengine_image_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        try:
            binding = self._configured_endpoint_binding(request, default_model="doubao-seedream-4-0-250828")
            provider_id = str(binding.get("providerId") or "volcengine_seedream")
            provider_meta = dict(binding.get("providerMeta") or {})
            creds = {
                "apiKey": str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip(),
                "baseUrl": str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/"),
                "imageModel": str(binding.get("providerModelId") or "doubao-seedream-4-0-250828"),
            }
            endpoint_path = str(binding.get("endpointPath") or "images/generations")
        except ValueError:
            creds = self._volc_credentials()
            provider_id = "volcengine_seedream"
            endpoint_path = "images/generations"
        if not creds["apiKey"]:
            raise ValueError("Volcengine Provider credential is not configured")
        if not creds["baseUrl"]:
            raise ValueError("Volcengine Provider base URL is not configured")
        prompt = str(request.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("image job requires prompt")
        model = str(creds["imageModel"])
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
            image_urls=self._image_urls_from_request(request),
        )
        job["providerRequestHash"] = self._provider_request_hash(payload)
        response = await self._request_json(
            "POST",
            self._join_api_path(creds["baseUrl"], endpoint_path),
            headers=self._bearer_headers(creds["apiKey"]),
            json=payload,
            timeout=180,
        )
        artifact = await self._artifact_from_image_response(response, job=job, provider=provider_id, model=model, mime_hint="image/png")
        job.update({"status": "succeeded", "artifacts": [artifact], "providerResponse": {"providerId": provider_id, "model": model, "size": size, "usage": response.get("usage") or {}}, "completedAt": utc_now_iso()})
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

    @staticmethod
    def _validate_minimax_business_response(response: dict[str, Any], *, action: str) -> str:
        base_resp = dict(response.get("base_resp") or {})
        raw_status_code = base_resp.get("status_code", 0)
        try:
            status_code = int(raw_status_code or 0)
        except (TypeError, ValueError):
            status_code = -1
        trace_id = str(response.get("trace_id") or response.get("traceId") or "").strip()
        if status_code != 0:
            status_message = str(base_resp.get("status_msg") or base_resp.get("message") or "MiniMax request failed").strip()
            trace_suffix = f"; trace_id={trace_id}" if trace_id else ""
            raise RuntimeError(f"MiniMax {action} failed: code={status_code}; {status_message}{trace_suffix}")
        return trace_id

    async def _submit_minimax_video_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        operation_kind = str(job.get("operationKind") or self._operation_kind_for_request("video", request))
        default_model = "S2V-01" if operation_kind == "video.reference_to_video" else "MiniMax-Hailuo-2.3"
        binding = self._configured_endpoint_binding(request, default_model=default_model)
        provider_id = str(binding.get("providerId") or "")
        provider_meta = dict(binding.get("providerMeta") or {})
        model = self._strip_provider_model_prefix(str(binding.get("providerModelId") or default_model))
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip()
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        if not api_key:
            raise ValueError(f"Provider {provider_id} has no API key")
        image_references = self._minimax_image_references_from_request(
            request,
            operation_kind=operation_kind,
        )
        payload = _build_minimax_video_payload(
            model=model,
            prompt=str(request.get("prompt") or ""),
            operation_kind=operation_kind,
            image_references=image_references,
            duration_seconds=request.get("durationSeconds", request.get("duration_seconds", request.get("duration"))),
            resolution=request.get("resolution"),
            prompt_optimizer=request.get("promptOptimizer", request.get("prompt_optimizer", True)),
            fast_pretreatment=request.get("fastPretreatment", request.get("fast_pretreatment")),
            aigc_watermark=_truthy(request.get("aigcWatermark", request.get("aigc_watermark", False))),
        )
        job["providerRequestHash"] = self._provider_request_hash(payload)
        endpoint_path = str(binding.get("endpointPath") or "video_generation").strip() or "video_generation"
        response = await self._request_json(
            "POST",
            self._join_api_path(base_url, endpoint_path),
            headers=self._bearer_headers(api_key),
            json=payload,
            timeout=180,
        )
        trace_id = self._validate_minimax_business_response(response, action="video submit")
        task_id = str(response.get("task_id") or response.get("taskId") or "").strip()
        if not task_id:
            raise RuntimeError("MiniMax video submit succeeded without task_id")
        job["status"] = "queued"
        job["providerTaskId"] = task_id
        job["providerResponse"] = {
            "providerId": provider_id,
            "taskId": task_id,
            "traceId": trace_id,
            "model": model,
            "operationKind": operation_kind,
        }
        return self._save_job(job)

    async def _poll_minimax_video_job(self, job: dict[str, Any]) -> dict[str, Any]:
        task_id = str(job.get("providerTaskId") or "").strip()
        if not task_id:
            job["status"] = "failed"
            job["error"] = "Missing providerTaskId"
            return self._save_job(job)
        request = dict(job.get("request") or {})
        provider_response = dict(job.get("providerResponse") or {})
        binding = self._configured_endpoint_binding(
            request,
            default_model=str(provider_response.get("model") or "S2V-01"),
        )
        provider_id = str(binding.get("providerId") or provider_response.get("providerId") or "")
        provider_meta = dict(binding.get("providerMeta") or {})
        model = self._strip_provider_model_prefix(str(binding.get("providerModelId") or provider_response.get("model") or ""))
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip()
        if not base_url:
            raise ValueError(f"Provider {provider_id} has no base_url")
        if not api_key:
            raise ValueError(f"Provider {provider_id} has no API key")
        response = await self._request_json(
            "GET",
            self._join_api_path(base_url, f"/v1/query/video_generation?task_id={quote(task_id)}"),
            headers=self._bearer_headers(api_key),
            timeout=60,
        )
        trace_id = self._validate_minimax_business_response(response, action="video query")
        raw_status = str(response.get("status") or "").strip()
        normalized_status = raw_status.lower()
        status = {
            "preparing": "running",
            "queueing": "queued",
            "processing": "running",
            "success": "succeeded",
            "fail": "failed",
        }.get(normalized_status, "running")
        job["status"] = status
        job["providerResponse"] = {
            **provider_response,
            "lastStatus": raw_status,
            "traceId": trace_id or provider_response.get("traceId") or "",
            "taskId": task_id,
        }
        if status == "succeeded":
            raw_file_id = response.get("file_id")
            file_id_text = str(raw_file_id or "").strip()
            if not file_id_text.isdigit():
                job["status"] = "failed"
                job["error"] = "MiniMax video task succeeded without a valid integer file_id"
                job["completedAt"] = utc_now_iso()
                return self._save_job(job)
            file_id = int(file_id_text)
            file_response = await self._request_json(
                "GET",
                self._join_api_path(base_url, f"/v1/files/retrieve?file_id={file_id}"),
                headers=self._bearer_headers(api_key),
                timeout=60,
            )
            file_trace_id = self._validate_minimax_business_response(file_response, action="video download lookup")
            file_data = dict(file_response.get("file") or {})
            download_url = str(file_data.get("download_url") or file_data.get("downloadUrl") or "").strip()
            if not download_url:
                job["status"] = "failed"
                job["error"] = "MiniMax file lookup succeeded without file.download_url"
                job["completedAt"] = utc_now_iso()
                return self._save_job(job)
            artifact = await self._artifact_from_url(
                download_url,
                job=job,
                kind="video",
                provider=provider_id,
                mime_hint="video/mp4",
                metadata={
                    "model": model,
                    "taskId": task_id,
                    "fileId": file_id,
                    "width": response.get("video_width"),
                    "height": response.get("video_height"),
                    "nativeAudio": False,
                    "audioMode": "silent_or_external_audio",
                },
            )
            job["artifacts"] = [artifact]
            job["providerResponse"].update(
                {
                    "fileId": file_id,
                    "fileTraceId": file_trace_id,
                    "width": response.get("video_width"),
                    "height": response.get("video_height"),
                }
            )
            job["completedAt"] = utc_now_iso()
        elif status == "failed":
            base_resp = dict(response.get("base_resp") or {})
            job["error"] = str(response.get("message") or response.get("error") or base_resp.get("status_msg") or "MiniMax video task failed")
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
            operation_kind=operation_kind,
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
        duration = int(request.get("duration") or request.get("durationSeconds") or request.get("duration_seconds") or 5)
        input_payload: dict[str, Any] = {}
        if prompt:
            input_payload["prompt"] = prompt
        image_urls = self._image_urls_from_request(request)
        video_url = self._video_url_from_request(request, "videoUrl", "video_url", "sourceVideoUrl", "source_video_url")
        audio_url = self._public_url_or_error(request.get("audioUrl") or request.get("audio_url"), field_name="audioUrl")
        if operation_kind == "video.reference_to_video":
            ref_image = self._public_url_or_error(request.get("referenceImageUrl") or request.get("reference_image_url"), field_name="referenceImageUrl")
            ref_video = self._public_url_or_error(request.get("referenceVideoUrl") or request.get("reference_video_url"), field_name="referenceVideoUrl")
            raw_ref_images = request.get("referenceImageUrls") or request.get("reference_image_urls") or []
            raw_ref_videos = request.get("referenceVideoUrls") or request.get("reference_video_urls") or []
            if isinstance(raw_ref_images, str):
                raw_ref_images = [raw_ref_images]
            if isinstance(raw_ref_videos, str):
                raw_ref_videos = [raw_ref_videos]
            ref_images = [
                self._public_url_or_error(item, field_name="referenceImageUrls")
                for item in list(raw_ref_images)
                if str(item or "").strip()
            ]
            ref_videos = [
                self._public_url_or_error(item, field_name="referenceVideoUrls")
                for item in list(raw_ref_videos)
                if str(item or "").strip()
            ]
        else:
            ref_image = ""
            ref_video = ""
            ref_images = []
            ref_videos = []
        if operation_kind in DASHSCOPE_VIDEO_OPERATION_KINDS:
            payload = _build_dashscope_video_payload(
                model=model,
                prompt=prompt,
                operation_kind=operation_kind,
                image_urls=image_urls,
                reference_image_url=ref_image,
                reference_video_url=ref_video,
                reference_image_urls=ref_images,
                reference_video_urls=ref_videos,
                audio_url=audio_url,
                resolution=str(request.get("resolution") or "720P"),
                ratio=str(request.get("ratio") or request.get("aspectRatio") or "16:9"),
                duration=duration,
                negative_prompt=str(request.get("negativePrompt") or request.get("negative_prompt") or ""),
                prompt_extend=request.get("promptExtend", request.get("prompt_extend", True)),
                watermark=request.get("watermark", False),
            )
        elif operation_kind == "video.action_transfer":
            if model != "wan2.2-animate-move":
                raise ValueError(
                    "Configured DashScope model cannot execute video.action_transfer; "
                    "select wan2.2-animate-move in the model control plane"
                )
            canvas_inputs = [dict(item) for item in list(request.get("canvasInputs") or []) if isinstance(item, dict)]

            def canvas_artifact_url(port_id: str) -> str:
                for item in canvas_inputs:
                    if str(item.get("portId") or "") != port_id:
                        continue
                    if str(item.get("origin") or "") != "artifact":
                        raise ValueError(
                            f"Canvas {port_id} input is local-only; the configured remote provider requires a public artifact URL"
                        )
                    url = self._artifact_provider_transport_url(str(item.get("id") or ""))
                    if not url:
                        raise ValueError(
                            f"Canvas {port_id} artifact has no provider-accessible URL; use a provider artifact or a local workflow"
                        )
                    return url
                return ""

            target_image = image_urls[0] if image_urls else self._public_url_or_error(
                request.get("targetImageUrl") or request.get("target_image_url"),
                field_name="targetImageUrl",
            ) or canvas_artifact_url("image")
            reference_video = self._public_url_or_error(
                request.get("referenceVideoUrl") or request.get("reference_video_url") or request.get("actionVideoUrl") or request.get("action_video_url"),
                field_name="referenceVideoUrl",
            ) or canvas_artifact_url("video")
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
        if operation_kind not in DASHSCOPE_VIDEO_OPERATION_KINDS:
            parameters = {
                "resolution": str(request.get("resolution") or "720P"),
                "duration": max(1, min(duration, 30)),
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

    def _comfyui_binding(self, request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, str]]:
        binding = self._configured_endpoint_binding(request, default_model="comfyui-workflow")
        provider_meta = dict(binding.get("providerMeta") or {})
        model_data = dict(binding.get("modelData") or {})
        adapter = str(binding.get("adapter") or "").strip().lower()
        api_standard = str(
            binding.get("apiStandard")
            or provider_meta.get("api_standard")
            or provider_meta.get("apiStandard")
            or ""
        ).strip().lower()
        if adapter != "comfyui_workflow" or api_standard != "comfyui":
            raise ValueError("Configured model is not bound to the ComfyUI workflow adapter")
        base_url = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").strip().rstrip("/")
        if not base_url:
            raise ValueError("Configured ComfyUI Provider has no base URL")
        workflow = validate_comfyui_workflow(dict(model_data.get("mediaLimits") or {}).get("comfyuiWorkflow"))
        api_key = str(provider_meta.get("api_key") or provider_meta.get("apiKey") or "").strip()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        return binding, workflow, base_url, headers

    async def _submit_comfyui_workflow_job(self, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
        _binding, workflow, base_url, headers = self._comfyui_binding(request)
        session_id = str(request.get("sessionId") or request.get("session_id") or "").strip()
        canvas_inputs = [dict(item) for item in list(request.get("canvasInputs") or []) if isinstance(item, dict)]
        uploaded_inputs: dict[str, str] = {}
        upload_proof: dict[str, dict[str, str]] = {}
        for port_id in ("image", "video"):
            matches = [item for item in canvas_inputs if str(item.get("portId") or "") == port_id]
            if len(matches) != 1:
                raise ValueError(f"ComfyUI action transfer requires exactly one Canvas {port_id} input")
            source_path = self._canvas_input_path(session_id=session_id, item=matches[0])
            mime_type = mimetypes.guess_type(source_path.name)[0] or "application/octet-stream"
            with source_path.open("rb") as source_file:
                response = await self._request_multipart_json(
                    "POST",
                    self._join_api_path(base_url, "upload/image"),
                    headers=headers,
                    data={"type": "input", "subfolder": f"v8os/{job['jobId']}"},
                    files={"image": (source_path.name, source_file, mime_type)},
                    timeout=600,
                )
            filename = str(response.get("name") or "").strip()
            subfolder = str(response.get("subfolder") or "").strip().replace("\\", "/")
            folder_type = str(response.get("type") or "input").strip()
            if not filename or "/" in filename or "\\" in filename or ".." in subfolder.split("/"):
                raise RuntimeError("ComfyUI returned an unsafe uploaded input reference")
            uploaded_inputs[port_id] = f"{subfolder}/{filename}".strip("/")
            upload_proof[port_id] = {"filename": filename, "subfolder": subfolder, "type": folder_type}

        prompt = bind_comfyui_inputs(workflow, uploaded_inputs)
        response = await self._request_json(
            "POST",
            self._join_api_path(base_url, "prompt"),
            headers=headers,
            json={"prompt": prompt, "client_id": f"v8os-{job['jobId']}"},
            timeout=120,
        )
        prompt_id = str(response.get("prompt_id") or "").strip()
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return a prompt id")
        job["providerTaskId"] = prompt_id
        job["providerRequestHash"] = self._provider_request_hash(prompt)
        job["providerResponse"] = {
            "providerId": str(job.get("providerId") or "comfyui"),
            "promptId": prompt_id,
            "workflowDigest": workflow["digest"],
            "uploadedInputs": upload_proof,
        }
        job["status"] = "running"
        return self._save_job(job)

    async def _poll_comfyui_workflow_job(self, job: dict[str, Any]) -> dict[str, Any]:
        request = dict(job.get("request") or {})
        _binding, workflow, base_url, headers = self._comfyui_binding(request)
        prompt_id = str(job.get("providerTaskId") or "").strip()
        history = await self._request_json(
            "GET",
            self._join_api_path(base_url, f"history/{quote(prompt_id, safe='')}"),
            headers=headers,
            timeout=30,
        )
        history_item = dict(history.get(prompt_id) or {})
        selected = select_comfyui_output(workflow, history_item)
        if selected is None:
            status = dict(history_item.get("status") or {})
            if str(status.get("status_str") or "").lower() == "error":
                job["status"] = "failed"
                job["error"] = "ComfyUI workflow execution failed"
                job["completedAt"] = utc_now_iso()
            elif status.get("completed") is True:
                job["status"] = "failed"
                job["error"] = "ComfyUI workflow completed without the configured output"
                job["completedAt"] = utc_now_iso()
            return self._save_job(job)

        extension = Path(selected["filename"]).suffix.lower()
        if extension not in VIDEO_EXTENSIONS:
            job["status"] = "failed"
            job["error"] = "ComfyUI action-transfer output is not a supported video file"
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        output_path = self._output_path(job, "comfyui-action-transfer", extension)
        query = urlencode(selected)
        content_type = await self._download_provider_file(
            f"{self._join_api_path(base_url, 'view')}?{query}",
            output_path,
            headers=headers,
            timeout=600,
        )
        mime_type = content_type or mimetypes.guess_type(selected["filename"])[0] or "video/mp4"
        if not mime_type.startswith("video/"):
            output_path.unlink(missing_ok=True)
            job["status"] = "failed"
            job["error"] = "ComfyUI action-transfer output did not have a video content type"
            job["completedAt"] = utc_now_iso()
            return self._save_job(job)
        artifact = self._record_local_artifact(
            file_path=output_path,
            job=job,
            kind="video",
            mime_type=mime_type,
            metadata={
                "origin": "configured_comfyui_workflow",
                "providerInvoked": True,
                "workflowDigest": workflow["digest"],
                "promptId": prompt_id,
            },
        )
        job["artifacts"] = [artifact]
        job["status"] = "succeeded"
        job["qualityStatus"] = "not_run"
        job["completedAt"] = utc_now_iso()
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

    def _artifact_from_hex(self, payload: str, *, job: dict[str, Any], kind: str, provider: str, mime_type: str, extension: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        raw = re.sub(r"\s+", "", str(payload or ""))
        if not raw:
            raise RuntimeError("Provider returned empty hex payload")
        data = bytes.fromhex(raw)
        path = self._output_path(job, kind, extension)
        path.write_bytes(data)
        return self._record_local_artifact(file_path=path, job=job, kind=kind, mime_type=mime_type, metadata={"provider": provider, "origin": "provider_result", **dict(metadata or {})})

    async def _artifact_from_url(self, url: str, *, job: dict[str, Any], kind: str, provider: str, mime_hint: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        headers = {
            "User-Agent": "V8-Agent-OS-CreativeMedia/1.0",
            "Accept-Encoding": "identity",
        }
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        path: Path | None = None
        content_type = mime_hint
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                    async with client.stream("GET", url, headers=headers) as response:
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip() or mime_hint
                        if path is None:
                            extension = self._extension_for_url(url, content_type, kind)
                            path = self._output_path(job, kind, extension)
                        with open(path, "wb") as file:
                            async for chunk in response.aiter_bytes():
                                if chunk:
                                    file.write(chunk)
                last_error = None
                break
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.TimeoutException) as exc:
                last_error = exc
                if path and path.exists():
                    path.unlink()
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2**attempt))
        if last_error is not None:
            raise last_error
        if path is None or not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError("Provider artifact download completed without a local file")
        parsed = urlparse(url)
        artifact = self._record_local_artifact(
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
        if kind == "image" and parsed.scheme in {"http", "https"}:
            artifact_id = str(artifact.get("artifactId") or "").strip()
            if artifact_id:
                self._provider_transport_urls[artifact_id] = url
                while len(self._provider_transport_urls) > 128:
                    self._provider_transport_urls.pop(next(iter(self._provider_transport_urls)))
        return artifact

    def _record_local_artifact(self, *, file_path: Path, job: dict[str, Any], kind: str, mime_type: str, metadata: dict[str, Any]) -> dict[str, Any]:
        workspace_root = Path(str(job.get("workspacePath") or "")).expanduser()
        try:
            workspace_relative_path = file_path.resolve().relative_to(workspace_root.resolve()).as_posix() if str(job.get("workspacePath") or "").strip() else ""
        except ValueError:
            workspace_relative_path = ""
        artifact = artifact_store.record_artifact(
            artifact_kind=kind,
            mime_type=mime_type,
            session_id=str(job.get("sessionId") or "") or None,
            run_id=str(job.get("runId") or "") or None,
            title=file_path.name,
            source_path=str(file_path),
            metadata={
                **metadata,
                "creativeMediaJobId": job["jobId"],
                "canvasOperationId": job.get("canvasOperationId") or "",
                "sourceId": job.get("sourceId") or "",
                "artifactId": job.get("artifactId") or "",
                "workspaceAssetId": job.get("workspaceAssetId") or "",
                "maskSourceId": job.get("maskSourceId") or "",
                "modality": job["modality"],
                "operationKind": job.get("operationKind") or "",
                "outputKind": job.get("outputKind") or "",
                "outputSlot": job.get("outputSlot") or "",
                "projectId": job.get("projectId") or "",
                "workspaceId": job.get("workspaceId") or "",
                "workspacePath": job.get("workspacePath") or "",
                "workspaceRelativePath": workspace_relative_path,
                "pathPlane": "runtime",
                "storageClass": "runtime_artifact",
                "surfaceVisible": True,
            },
            source_component="creative_media_runtime",
            node="creative_media_runtime",
        )
        session_id = str(job.get("sessionId") or "").strip()
        artifact_id = str(artifact.get("artifactId") or artifact.get("id") or "").strip()
        if session_id and artifact_id:
            try:
                workspace_media_library.register_artifact(
                    session_id=session_id,
                    artifact_id=artifact_id,
                    attach_to_session=True,
                )
            except (FileNotFoundError, PermissionError, ValueError):
                # Artifact truth is authoritative; workspace media indexing is reconciled separately.
                pass
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
        return {"image": ".png", "video": ".mp4", "audio": ".mp3", "model3d": ".glb"}.get(kind, ".bin")

    def _extract_task_id(self, payload: Any) -> str:
        for key in ("task_id", "taskId", "id", "job_id", "jobId"):
            value = self._find_first_value(payload, key)
            if value:
                return str(value)
        return ""

    def _find_first_value(self, payload: Any, target_key: str) -> Any:
        if isinstance(payload, dict):
            if target_key in payload and payload[target_key] not in (None, ""):
                return payload[target_key]
            for value in payload.values():
                found = self._find_first_value(value, target_key)
                if found not in (None, ""):
                    return found
        if isinstance(payload, list):
            for item in payload:
                found = self._find_first_value(item, target_key)
                if found not in (None, ""):
                    return found
        return None

    def _find_first_url(self, payload: Any, *, preferred_extensions: set[str] | None = None) -> str:
        preferred_extensions = preferred_extensions or set()
        urls: list[str] = []

        def visit(value: Any) -> None:
            if isinstance(value, str):
                if value.startswith("http://") or value.startswith("https://"):
                    urls.append(value)
                return
            if isinstance(value, dict):
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(payload)
        for url in urls:
            if Path(urlparse(url).path).suffix.lower() in preferred_extensions:
                return url
        return urls[0] if urls else ""

    def _best_model3d_url(self, payload: Any) -> str:
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, list):
            for wanted_type in ("glb", "obj", "zip", "fbx", "gltf", "usdz"):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type") or "").strip().lower() == wanted_type and item.get("url"):
                        return str(item["url"])
        return self._find_first_url(payload, preferred_extensions=MODEL3D_EXTENSIONS)

    @staticmethod
    def _normalize_async_status(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text in {"2", "200", "success", "succeeded", "completed", "complete", "finished", "done", "finish"}:
            return "succeeded"
        if text in {"-1", "3", "4", "failed", "failure", "error", "cancelled", "canceled"}:
            return "failed"
        return "running"

    @staticmethod
    def _compact_provider_response(payload: Any) -> str:
        try:
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except Exception:
            text = str(payload)
        return text[:500]

    def _tencent_tokenhub_3d_endpoints(self, provider_meta: dict[str, Any]) -> dict[str, str]:
        raw_base = str(provider_meta.get("base_url") or provider_meta.get("baseUrl") or "").rstrip("/")
        override_reason = ""
        if not raw_base or "ai3d.tencentcloudapi.com" in raw_base:
            raw_base = "https://tokenhub.tencentmaas.com/v1/api/3d"
            override_reason = "legacy_tencentcloud_ai3d_preset_uses_tokenhub_bearer"
        if raw_base.endswith("/submit"):
            submit = raw_base
            query = raw_base.rsplit("/", 1)[0] + "/query"
        elif raw_base.endswith("/query"):
            query = raw_base
            submit = raw_base.rsplit("/", 1)[0] + "/submit"
        elif raw_base.endswith("/v1"):
            submit = f"{raw_base}/api/3d/submit"
            query = f"{raw_base}/api/3d/query"
        else:
            submit = f"{raw_base}/submit"
            query = f"{raw_base}/query"
        return {"submit": submit, "query": query, "overrideReason": override_reason}

    def _bearer_headers(self, api_key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def _provider_http_timeout(self, timeout: float = 120.0) -> httpx.Timeout:
        timeout_seconds = max(1.0, min(float(timeout or 60.0), 900.0))
        connect_seconds = min(timeout_seconds, 30.0)
        write_seconds = min(timeout_seconds, 60.0)
        pool_seconds = min(timeout_seconds, 30.0)
        return httpx.Timeout(
            connect=connect_seconds,
            read=timeout_seconds,
            write=write_seconds,
            pool=pool_seconds,
        )

    async def _request_json(self, method: str, url: str, *, headers: Optional[dict[str, str]] = None, json: Optional[dict[str, Any]] = None, timeout: float = 120.0) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._provider_http_timeout(timeout)) as client:
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
        async with httpx.AsyncClient(timeout=self._provider_http_timeout(timeout)) as client:
            response = await client.request(method, url, headers=headers, data=data, files=files)
            if response.status_code >= 400:
                raise RuntimeError(f"Provider request failed ({response.status_code}) at {url}: {response.text[:500]}")
            return response.json()

    async def _download_provider_file(
        self,
        url: str,
        destination: Path,
        *,
        headers: Optional[dict[str, str]] = None,
        timeout: float = 600.0,
    ) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        content_type = ""
        try:
            async with httpx.AsyncClient(timeout=self._provider_http_timeout(timeout)) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code >= 400:
                        body = (await response.aread()).decode("utf-8", errors="replace")
                        raise RuntimeError(f"Provider download failed ({response.status_code}) at {url}: {body[:500]}")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                    with destination.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            if chunk:
                                output.write(chunk)
            if not destination.is_file() or destination.stat().st_size <= 0:
                raise RuntimeError("Provider download completed without a local file")
            return content_type
        except Exception:
            destination.unlink(missing_ok=True)
            raise


creative_media_runtime = runtime_registry.register(CreativeMediaRuntime())

__all__ = [
    "CreativeMediaRuntime",
    "creative_media_runtime",
    "_build_openai_image_payload",
    "_build_volcengine_image_payload",
    "_build_volcengine_video_payload",
    "_build_minimax_video_payload",
    "normalize_provider_status",
]
