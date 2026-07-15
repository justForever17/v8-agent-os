from __future__ import annotations

from typing import Any

from core.command_environment import default_shell_dialect, detect_command_environment
from core.engineering_capsule import effective_engineering_capsule, engineering_capsule_mode
from core.workspace_state_digest import build_workspace_state_digest_context


def _task_brief_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(state or {})
    branch = raw.get("parallel_branch") if isinstance(raw.get("parallel_branch"), dict) else {}
    if isinstance(branch.get("taskBrief"), dict):
        return dict(branch.get("taskBrief") or {})
    route = raw.get("current_route_context") if isinstance(raw.get("current_route_context"), dict) else {}
    for key in ("taskBrief", "task_brief", "engineeringTaskBrief", "engineering_task_brief"):
        if isinstance(route.get(key), dict):
            return dict(route.get(key) or {})
    return {}


def build_engineering_kernel_context(
    *,
    state: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    workspace_text, workspace_diagnostics = build_workspace_state_digest_context(
        state=state,
        session_id=session_id,
    )
    task_brief = _task_brief_from_state(state)
    capsule = effective_engineering_capsule(task_brief)
    mode = engineering_capsule_mode(task_brief)
    command_env = detect_command_environment()
    lines = [
        "[ENGINEERING KERNEL]",
        "This run starts with authoritative workspace awareness. Do not spend a tool call rediscovering the active workspace.",
        f"Environment: OS={command_env['osName']}; shellDialect={command_env['shellDialect']}; commandLanguage={command_env['commandLanguage']}",
        f"Execution posture: {mode if mode != 'none' else 'read_only_no_capsule'}",
    ]
    if capsule:
        lines.append(
            "Capsule: "
            f"id={capsule.get('capsuleId') or 'unknown'}; "
            f"readSet={len(capsule.get('readSet') or [])}; "
            f"writeSet={len(capsule.get('writeSet') or [])}; "
            f"expectedArtifacts={len(capsule.get('expectedArtifacts') or [])}"
        )
    else:
        lines.append("No Engineering Task Capsule is attached. File mutation and shell execution are not authorized; return a blocker or request a routed Engineering episode.")
    lines.append("Engineering episodes remain the durable execution, proof, dependency, and recovery control plane.")
    lines.append(workspace_text.strip())
    lines.append("[/ENGINEERING KERNEL]")
    diagnostics = [
        {
            "source": "engineering_kernel",
            "shellDialect": command_env["shellDialect"],
            "executionMode": mode,
            "capsuleId": capsule.get("capsuleId") if capsule else None,
            "workspaceSource": (workspace_diagnostics[0] if workspace_diagnostics else {}).get("source"),
            "estimatedTokens": max(1, sum(len(line) for line in lines) // 4),
        }
    ]
    return "\n".join(lines) + "\n", diagnostics


__all__ = [
    "build_engineering_kernel_context",
    "default_shell_dialect",
    "detect_command_environment",
]
