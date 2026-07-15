from __future__ import annotations

from typing import Any

import pytest

from core.model_control_plane import model_control_plane
from core.model_failover_service import ModelFailoverService
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.model_ref import make_model_ref


class FakeLLM:
    def __init__(self, *outcomes: Any):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.invocation_configs: list[dict[str, Any] | None] = []
        self.tool_bindings: list[dict[str, Any]] = []

    def bind_tools(self, _tools, **kwargs):
        self.tool_bindings.append(dict(kwargs))
        return self

    def effective_capability_matrix(self):
        return {
            "capabilityClass": "chat_tool_calling",
            "apiStandard": "openai",
            "supports_streaming": True,
            "supports_native_tools": True,
            "supports_prompt_emulated_tools": True,
            "supports_native_structured_output": True,
            "supports_prompt_fallback_structured_output": True,
            "runtime_ready": True,
        }

    def invoke(self, _messages, config=None):
        self.calls += 1
        self.invocation_configs.append(config)
        if not self.outcomes:
            raise AssertionError("unexpected fake LLM invocation")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _config(**governance_overrides: Any) -> dict[str, Any]:
    return model_control_plane.normalize_config(
        {
            "providers": {
                "p-openai-a": {
                    "provider": {"name": "OpenAI A", "api_standard": "openai", "api_key": "sk-test"},
                    "models": {
                        "primary": {
                            "type": "TEXT",
                            "capabilityClass": "chat_tool_calling",
                            "capabilities": {"supportsTools": True, "supportsStreaming": True},
                        }
                    },
                },
                "p-openai-b": {
                    "provider": {"name": "OpenAI B", "api_standard": "openai", "api_key": "sk-test"},
                    "models": {
                        "backup": {
                            "type": "TEXT",
                            "capabilityClass": "chat_tool_calling",
                            "capabilities": {"supportsTools": True, "supportsStreaming": True},
                        }
                    },
                },
                "p-gemini": {
                    "provider": {"name": "Gemini", "api_standard": "gemini", "api_key": "sk-test"},
                    "models": {
                        "gemini-backup": {
                            "type": "TEXT",
                            "capabilityClass": "chat_tool_calling",
                            "capabilities": {"supportsTools": True, "supportsStreaming": True},
                        }
                    },
                },
                "p-vision": {
                    "provider": {"name": "Vision", "api_standard": "openai", "api_key": "sk-test"},
                    "models": {
                        "vision-backup": {
                            "type": "MULTIMODAL",
                            "capabilityClass": "vision_multimodal",
                            "capabilities": {"vision": True, "supportsTools": True, "supportsStreaming": True},
                        }
                    },
                },
            },
            "roles": {
                "default": make_model_ref("p-openai-a", "primary"),
                "supervisor": make_model_ref("p-openai-a", "primary"),
            },
            "governance": {
                "allowSameCapabilityFailover": True,
                "strictCapabilityMatch": True,
                "maxLocalRetries": 1,
                "maxProviderSwitches": 5,
                **governance_overrides,
            },
        }
    )


def _patch_runtime_gates(monkeypatch: pytest.MonkeyPatch, service: ModelFailoverService) -> None:
    monkeypatch.setattr(service, "_runtime_ready_for_provider", lambda _provider_meta: True)
    monkeypatch.setattr(
        "core.model_failover_service.provider_health_service.build_provider_statuses",
        lambda _config, models, _roles: [
            {"providerId": model["providerId"], "status": "healthy", "circuitState": "closed"}
            for model in models
        ],
    )
    monkeypatch.setattr("core.model_failover_service.get_runtime_context", lambda: {})
    monkeypatch.setattr("core.model_failover_service.model_budget_service.enforce_or_raise", lambda **_kwargs: None)


def test_candidate_plan_keeps_same_capability_and_same_api_standard(monkeypatch: pytest.MonkeyPatch):
    service = ModelFailoverService()
    _patch_runtime_gates(monkeypatch, service)

    plan = service.build_candidate_plan(
        config=_config(),
        preferred_model_id=make_model_ref("p-openai-a", "primary"),
        role="supervisor",
        capability_requirements={"require_streaming": True},
    )

    assert [candidate.model_id for candidate in plan] == [
        make_model_ref("p-openai-a", "primary"),
        make_model_ref("p-openai-b", "backup"),
    ]
    assert {candidate.api_standard for candidate in plan} == {"openai"}


def test_transient_error_retries_then_failovers_to_same_format_candidate(monkeypatch: pytest.MonkeyPatch):
    service = ModelFailoverService()
    _patch_runtime_gates(monkeypatch, service)

    primary = FakeLLM(Exception("request timeout"), Exception("429 too many requests"))
    backup = FakeLLM("ok")
    fakes = {make_model_ref("p-openai-b", "backup"): backup}
    built: list[str] = []

    result = service.invoke_with_failover(
        config=_config(maxTotalAttempts=3, maxFailoverSeconds=30),
        base_llm_instance=primary,
        messages=[],
        tools=None,
        role="supervisor",
        preferred_model_id=make_model_ref("p-openai-a", "primary"),
        build_model=lambda model_id: built.append(model_id) or fakes[model_id],
    )

    assert result == "ok"
    assert primary.calls == 2
    assert backup.calls == 1
    assert built == [make_model_ref("p-openai-b", "backup")]


def test_invocation_config_is_forwarded_to_callback_events(monkeypatch: pytest.MonkeyPatch):
    service = ModelFailoverService()
    _patch_runtime_gates(monkeypatch, service)
    primary = FakeLLM("ok")
    invocation_config = {
        "metadata": {
            "v8_owner_runtime_kind": "subagent",
            "v8_owner_agent_id": "worker-one",
        },
        "tags": ["v8:runtime-owner"],
    }

    result = service.invoke_with_failover(
        config=_config(maxTotalAttempts=1, maxFailoverSeconds=30),
        base_llm_instance=primary,
        messages=[],
        tools=None,
        role="agent:worker-one",
        preferred_model_id=make_model_ref("p-openai-a", "primary"),
        build_model=lambda _model_id: FakeLLM("unused"),
        invocation_config=invocation_config,
    )

    assert result == "ok"
    assert primary.invocation_configs == [invocation_config]


def test_required_tool_choice_is_forwarded_to_every_failover_candidate(monkeypatch: pytest.MonkeyPatch):
    service = ModelFailoverService()
    _patch_runtime_gates(monkeypatch, service)
    primary = FakeLLM(Exception("request timeout"), Exception("request timeout"))
    backup = FakeLLM("ok")

    result = service.invoke_with_failover(
        config=_config(maxTotalAttempts=3, maxFailoverSeconds=30),
        base_llm_instance=primary,
        messages=[],
        tools=[object()],
        role="supervisor",
        preferred_model_id=make_model_ref("p-openai-a", "primary"),
        build_model=lambda _model_id: backup,
        tool_choice="required",
    )

    assert result == "ok"
    assert primary.tool_bindings == [{"tool_choice": "required"}]
    assert backup.tool_bindings == [{"tool_choice": "required"}]


def test_response_contract_violation_retries_with_next_candidate(monkeypatch: pytest.MonkeyPatch):
    service = ModelFailoverService()
    _patch_runtime_gates(monkeypatch, service)
    primary = FakeLLM({"tool_calls": []})
    backup = FakeLLM(
        {
            "tool_calls": [
                {
                    "name": "runtime_broker",
                    "args": {"mode": "route", "need": {"kind": "engineering"}},
                }
            ]
        }
    )

    result = service.invoke_with_failover(
        config=_config(maxLocalRetries=0, maxTotalAttempts=2, maxFailoverSeconds=30),
        base_llm_instance=primary,
        messages=[],
        tools=[object()],
        role="supervisor",
        preferred_model_id=make_model_ref("p-openai-a", "primary"),
        build_model=lambda _model_id: backup,
        tool_choice="runtime_broker",
        result_validator=lambda response: (
            None
            if list(response.get("tool_calls") or [])
            else "missing required runtime_broker call"
        ),
    )

    assert result == {
        "tool_calls": [
            {
                "name": "runtime_broker",
                "args": {"mode": "route", "need": {"kind": "engineering"}},
            }
        ]
    }
    assert primary.calls == 1
    assert backup.calls == 1
    assert primary.tool_bindings == [{"tool_choice": "runtime_broker"}]
    assert backup.tool_bindings == [{"tool_choice": "runtime_broker"}]


def test_non_transient_model_error_does_not_hidden_failover(monkeypatch: pytest.MonkeyPatch):
    service = ModelFailoverService()
    _patch_runtime_gates(monkeypatch, service)

    primary = FakeLLM(Exception("400 invalid request"))
    built: list[str] = []

    with pytest.raises(ModelGovernanceInterventionRequired) as exc_info:
        service.invoke_with_failover(
            config=_config(maxTotalAttempts=3, maxFailoverSeconds=30),
            base_llm_instance=primary,
            messages=[],
            tools=None,
            role="supervisor",
            preferred_model_id=make_model_ref("p-openai-a", "primary"),
            build_model=lambda model_id: built.append(model_id) or FakeLLM("should-not-run"),
        )

    assert primary.calls == 1
    assert built == []
    assert exc_info.value.details["attempts"][0]["code"] == "invalid_request"


def test_total_attempt_cap_stops_before_failover_candidate(monkeypatch: pytest.MonkeyPatch):
    service = ModelFailoverService()
    _patch_runtime_gates(monkeypatch, service)

    primary = FakeLLM(Exception("request timeout"), Exception("request timeout"))
    built: list[str] = []

    with pytest.raises(ModelGovernanceInterventionRequired) as exc_info:
        service.invoke_with_failover(
            config=_config(maxTotalAttempts=2, maxFailoverSeconds=30),
            base_llm_instance=primary,
            messages=[],
            tools=None,
            role="supervisor",
            preferred_model_id=make_model_ref("p-openai-a", "primary"),
            build_model=lambda model_id: built.append(model_id) or FakeLLM("should-not-run"),
        )

    assert primary.calls == 2
    assert built == []
    assert exc_info.value.details["capsExhaustedReason"] == "max_total_attempts_exhausted"
