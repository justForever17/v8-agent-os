from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core.audit_logger import audit_logger
from core.v8_agent_os_paths import WORKSPACE_HOME


logger = logging.getLogger("v8_agent_os.workspace_guard")


def _workspace_repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _normalize_path(value: str | Path) -> Path:
    return Path(str(value or "").strip()).expanduser()


def _legacy_pattern_matches(path_obj: Path) -> tuple[bool, str]:
    repo_root = _workspace_repo_root()
    normalized = path_obj.resolve(strict=False)
    legacy_targets = {
        repo_root / "apps" / "engine" / "workspace": "命中旧 monorepo engine workspace 路径",
        repo_root / "apps" / "admin": "命中旧 monorepo admin 根目录",
        repo_root / "apps" / "web": "命中旧 monorepo web 根目录",
    }

    for target, reason in legacy_targets.items():
        candidate = target.resolve(strict=False)
        if normalized == candidate:
            return True, reason
        try:
            normalized.relative_to(candidate)
            return True, f"{reason} 下的派生目录"
        except ValueError:
            continue
    return False, ""


def legacy_workspace_residue_status(value: str | Path | None) -> dict[str, Any]:
    raw = str(value or "").strip()
    recommended = str(WORKSPACE_HOME.expanduser())
    if not raw:
        return {
            "isLegacyResidue": False,
            "legacyReason": "",
            "recommendedPath": recommended,
            "autoCreateAllowed": False,
        }

    path_obj = _normalize_path(raw)
    matched, reason = _legacy_pattern_matches(path_obj)
    return {
        "isLegacyResidue": matched,
        "legacyReason": reason if matched else "",
        "recommendedPath": recommended,
        "autoCreateAllowed": not matched,
    }


def build_workspace_path_status(workspace_path: str) -> dict[str, Any]:
    normalized = str(workspace_path or "").strip()
    residue = legacy_workspace_residue_status(normalized)
    if not normalized:
        return {
            "exists": False,
            "isAbsolute": False,
            "writable": False,
            "writableTarget": "",
            "reason": "未设置主工作区路径",
            **residue,
        }

    path_obj = _normalize_path(normalized)
    is_absolute = path_obj.is_absolute()
    exists = path_obj.exists() if is_absolute else False
    writable_target = path_obj if exists else path_obj.parent
    writable = bool(writable_target and writable_target.exists() and os.access(writable_target, os.W_OK))

    if residue["isLegacyResidue"]:
        reason = (
            f"检测到 legacy monorepo residue：{residue['legacyReason']}。"
            "此路径不再接受为当前 canonical workspace，也不会允许自动建目录。"
        )
    elif not is_absolute:
        reason = "当前路径不是绝对路径"
    elif exists and not path_obj.is_dir():
        reason = "当前路径存在，但不是目录"
    elif not exists:
        reason = "目录尚不存在，将在首次使用时按需创建"
    elif not writable:
        reason = "当前目录存在，但没有写入权限"
    else:
        reason = "目录状态正常，可作为默认执行目录"

    return {
        "exists": exists,
        "isAbsolute": is_absolute,
        "writable": writable,
        "writableTarget": str(writable_target) if writable_target else "",
        "reason": reason,
        **residue,
    }


def ensure_workspace_auto_create_allowed(
    workspace_path: str | Path,
    *,
    source: str,
    allow_missing: bool = False,
) -> Path:
    path_obj = _normalize_path(workspace_path)
    residue = legacy_workspace_residue_status(path_obj)
    if residue["isLegacyResidue"]:
        try:
            audit_logger.log(
                source_type="SYSTEM",
                action="workspace_legacy_residue_blocked",
                status="WARNING",
                details=(
                    f"source={source}; path={path_obj}; "
                    f"reason={residue['legacyReason']}; recommended={residue['recommendedPath']}"
                ),
            )
        except Exception:
            pass
        logger.warning(
            "Blocked legacy workspace residue at source=%s path=%s reason=%s recommended=%s",
            source,
            str(path_obj),
            residue["legacyReason"],
            residue["recommendedPath"],
        )
        raise ValueError(
            f"当前工作区路径命中 legacy monorepo residue：{residue['legacyReason']}。"
            f"请改用 canonical workspace，例如：{residue['recommendedPath']}"
        )

    if not path_obj.is_absolute():
        raise ValueError("当前工作区必须是绝对路径，不能在此入口自动建目录。")

    if path_obj.exists() and not path_obj.is_dir():
        raise ValueError("当前工作区路径存在，但不是目录。")

    if not path_obj.exists() and not allow_missing:
        raise ValueError("当前工作区目录不存在，且该入口不允许自动创建。")

    return path_obj
