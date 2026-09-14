from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from core.tools.native.delegation_surface import delegation_broker, supervisor_delegation_broker
from graph.compat import sanitize_message_chain
from graph.supervisor_turn import _delegation_dispatch_contract_error, _normalize_runtime_broker_response_arguments
from graph.tool_routing import create_routed_tool_node


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("form", ["invalid_string", "json_string", "wrapped_dict", "mixed_array", "wrong_nested_type", "null_nested_type"])
def test_wrong_delegation_types_reach_real_tool_repair_and_next_request_unchanged(form):
    goal = "Public goal: preserve every observation and boundary.\n" * 180
    task = {"taskBriefId": "public-task", "targetAgentName": "Public Verifier", "goal": goal,
            "expectedOutputs": ["public evidence"], "acceptanceContract": ["all observations preserved"],
            "readOnly": True, "writeRequired": False, "writeSet": []}
    tasks = {"invalid_string": json.dumps([task])[:-4], "json_string": json.dumps([task]),
             "wrapped_dict": {"tasks": [task]}, "mixed_array": [task, "wrong member"],
             "wrong_nested_type": [{**task, "expectedOutputs": "wrong string"}],
             "null_nested_type": [{**task, "executionLaneHint": None}]}[form]
    arguments = {"mode": "dispatch", "tasks": tasks}
    original_hash = _digest(arguments)
    model = V8OpenAICompatibleChatModel(model="public-model", api_key="test-key", base_url="https://fixture.invalid/v1")
    message = model._create_chat_result({"choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": "public fixture", "tool_calls": [{"id": "public-call", "type": "function",
        "function": {"name": "delegation_broker", "arguments": json.dumps(arguments)}}]}}]}).generations[0].message
    response = _normalize_runtime_broker_response_arguments(message)
    assert _digest(response.tool_calls[0]["args"]) == original_hash
    assert _delegation_dispatch_contract_error(response) is None
    executed = []

    def should_not_execute(**kwargs):
        executed.append(kwargs)
        return "invalid task reached executor"

    node = create_routed_tool_node([supervisor_delegation_broker], "supervisor_tools", "supervisor")
    with patch.object(delegation_broker, "func", should_not_execute):
        result = asyncio.run(node({"messages": [HumanMessage(content="public request"), response]}, {}))
    assert executed == []
    replies = [item for item in result.update["messages"] if isinstance(item, ToolMessage)]
    assert len(replies) == 1
    reply = replies[0]
    assert reply.status == "error"
    assert reply.tool_call_id == response.tool_calls[0]["id"]
    assert any(field.startswith("tasks") for field in reply.additional_kwargs["invalidFields"])
    assert "missing_tasks" not in reply.content
    assert goal not in reply.content
    assert "arrays stay arrays" in reply.content or '"tasks": [{' in reply.content
    assert "schema" in reply.content or "Local shape" in reply.content
    serde = JsonPlusSerializer()
    history = serde.loads_typed(serde.dumps_typed([HumanMessage(content="public request"), response, reply]))
    payload = model._get_request_payload(sanitize_message_chain(history))["messages"]
    assert _digest(json.loads(payload[1]["tool_calls"][0]["function"]["arguments"])) == original_hash
    assert payload[2]["content"] == reply.content
    assert payload[2]["tool_call_id"] == payload[1]["tool_calls"][0]["id"]
    assert _digest(response.tool_calls[0]["args"]) == original_hash


def test_typed_task_array_keeps_full_goal_and_contract():
    goal = "Public complete goal\n" * 500
    arguments = {"mode": "dispatch", "tasks": [{"taskBriefId": "public-task", "targetAgentName": "Public Verifier", "goal": goal,
        "expectedOutputs": ["fact"], "acceptanceContract": ["check"], "readOnly": True, "writeSet": []}]}
    original = deepcopy(arguments)
    from langchain_core.messages import AIMessage
    message = AIMessage(content="", tool_calls=[{"id": "public-call", "name": "delegation_broker", "args": arguments}])
    _normalize_runtime_broker_response_arguments(message)
    typed = supervisor_delegation_broker.args_schema.model_validate(message.tool_calls[0]["args"])
    assert message.tool_calls[0]["args"] == original
    assert typed.tasks[0]["goal"] == goal
    assert len(typed.tasks[0]["goal"]) == 10500
    assert hashlib.sha256(typed.tasks[0]["goal"].encode()).digest() == hashlib.sha256(goal.encode()).digest()
