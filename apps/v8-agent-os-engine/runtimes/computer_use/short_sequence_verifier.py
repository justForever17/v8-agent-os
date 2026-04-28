from __future__ import annotations

import time
from dataclasses import dataclass, field
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
    deterministic = _deterministic_progress(goal=goal, pre=pre, post=post, expected_state_change=expected_state_change)
    changed_keys = sorted(
        key
        for key in set(pre.keys()) | set(post.keys())
        if pre.get(key) != post.get(key)
    )
    advanced = bool(deterministic["advanced"] or changed_keys)
    status = deterministic["status"] if deterministic["advanced"] else ("advanced" if advanced else "no_observed_progress")
    return {
        "version": 1,
        "goal": str(goal or "").strip(),
        "stages": ["pre", "action", "settle", "post"],
        "candidateId": (candidate or {}).get("candidateId"),
        "actionType": (action or {}).get("actionType"),
        "expectedStateChange": expected_state_change,
        "changedKeys": changed_keys,
        "status": status,
        "nextStep": "continue" if advanced else "try_next_candidate_or_ask_human",
        "deterministicEvidence": deterministic,
        "policy": "do_not_repeat_unverified_clicks",
    }


@dataclass(slots=True)
class ShortSequenceAttempt:
    candidateId: str | None
    candidate: dict[str, Any]
    action: dict[str, Any]
    preState: dict[str, Any]
    postState: dict[str, Any]
    verification: dict[str, Any]
    status: str
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidateId": self.candidateId,
            "candidate": dict(self.candidate or {}),
            "action": dict(self.action or {}),
            "preState": dict(self.preState or {}),
            "postState": dict(self.postState or {}),
            "verification": dict(self.verification or {}),
            "status": self.status,
            "error": self.error,
        }


@dataclass(slots=True)
class ShortSequenceResult:
    status: str
    attempts: list[ShortSequenceAttempt] = field(default_factory=list)
    selectedCandidateId: str | None = None
    reason: str | None = None
    recommendedNextAction: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "status": self.status,
            "attempts": [item.as_dict() for item in self.attempts],
            "selectedCandidateId": self.selectedCandidateId,
            "reason": self.reason,
            "recommendedNextAction": self.recommendedNextAction,
            "policy": "max_3_candidates_then_human_attention",
        }


class ShortSequenceVisualExecutor:
    """Execute bounded visual fallback attempts without allowing repeated blind clicks."""

    def __init__(
        self,
        *,
        observe,
        act,
        settle_seconds: float = 0.5,
        max_attempts: int = 3,
    ) -> None:
        self._observe = observe
        self._act = act
        self._settle_seconds = max(float(settle_seconds or 0.0), 0.0)
        self._max_attempts = max(1, min(int(max_attempts or 3), 3))

    def run(
        self,
        *,
        goal: str,
        candidates: list[dict[str, Any]],
        expected_state_change: str | None = None,
    ) -> ShortSequenceResult:
        attempts: list[ShortSequenceAttempt] = []
        for candidate in list(candidates or [])[: self._max_attempts]:
            candidate_payload = dict(candidate or {})
            candidate_id = str(candidate_payload.get("candidateId") or "").strip() or None
            pre_state = _safe_observe(self._observe)
            action_payload: dict[str, Any] = {
                "actionType": _action_type(candidate_payload),
                "candidateId": candidate_id,
                "point": candidate_payload.get("center") or candidate_payload.get("point"),
            }
            error: str | None = None
            try:
                result = self._act(candidate_payload)
                if isinstance(result, dict):
                    action_payload.update(result)
            except Exception as exc:
                error = str(exc)
                action_payload["ok"] = False
                action_payload["error"] = error
            if self._settle_seconds:
                time.sleep(self._settle_seconds)
            post_state = _safe_observe(self._observe)
            verification = build_short_sequence_verification(
                goal=goal,
                candidate=candidate_payload,
                pre_state=pre_state,
                action=action_payload,
                post_state=post_state,
                expected_state_change=expected_state_change,
            )
            status = "succeeded" if verification.get("nextStep") == "continue" and not error else "failed"
            attempt = ShortSequenceAttempt(
                candidateId=candidate_id,
                candidate=candidate_payload,
                action=action_payload,
                preState=pre_state,
                postState=post_state,
                verification=verification,
                status=status,
                error=error,
            )
            attempts.append(attempt)
            if status == "succeeded":
                return ShortSequenceResult(
                    status="succeeded",
                    attempts=attempts,
                    selectedCandidateId=candidate_id,
                    reason="post_action_state_progressed",
                )
        return ShortSequenceResult(
            status="needs_human_attention",
            attempts=attempts,
            reason="max_candidate_attempts_without_progress",
            recommendedNextAction="ask_user",
        )


def _safe_observe(observe) -> dict[str, Any]:
    try:
        value = observe()
    except Exception as exc:
        return {"observeError": str(exc)}
    return dict(value or {}) if isinstance(value, dict) else {"value": value}


def _action_type(candidate: dict[str, Any]) -> str:
    role = str(candidate.get("role") or "").lower()
    if role in {"textbox", "search_box", "input", "edit"}:
        return "focus_or_type"
    if role in {"checkbox", "radio", "toggle"}:
        return "toggle"
    return "click"


def _state_text(state: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("text", "visibleText", "ocrText", "title", "url", "status", "state"):
        value = state.get(key)
        if isinstance(value, str):
            parts.append(value)
    nested = state.get("verification")
    if isinstance(nested, dict):
        for key in ("text", "status", "state", "reason"):
            value = nested.get(key)
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts).lower()


def _deterministic_progress(
    *,
    goal: str,
    pre: dict[str, Any],
    post: dict[str, Any],
    expected_state_change: str | None,
) -> dict[str, Any]:
    if post.get("passed") is True or (isinstance(post.get("verification"), dict) and post["verification"].get("passed") is True):
        return {"advanced": True, "status": "verified", "reason": "explicit_post_verification_passed"}
    for key in ("screenHash", "treeHash", "ocrHash", "stateHash", "url", "title", "focusedElement"):
        if pre.get(key) != post.get(key) and post.get(key) is not None:
            return {"advanced": True, "status": "advanced", "reason": f"{key}_changed"}
    expected = str(expected_state_change or "").strip().lower()
    post_text = _state_text(post)
    pre_text = _state_text(pre)
    if expected:
        tokens = [token for token in expected.replace("/", " ").replace("_", " ").split() if len(token) >= 4]
        matched = [token for token in tokens if token in post_text and token not in pre_text]
        if matched:
            return {"advanced": True, "status": "semantic_progress", "reason": "expected_state_tokens_appeared", "tokens": matched[:8]}
    goal_text = str(goal or "").lower()
    if ("star" in goal_text or "星标" in goal_text) and ("starred" in post_text or "unstar" in post_text):
        return {"advanced": True, "status": "semantic_progress", "reason": "star_goal_state_visible"}
    return {"advanced": False, "status": "no_observed_progress", "reason": "no_deterministic_progress_signal"}
