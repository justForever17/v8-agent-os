from __future__ import annotations

from typing import Any, Dict, List

from runtimes.computer_use.fallback_policy import recovery_fallback_order


def build_recovery_ladder(*, high_risk: bool) -> List[str]:
    return list(recovery_fallback_order(high_risk=high_risk))


def build_recovery_policy_metadata(
    *,
    high_risk: bool,
    visual_fallback: Dict[str, Any] | None = None,
    attempt_count: int = 1,
) -> Dict[str, Any]:
    return {
        "recoveryLadder": build_recovery_ladder(high_risk=high_risk),
        "visualFallbackUsed": bool(visual_fallback),
        "attemptCount": max(1, int(attempt_count or 1)),
    }
