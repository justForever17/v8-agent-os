from __future__ import annotations

from copy import deepcopy
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from openai.types.chat import ChatCompletion

from core.llm_chat_adapter import V8ChatModelAdapter
from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel


def _adapter(protocol="openai.chat_completions"):
    return V8ChatModelAdapter(model_id="public-fixture", provider_standard="openai", role="supervisor",
        meta={"wire_protocol": protocol, "capabilities": {"supportsTools": True}},
        model_kwargs={}, builder=lambda: object())


@pytest.mark.parametrize("with_raw_shadow", [False, True])
@pytest.mark.parametrize("collision", ["duplicate", "canonical", "canonical_chain", "fallback_collision", "none"])
def test_chat_completion_id_projection_keeps_ambiguous_group_canonical(collision, with_raw_shadow):
    model = V8OpenAICompatibleChatModel(model="public-fixture", api_key="public-fixture-key", base_url="https://fixture.invalid/v1")
    provider_ids = ["provider-duplicate", "provider-duplicate", "provider-unique"] if collision in {"duplicate", "fallback_collision"} else ["provider-a", "provider-b", "provider-c"]
    sdk_response = ChatCompletion.model_validate({"id": "fixture", "object": "chat.completion", "created": 0,
        "model": "public-fixture", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": "public content", "reasoning_details": [{"type": "reasoning.text", "text": "public reasoning"}],
            "tool_calls": [{"id": provider_id, "type": "function", "function": {
                "name": f"fixture_{index}", "arguments": json.dumps({"value": f"argument {index}"})}}
                for index, provider_id in enumerate(provider_ids)]}}]})
    assistant = _adapter()._coerce_ai_message(model._create_chat_result(sdk_response).generations[0].message)
    canonical_ids = [call["id"] for call in assistant.tool_calls]
    assert len(set(canonical_ids)) == 3
    if with_raw_shadow:
        assistant.additional_kwargs["tool_calls"] = deepcopy(assistant.tool_calls)
    if collision.startswith("canonical") or collision == "fallback_collision":
        # This collision is only knowable after the native IDs become canonical.
        # Keep both representations aligned, as in a persisted native shadow.
        if collision == "fallback_collision":
            provider_ids[2] = canonical_ids[0]
        else:
            provider_ids[0] = canonical_ids[1]
            if collision == "canonical_chain":
                provider_ids[1] = canonical_ids[2]
        for calls in (assistant.tool_calls, assistant.additional_kwargs.get("tool_calls", [])):
            for call, provider_id in zip(calls, provider_ids):
                call["providerToolCallId"] = provider_id
    replies = [ToolMessage(content=f"result {index}", name=call["name"], tool_call_id=call["id"])
               for index, call in enumerate(assistant.tool_calls)]
    serde = JsonPlusSerializer()
    checkpoint = serde.dumps_typed([assistant, *replies])
    restored = serde.loads_typed(checkpoint)
    snapshot = deepcopy(restored)
    projected = _adapter().normalize_input_for_provider(restored)
    expected = (canonical_ids if collision in {"canonical_chain", "fallback_collision"}
                else [*canonical_ids[:2], provider_ids[2]] if collision != "none" else provider_ids)
    assert [call["id"] for call in projected[0].tool_calls] == expected
    if with_raw_shadow:
        assert [call["id"] for call in projected[0].additional_kwargs["tool_calls"]] == expected
    assert len(set(expected)) == 3
    assert [message.tool_call_id for message in projected[1:]] == expected
    assert [call["args"] for call in projected[0].tool_calls] == [call["args"] for call in assistant.tool_calls]
    assert projected[0].content == assistant.content
    assert projected[0].additional_kwargs["reasoning_details"] == assistant.additional_kwargs["reasoning_details"]
    assert _adapter().normalize_input_for_provider(projected) == projected
    assert restored == snapshot
    assert serde.dumps_typed(restored) == checkpoint
    wire = model._get_request_payload(projected)["messages"]
    assert [call["id"] for call in wire[0]["tool_calls"]] == expected
    assert [message["tool_call_id"] for message in wire[1:]] == expected


def test_chat_completion_reused_provider_id_in_separate_completed_turns_stays_exact():
    history = []
    for ordinal in range(2):
        canonical_id = f"call_v8_turn_{ordinal}"
        assistant = AIMessage(content="", tool_calls=[{"id": canonical_id, "name": "fixture", "args": {"ordinal": ordinal}}])
        assistant.tool_calls[0]["providerToolCallId"] = "provider-reused"
        history.extend([assistant, ToolMessage(content=f"result {ordinal}", tool_call_id=canonical_id)])
    projected = _adapter().normalize_input_for_provider(history)
    assert [projected[index].tool_calls[0]["id"] for index in (0, 2)] == ["provider-reused"] * 2
    assert [projected[index].tool_call_id for index in (1, 3)] == ["provider-reused"] * 2


def test_responses_native_function_call_and_output_keep_exact_provider_identity():
    canonical_id, provider_id = "call_v8_public", "call_provider_public"
    assistant = AIMessage(content=[{"type": "function_call", "id": "fc_public", "call_id": provider_id,
        "name": "fixture", "arguments": '{"value":1}'}],
        tool_calls=[{"id": canonical_id, "name": "fixture", "args": {"value": 1}}])
    assistant.tool_calls[0]["providerToolCallId"] = provider_id
    reply = ToolMessage(content="public result", tool_call_id=canonical_id)
    projected = _adapter("openai.responses").normalize_input_for_provider([assistant, reply])
    model = ChatOpenAI(model="public-fixture", api_key="public-fixture-key", use_responses_api=True)
    wire = model._get_request_payload(projected)["input"]
    assert [item["call_id"] for item in wire if item["type"] == "function_call"] == [provider_id]
    assert [item["call_id"] for item in wire if item["type"] == "function_call_output"] == [provider_id]
    assert assistant.tool_calls[0]["id"] == reply.tool_call_id == canonical_id
