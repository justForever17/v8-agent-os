from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_package_manifest(plugin_dir: Path) -> dict[str, Any]:
    package_json = plugin_dir / "package.json"
    return read_json_file(package_json) if package_json.exists() else {}


def read_plugin_manifest(plugin_dir: Path) -> dict[str, Any]:
    manifest_path = plugin_dir / "openclaw.plugin.json"
    return read_json_file(manifest_path) if manifest_path.exists() else {}


def iter_plugin_dir_candidates(root: Path) -> list[Path]:
    if not root.exists():
        return []

    candidates: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if (child / "openclaw.plugin.json").exists():
            candidates.append(child)
            continue
        if (child / "package.json").exists():
            package_manifest = read_package_manifest(child)
            openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
            if isinstance(openclaw_meta, dict):
                candidates.append(child)
    return sorted(candidates, key=lambda item: item.name.lower())


def build_manifest_summary(
    *,
    plugin_id: str,
    manifest: dict[str, Any],
    package_manifest: dict[str, Any],
    channels: list[str],
) -> dict[str, Any]:
    openclaw_meta = package_manifest.get("openclaw") if isinstance(package_manifest, dict) else {}
    channel_meta = openclaw_meta.get("channel") if isinstance(openclaw_meta, dict) else {}
    ui_hints = manifest.get("uiHints") if isinstance(manifest, dict) else {}
    config_schema = manifest.get("configSchema") if isinstance(manifest, dict) else {}
    properties = config_schema.get("properties") if isinstance(config_schema, dict) else {}
    return {
        "id": plugin_id,
        "name": str(manifest.get("name") or package_manifest.get("name") or plugin_id).strip() or plugin_id,
        "description": str(manifest.get("description") or package_manifest.get("description") or "").strip() or None,
        "channels": channels,
        "docsPath": str((channel_meta or {}).get("docsPath") or "").strip() or None,
        "selectionLabel": str((channel_meta or {}).get("selectionLabel") or "").strip() or None,
        "detailLabel": str((channel_meta or {}).get("detailLabel") or "").strip() or None,
        "packageName": str(package_manifest.get("name") or "").strip() or None,
        "packageVersion": str(package_manifest.get("version") or "").strip() or None,
        "packageDescription": str(package_manifest.get("description") or "").strip() or None,
        "docsLabel": str((channel_meta or {}).get("docsLabel") or "").strip() or None,
        "blurb": str((channel_meta or {}).get("blurb") or "").strip() or None,
        "hasConfigSchema": bool(properties),
        "hasUiHints": bool(isinstance(ui_hints, dict) and ui_hints),
    }
