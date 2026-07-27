from __future__ import annotations

import hashlib
import logging
from typing import Any

from core.engineering_capsule import effective_engineering_capsule, engineering_capsule_mode

from .contracts import SandboxNetworkProfile
from .service import get_engineering_sandbox_service
from .strategy import select_engineering_workspace_strategy


logger = logging.getLogger(__name__)


class EngineeringWorkspaceIsolationError(RuntimeError):
    """A task needs Git isolation that the bound workspace cannot provide."""

    def __init__(self, code: str, *, reason: str, strategy: dict[str, Any]) -> None:
        super().__init__(code)
        self.code = code
        self.details = {
            "reason": reason,
            "strategy": dict(strategy),
            "directExecutionAvailable": True,
        }


def prepare_delegated_engineering_workspace(
    *,
    base_state: dict[str, Any],
    task_brief: dict[str, Any],
    delegation_id: str,
    current_depth: int,
    runtime_context: dict[str, Any],
    parallel_dispatch: bool = False,
) -> dict[str, Any] | None:
    """Optionally allocate the worktree + sandbox lease for one write branch.

    A valid Capsule is always authoritative. Git isolation is late-bound and is
    selected only for parallel, risky, or explicitly recoverable writes. Serial
    low-risk work and non-Git workspaces continue in the bound workspace.
    """

    capsule = effective_engineering_capsule(task_brief)
    capsule_mode = engineering_capsule_mode(task_brief)
    if capsule_mode != "write":
        return None
    write_set = [
        str(item or "").strip()
        for item in list(capsule.get("writeSet") or [])
        if str(item or "").strip()
    ]
    if not write_set:
        raise RuntimeError("managed_worktree_write_set_required")
    route_context = dict(base_state.get("current_route_context") or {})
    original_workspace = str(
        base_state.get("original_workspace_path")
        or base_state.get("originalWorkspacePath")
        or route_context.get("original_workspace_path")
        or route_context.get("originalWorkspacePath")
        or base_state.get("workspace_path")
        or base_state.get("workspacePath")
        or runtime_context.get("workspace_path")
        or runtime_context.get("workspacePath")
        or ""
    ).strip()
    if not original_workspace:
        raise RuntimeError("managed_worktree_workspace_required")
    run_id = str(
        base_state.get("run_id")
        or base_state.get("runId")
        or runtime_context.get("run_id")
        or runtime_context.get("runId")
        or ""
    ).strip()
    if not run_id:
        raise RuntimeError("managed_worktree_run_required")
    task_context = task_brief.get("context") if isinstance(task_brief.get("context"), dict) else {}
    strategy = select_engineering_workspace_strategy(
        task_brief,
        parallel_dispatch=parallel_dispatch,
    )
    if strategy.strategy != "git_worktree":
        logger.info(
            "Engineering task will use the bound workspace directly",
            extra={
                "delegationId": delegation_id,
                "strategy": strategy.as_dict(),
            },
        )
        return {
            "workspace_path": original_workspace,
            "original_workspace_path": original_workspace,
            "write_set": write_set,
            "engineering_capsule_mode": capsule_mode,
            "engineering_workspace_strategy": "direct",
            "engineering_workspace_strategy_reasons": [],
            "managed_engineering_execution": False,
        }
    raw_network_profile = str(
        capsule.get("networkProfile")
        or task_brief.get("networkProfile")
        or task_context.get("networkProfile")
        or ""
    ).strip().lower()
    try:
        network_profile = SandboxNetworkProfile(
            raw_network_profile or SandboxNetworkProfile.NETWORKED_PARTIAL.value
        )
    except ValueError as exc:
        raise RuntimeError(f"sandbox_network_profile_invalid:{raw_network_profile}") from exc
    worktree_id = "wt_" + hashlib.sha256(delegation_id.encode("utf-8")).hexdigest()[:24]
    parent_worktree_id = str(
        base_state.get("worktree_id")
        or route_context.get("worktree_id")
        or route_context.get("worktreeId")
        or ""
    ).strip() or None
    sandbox_service = get_engineering_sandbox_service()
    try:
        repository_status = sandbox_service.project_repository_status(
            workspace_root=original_workspace,
            project_id=str(base_state.get("project_id") or runtime_context.get("project_id") or "").strip()
            or None,
        )
    except Exception as exc:
        if str(getattr(exc, "code", "") or "").strip() != "git_not_installed":
            raise
        raise EngineeringWorkspaceIsolationError(
            "git_parallel_isolation_unavailable",
            reason="git_not_installed",
            strategy=strategy.as_dict(),
        ) from exc
    if str((repository_status.get("repository") or {}).get("state") or "").strip() != "ready":
        raise EngineeringWorkspaceIsolationError(
            "git_parallel_isolation_not_enabled",
            reason=str((repository_status.get("repository") or {}).get("state") or "not_enabled"),
            strategy=strategy.as_dict(),
        )
    prepared = sandbox_service.prepare_task_workspace(
        workspace_root=original_workspace,
        project_id=str(base_state.get("project_id") or runtime_context.get("project_id") or "").strip()
        or None,
        session_id=str(base_state.get("session_id") or runtime_context.get("session_id") or "").strip()
        or None,
        run_id=run_id,
        delegation_id=delegation_id,
        worktree_id=worktree_id,
        write_set=write_set,
        actor_role="grandchild" if current_depth > 0 else "direct_subagent",
        runtime_kind="engineering",
        execution_mode="write",
        network_profile=network_profile,
        parent_worktree_id=parent_worktree_id,
    )
    return {
        **prepared.runtime_context(),
        "run_id": run_id,
        "parent_worktree_id": parent_worktree_id,
        "write_set": write_set,
        "engineering_capsule_mode": capsule_mode,
        "engineering_workspace_strategy": "git_worktree",
        "engineering_workspace_strategy_reasons": list(strategy.isolation_reasons),
        "change_set_status": "pending",
    }


__all__ = [
    "EngineeringWorkspaceIsolationError",
    "prepare_delegated_engineering_workspace",
]
