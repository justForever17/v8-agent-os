from __future__ import annotations

import mimetypes
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib import parse as urllib_parse
from urllib import request as urllib_request

from core.workspace_resolution import workspace_resolution_service


def _plugin_host_workspace_root() -> Path:
    root = Path(workspace_resolution_service.get_main_workspace_path()).expanduser() / "plugin_host"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _tts_root() -> Path:
    root = _plugin_host_workspace_root() / "tts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _slot_root(direction: str) -> Path:
    root = _plugin_host_workspace_root() / direction / "last"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _clear_slot(root: Path) -> None:
    for child in root.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)


def _safe_filename(name: str | None, fallback: str) -> str:
    candidate = str(name or "").strip()
    if not candidate:
        return fallback
    normalized = candidate.replace("\\", "/").split("/")[-1].strip()
    normalized = normalized or fallback
    forbidden = '<>:"/\\|?*'
    normalized = "".join("_" if char in forbidden else char for char in normalized)
    normalized = normalized.strip(" .")
    return normalized or fallback


def _guess_mime_type(path: str | Path | None) -> str | None:
    if not path:
        return None
    if str(Path(str(path)).suffix).lower() == ".silk":
        return "audio/silk"
    mime_type, _ = mimetypes.guess_type(str(path))
    return mime_type or None


def _guess_asset_kind(*, mime_type: str | None, path: str | Path | None) -> str:
    normalized_mime = str(mime_type or "").strip().lower()
    if normalized_mime.startswith("audio/"):
        return "audio"
    if normalized_mime.startswith("image/"):
        return "image"
    if normalized_mime.startswith("video/"):
        return "video"
    suffix = str(Path(str(path or "")).suffix).lower()
    if suffix in {".mp3", ".wav", ".opus", ".m4a", ".ogg", ".aac", ".silk"}:
        return "audio"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
        return "image"
    if suffix in {".mp4", ".mov", ".avi", ".mkv", ".webm"}:
        return "video"
    return "file"


def build_tts_output_paths(*, stem: str) -> dict[str, Path]:
    root = _tts_root()
    return {
        "opus": root / f"{stem}.opus",
        "ogg": root / f"{stem}.ogg",
        "mp3": root / f"{stem}.mp3",
        "silk": root / f"{stem}.silk",
    }


def _normalize_asset_source(
    *,
    source_path: str | Path | None = None,
    source_url: str | None = None,
    preferred_name: str | None = None,
    asset_kind: str | None = None,
    delivery_mode: str = "attachment",
) -> dict[str, Any] | None:
    source_path_value = str(source_path or "").strip()
    source_url_value = str(source_url or "").strip()
    if not source_path_value and not source_url_value:
        return None
    return {
        "sourcePath": source_path_value or None,
        "sourceUrl": source_url_value or None,
        "preferredName": str(preferred_name or "").strip() or None,
        "assetKind": str(asset_kind or "").strip() or None,
        "deliveryMode": str(delivery_mode or "attachment").strip() or "attachment",
    }


def _copy_asset_to_target(*, direction: str, target_dir: Path, asset_source: dict[str, Any], index: int) -> dict[str, Any]:
    source_path_value = str(asset_source.get("sourcePath") or "").strip()
    source_url_value = str(asset_source.get("sourceUrl") or "").strip()
    preferred_name = str(asset_source.get("preferredName") or "").strip() or None
    delivery_mode = str(asset_source.get("deliveryMode") or "attachment").strip() or "attachment"
    explicit_kind = str(asset_source.get("assetKind") or "").strip() or None
    target_dir.mkdir(parents=True, exist_ok=True)

    def _unique_target(file_name: str) -> Path:
        candidate = target_dir / file_name
        if not candidate.exists():
            return candidate
        stem = candidate.stem
        suffix = candidate.suffix
        counter = 2
        while True:
            next_candidate = target_dir / f"{stem}_{counter}{suffix}"
            if not next_candidate.exists():
                return next_candidate
            counter += 1

    if source_path_value:
        origin = Path(source_path_value).expanduser()
        if not origin.exists() or not origin.is_file():
            raise FileNotFoundError(f"附件路径不存在：{origin}")
        fallback_name = origin.name or f"attachment_{index + 1}.bin"
        file_name = _safe_filename(preferred_name or origin.name, fallback_name)
        target = _unique_target(file_name)
        shutil.copy2(origin, target)
        mime_type = _guess_mime_type(target)
        return {
            "assetKind": explicit_kind or _guess_asset_kind(mime_type=mime_type, path=target),
            "workspacePath": str(target),
            "originalFileName": origin.name or file_name,
            "mimeType": mime_type,
            "direction": direction,
            "deliveryMode": delivery_mode,
            "sourcePath": str(origin),
            "sourceUrl": None,
        }

    parsed = urllib_parse.urlparse(source_url_value)
    fallback_name = Path(parsed.path).name or f"attachment_{index + 1}.bin"
    file_name = _safe_filename(preferred_name or fallback_name, fallback_name)
    target = _unique_target(file_name)
    with urllib_request.urlopen(source_url_value, timeout=20) as response:
        target.write_bytes(response.read())
        mime_type = str(response.headers.get_content_type() or "").strip() or _guess_mime_type(target)
    return {
        "assetKind": explicit_kind or _guess_asset_kind(mime_type=mime_type, path=target),
        "workspacePath": str(target),
        "originalFileName": file_name,
        "mimeType": mime_type,
        "direction": direction,
        "deliveryMode": delivery_mode,
        "sourcePath": None,
        "sourceUrl": source_url_value,
    }


def materialize_last_assets(
    *,
    direction: str,
    sources: list[dict[str, Any]],
    message_slot: str | None = None,
    replace_root: bool = True,
) -> dict[str, Any] | None:
    normalized_direction = "inbound" if str(direction).strip().lower() == "inbound" else "outbound"
    normalized_sources = [dict(item) for item in sources if isinstance(item, dict) and (item.get("sourcePath") or item.get("sourceUrl"))]
    if not normalized_sources:
        return None
    root = _slot_root(normalized_direction)
    if replace_root:
        _clear_slot(root)
    slot = _safe_filename(message_slot, f"message_{uuid.uuid4().hex[:12]}")
    target_dir = root / slot
    assets = [
        _copy_asset_to_target(direction=normalized_direction, target_dir=target_dir, asset_source=asset_source, index=index)
        for index, asset_source in enumerate(normalized_sources)
    ]
    return {
        "messageSlot": slot,
        "workspaceDirectory": str(target_dir),
        "assetCount": len(assets),
        "direction": normalized_direction,
        "deliveryMode": str(normalized_sources[0].get("deliveryMode") or "attachment"),
        "assets": assets,
    }


def materialize_last_asset(
    *,
    direction: str,
    source_path: str | Path | None = None,
    source_url: str | None = None,
    preferred_name: str | None = None,
    delivery_mode: str = "attachment",
    asset_kind: str | None = None,
) -> dict[str, Any] | None:
    source = _normalize_asset_source(
        source_path=source_path,
        source_url=source_url,
        preferred_name=preferred_name,
        asset_kind=asset_kind,
        delivery_mode=delivery_mode,
    )
    manifest = materialize_last_assets(direction=direction, sources=[source] if source else [])
    if not manifest:
        return None
    first_asset = dict((manifest.get("assets") or [None])[0] or {})
    first_asset["messageSlot"] = manifest.get("messageSlot")
    first_asset["workspaceDirectory"] = manifest.get("workspaceDirectory")
    return first_asset


def normalize_asset_sources(
    *,
    source_path: str | Path | None = None,
    source_url: str | None = None,
    preferred_name: str | None = None,
    asset_kind: str | None = None,
    delivery_mode: str = "attachment",
) -> list[dict[str, Any]]:
    source = _normalize_asset_source(
        source_path=source_path,
        source_url=source_url,
        preferred_name=preferred_name,
        asset_kind=asset_kind,
        delivery_mode=delivery_mode,
    )
    return [source] if source else []
