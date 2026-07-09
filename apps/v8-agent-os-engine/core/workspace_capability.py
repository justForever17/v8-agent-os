from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.workspace_authority import workspace_authority_service
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
    trust_state: str = "trusted"
    trust_source: str = "legacy_auto_trusted"
    is_fallback_to_main: bool = False
    side_effects_allowed: bool = True
    capabilities: dict[str, bool] | None = None
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
            "trustState": self.trust_state,
            "trustSource": self.trust_source,
            "isFallbackToMain": self.is_fallback_to_main,
            "sideEffectsAllowed": self.side_effects_allowed,
            "capabilities": dict(self.capabilities or {}),
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


def global_skill_roots() -> tuple[Path, ...]:
    home = _resolve_path(Path.home())
    return (
        _resolve_path(home / ".agents" / "skills"),
        _resolve_path(home / ".agent" / "skill"),
    )


def is_global_skill_path(path: str | Path) -> bool:
    resolved = _resolve_path(path)
    return any(_is_within(resolved, root) for root in global_skill_roots())


_SKILL_PATH_MUTATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)(^|[\s;&|])(?:remove-item|del|erase|rm|rmdir|rd|move-item|move|rename-item|ren|mv|"
        r"set-content|add-content|out-file|new-item|copy-item|copy|cp|xcopy|robocopy|chmod|chown|"
        r"icacls|takeown)\b"
    ),
    re.compile(r"(?i)(^|[\s;&|])(?:python|python3|node|pwsh|powershell|cmd|bash|sh)\b.*\b(?:unlink|rmtree|remove|rename|write_text|write_bytes)\b"),
)


def command_appears_to_mutate_path(command: str) -> bool:
    text = str(command or "")
    return any(pattern.search(text) for pattern in _SKILL_PATH_MUTATION_PATTERNS)


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
    descriptor = workspace_authority_service.resolve_from_context(context, runtime_kind=effective_runtime_kind).as_dict()
    active_root = _resolve_path(str(descriptor.get("workspaceRoot") or workspace_resolution_service.get_main_workspace_path()))
    main_root = _resolve_path(str(descriptor.get("mainWorkspaceRoot") or descriptor.get("mainWorkspacePath") or workspace_resolution_service.get_main_workspace_path()))
    return WorkspaceBinding(
        runtime_kind=effective_runtime_kind,
        workspace_id=str(descriptor.get("workspaceId") or context.get("workspace_id") or context.get("workspaceId") or "").strip(),
        project_id=str(descriptor.get("projectId") or context.get("project_id") or context.get("projectId") or "").strip(),
        active_workspace_root=active_root,
        main_workspace_root=main_root,
        source=str(descriptor.get("source") or "").strip() or "main_workspace",
        uses_scoped_workspace=bool(descriptor.get("usesScopedWorkspace")),
        is_scoped_override=bool(descriptor.get("isScopedOverride")),
        trust_state=str(descriptor.get("trustState") or "trusted").strip() or "trusted",
        trust_source=str(descriptor.get("trustSource") or "legacy_auto_trusted").strip() or "legacy_auto_trusted",
        is_fallback_to_main=bool(descriptor.get("isFallbackToMain")),
        side_effects_allowed=bool(descriptor.get("sideEffectsAllowed")),
        capabilities=dict(descriptor.get("capabilities") or {}),
        allowed_extra_roots=_iter_extra_roots(context),
    )


def workspace_side_effect_block_payload(binding: WorkspaceBinding, *, operation: str, subject: str = "") -> dict[str, Any]:
    reason = "workspace_fallback_to_main" if binding.is_fallback_to_main else "workspace_not_trusted"
    summary = (
        "当前会话尚未绑定明确项目工作区，已阻止本机副作用操作以避免误写入主工作区。"
        if binding.is_fallback_to_main
        else "当前工作区尚未被信任，已阻止本机副作用操作。"
    )
    return {
        "ok": False,
        "kind": "workspace_side_effect_blocked",
        "error": reason,
        "summary": summary,
        "operation": operation,
        "subject": subject,
        "workspaceBinding": binding.as_dict(),
        "recommendedNextAction": "先选择并信任项目工作区，再重试该操作。",
    }


def ensure_workspace_side_effect_allowed(
    runtime_context: dict[str, Any] | None = None,
    *,
    runtime_kind: str | None = None,
    operation: str = "workspace_side_effect",
    subject: str = "",
) -> dict[str, Any]:
    binding = build_workspace_binding(runtime_context, runtime_kind=runtime_kind)
    if binding.side_effects_allowed:
        return {"ok": True, "binding": binding.as_dict()}
    return workspace_side_effect_block_payload(binding, operation=operation, subject=subject)


def resolve_workspace_tool_path(
    path: str,
    *,
    runtime_context: dict[str, Any] | None = None,
    runtime_kind: str | None = None,
    allow_global_skill_read: bool = False,
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
    elif allow_global_skill_read and is_global_skill_path(resolved):
        relation = "inside_global_skill_read_execute_root"
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
    if not binding.side_effects_allowed:
        payload = workspace_side_effect_block_payload(binding, operation="command", subject=command)
        return {**payload, "binding": binding.as_dict()}
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
            resolved_path = str(result.get("resolvedPath") or raw_path)
            if is_global_skill_path(resolved_path):
                if command_appears_to_mutate_path(command):
                    violations.append(
                        {
                            "path": raw_path,
                            "resolvedPath": resolved_path,
                            "relation": "global_skill_read_execute_only",
                        }
                    )
                continue
            violations.append(
                {
                    "path": raw_path,
                    "resolvedPath": resolved_path,
                    "relation": str(result.get("relation") or ""),
                }
            )

    if violations:
        skill_mutation = any(item.get("relation") == "global_skill_read_execute_only" for item in violations)
        return {
            "ok": False,
            "error": "global_skill_mutation_violation" if skill_mutation else "workspace_command_path_violation",
            "summary": (
                "全局 Skill 目录只允许读取和执行，禁止通过 Agent 命令修改、移动或删除。"
                if skill_mutation
                else "命令引用了当前 Active Workspace Root 之外的绝对路径，已按硬工作区边界拒绝。"
            ),
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
