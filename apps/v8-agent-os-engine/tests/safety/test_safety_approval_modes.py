from __future__ import annotations

import pytest

from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.tools.native import tool_governance
from erc.safety_guardian import SafetyDecision
from erc.runtime_context import bind_runtime_context
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


def _silence_safety_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_governance.safety_guardian, "log_decision_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool_governance.safety_guardian, "is_allowlisted", lambda decision: None)
    monkeypatch.setattr(tool_governance.safety_guardian, "build_allowlist_candidate", lambda decision: {})


def test_native_tool_safety_enforcement_manual_still_interrupts_low_risk_review(monkeypatch: pytest.MonkeyPatch) -> None:
    _silence_safety_ledger(monkeypatch)

    with bind_runtime_context(safety_approval_mode="manual"):
        with pytest.raises(ModelGovernanceInterventionRequired):
            tool_governance._enforce_safety_decision(
                _review("external_mutating_http"),
                tool_call_id="tool_call_manual",
                question="POST https://example.test/api",
            )


def test_native_tool_safety_enforcement_reduced_auto_approves_low_risk_review(monkeypatch: pytest.MonkeyPatch) -> None:
    _silence_safety_ledger(monkeypatch)

    with bind_runtime_context(safety_approval_mode="reduced"):
        allowed, reason = tool_governance._enforce_safety_decision(
            _review("external_mutating_http"),
            tool_call_id="tool_call_reduced",
            question="POST https://example.test/api",
        )

    assert allowed is True
    assert reason is None


def test_native_tool_safety_enforcement_minimal_keeps_hard_review_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    _silence_safety_ledger(monkeypatch)

    with bind_runtime_context(safety_approval_mode="minimal"):
        with pytest.raises(ModelGovernanceInterventionRequired):
            tool_governance._enforce_safety_decision(
                _review("protected_config_write", governance_target="v8_integrity"),
                tool_call_id="tool_call_minimal_hard",
                question="write ~/.v8-agent-os/config.json",
            )
