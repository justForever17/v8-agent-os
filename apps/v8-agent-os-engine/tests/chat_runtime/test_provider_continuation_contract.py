from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from api.models import ChatMessage
from core.context_orchestrator import ContextOrchestrator
from core.openai_codex_runtime import _build_messages
from core.provider_continuation import (
    PRIVATE_PROVIDER_CONTINUATION_KEY,
    build_replay_ai_message_payload,
    extract_provider_continuation,
    merge_provider_continuations,
    provider_continuation_matches_target,
    strip_private_provider_continuation,
)
from core.response_normalizer import extract_text_and_reasoning
from erc.chat_canonical_transcript import format_canonical_message
from erc.checkpoint_security import StrictCheckpointSerializer
from graph.compat import sanitize_message_chain
from runtimes.chat.runtime import (
    ChatStreamState,
    _apply_provider_continuation_event,
    _assistant_export_requires_persistence,
)


def test_openai_encrypted_reasoning_is_private_but_replayable():
    message = AIMessage(
        content=[
            {
                "type": "reasoning",
                "id": "reasoning_1",
                "summary": [{"type": "summary_text", "text": "检查输入。"}],
                "encrypted_content": "opaque-openai-continuation",
            },
            {"type": "text", "text": "已完成。"},
        ],
        response_metadata={"v8_provider_standard": "openai", "id": "resp_1"},
    )

    continuation = extract_provider_continuation(message)
    assert continuation["providerStandard"] == "openai"
    assert continuation["contentBlocks"][0]["encrypted_content"] == "opaque-openai-continuation"
    content, additional_kwargs, response_metadata = build_replay_ai_message_payload("已完成。", continuation)
    assert content[0]["encrypted_content"] == "opaque-openai-continuation"
    assert response_metadata["id"] == "resp_1"
    assert extract_text_and_reasoning(message) == ("已完成。", "检查输入。")
    assert "opaque-openai-continuation" not in str(strip_private_provider_continuation({PRIVATE_PROVIDER_CONTINUATION_KEY: continuation}))
    assert additional_kwargs == {}


def test_anthropic_signed_and_redacted_thinking_blocks_are_preserved():
    message = AIMessage(
        content=[
            {"type": "thinking", "thinking": "内部摘要", "signature": "sig_1"},
            {"type": "redacted_thinking", "data": "redacted_1"},
            {"type": "text", "text": "结果"},
        ],
        response_metadata={"v8_provider_standard": "anthropic"},
    )

    continuation = extract_provider_continuation(message)
    assert continuation["providerStandard"] == "anthropic"
    assert {item["type"] for item in continuation["contentBlocks"]} == {"thinking", "redacted_thinking"}
    replay, _, _ = build_replay_ai_message_payload("结果", continuation)
    assert replay[0]["signature"] == "sig_1"
    assert replay[1]["data"] == "redacted_1"


def test_streamed_anthropic_signature_fragments_collapse_by_block_index():
    partial = {
        "schemaVersion": 1,
        "providerStandard": "anthropic",
        "contentBlocks": [{"type": "thinking", "index": 0, "signature": "sig-part"}],
        "additionalKwargs": {},
        "kinds": ["thinking"],
    }
    completed = {
        "schemaVersion": 1,
        "providerStandard": "anthropic",
        "contentBlocks": [
            {"type": "thinking", "index": 0, "thinking": "完整思考", "signature": "sig-complete"}
        ],
        "additionalKwargs": {},
        "kinds": ["thinking"],
    }
    merged = merge_provider_continuations(partial, completed)
    assert len(merged["contentBlocks"]) == 1
    replay, _, _ = build_replay_ai_message_payload("结果", merged)
    assert replay[0] == {"type": "thinking", "thinking": "完整思考", "signature": "sig-complete"}


def test_gemini_part_and_function_call_signatures_are_replayed_without_text_leak():
    message = AIMessage(
        content=[{"type": "text", "text": "完成", "extras": {"signature": "part_sig"}}],
        additional_kwargs={
            "__gemini_function_call_thought_signatures__": {"call_1": "function_sig"}
        },
        response_metadata={"v8_provider_standard": "gemini"},
    )

    continuation = extract_provider_continuation(message)
    assert continuation["providerStandard"] == "gemini"
    assert continuation["additionalKwargs"]["__gemini_function_call_thought_signatures__"]["call_1"] == "function_sig"
    assert continuation["contentBlocks"][0]["extras"]["signature"] == "part_sig"
    replay, replay_kwargs, _ = build_replay_ai_message_payload("完成", continuation)
    assert replay[0]["extras"]["signature"] == "part_sig"
    assert replay_kwargs["__gemini_function_call_thought_signatures__"]["call_1"] == "function_sig"


def test_gemini_nested_thought_signature_is_preserved_exactly():
    message = AIMessage(
        content=[
            {
                "type": "function_call",
                "name": "lookup",
                "args": {"q": "v8"},
                "extras": {"thoughtSignature": "nested_sig"},
            }
        ],
        response_metadata={"v8_provider_standard": "gemini"},
    )

    continuation = extract_provider_continuation(message)
    assert continuation["wireProtocols"] == ["gemini.generate_content"]
    replay, _, _ = build_replay_ai_message_payload("", continuation)
    assert replay[0]["extras"]["thoughtSignature"] == "nested_sig"


def test_provider_continuation_never_crosses_provider_or_openai_wire_protocol():
    continuation = {
        "providerStandard": "openai",
        "wireProtocols": ["openai.responses"],
        "contentBlocks": [{"type": "reasoning", "encrypted_content": "opaque"}],
    }
    assert provider_continuation_matches_target(
        continuation,
        target_provider="openai",
        target_wire_protocol="openai.responses",
    )
    assert not provider_continuation_matches_target(
        continuation,
        target_provider="openai",
        target_wire_protocol="openai.chat_completions",
    )
    assert not provider_continuation_matches_target(
        continuation,
        target_provider="anthropic",
        target_wire_protocol="anthropic.messages",
    )
    assert provider_continuation_matches_target(
        continuation,
        target_provider="openai",
        target_provider_adapter="openai-codex-responses",
    )


def test_responses_input_replays_opaque_reasoning_before_visible_assistant_text():
    assistant = AIMessage(
        content=[
            {
                "type": "reasoning",
                "id": "reasoning_2",
                "encrypted_content": "opaque-2",
                "summary": [],
            },
            {"type": "text", "text": "answer"},
        ],
        response_metadata={"v8_provider_standard": "openai"},
    )
    _, input_items = _build_messages([assistant, HumanMessage(content="继续")])
    assert input_items[0]["type"] == "reasoning"
    assert input_items[0]["encrypted_content"] == "opaque-2"
    assert input_items[1]["role"] == "assistant"
    assert input_items[-1]["role"] == "user"


def test_context_compaction_keeps_latest_opaque_chain_and_omits_it_from_summary_text():
    old = HumanMessage(content="旧问题")
    active = AIMessage(
        content=[
            {"type": "reasoning", "encrypted_content": "do-not-render", "summary": []},
            {"type": "text", "text": "中间结果"},
        ]
    )
    current = HumanMessage(content="继续完成")
    messages = [old, active, current, HumanMessage(content="最后要求")]
    boundary = ContextOrchestrator._protect_latest_provider_continuation(messages, 2)
    assert boundary == 1
    assert "do-not-render" not in ContextOrchestrator._message_text(active)
    assert ContextOrchestrator._message_text(active) == "中间结果"


def test_tool_pair_sanitizer_does_not_drop_provider_response_metadata():
    message = AIMessage(
        content=[{"type": "reasoning", "encrypted_content": "opaque", "summary": []}],
        tool_calls=[{"id": "orphan", "name": "tool", "args": {}}],
        response_metadata={"v8_provider_standard": "openai"},
    )
    sanitized = sanitize_message_chain([message])
    assert len(sanitized) == 1
    assert sanitized[0].response_metadata["v8_provider_standard"] == "openai"
    assert sanitized[0].content[0]["encrypted_content"] == "opaque"


def test_persisted_history_rehydrates_private_provider_continuation_without_api_projection():
    continuation = {
        "schemaVersion": 1,
        "providerStandard": "openai",
        "contentBlocks": [
            {"type": "reasoning", "id": "reasoning_db", "encrypted_content": "opaque-db"}
        ],
        "additionalKwargs": {},
        "kinds": ["reasoning"],
    }
    record = {
        "role": "assistant",
        "content": "继续执行。",
        "metadata": {PRIVATE_PROVIDER_CONTINUATION_KEY: continuation},
    }
    restored = ChatMessage.from_persisted_record(record)
    assert restored._provider_continuation == continuation
    assert PRIVATE_PROVIDER_CONTINUATION_KEY not in restored.model_dump()

    row = {
        "id": "message_db",
        "role": "assistant",
        "run_id": "run_db",
        "ordinal": 1,
        "state": "completed",
        "version": 1,
        "content_text": "继续执行。",
        "reasoning_text": "",
        "created_at": "2026-07-24T00:00:00+00:00",
        "metadata": {PRIVATE_PROVIDER_CONTINUATION_KEY: continuation},
        "nodes": [],
        "artifacts": [],
    }
    public = format_canonical_message(row)
    internal = format_canonical_message(row, include_private=True)
    assert PRIVATE_PROVIDER_CONTINUATION_KEY not in public["metadata"]
    assert internal["metadata"][PRIVATE_PROVIDER_CONTINUATION_KEY] == continuation


def test_think_only_provider_continuation_still_persists_for_next_turn_replay():
    continuation = {
        "schemaVersion": 1,
        "providerStandard": "openai",
        "contentBlocks": [
            {"type": "reasoning", "id": "reasoning_only", "encrypted_content": "opaque-only"}
        ],
        "additionalKwargs": {},
        "kinds": ["reasoning"],
    }
    assert _assistant_export_requires_persistence(
        {
            "content": "",
            "reasoning_content": None,
            "tool_calls": None,
            "metadata": {PRIVATE_PROVIDER_CONTINUATION_KEY: continuation},
        }
    )


def test_only_latest_model_call_continuation_is_carried_into_next_user_turn():
    state = ChatStreamState()
    _apply_provider_continuation_event(state, kind="on_chat_model_start", model_run_id="model-1")
    _apply_provider_continuation_event(
        state,
        kind="on_chat_model_end",
        model_run_id="model-1",
        candidate=AIMessage(
            content=[{"type": "reasoning", "id": "r1", "encrypted_content": "opaque-1"}]
        ),
    )
    _apply_provider_continuation_event(state, kind="on_chat_model_start", model_run_id="model-2")
    _apply_provider_continuation_event(
        state,
        kind="on_chat_model_end",
        model_run_id="model-2",
        candidate=AIMessage(
            content=[{"type": "reasoning", "id": "r2", "encrypted_content": "opaque-2"}]
        ),
    )
    rendered = str(state.provider_continuation)
    assert "opaque-2" in rendered
    assert "opaque-1" not in rendered


def test_persisted_history_rehydrates_tool_calls_alongside_private_continuation():
    continuation = {
        "schemaVersion": 1,
        "providerStandard": "openai",
        "contentBlocks": [{"type": "reasoning", "encrypted_content": "opaque-tool-loop"}],
        "additionalKwargs": {},
        "kinds": ["reasoning"],
    }
    restored = ChatMessage.from_persisted_record(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "toolCallId": "call_v8_test",
                    "toolName": "example_tool",
                    "args": {"value": 7},
                }
            ],
            "metadata": {PRIVATE_PROVIDER_CONTINUATION_KEY: continuation},
        }
    )
    assert restored.tool_calls is not None
    assert restored.tool_calls[0].function.name == "example_tool"
    assert restored.tool_calls[0].function.arguments == '{"value": 7}'
    assert restored._provider_continuation == continuation


def test_strict_checkpoint_round_trip_preserves_opaque_provider_blocks_exactly():
    serializer = StrictCheckpointSerializer()
    original = AIMessage(
        content=[
            {
                "type": "reasoning",
                "id": "reasoning_checkpoint",
                "summary": [],
                "encrypted_content": "opaque-checkpoint",
            },
            {"type": "text", "text": "可见结果"},
        ],
        response_metadata={"v8_provider_standard": "openai"},
    )
    serializer.assert_write_safe({"messages": [original]}, root="provider-continuation-test")
    restored = serializer.loads_typed(serializer.dumps_typed({"messages": [original]}))["messages"][0]
    assert isinstance(restored, AIMessage)
    assert restored.content == original.content
    assert restored.response_metadata == original.response_metadata
