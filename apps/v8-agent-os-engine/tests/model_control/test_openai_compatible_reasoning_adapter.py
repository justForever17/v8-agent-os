from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from typing import Any
from types import SimpleNamespace

import pytest

from langchain_core.language_models.chat_models import generate_from_stream
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from core.provider_compatibility import install_provider_compatibility_patches


install_provider_compatibility_patches()

_MINIMAX_REASONING_SURFACE = {"mode": "provider_reasoning", "trust": "official", "requestStyle": "minimax_interleaved_thinking", "responseFields": ["content[inline_think]", "reasoning_details"]}


def _model(
    *,
    model: str = "MiniMax-M3",
    base_url: str = "https://api.minimax.io/v1",
    model_ref: str = "minimax-cn::MiniMax-M3",
    stream_mode: str = "cumulative",
) -> V8OpenAICompatibleChatModel:
    return V8OpenAICompatibleChatModel(
        model=model,
        api_key="test-key",
        base_url=base_url,
        v8_model_ref=model_ref,
        v8_reasoning_surface={"mode": "provider_reasoning", "trust": "adapter_verified", "responseFields": ["reasoning_details", "reasoning_content"], "streamMode": stream_mode},
    )


def _tool_call() -> dict[str, Any]:
    return {
        "id": "call-1",
        "type": "function",
        "function": {"name": "lookup", "arguments": "{}"},
    }


def _stream_chunks() -> list[dict[str, Any]]:
    return [
        {
            "id": "chatcmpl-1",
            "model": "MiniMax-M3",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_details": [{"type": "reasoning.text", "text": "Plan"}],
                        "reasoning_content": "Plan",
                        "reasoning": "generic-1",
                        "thinking_delta": "hidden-1",
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-1",
            "model": "MiniMax-M3",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "",
                        "reasoning_details": [{"type": "reasoning.text", "text": "Plan done"}],
                        "reasoning_content": "Plan done",
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-1",
            "model": "MiniMax-M3",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": "",
                        "reasoning_details": [{"type": "reasoning.text", "text": "Plan done"}],
                        "reasoning_content": "Plan done",
                        "reasoning": "generic-2",
                        "thinking_delta": "hidden-2",
                    },
                    "finish_reason": None,
                }
            ],
        },
        {
            "id": "chatcmpl-1",
            "model": "MiniMax-M3",
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "content": None,
                        "tool_calls": [{"index": 0, **_tool_call()}],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        },
    ]


class _SyncResponse:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = deepcopy(chunks)

    def __enter__(self) -> _SyncResponse:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def __iter__(self):
        return iter(self._chunks)


class _SyncClient:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    def create(self, **_kwargs: Any) -> _SyncResponse:
        return _SyncResponse(self._chunks)


class _AsyncResponse:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = iter(deepcopy(chunks))

    async def __aenter__(self) -> _AsyncResponse:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def __aiter__(self) -> _AsyncResponse:
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _AsyncClient:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    async def create(self, **_kwargs: Any) -> _AsyncResponse:
        return _AsyncResponse(self._chunks)


def test_non_stream_response_preserves_canonical_reasoning_fields() -> None:
    result = _model()._create_chat_result(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "done",
                        "reasoning_details": [{"type": "reasoning.text", "text": "thinking"}],
                        "reasoning_content": "thinking",
                        "reasoning": "generic reasoning",
                        "thinking_delta": "stream-only",
                    },
                    "finish_reason": "stop",
                }
            ]
        }
    )

    message = result.generations[0].message
    assert message.additional_kwargs["reasoning_details"][0]["text"] == "thinking"
    assert message.additional_kwargs["reasoning_content"] == "thinking"
    assert message.additional_kwargs["reasoning"] == "generic reasoning"
    assert message.additional_kwargs["thinking_delta"] == "stream-only"


def test_stream_delta_preserves_generic_reasoning_for_canonical_reader() -> None:
    generation = _model()._convert_chunk_to_generation_chunk(
        {
            "choices": [
                {
                    "delta": {
                        "role": "assistant",
                        "content": "",
                        "reasoning": "step",
                        "thinking_delta": "thought",
                    },
                    "finish_reason": None,
                }
            ]
        },
        AIMessageChunk,
        None,
    )

    assert generation is not None
    assert generation.message.additional_kwargs["reasoning"] == "step"
    assert generation.message.additional_kwargs["thinking_delta"] == "thought"


def test_generate_from_stream_deduplicates_cumulative_details_and_replays_exact_tool_continuation() -> None:
    model = _model()
    object.__setattr__(model, "client", _SyncClient(_stream_chunks()))

    result = generate_from_stream(model._stream([HumanMessage(content="use a tool")]))
    message = result.generations[0].message

    details = message.additional_kwargs["reasoning_details"]
    assert len(details) == 1
    assert details[0]["text"] == "Plan done"
    assert message.additional_kwargs["reasoning_content"] == "Plan done"
    assert message.tool_calls[0]["name"] == "lookup"

    payload = model._get_request_payload([message])
    wire_message = payload["messages"][0]
    assert wire_message["reasoning_details"] == [
        {"type": "reasoning.text", "text": "Plan done"}
    ]
    assert wire_message["reasoning_content"] == "Plan done"
    assert "reasoning" not in wire_message
    assert "thinking" not in wire_message
    assert "thinking_delta" not in wire_message


def test_reasoning_continuation_requires_tool_call_and_exact_model_provider_origin() -> None:
    source_model = _model()
    source = source_model._create_chat_result(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [_tool_call()],
                        "reasoning_details": [{"type": "reasoning.text", "text": "prior"}],
                        "thinking_delta": "must-not-leak",
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    ).generations[0].message

    same_origin = source_model._get_request_payload([source])["messages"][0]
    assert same_origin["reasoning_details"][0]["text"] == "prior"
    assert "thinking_delta" not in same_origin

    different_model = _model(
        model="MiniMax-M2.7",
        model_ref="minimax-cn::MiniMax-M2.7",
    )._get_request_payload([source])["messages"][0]
    different_provider = _model(base_url="https://other-provider.example/v1")._get_request_payload([source])["messages"][0]
    different_binding = _model(model_ref="minimax-global::MiniMax-M3")._get_request_payload([source])["messages"][0]
    assert "reasoning_details" not in different_model
    assert "reasoning_details" not in different_provider
    assert "reasoning_details" not in different_binding
    assert different_model["reasoning_content"] == ""
    assert different_provider["reasoning_content"] == ""
    assert different_binding["reasoning_content"] == ""

    ordinary_message = AIMessage(
        content="done",
        additional_kwargs=deepcopy(source.additional_kwargs),
    )
    ordinary_payload = source_model._get_request_payload([ordinary_message])["messages"][0]
    assert "reasoning_details" not in ordinary_payload
    assert "thinking_delta" not in ordinary_payload


def test_public_astream_events_emits_incremental_details_and_aggregates_once() -> None:
    model = _model()
    object.__setattr__(model, "async_client", _AsyncClient(_stream_chunks()))

    async def collect_events() -> list[dict[str, Any]]:
        return [
            event
            async for event in model.astream_events(
                [HumanMessage(content="use a tool")],
                version="v2",
            )
        ]

    events = asyncio.run(collect_events())
    chunks = [
        event["data"]["chunk"]
        for event in events
        if event["event"] == "on_chat_model_stream"
    ]
    detail_chunks = [
        chunk.additional_kwargs["reasoning_details"]
        for chunk in chunks
        if chunk.additional_kwargs.get("reasoning_details")
    ]
    assert [details[0]["text"] for details in detail_chunks] == ["Plan", " done"]
    assert [
        chunk.additional_kwargs["reasoning"]
        for chunk in chunks
        if chunk.additional_kwargs.get("reasoning")
    ] == ["generic-1", "generic-2"]

    combined = chunks[0]
    for chunk in chunks[1:]:
        combined = combined + chunk
    assert combined.additional_kwargs["reasoning_details"][0]["text"] == "Plan done"
    assert combined.additional_kwargs["reasoning_content"] == "Plan done"


@pytest.mark.parametrize("transport", ["nonstream", "sync", "async"])
@pytest.mark.parametrize("with_tool", [True, False])
def test_inline_reasoning_origin_survives_adapter_checkpoint_and_next_request(transport, with_tool) -> None:
    from openai.types.chat import ChatCompletion, ChatCompletionChunk
    from langchain_core.messages import ToolMessage, message_chunk_to_message
    from langgraph.graph.message import add_messages
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.response_normalizer import ensure_reasoning_content
    from graph.compat import sanitize_message_chain

    model = V8OpenAICompatibleChatModel(
        model="MiniMax-M3", api_key="test-key", base_url="https://api.minimax.io/v1",
        v8_model_ref="minimax-cn::MiniMax-M3", v8_reasoning_surface=_MINIMAX_REASONING_SURFACE,
    )
    wrapped = V8ChatModelAdapter(model_id="MiniMax-M3", provider_standard="openai", role="supervisor",
        meta={"api_standard": "openai", "model_ref": "minimax-cn::MiniMax-M3", "capabilities": {"supportsTools": True, "supportsStreaming": True}},
        model_kwargs={}, builder=lambda: model)
    # Native leading thought is private; a later code example is visible content.
    visible = "\nResult. Example: `<think>literal</think>`\n"
    content = "<think>public fixture analysis</think>" + visible
    if transport == "nonstream":
        raw = {"role": "assistant", "content": content}
        if with_tool:
            raw["tool_calls"] = [_tool_call()]
        sdk_result = ChatCompletion.model_validate({"id": "public-response", "object": "chat.completion", "created": 0,
            "model": "MiniMax-M3", "choices": [{"index": 0, "message": raw, "finish_reason": "tool_calls" if with_tool else "stop"}]})
        message = wrapped._coerce_ai_message(model._create_chat_result(sdk_result).generations[0].message)
    else:
        fragments = ["", "<thi", "nk>public fixture analysis</thi", "nk>" + visible]
        chunks = [ChatCompletionChunk.model_validate({"id": "public-response", "object": "chat.completion.chunk", "created": 0,
            "model": "MiniMax-M3", "choices": [{"index": 0, "delta": {"role": "assistant", "content": fragment}, "finish_reason": None}]}) for fragment in fragments]
        chunks.append(ChatCompletionChunk.model_validate({"id": "public-response", "object": "chat.completion.chunk", "created": 0,
            "model": "MiniMax-M3", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, **_tool_call()}]} if with_tool else {},
            "finish_reason": "tool_calls" if with_tool else "stop"}]}))
        object.__setattr__(model, "client", _SyncClient(chunks))
        object.__setattr__(model, "async_client", _AsyncClient(chunks))
        if transport == "async":
            async def collect():
                return [chunk async for chunk in wrapped.astream([HumanMessage(content="public fixture")])]
            emitted = asyncio.run(collect())
        else:
            emitted = list(wrapped.stream([HumanMessage(content="public fixture")]))
        combined = emitted[0]
        for chunk in emitted[1:]:
            combined += chunk
        message = message_chunk_to_message(combined)
    history = add_messages([], [HumanMessage(content="public fixture"), message])
    if with_tool:
        history = add_messages(history, [ToolMessage(content="public tool result", tool_call_id=message.tool_calls[0]["id"])])
    serde = JsonPlusSerializer()
    restored = serde.loads_typed(serde.dumps_typed(history))
    prepared = sanitize_message_chain([ensure_reasoning_content(item) for item in restored])
    wire = model._get_request_payload(prepared)["messages"][1]
    assert hashlib.sha256(wire["content"].encode()).digest() == hashlib.sha256(content.encode()).digest()
    for other in (_model(base_url="https://other-provider.example/v1"), _model(model_ref="another-binding::MiniMax-M3"), _model(model="another-model")):
        foreign = other._get_request_payload(prepared)["messages"][1]
        assert foreign["content"] == visible
        assert "reasoning_details" not in foreign
        assert not foreign.get("reasoning_content")
    changed_provider = _model()
    object.__setattr__(changed_provider, "callbacks", [SimpleNamespace(provider_id="different-provider")])
    assert changed_provider._get_request_payload(prepared)["messages"][1]["content"] == visible
    assert message.content == content


@pytest.mark.parametrize("surface", [{}, {"trust": "unknown", "requestStyle": "minimax_interleaved_thinking"}, {"trust": "official", "requestStyle": "different_protocol"}, _MINIMAX_REASONING_SURFACE])
@pytest.mark.parametrize("content", ["Ordinary <think>body text</think> remains.", "```xml\n<think>code example</think>\n```", "&lt;think&gt;escaped example&lt;/think&gt;"])
def test_inline_origin_filter_preserves_ordinary_body_and_code_examples(surface, content) -> None:
    model = V8OpenAICompatibleChatModel(model="ordinary-model", api_key="test-key", base_url="https://source.example/v1", v8_reasoning_surface=surface)
    message = model._create_chat_result({"choices": [{"message": {"role": "assistant", "content": content, "tool_calls": [_tool_call()]}}]}).generations[0].message
    assert _model()._get_request_payload([message])["messages"][0]["content"] == content


def test_unknown_model_leading_think_example_has_no_native_reasoning_authority() -> None:
    content = "<think>This is literal sample markup</think>"
    message = _model()._create_chat_result({"choices": [{"message": {"role": "assistant", "content": content}}]}).generations[0].message
    assert _model(base_url="https://other.example/v1")._get_request_payload([message])["messages"][0]["content"] == content


@pytest.mark.parametrize("stream_mode", ["delta", "cumulative"])
@pytest.mark.parametrize("indexed", [True, False])
@pytest.mark.parametrize("asynchronous", [True, False])
def test_reasoning_stream_contract_preserves_repeated_fragments_and_block_identity(stream_mode, indexed, asynchronous) -> None:
    from openai.types.chat import ChatCompletionChunk
    from langchain_core.messages import ToolMessage, message_chunk_to_message
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from core.llm_chat_adapter import V8ChatModelAdapter
    from core.response_normalizer import ensure_reasoning_content

    model = _model(stream_mode=stream_mode)
    wrapped = V8ChatModelAdapter(model_id="MiniMax-M3", provider_standard="openai", role="supervisor",
        meta={"api_standard": "openai", "model_ref": "minimax-cn::MiniMax-M3", "capabilities": {"supportsTools": True, "supportsStreaming": True}},
        model_kwargs={}, builder=lambda: model)
    text_parts = [(0, "A"), (1, "B"), (0, "A" if stream_mode == "delta" else "AA"), (1, "BC" if stream_mode == "delta" else "BBC"), (0, "tail" if stream_mode == "delta" else "AAtail")]
    if stream_mode == "cumulative":
        text_parts.append((0, "AAtail"))
    chunks = []
    for position, (block, part) in enumerate(text_parts):
        details = {"type": "reasoning.text", "id": f"block-{block}", "format": "native-v1", "text": part}
        if indexed:
            details["index"] = 4 if block == 0 else 9
        chunks.append(ChatCompletionChunk.model_validate({"id": "public-delta", "object": "chat.completion.chunk", "created": 0,
            "model": "MiniMax-M3", "choices": [{"index": 0, "delta": {"role": "assistant", "reasoning_details": [details],
            "reasoning_content": "A" if stream_mode == "delta" else "A" * (position + 1)}, "finish_reason": None}]}))
    chunks.append(ChatCompletionChunk.model_validate({"id": "public-delta", "object": "chat.completion.chunk", "created": 0,
        "model": "MiniMax-M3", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, **_tool_call()}]}, "finish_reason": "tool_calls"}]}))
    object.__setattr__(model, "client", _SyncClient(chunks))
    object.__setattr__(model, "async_client", _AsyncClient(chunks))
    if asynchronous:
        async def collect():
            return [chunk async for chunk in wrapped.astream([HumanMessage(content="fixture")])]
        emitted = asyncio.run(collect())
    else:
        emitted = list(wrapped.stream([HumanMessage(content="fixture")]))
    merged = emitted[0]
    for chunk in emitted[1:]:
        merged += chunk
    message = message_chunk_to_message(merged)
    expected = [{"type": "reasoning.text", "id": f"block-{block}", "format": "native-v1", "text": part,
        **({"index": 4 if block == 0 else 9} if indexed else {})} for block, part in [(0, "AAtail"), (1, "BBC")]]
    native_details = [{key: value for key, value in block.items() if key != "index" or not str(value).startswith("lc_v8_")} for block in message.additional_kwargs["reasoning_details"]]
    assert native_details == expected
    assert message.additional_kwargs["reasoning_content"] == "A" * len(text_parts)
    serde = JsonPlusSerializer()
    restored = serde.loads_typed(serde.dumps_typed(message))
    wire = model._get_request_payload([HumanMessage(content="fixture"), ensure_reasoning_content(restored), ToolMessage(content="done", tool_call_id=restored.tool_calls[0]["id"])])["messages"][1]
    assert wire["reasoning_details"] == expected
    assert wire["reasoning_content"] == "A" * len(text_parts)
    assert "reasoning_details" not in _model(base_url="https://other.example/v1")._get_request_payload([restored])["messages"][0]
