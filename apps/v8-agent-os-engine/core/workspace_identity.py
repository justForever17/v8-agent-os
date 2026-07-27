from __future__ import annotations

from pathlib import Path

from core.storage import storage
from core.v8_agent_os_paths import WORKSPACE_HOME
from core.workspace_guard import legacy_workspace_residue_status


def normalize_workspace_path(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve(strict=False))
    except Exception:
        return str(Path(raw).expanduser())


def workspace_path_key(value: str | None) -> str:
    normalized = normalize_workspace_path(value)
    return normalized.rstrip("\\/").replace("\\", "/").lower() if normalized else ""


def get_main_workspace_path() -> str:
    configured = str((storage.get_workspace_config() or {}).get("agent_workspace_path") or "").strip()
    if configured:
        expanded = normalize_workspace_path(configured)
        residue = legacy_workspace_residue_status(expanded)
        if not residue["isLegacyResidue"]:
            return expanded
    return normalize_workspace_path(str(WORKSPACE_HOME))


def is_main_workspace_path(value: str | None) -> bool:
    return bool(workspace_path_key(value)) and workspace_path_key(value) == workspace_path_key(get_main_workspace_path())
