from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.workspace_resolution import workspace_resolution_service


@dataclass(frozen=True)
class WorkspaceBinding:
    runtime_kind: str
    workspace_id: str
    project_id: str
    active_workspace_root: Path
    main_workspace_root: Path
    source: str
    uses_scoped_workspace: bool
    is_scoped_override: bool
    allowed_extra_roots: tuple[Path, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "runtimeKind": self.runtime_kind,
            "workspaceId": self.workspace_id,
            "projectId": self.project_id,
            "activeWorkspaceRoot": str(self.active_workspace_root),
            "mainWorkspaceRoot": str(self.main_workspace_root),
            "source": self.source,
            "usesScopedWorkspace": self.uses_scoped_workspace,
            "isScopedOverride": self.is_scoped_override,
            "allowedExtraRoots": [str(item) for item in self.allowed_extra_roots],
        }


def _resolve_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _is_within(path: Path, root: Path) -> bool:
    try:
        _resolve_path(path).relative_to(_resolve_path(root))
        return True
    except Exception:
        return False


def _iter_extra_roots(context: dict[str, Any]) -> tuple[Path, ...]:
    raw = context.get("allowed_extra_roots") or context.get("allowedExtraRoots") or []
    if isinstance(raw, str):
        raw = [raw]
    roots: list[Path] = []
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            text = str(item or "").strip()
            if text:
                roots.append(_resolve_path(text))
    return tuple(roots)


def build_workspace_binding(runtime_context: dict[str, Any] | None = None, *, runtime_kind: str | None = None) -> WorkspaceBinding:
    context = dict(runtime_context or {})
    effective_runtime_kind = str(runtime_kind or context.get("runtime_kind") or context.get("runtimeKind") or "chat").strip() or "chat"
    descriptor = workspace_resolution_service.resolve_workspace_descriptor(
        runtime_kind=effective_runtime_kind,
        session_id=str(context.get("session_id") or context.get("sessionId") or "").strip() or None,
        explicit_workspace_id=str(context.get("workspace_id") or context.get("workspaceId") or "").strip() or None,
        explicit_workspace_path=str(context.get("workspace_path") or context.get("workspacePath") or "").strip() or None,
        explicit_project_id=str(context.get("project_id") or context.get("projectId") or "").strip() or None,
    )
    active_root = _resolve_path(str(descriptor.get("workspaceRoot") or workspace_resolution_service.get_main_workspace_path()))
    main_root = _resolve_path(str(descriptor.get("mainWorkspacePath") or workspace_resolution_service.get_main_workspace_path()))
    return WorkspaceBinding(
        runtime_kind=effective_runtime_kind,
        workspace_id=str(descriptor.get("workspaceId") or context.get("workspace_id") or context.get("workspaceId") or "").strip(),
        project_id=str(descriptor.get("projectId") or context.get("project_id") or context.get("projectId") or "").strip(),
        active_workspace_root=active_root,
        main_workspace_root=main_root,
        source=str(descriptor.get("source") or "").strip() or "main_workspace",
        uses_scoped_workspace=bool(descriptor.get("usesScopedWorkspace")),
        is_scoped_override=bool(descriptor.get("isScopedOverride")),
        allowed_extra_roots=_iter_extra_roots(context),
    )


def resolve_workspace_tool_path(
    path: str,
    *,
    runtime_context: dict[str, Any] | None = None,
    runtime_kind: str | None = None,
) -> dict[str, Any]:
    binding = build_workspace_binding(runtime_context, runtime_kind=runtime_kind)
    raw = str(path or "").strip()
    if not raw:
        return {
            "ok": False,
            "error": "missing_path",
            "summary": "路径不能为空。",
            "binding": binding.as_dict(),
        }

    input_path = Path(raw).expanduser()
    input_was_relative = not input_path.is_absolute()
    resolved = _resolve_path(binding.active_workspace_root / input_path if input_was_relative else input_path)

    if _is_within(resolved, binding.active_workspace_root):
        relation = "inside_active_workspace"
        allowed = True
    elif any(_is_within(resolved, root) for root in binding.allowed_extra_roots):
        relation = "inside_allowed_extra_root"
        allowed = True
    else:
        relation = "outside_active_workspace"
        allowed = False

    payload = {
        "ok": allowed,
        "inputPath": raw,
        "resolvedPath": str(resolved),
        "inputWasRelative": input_was_relative,
        "relation": relation,
        "binding": binding.as_dict(),
    }
    if not allowed:
        payload.update(
            {
                "error": "workspace_boundary_violation",
                "summary": (
                    "路径不在当前 Active Workspace Root 内，已按硬工作区边界拒绝。"
                    f" activeWorkspaceRoot={binding.active_workspace_root}"
                ),
            }
        )
    return payload


_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w:])([A-Za-z]:[\\/][^\s\"'<>|;&]+)")
_POSIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![:\w])(/(?:[^\s\"'<>|;&]+))")


def extract_absolute_paths_from_command(command: str) -> list[str]:
    text = str(command or "")
    userprofile = str(os.environ.get("USERPROFILE") or Path.home())
    expanded_text = os.path.expandvars(
        text.replace("%USERPROFILE%", userprofile)
        .replace("$env:USERPROFILE", userprofile)
        .replace("${env:USERPROFILE}", userprofile)
    )
    paths: list[str] = []
    patterns = [_WINDOWS_ABSOLUTE_PATH_RE]
    if os.name != "nt":
        patterns.append(_POSIX_ABSOLUTE_PATH_RE)
    for source_text in (text, expanded_text):
        tilde_expanded = source_text.replace("~/", f"{Path.home()}/").replace("~\\", f"{Path.home()}\\")
        for pattern in patterns:
            for match in pattern.finditer(tilde_expanded):
                value = str(match.group(1) or "").rstrip(".,)")
                if value and value not in paths:
                    paths.append(value)
    return paths


def preflight_command_workspace(
    command: str,
    *,
    cwd: str | None = None,
    runtime_context: dict[str, Any] | None = None,
    runtime_kind: str | None = None,
) -> dict[str, Any]:
    binding = build_workspace_binding(runtime_context, runtime_kind=runtime_kind)
    cwd_result = resolve_workspace_tool_path(cwd or ".", runtime_context=runtime_context, runtime_kind=runtime_kind)
    if not cwd_result.get("ok"):
        return {
            "ok": False,
            "error": "workspace_cwd_violation",
            "summary": "命令 cwd 不在当前 Active Workspace Root 内，已拒绝执行。",
            "cwd": cwd,
            "resolvedCwd": cwd_result.get("resolvedPath"),
            "binding": binding.as_dict(),
        }

    violations: list[dict[str, str]] = []
    for raw_path in extract_absolute_paths_from_command(command):
        result = resolve_workspace_tool_path(raw_path, runtime_context=runtime_context, runtime_kind=runtime_kind)
        if not result.get("ok"):
            violations.append(
                {
                    "path": raw_path,
                    "resolvedPath": str(result.get("resolvedPath") or ""),
                    "relation": str(result.get("relation") or ""),
                }
            )

    if violations:
        return {
            "ok": False,
            "error": "workspace_command_path_violation",
            "summary": "命令引用了当前 Active Workspace Root 之外的绝对路径，已按硬工作区边界拒绝。",
            "cwd": cwd,
            "resolvedCwd": cwd_result.get("resolvedPath"),
            "violations": violations[:8],
            "binding": binding.as_dict(),
        }

    return {
        "ok": True,
        "cwd": str(cwd_result.get("resolvedPath") or binding.active_workspace_root),
        "binding": binding.as_dict(),
    }
