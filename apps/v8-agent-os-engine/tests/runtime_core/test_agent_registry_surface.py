from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.tools.native import agent as registry
from erc.runtime_context import bind_runtime_context
from graph.tool_routing import create_routed_tool_node


@pytest.mark.parametrize("mode", ["list", "inspect", "validate"])
def test_registry_toolnode_supplies_selection_evidence_in_final_provider_request(monkeypatch, mode):
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
    from core.tools.native.delegation_surface import supervisor_delegation_broker

    name = "Fixture Verification Agent"
    description = "Checks command results and verifies evidence without modifying inputs."
    monkeypatch.setattr(registry.storage, "get_all_agents", lambda: [{
        "id": "fixture-checker", "name": name, "description": description, "model": "fixture-provider::model",
        "system_prompt": "PRIVATE PROMPT", "apiKey": "PRIVATE KEY",
        "capabilitySnapshot": {"specialistFamily": "engineering", "runtimeBindings": [
            {"runtimeKind": "engineering", "grantGroups": ["engineering.core"]},
        ]},
    }])
    monkeypatch.setattr(registry.storage, "get_default_agent_model_id", lambda: "fixture-default")
    call = {"id": "registry-observation", "name": "agent_broker", "args": {"mode": mode, "agentName": name}}
    node = create_routed_tool_node([registry.agent_broker], "supervisor_tools", "supervisor")
    with bind_runtime_context(actor_role="supervisor", agent_id="supervisor", runtime_kind="chat"):
        result = asyncio.run(node({"messages": [AIMessage(content="", tool_calls=[call])]}))
    visible = result.update["messages"][0]
    assert description in visible.content
    assert name in visible.content and "engineering.core" in visible.content
    assert "fixture-provider::model" in visible.content
    assert "tool_observation_detail(" not in visible.content
    assert "PRIVATE" not in visible.content
    if mode == "validate":
        assert "ready" in visible.content
    else:
        assert "not validated" in visible.content
    model = V8OpenAICompatibleChatModel(model="offline-fixture", api_key="test-only-not-a-credential")
    adapter = V8ChatModelAdapter(model_id="offline-fixture", provider_standard="openai", role="supervisor",
        meta={"capabilityClass": "chat_tool_calling", "capabilities": {"supportsTools": True}}, model_kwargs={},
        builder=lambda: model).bind_tools([registry.agent_broker, supervisor_delegation_broker], tool_choice="required")
    bound = adapter._get_runtime_model()
    payload = model._get_request_payload([HumanMessage(content="Choose the matching registered Agent."),
                                         AIMessage(content="", tool_calls=[call]), visible], **bound.kwargs)
    assert {tool["function"]["name"] for tool in payload["tools"]} == {"agent_broker", "delegation_broker"}
    assert payload["tool_choice"] == "required"
    actual_reply = payload["messages"][-1]["content"]
    assert description in actual_reply and "engineering.core" in actual_reply
    assert "tool_observation_detail(" not in actual_reply


def test_registry_projection_keeps_capabilities_and_explicit_unready_without_leaking_config():
    from core.tool_surface import apply_tool_surface_budget
    from langchain_core.messages import ToolMessage
    payload = {"ok": False, "mode": "validate", "status": "needs_model_configuration", "effectiveModelId": "",
               "summary": "Model binding is not ready.", "item": {"name": "Fixture Agent", "agentId": "fixture",
               "description": "Validates inputs", "family": "engineering", "modelId": "", "enabled": True,
               "runtimeBindings": [{"runtimeKind": "engineering", "grantGroups": ["engineering.core"], "apiKey": "SECRET-BINDING"}],
               "domainTags": ["validation"], "operationCapabilities": ["verify"], "artifactCapabilities": ["report"],
               "config": {"apiKey": "SECRET-CONFIG"}, "systemPrompt": "SECRET-PROMPT"}}
    result = apply_tool_surface_budget(ToolMessage(content=json.dumps(payload), name="agent_broker", tool_call_id="registry"),
                                      {"agentVisibleBudget": 2000})
    assert "needs_model_configuration" in result.content
    assert all(value in result.content for value in ("validation", "verify", "report", "engineering.core"))
    assert "SECRET" not in result.content and "apiKey" not in result.content
    assert "tool_observation_detail(" not in result.content
