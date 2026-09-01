from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from api.models import EngineConfig
from core.llm_factory import LLMFactory
from graph.agent_factories import (
    _bounded_delegated_task_messages,
    _delegated_tool_call_dicts,
    _delegated_tool_loop_observation,
    _delegated_write_tool_observation,
    _required_write_tool_choice,
    _restore_required_artifact_tools,
    build_agent_node,
    build_reviewer_node,
    create_subagent_chat_model,
    subagent_model_kwargs,
)
from graph.supervisor_builder import _is_request_model_override, build_supervisor_runtime_bundle


def test_subagent_model_budget_uses_configured_model_limit(monkeypatch):
    monkeypatch.setattr(
        "graph.agent_factories.llm_factory.get_model_max_output_tokens",
        lambda _model_id: 131072,
    )

    assert subagent_model_kwargs("provider::long-output-model") == {"max_tokens": 131072}


def _owned_tool_call(name: str, call_id: str, args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args}],
        additional_kwargs={
            "v8_owner_agent_id": "worker",
            "v8_owner_subagent_id": "worker",
        },
    )


def test_delegated_context_window_keeps_instruction_and_tool_pair_boundary():
    instruction = HumanMessage(
        content="delegated",
        additional_kwargs={"v8_governance_type": "delegated_task_instruction"},
    )
    messages = [instruction]
    for index in range(20):
        messages.extend([
            _owned_tool_call("read_native_file", f"read-{index}", {"path": f"file-{index}.txt"}),
            ToolMessage(content=f"file-{index}", name="read_native_file", tool_call_id=f"read-{index}"),
        ])

    bounded = _bounded_delegated_task_messages(messages, {"goal": "Inspect files"})

    assert bounded[0] is instruction
    assert len(bounded) <= 28
    assert not isinstance(bounded[1], ToolMessage)


def test_delegated_tool_loop_blocks_current_repeat_but_allows_a_new_recovery_call():
    repeated_history = [
        _owned_tool_call("read_native_file", f"repeat-{index}", {"path": "same.txt"})
        for index in range(3)
    ]
    new_call = _owned_tool_call("write_native_file", "write-new", {"path": "result.txt", "content": "done"})
    recovered = _delegated_tool_loop_observation(
        [*repeated_history, new_call],
        agent_id="worker",
        current_message=new_call,
    )

    third_repeat = repeated_history[-1]
    blocked = _delegated_tool_loop_observation(
        repeated_history,
        agent_id="worker",
        current_message=third_repeat,
    )

    assert recovered["historicalExactRepeatCount"] == 3
    assert recovered["exactRepeatCount"] == 1
    assert recovered["blocked"] is False
    assert blocked["exactRepeatCount"] == 3
    assert blocked["blocked"] is True
    assert blocked["reason"] == "delegated_exact_tool_loop"


def test_provider_duplicate_tool_projection_counts_as_one_call():
    call = {"id": "write-1", "name": "write_native_file", "args": {"path": "result.txt", "content": "done"}}
    message = AIMessage(
        content="",
        tool_calls=[call],
        additional_kwargs={
            "v8_owner_agent_id": "worker",
            "tool_calls": [
                {
                    "id": "write-1",
                    "type": "function",
                    "function": {
                        "name": "write_native_file",
                        "arguments": '{"content":"done","path":"result.txt"}',
                    },
                }
            ],
        },
    )

    assert len(_delegated_tool_call_dicts(message)) == 1
    observation = _delegated_tool_loop_observation(
        [message],
        agent_id="worker",
        current_message=message,
    )
    assert observation["toolCallCount"] == 1
    assert observation["exactRepeatCount"] == 1
    assert observation["blocked"] is False


def test_ownerless_branch_write_receipt_still_stops_required_tool_choice():
    instruction = HumanMessage(
        content="delegated",
        additional_kwargs={"v8_governance_type": "delegated_task_instruction"},
    )
    call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "write-ownerless",
                "name": "write_native_file",
                "args": {"path": "result.txt", "content": "done"},
            }
        ],
    )
    result = ToolMessage(
        content="Successfully Created/Overwritten file: result.txt (4 chars written)",
        name="write_native_file",
        tool_call_id="write-ownerless",
    )

    observation = _delegated_write_tool_observation(
        [instruction, call, result],
        agent_id="worker",
    )

    assert observation["successful"] is True
    assert _required_write_tool_choice(
        task_brief={"taskBriefId": "repair-result", "writeRequired": True},
        messages=[instruction, call, result],
        agent_id="worker",
        write_observation=observation,
        write_tool_visible=True,
    ) is None


def test_required_write_choice_tightens_after_inspection_or_correction():
    task = {"taskBriefId": "engineering-implementation", "writeRequired": True}
    observation = {"successful": False, "toolCallCount": 0}
    assert _required_write_tool_choice(
        task_brief=task,
        messages=[],
        agent_id="worker",
        write_observation=observation,
        write_tool_visible=True,
    ) == "required"

    inspected = _owned_tool_call("read_native_file", "read-1", {"path": "input.txt"})
    assert _required_write_tool_choice(
        task_brief=task,
        messages=[inspected],
        agent_id="worker",
        write_observation={"successful": False, "toolCallCount": 1},
        write_tool_visible=True,
    ) == {"type": "function", "function": {"name": "write_native_file"}}

    correction = HumanMessage(
        content="write now",
        additional_kwargs={"v8_governance_type": "required_artifact_tool_correction"},
    )
    assert _required_write_tool_choice(
        task_brief=task,
        messages=[correction],
        agent_id="worker",
        write_observation=observation,
        write_tool_visible=True,
    ) == {"type": "function", "function": {"name": "write_native_file"}}


def test_contextual_prefilter_cannot_hide_required_writer(monkeypatch):
    read_tool = SimpleNamespace(name="read_native_file")
    write_tool = SimpleNamespace(name="write_native_file")
    task = {"taskBriefId": "engineering-verification", "writeRequired": True}
    monkeypatch.setattr("graph.agent_factories.engineering_tool_allowed", lambda *_args: True)

    restored = _restore_required_artifact_tools([read_tool], [read_tool, write_tool], task)

    assert [item.name for item in restored] == ["read_native_file", "write_native_file"]


def test_request_model_override_compares_provider_qualified_identity(monkeypatch):
    monkeypatch.setattr(
        "graph.supervisor_builder.model_control_plane.get_model_record",
        lambda model_id, *, provider_id=None: {
            "model_ref": f"{provider_id}::{model_id}",
        },
    )

    assert _is_request_model_override(
        EngineConfig(provider="provider-b", model_name="shared-model"),
        "provider-a::shared-model",
    )
    assert not _is_request_model_override(
        EngineConfig(provider="provider-a", model_name="shared-model"),
        "provider-a::shared-model",
    )

    assert _is_request_model_override(
        EngineConfig(provider="openai", model_name="shared-model"),
        "codex::shared-model",
    )

    monkeypatch.setattr(
        "graph.supervisor_builder.model_control_plane.get_model_record",
        lambda *_args, **_kwargs: None,
    )
    assert _is_request_model_override(
        EngineConfig(provider="provider-b", model_name="shared-model"),
        "provider-a::shared-model",
    )
    assert not _is_request_model_override(
        EngineConfig(provider="provider-a", model_name="shared-model"),
        "provider-a::shared-model",
    )


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
            "streaming": True,
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
            {"streaming": True, "timeout": 180},
        ),
        (
            "demo::reviewer-model",
            "reviewer:worker",
            {"streaming": True, "timeout": 180},
        ),
    ]


def test_write_required_subagent_forces_tool_choice_until_successful_write(monkeypatch):
    captured = []
    route_selected = []
    write_tool = SimpleNamespace(name="write_native_file", metadata={})
    task_brief = {
        "taskBriefId": "brief-write",
        "goal": "Create the bounded artifact.",
        "writeRequired": True,
        "writeSet": ["artifact.html"],
        "expectedOutputs": ["artifact.html"],
    }

    monkeypatch.setattr(
        "graph.agent_factories._resolved_workspace_binding_for_state",
        lambda _state: {"activeWorkspaceRoot": ".", "mainWorkspaceRoot": "."},
    )
    monkeypatch.setattr(
        "graph.agent_factories.build_engineering_kernel_context",
        lambda **_kwargs: ("", {}),
    )
    monkeypatch.setattr(
        "graph.agent_factories.detect_command_environment",
        lambda: {"commandLanguage": "PowerShell", "shellDialect": "powershell"},
    )
    monkeypatch.setattr("graph.agent_factories.render_host_alerts_line", lambda: "")
    monkeypatch.setattr("graph.agent_factories.render_host_load_line", lambda: "")
    monkeypatch.setattr("graph.agent_factories.utc_now_iso", lambda: "2026-08-21T00:00:00Z")
    monkeypatch.setattr(
        "graph.agent_factories._resolve_inherited_route_context",
        lambda *_args, **_kwargs: {
            "taskBrief": task_brief,
            "query": task_brief["goal"],
            "delegationDepth": 2,
        },
    )
    monkeypatch.setattr("graph.agent_factories._apply_task_tool_policy", lambda tools, _brief: list(tools))
    monkeypatch.setattr("graph.agent_factories._build_agent_system_bundle", lambda **_kwargs: {"content": "", "segments": []})
    monkeypatch.setattr("graph.agent_factories.ensure_reasoning_content", lambda message: message)
    monkeypatch.setattr("graph.agent_factories.context_orchestrator.prepare", lambda **kwargs: SimpleNamespace(messages=kwargs["messages"], audit={}))
    monkeypatch.setattr("graph.agent_factories.emit_context_prepared_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("graph.agent_factories.build_delegation_context", lambda **_kwargs: {})
    monkeypatch.setattr("graph.agent_factories.extensions_runtime_service.bind_execution_context", lambda **_kwargs: object())
    monkeypatch.setattr("graph.agent_factories.extensions_runtime_service.reset_execution_context", lambda *_args: None)
    monkeypatch.setattr(
        "graph.agent_factories.extensions_runtime_service.emit_route_selected",
        lambda **kwargs: route_selected.append(kwargs),
    )
    monkeypatch.setattr("graph.agent_factories.bind_runtime_context", lambda **_kwargs: nullcontext())

    def _invoke(_llm, _messages, _tools, **kwargs):
        captured.append(kwargs.get("tool_choice"))
        if len(captured) == 1:
            return AIMessage(content="I need to inspect the task first.")
        return AIMessage(content="The artifact is complete.")

    node = build_agent_node(
        agent_id="worker",
        agent_data={"id": "worker", "capabilitySnapshot": {}},
        agent_name="Worker",
        agent_system_prompt="",
        agent_tool_selectors=[],
        agent_tool_mode="explicit",
        all_mcp_tools=[],
        filtered_native_tools=[write_tool],
        fetch_skill_instructions_tool=None,
        reflection_enabled=False,
        agent_model_id=None,
        default_agent_llm=object(),
        supervisor_model_id="demo::supervisor-model",
        robust_invoke=_invoke,
        build_failure_command=lambda **kwargs: pytest.fail(f"unexpected failure: {kwargs}"),
        extract_task_context=lambda messages: messages,
        resolve_todos=lambda value: {"task_info": {}, "items": []},
        sanitize_message_chain=lambda messages: messages,
        sanitize_response_tool_calls=lambda response: response,
    )

    first = node(
        {
            "messages": [HumanMessage(content="delegated", additional_kwargs={"v8_governance_type": "delegated_task_instruction"})],
            "current_route_context": {"taskBrief": task_brief, "delegationDepth": 2},
            "parallel_branch": {"agentName": "Worker"},
            "workspace_path": ".",
        }
    )
    assert captured == ["required"]
    assert first.goto == "supervisor"

    successful_tool_call = AIMessage(
        content="",
        tool_calls=[{"id": "write-1", "name": "write_native_file", "args": {"path": "artifact.html"}}],
        additional_kwargs={
            "v8_owner_agent_id": "worker",
            "v8_owner_subagent_id": "worker",
        },
    )
    successful_tool_result = ToolMessage(
        content="Successfully Created/Overwritten file: artifact.html (10 chars written)",
        name="write_native_file",
        tool_call_id="write-1",
    )
    second = node(
        {
            "messages": [
                HumanMessage(content="delegated", additional_kwargs={"v8_governance_type": "delegated_task_instruction"}),
                successful_tool_call,
                successful_tool_result,
            ],
            "current_route_context": {"taskBrief": task_brief, "delegationDepth": 2},
            "parallel_branch": {"agentName": "Worker"},
            "workspace_path": ".",
        }
    )
    assert captured == ["required", None]
    assert len(route_selected) == 1
    assert second.goto == "supervisor"


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
    (
        "config",
        "role_model",
        "role_model_ref",
        "default_role_model",
        "default_agent_model",
        "expected_supervisor_model",
        "expected_agent_model",
    ),
    [
        (EngineConfig(), "shared-model", "", "shared-model", "shared-model", "shared-model", "shared-model"),
        (
            EngineConfig(provider="custom-provider", model_name="request-override-model"),
            "role-supervisor-model",
            "",
            "default-role-model",
            "configured-subagent-model",
            "request-override-model",
            "request-override-model",
        ),
        (
            EngineConfig(),
            "duplicate-model",
            "provider-a::duplicate-model",
            "provider-default::default-model",
            "provider-sub::subagent-model",
            "provider-a::duplicate-model",
            "provider-sub::subagent-model",
        ),
    ],
)
def test_default_agent_and_request_override_models_use_subagent_factory(
    monkeypatch,
    config,
    role_model,
    role_model_ref,
    default_role_model,
    default_agent_model,
    expected_supervisor_model,
    expected_agent_model,
):
    created = []
    default_agent_llm = object()
    monkeypatch.setattr(
        "graph.supervisor_builder.resolve_engine_config_for_role",
        lambda _role: {
            "engine_config": EngineConfig(provider="role-provider", model_name=role_model),
            "resolution": {
                "resolvedModelId": role_model,
                "resolvedModelRef": role_model_ref,
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
    supervisor_models = []

    def _create_supervisor(model_id, **kwargs):
        supervisor_models.append((model_id, kwargs))
        return object()

    monkeypatch.setattr("graph.supervisor_builder.llm_factory.create_chat_model", _create_supervisor)
    monkeypatch.setattr("graph.supervisor_builder.model_control_plane.get_model_record", lambda *_args, **_kwargs: None)

    def _create_default(model_id, *, role, **kwargs):
        created.append((model_id, role, kwargs))
        return default_agent_llm

    monkeypatch.setattr("graph.supervisor_builder.create_subagent_chat_model", _create_default)
    monkeypatch.setattr("graph.supervisor_builder.extensions_runtime_service.get_mcp_tools", lambda: [])
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
            expected_agent_model,
            "subagent",
            {"streaming": True, "timeout": 180},
        )
    ]
    assert supervisor_models == [(expected_supervisor_model, {"streaming": True, "timeout": 180})]
