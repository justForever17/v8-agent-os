from __future__ import annotations

from typing import Any, Dict

from runtimes.computer_use.types import ComputerUseVerification


VERIFICATION_LEVELS = {"verified", "soft_verified", "executed_only", "review_required", "failed"}

_STATUS_TO_LEVEL = {
    "verified": "verified",
    "text_verified": "verified",
    "focus_verified": "soft_verified",
    "scroll_verified": "verified",
    "window_verified": "verified",
    "soft_verified_target_only": "soft_verified",
    "target_verified": "soft_verified",
    "coordinate_click_executed": "executed_only",
    "coordinate_text_executed": "executed_only",
    "coordinate_file_paste_executed": "executed_only",
    "review_required": "review_required",
    "review_required_dynamic_input": "review_required",
    "review_required_unconfirmed_input": "review_required",
    "high_risk_visual_confirmation_required": "review_required",
    "high_risk_pre_action_confirmation_required": "review_required",
    "visual_guard_unconfirmed": "review_required",
    "failed": "failed",
    "text_mismatch": "failed",
    "target_unresolved": "failed",
    "window_unresolved": "failed",
    "scroll_no_change": "failed",
    "blocked_non_editable_target": "failed",
}


def infer_verification_level(*, passed: Any, status: str | None = None, level: str | None = None) -> str:
    normalized_level = str(level or "").strip().lower()
    if normalized_level in VERIFICATION_LEVELS:
        return normalized_level
    normalized_status = str(status or "").strip().lower()
    if normalized_status in _STATUS_TO_LEVEL:
        return _STATUS_TO_LEVEL[normalized_status]
    if passed is True:
        return "soft_verified" if normalized_status in {"skipped", "unknown"} else "verified"
    if passed is False:
        return "failed"
    return "review_required"


def normalize_verification_payload(
    verification: Dict[str, Any] | ComputerUseVerification | None,
) -> ComputerUseVerification:
    if isinstance(verification, ComputerUseVerification):
        level = infer_verification_level(
            passed=verification.passed,
            status=verification.status,
            level=verification.level,
        )
        return ComputerUseVerification(
            passed=bool(verification.passed),
            status=str(verification.status or "unknown"),
            reason=str(verification.reason or "未提供验证结果。"),
            details=dict(verification.details or {}),
            level=level,
        )
    if not isinstance(verification, dict):
        return ComputerUseVerification(
            passed=True,
            status="skipped",
            reason="未执行额外验证。",
            level="soft_verified",
        )
    status = str(verification.get("status") or "unknown")
    passed = verification.get("passed")
    level = infer_verification_level(
        passed=passed,
        status=status,
        level=verification.get("level"),
    )
    return ComputerUseVerification(
        passed=bool(passed),
        status=status,
        reason=str(verification.get("reason") or "未提供验证结果。"),
        details=dict(verification.get("details") or {}),
        level=level,
    )
