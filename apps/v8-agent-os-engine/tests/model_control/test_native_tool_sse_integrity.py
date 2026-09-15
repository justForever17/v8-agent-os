from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.tools import StructuredTool

from core.llm_chat_adapter import V8ChatModelAdapter
from core.openai_compatible_chat_model import V8OpenAICompatibleChatModel
from core.llm_exceptions import V8LLMError


def _delegation_tool():
    def dispatch(tasks: list[dict], mode: str = "dispatch") -> str:
        """Record no side effect; the test only inspects the model response."""
        return f"{mode}:{len(tasks)}"

    return StructuredTool.from_function(dispatch, name="delegation_broker")


def _sse_response(arguments: str, *, omit_tail: bool = False):
    split_one = min(37, max(1, len(arguments) // 3))
    split_two = max(split_one + 1, len(arguments) - 19)
    fragments = [arguments[:split_one], arguments[split_one:split_two], arguments[split_two:]]
    if omit_tail:
        fragments = fragments[:2]

    def handler(_request: httpx.Request) -> httpx.Response:
        events = []
        for index, fragment in enumerate(fragments):
            function = {
                "arguments": fragment,
                **({"name": "delegation_broker"} if index == 0 else {}),
            }
            call = {
                "index": 0,
                "function": function,
                **({"id": "provider-call-1", "type": "function"} if index == 0 else {}),
            }
            events.append(
                "data: "
                + json.dumps(
                    {
                        "id": "fixture-completion",
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {
                                    **({"role": "assistant"} if index == 0 else {}),
                                    "tool_calls": [call],
                                },
                                "finish_reason": None,
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
        events.append(
            "data: "
            + json.dumps(
                {
                    "id": "fixture-completion",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "tool_calls"}
                    ],
                }
            )
            + "\n\n"
        )
        events.append("data: [DONE]\n\n")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content="".join(events).encode("utf-8"),
        )

    return handler


def _adapter(arguments: str, *, omit_tail: bool = False, asynchronous: bool = False):
    transport = httpx.MockTransport(_sse_response(arguments, omit_tail=omit_tail))
    client = (
        httpx.AsyncClient(transport=transport)
        if asynchronous
        else httpx.Client(transport=transport)
    )
    model = V8OpenAICompatibleChatModel(
        model="fixture-sse-model",
        api_key="fixture-only",
        base_url="https://fixture.invalid/v1",
        **({"http_async_client": client} if asynchronous else {"http_client": client}),
        max_retries=0,
    )
    adapter = V8ChatModelAdapter(
        model_id="fixture-sse-model",
        provider_standard="openai",
        role="supervisor",
        meta={
            "capabilities": {"supportsTools": True, "supportsStreaming": True},
        },
        model_kwargs={},
        builder=lambda: model,
    ).bind_tools([_delegation_tool()])
    return client, adapter


def _collect(adapter):
    chunks = list(adapter.stream([HumanMessage(content="dispatch the fixture")]))
    assert chunks
    combined = chunks[0]
    for chunk in chunks[1:]:
        combined = combined + chunk
    raw_fragments = [
        str(item.get("args") or "")
        for chunk in chunks
        for item in list(getattr(chunk, "tool_call_chunks", None) or [])
        if isinstance(item, dict) and item.get("args") is not None
    ]
    return combined, "".join(raw_fragments)


async def _collect_async(adapter):
    chunks = [chunk async for chunk in adapter.astream([HumanMessage(content="dispatch the fixture")])]
    assert chunks
    combined = chunks[0]
    for chunk in chunks[1:]:
        combined = combined + chunk
    raw_fragments = [
        str(item.get("args") or "")
        for chunk in chunks
        for item in list(getattr(chunk, "tool_call_chunks", None) or [])
        if isinstance(item, dict) and item.get("args") is not None
    ]
    return combined, "".join(raw_fragments)


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_real_openai_sse_fragments_preserve_long_delegation_arguments(asynchronous):
    arguments = json.dumps(
        {
            "mode": "dispatch",
            "tasks": [
                {
                    "taskBriefId": "fixture-task",
                    "targetAgentName": "Fixture Verifier",
                    "goal": "保留长字段、引号 \\\"和数组\\\"。" * 80,
                    "expectedOutputs": ["完整回执"],
                    "acceptanceContract": ["逐字节保留 SSE 参数后再由 schema 解析"],
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    client, adapter = _adapter(arguments, asynchronous=asynchronous)
    try:
        if asynchronous:
            response, raw = asyncio.run(_collect_async(adapter))
        else:
            response, raw = _collect(adapter)
    finally:
        if asynchronous:
            asyncio.run(client.aclose())
        else:
            client.close()

    assert raw == arguments
    assert response.invalid_tool_calls == []
    assert response.tool_calls == [
        {"name": "delegation_broker", "args": json.loads(arguments), "id": "provider-call-1", "type": "tool_call"}
    ]


@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
def test_real_openai_sse_missing_tail_with_tool_finish_stays_unexecutable(asynchronous):
    arguments = json.dumps(
        {
            "mode": "dispatch",
            "tasks": [
                {
                    "taskBriefId": "fixture-task",
                    "goal": "A long argument whose final JSON delimiters are intentionally omitted.",
                    "expectedOutputs": ["回执"],
                    "acceptanceContract": ["不应执行"],
                }
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # The fixture omits the final SSE fragment entirely; the first two
    # fragments are still delivered byte-for-byte.
    truncated = arguments[:-19]
    client, adapter = _adapter(arguments, omit_tail=True, asynchronous=asynchronous)
    try:
        with pytest.raises(V8LLMError) as failure:
            if asynchronous:
                asyncio.run(_collect_async(adapter))
            else:
                _collect(adapter)
    finally:
        if asynchronous:
            asyncio.run(client.aclose())
        else:
            client.close()

    assert failure.value.code == "model_output_incomplete"
    assert truncated not in str(failure.value)
