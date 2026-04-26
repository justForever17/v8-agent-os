from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict


ASSET_DIR = Path(__file__).resolve().parent / "assets"
PROVIDER_MATRIX_PATH = ASSET_DIR / "media_provider_format_matrix.json"
RESOLUTION_PRESETS_PATH = ASSET_DIR / "media_resolution_presets.json"
VISUAL_RECIPE_LIBRARY_PATH = ASSET_DIR / "visual_recipe_library.json"
VIDEO_RECIPE_LIBRARY_PATH = ASSET_DIR / "video_recipe_library.json"
AUDIO_MUSIC_RECIPE_LIBRARY_PATH = ASSET_DIR / "audio_music_recipe_library.json"


def _read_asset(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_provider_matrix() -> Dict[str, Any]:
    return deepcopy(_read_asset(PROVIDER_MATRIX_PATH))


def load_resolution_presets() -> Dict[str, Any]:
    return deepcopy(_read_asset(RESOLUTION_PRESETS_PATH))


def load_visual_recipe_library() -> Dict[str, Any]:
    return deepcopy(_read_asset(VISUAL_RECIPE_LIBRARY_PATH))


def load_video_recipe_library() -> Dict[str, Any]:
    return deepcopy(_read_asset(VIDEO_RECIPE_LIBRARY_PATH))


def load_audio_music_recipe_library() -> Dict[str, Any]:
    return deepcopy(_read_asset(AUDIO_MUSIC_RECIPE_LIBRARY_PATH))


def _normalize_ratio(value: str | None, default: str = "1:1") -> str:
    candidate = str(value or "").strip()
    return candidate or default


def resolve_image_size(*, ratio: str | None = None, preset: str | None = None, adapter: str | None = None, explicit_size: str | None = None) -> str:
    if explicit_size:
        return str(explicit_size).strip()
    presets = load_resolution_presets()
    normalized_adapter = str(adapter or "").strip()
    normalized_ratio = _normalize_ratio(ratio, "1:1")
    if normalized_adapter == "openai_images":
        if normalized_ratio == "9:16":
            return "1024x1536"
        if normalized_ratio == "16:9":
            return "1536x1024"
        return "1024x1024"
    if normalized_adapter == "volcengine_ark":
        provider_aliases = ((presets.get("image") or {}).get("providerAliases") or {}).get("volcengine_seedream") or {}
        if normalized_ratio in provider_aliases:
            return str(provider_aliases[normalized_ratio])
    preset_key = str(preset or "2K").strip().upper()
    image_presets = (presets.get("image") or {}).get("presets") or {}
    return str((image_presets.get(preset_key) or image_presets.get("2K") or {}).get(normalized_ratio) or "2048x2048")


def resolve_video_resolution(*, preset: str | None = None, explicit_resolution: str | None = None) -> str:
    if explicit_resolution:
        return str(explicit_resolution).strip()
    normalized = str(preset or "720p").strip()
    aliases = {
        "720": "720p",
        "720P": "720p",
        "720p": "720p",
        "1080": "1080p",
        "1080P": "1080p",
        "1080p": "1080p",
    }
    return aliases.get(normalized, normalized)


def normalize_provider_status(status: str | None, *, provider: str | None = None) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"queued", "pending", "created", "submitted", "ordered", "waiting"}:
        return "queued"
    if normalized in {"running", "processing", "in_progress", "started"}:
        return "running"
    if normalized in {"succeeded", "success", "completed", "complete", "done"}:
        return "succeeded"
    if normalized in {"failed", "error", "cancelled", "canceled"}:
        return "cancelled" if normalized in {"cancelled", "canceled"} else "failed"
    return "running" if provider else (normalized or "queued")
