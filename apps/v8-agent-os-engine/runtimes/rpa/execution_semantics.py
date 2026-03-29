from __future__ import annotations

from typing import Any


_SCRIPT_ASSESSMENT_STATUS_ALIASES = {
    "trusted": "accepted",
    "blocked": "compile_blocked",
}

_OUTCOME_FAMILY_MAP = {
    "completed": "completed",
    "completed_via_computer_use_primary": "completed",
    "completed_with_fallback": "completed_with_fallback",
    "review_required": "review_required",
    "compile_blocked": "blocked",
    "blocked": "blocked",
    "failed": "failed",
    "fallback_failed": "failed",
}


def normalize_script_assessment_status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "review_required"
    return _SCRIPT_ASSESSMENT_STATUS_ALIASES.get(normalized, normalized)


def outcome_family_for_execution_state(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    return _OUTCOME_FAMILY_MAP.get(normalized)

