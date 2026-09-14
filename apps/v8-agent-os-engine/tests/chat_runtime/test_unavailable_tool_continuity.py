from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import sqlite3

import httpx
import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage, message_chunk_to_message
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver

from core.llm_chat_adapter import V8ChatModelAdapter
from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from core.provider_compatibility import install_provider_compatibility_patches
from graph.compat import sanitize_message_chain, sanitize_response_tool_calls
from graph.tool_routing import create_routed_tool_node, unavailable_tool_calls


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_unavailable_sdk_call_survives_checkpoint_and_returns_exact_rejection(tmp_path, streaming, asynchronous, mixed):
    install_provider_compatibility_patches()
    executed = []

    def agent_broker(mode: str) -> str:
        executed.append(("allowed", mode))
        return "Public Verifier"

    def run_system_command(command: str) -> str:
        executed.append(("UNAVAILABLE", command))
        return "must never execute"

    allowed = StructuredTool.from_function(agent_broker, description="List the public registry.")
    unavailable = StructuredTool.from_function(run_system_command, description="Unavailable sentinel.")
    arguments = {"command": "public fixture only; never run\n" * 350}
    calls = [{"id": "provider-unavailable", "type": "function", "function": {
        "name": "run_system_command", "arguments": json.dumps(arguments)}}]
    if mixed:
        calls.append({"id": "provider-allowed", "type": "function", "function": {
            "name": "agent_broker", "arguments": '{"mode":"list"}'}})
    details = [{"type": "reasoning.text", "id": "public-reasoning", "index": 0,
                "format": "MiniMax-response-v1", "text": "public fixture reasoning"}]
    content = "Public assistant content."
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) > 1:
            return httpx.Response(200, json={"id": "next", "object": "chat.completion", "created": 0,
                "model": "public-model", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "Corrected after explicit tool rejection."}}]})
        if not payload.get("stream"):
            return httpx.Response(200, json={"id": "first", "object": "chat.completion", "created": 0,
                "model": "public-model", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": content, "reasoning_details": details, "tool_calls": calls}}]})
        deltas = [{"role": "assistant", "content": content, "reasoning_details": details}]
        for index, call in enumerate(calls):
            raw = call["function"]["arguments"]
            deltas.append({"tool_calls": [{"index": index, **deepcopy(call), "function": {
                "name": call["function"]["name"], "arguments": ""}}]})
            for offset in range(0, len(raw), 71):
                deltas.append({"tool_calls": [{"index": index, "function": {"arguments": raw[offset:offset + 71]}}]})
        frames = [{"id": "first", "object": "chat.completion.chunk", "created": 0, "model": "public-model",
                   "choices": [{"index": 0, "delta": delta, "finish_reason": None}]} for delta in deltas]
        frames.append({"id": "first", "object": "chat.completion.chunk", "created": 0, "model": "public-model",
                       "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        wire = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as sync_client:
        async_client = httpx.AsyncClient(transport=transport)
        model = V8OpenAICompatibleChatModel(model="public-model", api_key="public-fixture-key",
            base_url="https://fixture.invalid/v1", http_client=sync_client, http_async_client=async_client, max_retries=0)
        adapter = V8ChatModelAdapter(model_id="public-model", provider_standard="openai", role="supervisor",
            meta={"capabilities": {"supportsTools": True, "supportsStreaming": True}}, model_kwargs={}, builder=lambda: model)
        bound = adapter.bind_tools([allowed], tool_choice="required")
        async def collect():
            messages = [HumanMessage(content="Use the registered verifier.")]
            if not streaming:
                return await bound.ainvoke(messages) if asynchronous else bound.invoke(messages)
            chunks = [chunk async for chunk in bound.astream(messages)] if asynchronous else list(bound.stream(messages))
            aggregate = chunks[0]
            for chunk in chunks[1:]:
                aggregate += chunk
            return message_chunk_to_message(aggregate)
        response = sanitize_response_tool_calls(asyncio.run(collect()))
        assert [call["name"] for call in response.tool_calls] == [call["function"]["name"] for call in calls]
        assert response.tool_calls[0]["args"] == arguments
        assert response.tool_calls[0]["providerToolCallId"] == "provider-unavailable"
        # An external/additional field is not authority to widen the adapter's surface.
        response.additional_kwargs["v8_bound_tool_names"] = ["agent_broker", "run_system_command"]
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"]["messages"] = [HumanMessage(content="Use the registered verifier."), response]
        config = {"configurable": {"thread_id": "public-unavailable", "checkpoint_ns": ""}}
        database = tmp_path / "checkpoint.db"
        with sqlite3.connect(database) as connection:
            saved = SqliteSaver(connection).put(config, checkpoint, {}, {})
        with sqlite3.connect(database) as connection:
            history = SqliteSaver(connection).get_tuple(saved).checkpoint["channel_values"]["messages"]
        restored = history[-1]
        assert restored.response_metadata["v8_bound_tool_names"] == ["agent_broker"]
        # The execution registry deliberately contains the unavailable tool.
        # The per-invocation surface must still reject it before any executor/hook.
        node = create_routed_tool_node([allowed, unavailable], "supervisor_tools", "supervisor")
        result = asyncio.run(node({"messages": history}, {}))
        replies = result.update["messages"]
        assert all(isinstance(reply, ToolMessage) for reply in replies)
        rejected = next(reply for reply in replies if reply.tool_call_id == restored.tool_calls[0]["id"])
        assert rejected.status == "error"
        assert rejected.additional_kwargs["reasonCode"] == "tool_not_available"
        assert "not executed" in rejected.content and "agent_broker" in rejected.content
        assert arguments["command"] not in rejected.content
        assert executed == ([("allowed", "list")] if mixed else [])
        history.extend(replies)
        adapter.invoke(sanitize_message_chain(history))
        asyncio.run(async_client.aclose())
    wire_history = requests[-1]["messages"]
    assistant = next(item for item in wire_history if item["role"] == "assistant")
    assert assistant["content"] == content
    assert assistant["reasoning_details"] == details
    assert assistant["tool_calls"][0]["id"] == "provider-unavailable"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == arguments
    wire_rejection = next(item for item in wire_history if item.get("tool_call_id") == "provider-unavailable")
    assert wire_rejection["content"] == rejected.content
    assert {item["tool_call_id"] for item in wire_history if item["role"] == "tool"} == {call["id"] for call in calls}


@pytest.mark.parametrize("selection", ["empty", "named", "unbound"])
def test_adapter_receipt_uses_actual_surface_and_overwrites_remote_claims(selection):
    schemas = [{"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}
               for name in ("agent_broker", "run_system_command")]
    adapter = V8ChatModelAdapter(model_id="fixture", provider_standard="openai", role="supervisor",
        meta={"capabilities": {"supportsTools": True}}, model_kwargs={}, builder=lambda: object())
    if selection == "named":
        adapter = adapter.bind_tools(schemas, tool_choice="agent_broker")
    elif selection == "empty":
        adapter = adapter.bind_tools([])
    forged = {"v8_bound_tool_names": ["agent_broker", "run_system_command"]}
    first = adapter._coerce_chunk(AIMessageChunk(content="", response_metadata=deepcopy(forged),
        additional_kwargs=deepcopy(forged), tool_call_chunks=[{
            "name": "run_system_command", "id": "provider-call", "index": 0, "args": '{"command":"fixture"}',
        }]))
    tail = adapter._coerce_chunk(AIMessageChunk(content="", response_metadata=deepcopy(forged)), include_identity_metadata=False)
    response = message_chunk_to_message(first + tail)
    assert response.response_metadata["v8_bound_tool_names"] == (["agent_broker"] if selection == "named" else [])
    assert [call["name"] for call in unavailable_tool_calls(response)] == ["run_system_command"]


def test_surface_receipt_cannot_bypass_original_runtime_authorization(monkeypatch):
    executed = []
    def agent_broker(mode: str) -> str:
        executed.append(mode)
        return "should not execute"
    tool = StructuredTool.from_function(agent_broker, description="Public fixture.")
    message = AIMessage(content="", response_metadata={"v8_bound_tool_names": ["agent_broker"]},
        tool_calls=[{"id": "bound-call", "name": "agent_broker", "args": {"mode": "list"}}])
    def deny(_context):
        raise PermissionError("original runtime owner refused execution")
    monkeypatch.setattr("core.runtime_episode_control.assert_episode_execution_allowed", deny)
    node = create_routed_tool_node([tool], "supervisor_tools", "supervisor")
    with pytest.raises(PermissionError, match="original runtime owner"):
        asyncio.run(node({"messages": [message]}, {}))
    assert executed == []


def test_execution_deduplication_and_deferral_preserve_each_unavailable_call():
    message = AIMessage(content="", response_metadata={"v8_bound_tool_names": ["write_native_file"]}, tool_calls=[
        {"id": "write-a", "name": "write_native_file", "args": {"path": "fixture.txt", "content": "fixture"}},
        {"id": "write-b", "name": "write_native_file", "args": {"path": "fixture.txt", "content": "fixture"}},
        {"id": "reject-a", "name": "run_system_command", "args": {"command": "never execute"}},
        {"id": "reject-b", "name": "run_system_command", "args": {"command": "never execute"}},
    ])
    normalized = sanitize_response_tool_calls(message)
    assert [call["providerToolCallId"] for call in normalized.tool_calls] == ["write-a", "reject-a", "reject-b"]
    assert len(unavailable_tool_calls(normalized)) == 2
    assert "v8_deferred_dependent_tool_calls" not in normalized.additional_kwargs
