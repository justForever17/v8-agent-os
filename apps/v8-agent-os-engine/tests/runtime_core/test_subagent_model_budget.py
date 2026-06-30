import pytest

from api.models import EngineConfig
from core.llm_factory import LLMFactory
from graph.agent_factories import (
    build_agent_node,
    build_reviewer_node,
    create_subagent_chat_model,
    subagent_model_kwargs,
)
from graph.supervisor_builder import build_supervisor_runtime_bundle


def test_subagent_model_budget_uses_configured_model_limit(monkeypatch):
    monkeypatch.setattr(
        "graph.agent_factories.llm_factory.get_model_max_output_tokens",
        lambda _model_id: 131072,
    )

    assert subagent_model_kwargs("provider::long-output-model") == {"max_tokens": 131072}


def test_subagent_model_budget_omits_unknown_limit(monkeypatch):
    monkeypatch.setattr(
        "graph.agent_factories.llm_factory.get_model_max_output_tokens",
        lambda _model_id: None,
    )

    assert subagent_model_kwargs("provider::unknown-output-model") == {}
    assert subagent_model_kwargs(None) == {}


def test_model_output_limit_falls_back_to_provider_catalog(monkeypatch):
    monkeypatch.setattr(
        LLMFactory,
        "_resolve_model_metadata",
        classmethod(
            lambda cls, _model_id: {
                "is_found": True,
                "provider_id": "demo",
                "model_id": "demo-model",
                "global_max_tokens": None,
                "model_record": {},
            }
        ),
    )
    monkeypatch.setattr(
        "core.model_provider_catalog.model_provider_catalog.get_provider",
        lambda _provider_id: {"id": "demo", "models": [{"id": "demo-model"}]},
    )
    monkeypatch.setattr(
        "core.model_provider_catalog.model_provider_catalog.normalize_model",
        lambda _provider, _model_id: {"maxTokens": 65536},
    )

    assert LLMFactory.get_model_max_output_tokens("demo::demo-model") == 65536


def test_model_output_limit_falls_back_to_capability_registry(monkeypatch):
    monkeypatch.setattr(
        LLMFactory,
        "_resolve_model_metadata",
        classmethod(
            lambda cls, _model_id: {
                "is_found": True,
                "provider_id": "custom",
                "model_id": "known-model",
                "global_max_tokens": None,
                "model_record": {},
            }
        ),
    )
    monkeypatch.setattr(
        "core.model_provider_catalog.model_provider_catalog.get_provider",
        lambda _provider_id: None,
    )
    monkeypatch.setattr(
        "core.model_capability_registry.model_capability_registry.find",
        lambda _model_id: {"maxOutputTokens": 98304},
    )

    assert LLMFactory.get_model_max_output_tokens("custom::known-model") == 98304


def test_create_subagent_chat_model_enforces_resolved_limit_and_role(monkeypatch):
    captured = {}
    sentinel = object()
    monkeypatch.setattr(
        "graph.agent_factories.llm_factory.get_model_max_output_tokens",
        lambda _model_id: 49152,
    )

    def _create(model_id, **kwargs):
        captured["model_id"] = model_id
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr("graph.agent_factories.llm_factory.create_chat_model", _create)

    result = create_subagent_chat_model(
        "demo::worker-model",
        role="reviewer:worker",
        streaming=False,
        timeout=180,
        max_tokens=1234,
    )

    assert result is sentinel
    assert captured == {
        "model_id": "demo::worker-model",
        "kwargs": {
            "_role": "reviewer:worker",
            "streaming": False,
            "timeout": 180,
            "max_tokens": 49152,
        },
    }


def test_explicit_agent_and_reviewer_initial_models_use_subagent_factory(monkeypatch):
    calls = []

    def _create(model_id, *, role, **kwargs):
        calls.append((model_id, role, kwargs))
        return object()

    monkeypatch.setattr("graph.agent_factories.create_subagent_chat_model", _create)

    build_agent_node(
        agent_id="worker",
        agent_data={"id": "worker", "capabilitySnapshot": {}},
        agent_name="Worker",
        agent_system_prompt="",
        agent_tool_selectors=[],
        agent_tool_mode="contextual_auto",
        all_mcp_tools=[],
        all_plugin_host_tools=[],
        filtered_native_tools=[],
        fetch_skill_instructions_tool=None,
        reflection_enabled=True,
        agent_model_id="demo::worker-model",
        default_agent_llm=object(),
        supervisor_model_id="demo::supervisor-model",
        robust_invoke=lambda *args, **kwargs: None,
        build_failure_command=lambda **kwargs: None,
        extract_task_context=lambda _state: {},
        resolve_todos=lambda value: value,
        sanitize_message_chain=lambda value: value,
        sanitize_response_tool_calls=lambda value: value,
    )
    build_reviewer_node(
        agent_id="worker",
        agent_name="Worker",
        max_reflections=2,
        agent_model_id="demo::reviewer-model",
        default_agent_llm=object(),
        supervisor_model_id="demo::supervisor-model",
        robust_invoke=lambda *args, **kwargs: None,
        build_failure_command=lambda **kwargs: None,
        sanitize_message_chain=lambda value: value,
    )

    assert calls == [
        (
            "demo::worker-model",
            "agent:worker",
            {"streaming": False, "timeout": 180},
        ),
        (
            "demo::reviewer-model",
            "reviewer:worker",
            {"streaming": False, "timeout": 180},
        ),
    ]


def test_reviewer_without_override_reuses_budgeted_default_agent_model(monkeypatch):
    default_agent_llm = object()
    monkeypatch.setattr(
        "graph.agent_factories.create_subagent_chat_model",
        lambda *args, **kwargs: pytest.fail("reviewer should reuse the budgeted default agent model"),
    )

    reviewer_node = build_reviewer_node(
        agent_id="worker",
        agent_name="Worker",
        max_reflections=2,
        agent_model_id=None,
        default_agent_llm=default_agent_llm,
        supervisor_model_id="demo::supervisor-model",
        robust_invoke=lambda *args, **kwargs: None,
        build_failure_command=lambda **kwargs: None,
        sanitize_message_chain=lambda value: value,
    )

    assert reviewer_node is not None


@pytest.mark.parametrize(
    ("config", "role_model", "default_role_model", "default_agent_model", "expected_model"),
    [
        (EngineConfig(), "shared-model", "shared-model", "shared-model", "shared-model"),
        (
            EngineConfig(provider="custom-provider", model_name="request-override-model"),
            "role-supervisor-model",
            "default-role-model",
            "configured-subagent-model",
            "request-override-model",
        ),
    ],
)
def test_default_agent_and_request_override_models_use_subagent_factory(
    monkeypatch,
    config,
    role_model,
    default_role_model,
    default_agent_model,
    expected_model,
):
    created = []
    default_agent_llm = object()
    monkeypatch.setattr(
        "graph.supervisor_builder.resolve_engine_config_for_role",
        lambda _role: {
            "resolution": {
                "resolvedModelId": role_model,
                "bindingState": "explicit",
            }
        },
    )
    monkeypatch.setattr("graph.supervisor_builder.storage.get_supervisor_config", lambda: {})
    monkeypatch.setattr("graph.supervisor_builder.storage.get_role_model_id", lambda _role: default_role_model)
    monkeypatch.setattr(
        "graph.supervisor_builder.storage.get_default_agent_model_id",
        lambda: default_agent_model,
    )
    monkeypatch.setattr("graph.supervisor_builder.storage.get_all_agents", lambda: [])
    monkeypatch.setattr("graph.supervisor_builder.llm_factory.create_chat_model", lambda *args, **kwargs: object())

    def _create_default(model_id, *, role, **kwargs):
        created.append((model_id, role, kwargs))
        return default_agent_llm

    monkeypatch.setattr("graph.supervisor_builder.create_subagent_chat_model", _create_default)
    monkeypatch.setattr("graph.supervisor_builder.extensions_runtime_service.get_mcp_tools", lambda: [])
    monkeypatch.setattr("graph.supervisor_builder.plugin_host_tool_registry.build_supervisor_tools", lambda: [])
    monkeypatch.setattr("graph.supervisor_builder.build_external_langchain_tools", lambda _tools: [])
    monkeypatch.setattr("graph.supervisor_builder.capability_registry.filter_direct_tools", lambda _tools: [])
    monkeypatch.setattr("graph.supervisor_builder.create_robust_invoke", lambda **kwargs: object())
    monkeypatch.setattr("graph.supervisor_builder.build_supervisor_toolset", lambda **kwargs: [])

    def _build_components(**kwargs):
        assert kwargs["default_agent_llm"] is default_agent_llm
        return {}

    monkeypatch.setattr("graph.supervisor_builder.build_specialist_agent_components", _build_components)

    build_supervisor_runtime_bundle(
        config=config,
        fetch_skill_instructions_tool=object(),
        build_failure_command=lambda **kwargs: None,
        extract_task_context=lambda _state: {},
        resolve_todos=lambda value: value,
        sanitize_message_chain=lambda value: value,
        sanitize_response_tool_calls=lambda value: value,
    )

    assert created == [
        (
            expected_model,
            "subagent",
            {"streaming": False, "timeout": 180},
        )
    ]
