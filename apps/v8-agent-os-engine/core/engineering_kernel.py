from __future__ import annotations

from typing import Any

from core.actor_identity import resolve_collaboration_actor
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


def _supervisor_work_mode_from_state(state: dict[str, Any] | None) -> str:
    raw = dict(state or {})
    route = raw.get("current_route_context") if isinstance(raw.get("current_route_context"), dict) else {}
    normalized = str(
        raw.get("supervisor_work_mode")
        or raw.get("supervisorWorkMode")
        or route.get("supervisor_work_mode")
        or route.get("supervisorWorkMode")
        or "daily"
    ).strip().lower()
    return normalized if normalized in {"daily", "engineering"} else "daily"


def build_engineering_kernel_context(
    *,
    state: dict[str, Any] | None = None,
    session_id: str | None = None,
    actor: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    workspace_text, workspace_diagnostics = build_workspace_state_digest_context(
        state=state,
        session_id=session_id,
    )
    task_brief = _task_brief_from_state(state)
    capsule = effective_engineering_capsule(task_brief)
    mode = engineering_capsule_mode(task_brief)
    actor_identity = resolve_collaboration_actor(actor=actor, route_context=state)
    supervisor_work_mode = _supervisor_work_mode_from_state(state)
    execution_posture = (
        f"supervisor_{supervisor_work_mode}"
        if actor_identity.is_supervisor and mode == "none"
        else mode if mode != "none" else "read_only_no_capsule"
    )
    command_env = detect_command_environment()
    lines = [
        "[ENGINEERING KERNEL]",
        "This run starts with authoritative workspace awareness. Do not spend a tool call rediscovering the active workspace.",
        f"Environment: OS={command_env['osName']}; shellDialect={command_env['shellDialect']}; commandLanguage={command_env['commandLanguage']}",
        f"Execution posture: {execution_posture}",
    ]
    if capsule:
        lines.append(
            "Capsule: "
            f"id={capsule.get('capsuleId') or 'unknown'}; "
            f"readSet={len(capsule.get('readSet') or [])}; "
            f"writeSet={len(capsule.get('writeSet') or [])}; "
            f"expectedArtifacts={len(capsule.get('expectedArtifacts') or [])}"
        )
    elif actor_identity.is_supervisor:
        if supervisor_work_mode == "engineering":
            lines.append(
                "Engineering work mode is active. The Supervisor may independently execute long-running project work with common file and command tools inside the active workspace. Delegation and Engineering episodes are optional execution strategies for parallelism, specialist context, recovery, or durable proof; they are not prerequisites for direct implementation."
            )
            lines.append(
                "For direct implementation, inspect before editing, keep changes scoped to the user's task, preserve unrelated work, verify proportionally to risk, and report concrete evidence."
            )
        else:
            lines.append(
                "Daily work mode is active. The Supervisor may still use common tools directly when useful, but should keep the interaction concise and should not silently turn an ordinary request into a persistent engineering project."
            )
    else:
        lines.append("No Engineering Task Capsule is attached. File mutation and shell execution are not authorized; return a blocker or request a routed Engineering episode.")
    lines.append("Engineering episodes are the optional durable execution, proof, dependency, and recovery control plane.")
    lines.append(workspace_text.strip())
    lines.append("[/ENGINEERING KERNEL]")
    diagnostics = [
        {
            "source": "engineering_kernel",
            "shellDialect": command_env["shellDialect"],
            "executionMode": mode,
            "executionPosture": execution_posture,
            "supervisorWorkMode": supervisor_work_mode,
            "actorRole": actor_identity.role,
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
