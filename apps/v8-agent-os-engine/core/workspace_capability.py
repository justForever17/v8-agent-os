from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.actor_identity import resolve_collaboration_actor
from core.engineering_sandbox.contracts import SandboxPolicy
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
    authority_workspace_root: Path | None = None
    managed_execution: bool = False

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
            "authorityWorkspaceRoot": str(self.authority_workspace_root or self.active_workspace_root),
            "managedExecution": self.managed_execution,
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


def _managed_execution_policy(context: dict[str, Any]) -> SandboxPolicy | None:
    managed = bool(
        context.get("managed_engineering_execution")
        or context.get("managedEngineeringExecution")
    )
    if not managed:
        return None
    payload = context.get("sandbox_policy") or context.get("sandboxPolicy")
    if not isinstance(payload, dict):
        raise RuntimeError("managed_workspace_sandbox_policy_required")
    policy = SandboxPolicy.from_dict(payload)
    expected_digest = str(
        context.get("sandbox_policy_digest")
        or context.get("sandboxPolicyDigest")
        or ""
    ).strip()
    if not expected_digest or expected_digest != policy.digest:
        raise RuntimeError("managed_workspace_sandbox_policy_digest_mismatch")
    lease_id = str(
        context.get("sandbox_lease_id")
        or context.get("sandboxLeaseId")
        or ""
    ).strip()
    if lease_id and lease_id != policy.lease_id:
        raise RuntimeError("managed_workspace_sandbox_lease_mismatch")
    execution_root = str(context.get("workspace_path") or context.get("workspacePath") or "").strip()
    if execution_root and _resolve_path(execution_root) != _resolve_path(policy.worktree_root):
        raise RuntimeError("managed_workspace_execution_root_mismatch")
    original_root = str(
        context.get("original_workspace_path")
        or context.get("originalWorkspacePath")
        or ""
    ).strip()
    if original_root and _resolve_path(original_root) != _resolve_path(policy.original_workspace_root):
        raise RuntimeError("managed_workspace_authority_root_mismatch")
    return policy


def build_workspace_binding(runtime_context: dict[str, Any] | None = None, *, runtime_kind: str | None = None) -> WorkspaceBinding:
    context = dict(runtime_context or {})
    effective_runtime_kind = str(runtime_kind or context.get("runtime_kind") or context.get("runtimeKind") or "chat").strip() or "chat"
    managed_policy = _managed_execution_policy(context)
    authority_context = dict(context)
    if managed_policy is not None:
        # The user authorizes the durable workspace. The lease then replaces only
        # the process/file execution root with its isolated worktree.
        authority_context["workspace_path"] = managed_policy.original_workspace_root
        authority_context["workspacePath"] = managed_policy.original_workspace_root
    descriptor = workspace_authority_service.resolve_from_context(
        authority_context,
        runtime_kind=effective_runtime_kind,
    ).as_dict()
    authority_root = _resolve_path(
        str(descriptor.get("workspaceRoot") or workspace_resolution_service.get_main_workspace_path())
    )
    if managed_policy is not None and authority_root != _resolve_path(managed_policy.original_workspace_root):
        raise RuntimeError("managed_workspace_authority_resolution_mismatch")
    active_root = (
        _resolve_path(managed_policy.worktree_root)
        if managed_policy is not None
        else authority_root
    )
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
        authority_workspace_root=authority_root,
        managed_execution=managed_policy is not None,
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
_UNC_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w:\\])((?:\\\\|//)[^\\/\s]+[\\/][^\s\"'<>|;&]+)")
_COMMAND_WORD_RE = re.compile(r'''"([^"]*)"|'([^']*)'|([^\s"']+)''')
_COMMAND_ENV_RE = re.compile(r"(?i)%([a-z_][a-z0-9_()]*)%|\$env:([a-z_][a-z0-9_]*)|\$\{env:([a-z_][a-z0-9_()]*)\}")


def _expand_command_path_variables(text: str) -> str:
    environment = {name.casefold(): value for name, value in os.environ.items()}
    environment.setdefault("userprofile", str(Path.home()))
    return _COMMAND_ENV_RE.sub(
        lambda match: environment.get(next(group for group in match.groups() if group).casefold(), match.group(0)),
        text,
    )


def _is_absolute_command_path(text: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]:[\\/]", text) or text.startswith("\\\\") or (os.name != "nt" and text.startswith("/")))


def extract_absolute_paths_from_command(command: str) -> list[str]:
    paths: list[str] = []
    patterns = [_WINDOWS_ABSOLUTE_PATH_RE, _UNC_ABSOLUTE_PATH_RE]
    if os.name != "nt":
        patterns.append(_POSIX_ABSOLUTE_PATH_RE)
    # Expand each word after retaining its quote boundary: an environment value
    # containing spaces is one path, not a new command token named C:\Program.
    for word in _COMMAND_WORD_RE.finditer(str(command or "")):
        text = next(group for group in word.groups() if group is not None)
        expanded = _expand_command_path_variables(text)
        expanded = expanded.replace("~/", f"{Path.home()}/").replace("~\\", f"{Path.home()}\\")
        if _is_absolute_command_path(expanded):
            candidates = [expanded] if word.group(3) is None or text != expanded else []
        else:
            candidates = []
        if not candidates:
            candidates = [match.group(1).rstrip(".,)") for pattern in patterns for match in pattern.finditer(expanded)]
        for value in candidates:
            if value and value not in paths:
                paths.append(value)
    return paths


def simple_host_command_access(command: str) -> tuple[str, str] | None:
    """Recognize a single literal read or argument-free app launch, never a script.

    This is workspace routing evidence, not a Safety approval. Other grammar,
    remote paths, interpreters, arguments and dynamic expressions stay scoped.
    """
    raw = str(command or "").strip()
    if not raw or "\n" in raw or "\r" in raw:
        return None
    tokens: list[str] = []
    end = 0
    for match in _COMMAND_WORD_RE.finditer(raw):
        if (tokens and match.start() == end) or raw[end:match.start()].strip():
            return None
        word = next(group for group in match.groups() if group is not None)
        expanded = _expand_command_path_variables(word)
        if match.group(3) is not None and word == expanded and re.search(r"[(){}]", word):
            return None
        if re.search(r"[;$`|<>\r\n{}]", expanded) or ("&" in expanded and not (not tokens and expanded == "&")):
            return None
        tokens.append(expanded)
        end = match.end()
    if raw[end:].strip() or not tokens:
        return None
    name = tokens.pop(0).lower()
    action = "host_read"
    if name in {"get-childitem", "get-item", "test-path", "get-content"}:
        flags = {"-force", "-name", "-file", "-directory", "-raw", "-recurse"}
        path_options = {"-path", "-literalpath"}
        target = ""
        while tokens:
            token = tokens.pop(0)
            if token.lower() in flags:
                continue
            if token.lower() == "-encoding" and name == "get-content":
                if not tokens or tokens.pop(0).lower() not in {"utf8", "utf8bom", "utf8nobom", "unicode", "ascii", "default"}:
                    return None
                continue
            if token.lower() in path_options:
                if not tokens:
                    return None
                token = tokens.pop(0)
            if target or not _is_absolute_command_path(token):
                return None
            target = token
    else:
        action = "host_local_launch"
        if name == "start-process":
            if tokens and tokens[0].lower() == "-filepath":
                tokens.pop(0)
            target = tokens.pop(0) if tokens else ""
        elif name == "&":
            target = tokens.pop(0) if tokens else ""
        else:
            target = name
        if tokens or not target.lower().endswith(".exe"):
            return None
        if re.split(r"[\\/]", target)[-1].lower() in {
            "cmd.exe", "powershell.exe", "pwsh.exe", "wscript.exe", "cscript.exe", "mshta.exe",
            "rundll32.exe", "regsvr32.exe", "python.exe", "pythonw.exe", "node.exe", "bash.exe", "sh.exe", "wsl.exe",
        }:
            return None
    if not _is_absolute_command_path(target) or target.startswith(("\\\\", "//")) or re.search(r"[*?\[\]]", target):
        return None
    return action, target


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
    context = dict(runtime_context or {})
    host_access = simple_host_command_access(command)
    root_host_access = bool(
        host_access
        and resolve_collaboration_actor(runtime_context=context).is_supervisor
        and str(runtime_kind or context.get("runtime_kind") or context.get("runtimeKind") or "").lower() in {"chat", "supervisor"}
        and not binding.managed_execution
        and not (context.get("sandbox_policy") or context.get("sandboxPolicy"))
        and not (context.get("engineering_task_capsule") or context.get("engineeringTaskCapsule"))
        and str(context.get("engineering_capsule_mode") or context.get("engineeringCapsuleMode") or "none").lower() == "none"
        and not (context.get("delegation_id") or context.get("delegationId"))
    )
    expanded_command = _expand_command_path_variables(str(command or ""))
    if any(expanded_command[match.end():match.end() + 1] in {"\\", "/"} for match in _COMMAND_ENV_RE.finditer(expanded_command)):
        return {
            "ok": False,
            "error": "workspace_command_unresolved_path",
            "summary": "命令中的环境路径无法解析，请使用可核验的完整路径。",
            "binding": binding.as_dict(),
        }
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
            if root_host_access:
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
        **({"hostAccess": {"action": host_access[0], "safetyAssessmentRequired": True}} if root_host_access else {}),
    }
