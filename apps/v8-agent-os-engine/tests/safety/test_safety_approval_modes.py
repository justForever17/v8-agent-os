from __future__ import annotations

from erc.safety_guardian import SafetyDecision
from core.tools.native.tool_governance import (
    normalize_safety_approval_mode,
    should_auto_approve_safety_review,
)


def _review(
    risk_code: str,
    *,
    governance_target: str = "external_mutation",
    allow_override: bool = True,
    details: dict | None = None,
) -> SafetyDecision:
    return SafetyDecision(
        verdict="review",
        risk_code=risk_code,
        governance_target=governance_target,
        allow_override=allow_override,
        details=details or {},
    )


def test_safety_approval_mode_normalization_defaults_to_manual() -> None:
    assert normalize_safety_approval_mode("reduced") == "reduced"
    assert normalize_safety_approval_mode("minimal") == "minimal"
    assert normalize_safety_approval_mode("unknown") == "manual"
    assert normalize_safety_approval_mode(None) == "manual"


def test_manual_mode_keeps_all_reviews_interactive() -> None:
    decision = _review("external_mutating_http")

    assert should_auto_approve_safety_review(decision, mode="manual") is False


def test_reduced_mode_auto_approves_only_low_risk_reviews() -> None:
    assert should_auto_approve_safety_review(_review("external_mutating_http"), mode="reduced") is True
    assert should_auto_approve_safety_review(_review("review_host"), mode="reduced") is True
    assert should_auto_approve_safety_review(_review("review_command_pattern"), mode="reduced") is False
    assert should_auto_approve_safety_review(_review("trusted_financial_mutation_http"), mode="reduced") is False


def test_reduced_and_minimal_do_not_bypass_hard_protections() -> None:
    hard_reviews = [
        _review("download_execute_command", governance_target="system_integrity"),
        _review("privilege_elevation_review", governance_target="system_integrity"),
        _review("protected_config_write", governance_target="v8_integrity"),
        _review("credential_exfiltration_http", governance_target="private_data_exfiltration"),
        _review("windows_profile_registry_mutation", governance_target="system_integrity"),
        _review("external_mutating_http", allow_override=False),
    ]

    for decision in hard_reviews:
        assert should_auto_approve_safety_review(decision, mode="reduced") is False
        assert should_auto_approve_safety_review(decision, mode="minimal") is False


def test_minimal_mode_can_auto_approve_non_hard_reviews() -> None:
    assert should_auto_approve_safety_review(_review("review_command_pattern"), mode="minimal") is True
    assert should_auto_approve_safety_review(_review("external_mutating_http"), mode="minimal") is True
