from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.engineering_capsule import effective_engineering_capsule, engineering_capsule_mode


EngineeringWorkspaceStrategy = Literal["direct", "git_worktree"]

_NON_ISOLATION_RISK_FLAGS = {
    # These are contract/probe diagnostics. They must be repaired or reported,
    # but none of them becomes safer merely because Git is enabled.
    "critical_files_not_proven",
    "repo_not_detected",
    "verification_candidates_missing",
    "write_set_missing",
    "grandchild_explicit_write_subset",
    "grandchild_write_authority_not_inherited",
}


def _truthy_flag(*values: Any) -> bool:
    for value in values:
        if isinstance(value, bool):
            if value:
                return True
            continue
        if str(value or "").strip().lower() in {"1", "true", "yes", "required"}:
            return True
    return False


@dataclass(frozen=True)
class EngineeringWorkspaceStrategyDecision:
    strategy: EngineeringWorkspaceStrategy
    write_contract_present: bool
    isolation_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "writeContractPresent": self.write_contract_present,
            "isolationReasons": list(self.isolation_reasons),
        }


def select_engineering_workspace_strategy(
    task_brief: dict[str, Any] | None,
    *,
    parallel_dispatch: bool = False,
) -> EngineeringWorkspaceStrategyDecision:
    """Choose an execution checkout only after a complete write contract exists.

    Git worktrees are an optional isolation mechanism, not an Engineering
    prerequisite. A serial low-risk task stays in the bound workspace. A task
    becomes eligible for a worktree only when it is both writable and needs
    parallel isolation, risk isolation, or explicitly durable recovery.
    """

    task = dict(task_brief or {})
    context = task.get("context") if isinstance(task.get("context"), dict) else {}
    capsule = effective_engineering_capsule(task)
    write_contract_present = bool(
        engineering_capsule_mode(task) == "write"
        and str(capsule.get("contractStatus") or "").strip().lower() == "valid"
        and list(capsule.get("writeSet") or [])
        and list(capsule.get("expectedOutputs") or [])
        and capsule.get("acceptance") not in (None, "", [], {})
    )
    if not write_contract_present:
        return EngineeringWorkspaceStrategyDecision(
            strategy="direct",
            write_contract_present=False,
        )

    reasons: list[str] = []
    sibling_count = int(task.get("siblingCount") or 0)
    parallel_worker = context.get("parallelWorker") if isinstance(context.get("parallelWorker"), dict) else {}
    if parallel_dispatch or sibling_count > 1 or int(parallel_worker.get("count") or 0) > 1:
        reasons.append("parallel_writes")

    risk_flags = [
        str(value or "").strip()
        for value in list(capsule.get("riskFlags") or [])
        if str(value or "").strip()
        and str(value or "").strip() not in _NON_ISOLATION_RISK_FLAGS
    ]
    if risk_flags or _truthy_flag(
        capsule.get("requiresIsolation"),
        task.get("requiresIsolation"),
        context.get("requiresIsolation"),
    ):
        reasons.append("risk_isolation")

    if _truthy_flag(
        capsule.get("durableRecovery"),
        capsule.get("recoveryRequired"),
        task.get("durableRecovery"),
        task.get("recoveryRequired"),
        context.get("durableRecovery"),
        context.get("recoveryRequired"),
        context.get("longRunning"),
    ):
        reasons.append("durable_recovery")

    return EngineeringWorkspaceStrategyDecision(
        strategy="git_worktree" if reasons else "direct",
        write_contract_present=True,
        isolation_reasons=tuple(dict.fromkeys(reasons)),
    )


__all__ = [
    "EngineeringWorkspaceStrategyDecision",
    "select_engineering_workspace_strategy",
]
