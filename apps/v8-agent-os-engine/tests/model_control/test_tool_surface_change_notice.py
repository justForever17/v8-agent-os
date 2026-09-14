from __future__ import annotations

from copy import deepcopy
import json

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.llm_chat_adapter import V8ChatModelAdapter
from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel


def schema(name):
    return {"type": "function", "function": {"name": name, "description": name,
            "parameters": {"type": "object", "properties": {}}}}


def adapter(model=None, *, standard="openai", role="supervisor"):
    return V8ChatModelAdapter(model_id="fixture", provider_standard=standard, role=role,
        meta={"capabilities": {"supportsTools": True, "supportsStreaming": True}}, model_kwargs={}, builder=lambda: model)


@pytest.mark.parametrize("names,choice", [(["agent_broker", "delegation_broker", "runtime_broker", "write_native_file"], None),
                                          ([], None), (["agent_broker", "delegation_broker"], "agent_broker")])
def test_actual_sdk_transition_notice_matches_bound_surface_without_losing_history(names, choice):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        message = {"role": "assistant", "content": "Initial two tools only.",
            "reasoning_details": [{"type": "reasoning.text", "text": "public synthetic reasoning", "id": "fixture-thinking"}],
            "tool_calls": [{"id": "original-provider-id", "type": "function", "function": {
                "name": "agent_broker", "arguments": '{"mode":"list"}'}}]}
        return httpx.Response(200, json={"id": "fixture", "object": "chat.completion", "created": 0, "model": "fixture",
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": message}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = V8OpenAICompatibleChatModel(model="fixture", api_key="fixture-only", base_url="https://fixture.invalid/v1",
                                           http_client=client, max_retries=0)
        first = adapter(model).bind_tools([schema("agent_broker"), schema("delegation_broker")]).invoke([HumanMessage(content="Original user task.")])
        original = first.model_dump()
        first.additional_kwargs["v8_bound_tool_names"] = names  # Not the prior invocation receipt.
        history = [SystemMessage(content="Original system instruction."), HumanMessage(content="Original user task."), first,
                   ToolMessage(content="Public registry result.", tool_call_id=first.tool_calls[0]["id"], name="agent_broker")]
        snapshot = [deepcopy(message.model_dump()) for message in history]
        current = adapter(model).bind_tools([schema(name) for name in names], **({"tool_choice": choice} if choice else {}))
        current.invoke(history)
    wire = requests[-1]
    actual = sorted(tool["function"]["name"] for tool in wire.get("tools", []))
    notice = next(message["content"] for message in wire["messages"] if message["role"] == "system"
                  and "[Current tool availability;" in message["content"])
    assert "Current provided tools: " + (", ".join(actual) or "(none)") + "." in notice
    assert "does not grant permission" in notice
    carried = next(message for message in wire["messages"] if message["role"] == "assistant")
    assert carried["content"] == original["content"]
    assert carried["reasoning_details"] == original["additional_kwargs"]["reasoning_details"]
    assert carried["tool_calls"][0]["id"] == "original-provider-id"
    assert next(message for message in wire["messages"] if message["role"] == "tool")["tool_call_id"] == "original-provider-id"
    assert [message.model_dump() for message in history] == snapshot


@pytest.mark.parametrize("prior,role", [(None, "supervisor"), (["b", "a"], "supervisor"), (["a"], "direct_subagent")])
def test_unchanged_unknown_or_other_role_does_not_add_notice(prior, role):
    message = AIMessage(content="history", response_metadata={} if prior is None else {"v8_bound_tool_names": prior},
                        additional_kwargs={"v8_bound_tool_names": []})
    bound = adapter(role=role).bind_tools([schema("a"), schema("b")])
    assert len(bound._tool_surface_change_messages([message])) == 1


@pytest.mark.parametrize("standard", ["openai", "anthropic"])
def test_notice_normalization_is_repeatable_and_later_receipt_stops_notice(standard):
    bound = adapter(standard=standard).bind_tools([schema("a"), schema("b")])
    messages = [SystemMessage(content="System."), AIMessage(content="History.", response_metadata={"v8_bound_tool_names": ["a"]})]
    normalized = bound._normalize_messages_for_provider(messages)
    again = bound._normalize_messages_for_provider(normalized)
    assert [message.model_dump() for message in again] == [message.model_dump() for message in normalized]
    assert sum(str(message.content).count("[Current tool availability;") for message in again) == 1
    if standard == "anthropic":
        from langchain_anthropic.chat_models import _format_messages
        system, _ = _format_messages(again)
        assert "Current tool availability" in str(system)
    new_history = [*messages, AIMessage(content="Current result.", response_metadata={"v8_bound_tool_names": ["a", "b"]})]
    assert len(bound._tool_surface_change_messages(new_history)) == len(new_history)
