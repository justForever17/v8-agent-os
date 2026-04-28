from __future__ import annotations

from typing import Any


def build_short_sequence_verification(
    *,
    goal: str,
    candidate: dict[str, Any] | None = None,
    pre_state: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
    post_state: dict[str, Any] | None = None,
    expected_state_change: str | None = None,
) -> dict[str, Any]:
    pre = dict(pre_state or {})
    post = dict(post_state or {})
    changed_keys = sorted(
        key
        for key in set(pre.keys()) | set(post.keys())
        if pre.get(key) != post.get(key)
    )
    advanced = bool(changed_keys)
    return {
        "version": 1,
        "goal": str(goal or "").strip(),
        "stages": ["pre", "action", "settle", "post"],
        "candidateId": (candidate or {}).get("candidateId"),
        "actionType": (action or {}).get("actionType"),
        "expectedStateChange": expected_state_change,
        "changedKeys": changed_keys,
        "status": "advanced" if advanced else "no_observed_progress",
        "nextStep": "continue" if advanced else "try_next_candidate_or_ask_human",
        "policy": "do_not_repeat_unverified_clicks",
    }
