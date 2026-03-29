from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class ComputerUseStepBudget:
    time_budget_ms: int
    retry_budget: int
    vision_budget: int
    token_budget: int
    fallback_budget: int
    settle_budget_ms: int
    source: str = "default"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "timeBudgetMs": int(self.time_budget_ms),
            "retryBudget": int(self.retry_budget),
            "visionBudget": int(self.vision_budget),
            "tokenBudget": int(self.token_budget),
            "fallbackBudget": int(self.fallback_budget),
            "settleBudgetMs": int(self.settle_budget_ms),
            "source": self.source,
        }


@dataclass(slots=True)
class ComputerUseBudgetUsage:
    elapsed_ms: int
    attempts_used: int
    vision_calls_used: int
    token_usage: int
    fallbacks_used: int
    within_budget: bool
    exceeded: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "elapsedMs": int(self.elapsed_ms),
            "attemptsUsed": int(self.attempts_used),
            "visionCallsUsed": int(self.vision_calls_used),
            "tokenUsage": int(self.token_usage),
            "fallbacksUsed": int(self.fallbacks_used),
            "withinBudget": bool(self.within_budget),
            "exceeded": list(self.exceeded),
        }


_BASE_BUDGETS: Dict[str, Dict[str, int]] = {
    "open_app": {"time": 20000, "retry": 2, "vision": 2, "token": 14000, "fallback": 2, "settle": 8000},
    "focus_window": {"time": 16000, "retry": 2, "vision": 2, "token": 12000, "fallback": 2, "settle": 6000},
    "observe": {"time": 6000, "retry": 1, "vision": 1, "token": 6000, "fallback": 0, "settle": 1500},
    "find": {"time": 6000, "retry": 1, "vision": 1, "token": 6000, "fallback": 0, "settle": 1500},
    "click": {"time": 10000, "retry": 2, "vision": 2, "token": 10000, "fallback": 2, "settle": 4000},
    "double_click": {"time": 10000, "retry": 2, "vision": 2, "token": 10000, "fallback": 2, "settle": 4000},
    "right_click": {"time": 10000, "retry": 2, "vision": 2, "token": 10000, "fallback": 2, "settle": 4000},
    "hover": {"time": 8000, "retry": 1, "vision": 1, "token": 8000, "fallback": 1, "settle": 2000},
    "drag": {"time": 14000, "retry": 2, "vision": 2, "token": 12000, "fallback": 2, "settle": 5000},
    "scroll": {"time": 10000, "retry": 2, "vision": 1, "token": 8000, "fallback": 1, "settle": 3000},
    "page_scroll": {"time": 10000, "retry": 2, "vision": 1, "token": 8000, "fallback": 1, "settle": 3000},
    "scroll_list": {"time": 10000, "retry": 2, "vision": 1, "token": 8000, "fallback": 1, "settle": 3000},
    "type_text": {"time": 12000, "retry": 2, "vision": 2, "token": 12000, "fallback": 2, "settle": 4500},
    "find_and_type": {"time": 14000, "retry": 2, "vision": 2, "token": 12000, "fallback": 2, "settle": 4500},
    "hotkey": {"time": 8000, "retry": 2, "vision": 1, "token": 6000, "fallback": 1, "settle": 2500},
    "wait": {"time": 12000, "retry": 1, "vision": 1, "token": 6000, "fallback": 0, "settle": 6000},
    "wait_for_element": {"time": 12000, "retry": 1, "vision": 1, "token": 6000, "fallback": 0, "settle": 6000},
    "screenshot": {"time": 8000, "retry": 1, "vision": 0, "token": 0, "fallback": 0, "settle": 1000},
    "capture_screenshot": {"time": 8000, "retry": 1, "vision": 0, "token": 0, "fallback": 0, "settle": 1000},
}


def _int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except Exception:
        return default


def resolve_step_budget(
    *,
    action_type: str,
    action_payload: Dict[str, Any] | None = None,
    high_risk_action: bool = False,
    visual_guard_requested: bool = False,
) -> Dict[str, Any]:
    payload = dict(action_payload or {})
    base = dict(_BASE_BUDGETS.get(str(action_type or "").strip().lower(), _BASE_BUDGETS["click"]))
    source = "default"
    feedback_source = str(payload.get("_budget_feedback_source") or "").strip()
    if high_risk_action:
        base["time"] += 4000
        base["vision"] = max(base["vision"], 2)
        base["fallback"] += 1
    if visual_guard_requested:
        base["vision"] = max(base["vision"], 2)
    if bool(payload.get("window_typing")):
        base["time"] += 2000
    if list(payload.get("file_paths") or []) or list(payload.get("attachment_paths") or []) or payload.get("file_path"):
        base["time"] += 3000
        base["fallback"] += 1
    if payload.get("time_budget_ms") not in (None, ""):
        base["time"] = _int(payload.get("time_budget_ms"), base["time"])
        source = "explicit"
    if payload.get("retry_budget") not in (None, ""):
        base["retry"] = _int(payload.get("retry_budget"), base["retry"])
        source = "explicit"
    if payload.get("vision_budget") not in (None, ""):
        base["vision"] = _int(payload.get("vision_budget"), base["vision"])
        source = "explicit"
    if payload.get("token_budget") not in (None, ""):
        base["token"] = _int(payload.get("token_budget"), base["token"])
        source = "explicit"
    if payload.get("fallback_budget") not in (None, ""):
        base["fallback"] = _int(payload.get("fallback_budget"), base["fallback"])
        source = "explicit"
    if source == "explicit" and feedback_source:
        source = feedback_source
    return ComputerUseStepBudget(
        time_budget_ms=max(1000, base["time"]),
        retry_budget=max(1, base["retry"]),
        vision_budget=max(0, base["vision"]),
        token_budget=max(0, base["token"]),
        fallback_budget=max(0, base["fallback"]),
        settle_budget_ms=max(0, base["settle"]),
        source=source,
    ).as_dict()


def collect_budget_usage(
    *,
    budget: Dict[str, Any] | None,
    result_metadata: Dict[str, Any] | None,
    elapsed_ms: int,
    attempts_used: int,
) -> Dict[str, Any]:
    budget_payload = dict(budget or {})
    metadata = dict(result_metadata or {})
    vision_sources = [
        metadata.get("preActionVisualGuard"),
        metadata.get("visualGuard"),
        metadata.get("visualFallback"),
    ]
    vision_calls_used = sum(1 for item in vision_sources if isinstance(item, dict) and item)
    fallbacks_used = 0
    if dict(metadata.get("visualFallback") or {}):
        fallbacks_used += 1
    if dict(metadata.get("selectorFallback") or {}):
        fallbacks_used += 1
    if attempts_used > 1:
        fallbacks_used += attempts_used - 1
    token_usage = 0
    for item in vision_sources:
        if isinstance(item, dict):
            usage = dict(item.get("usage") or {})
            token_usage += _int(usage.get("totalTokens") or usage.get("total_tokens"), 0)
    exceeded: List[str] = []
    if elapsed_ms > _int(budget_payload.get("timeBudgetMs"), 0):
        exceeded.append("time")
    if attempts_used > _int(budget_payload.get("retryBudget"), 0):
        exceeded.append("retry")
    if vision_calls_used > _int(budget_payload.get("visionBudget"), 0):
        exceeded.append("vision")
    if token_usage > _int(budget_payload.get("tokenBudget"), 0):
        exceeded.append("token")
    if fallbacks_used > _int(budget_payload.get("fallbackBudget"), 0):
        exceeded.append("fallback")
    return ComputerUseBudgetUsage(
        elapsed_ms=elapsed_ms,
        attempts_used=attempts_used,
        vision_calls_used=vision_calls_used,
        token_usage=token_usage,
        fallbacks_used=fallbacks_used,
        within_budget=not exceeded,
        exceeded=exceeded,
    ).as_dict()


def build_budget_update_request(
    *,
    action_type: str,
    budget: Dict[str, Any] | None,
    usage: Dict[str, Any] | None,
    verification: Dict[str, Any] | None = None,
) -> Dict[str, Any] | None:
    budget_payload = dict(budget or {})
    usage_payload = dict(usage or {})
    exceeded = [str(item) for item in list(usage_payload.get("exceeded") or []) if str(item).strip()]
    if not exceeded:
        return None
    verification_payload = dict(verification or {})
    verification_level = str(verification_payload.get("level") or "").strip().lower()
    if verification_level == "verified" and "time" in exceeded and len(exceeded) == 1:
        return None
    return {
        "requested": True,
        "kind": "budget_update_request",
        "reason": f"动作 `{action_type}` 已超出预算：{', '.join(exceeded)}",
        "actionType": action_type,
        "budget": budget_payload,
        "usage": usage_payload,
        "verification": verification_payload,
    }
