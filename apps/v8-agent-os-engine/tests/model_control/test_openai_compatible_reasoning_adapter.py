from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from langchain_core.language_models.chat_models import generate_from_stream
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from core.provider_compatibility import install_provider_compatibility_patches


install_provider_compatibility_patches()


def _model(
    *,
    model: str = "MiniMax-M3",
    base_url: str = "https://api.minimax.io/v1",
    model_ref: str = "minimax-cn::MiniMax-M3",
) -> V8OpenAICompatibleChatModel:
    return V8OpenAICompatibleChatModel(
        model=model,
        api_key="test-key",
        base_url=base_url,
        v8_model_ref=model_ref,
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
