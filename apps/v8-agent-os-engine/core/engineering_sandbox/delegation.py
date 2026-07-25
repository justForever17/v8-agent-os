from __future__ import annotations

import hashlib
from typing import Any

from core.engineering_capsule import effective_engineering_capsule, engineering_capsule_mode

from .contracts import SandboxNetworkProfile
from .service import get_engineering_sandbox_service


def prepare_delegated_engineering_workspace(
    *,
    base_state: dict[str, Any],
    task_brief: dict[str, Any],
    delegation_id: str,
    current_depth: int,
    runtime_context: dict[str, Any],
) -> dict[str, Any] | None:
    """Allocate the worktree + sandbox lease used by one delegated branch.

    This belongs to the engineering execution layer rather than a particular tool
    entry point because durable Runtime episodes and direct broker calls must use
    exactly the same allocation contract.
    """

    capsule = effective_engineering_capsule(task_brief)
    capsule_mode = engineering_capsule_mode(task_brief)
    if capsule_mode not in {"verify", "write"}:
        return None
    write_set = [
        str(item or "").strip()
        for item in list(capsule.get("writeSet") or [])
        if str(item or "").strip()
    ]
    if capsule_mode == "write" and not write_set:
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
    if capsule_mode == "verify" and not parent_worktree_id:
        # A read-only inspection has no candidate branch to isolate. Keep it on
        # the active/original workspace so its Capsule path and native read-tool
        # boundary describe the same checkout. A verification task receives a
        # child worktree only when it must snapshot a managed parent's pending
        # changes.
        return None
    prepared = get_engineering_sandbox_service().prepare_task_workspace(
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
        execution_mode="write" if capsule_mode == "write" else "read",
        network_profile=network_profile,
        parent_worktree_id=parent_worktree_id,
    )
    return {
        **prepared.runtime_context(),
        "run_id": run_id,
        "parent_worktree_id": parent_worktree_id,
        "write_set": write_set,
        "engineering_capsule_mode": capsule_mode,
        "change_set_status": "pending",
    }


__all__ = ["prepare_delegated_engineering_workspace"]
