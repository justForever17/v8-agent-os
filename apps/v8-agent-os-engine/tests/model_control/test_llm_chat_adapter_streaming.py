from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.tools import StructuredTool

from core.llm_chat_adapter import V8ChatModelAdapter
from core.prompt_cache_gateway import PreparedPromptCacheRequest, prompt_cache_gateway


class _ThreeChunkModel:
    async def astream(self, _messages, *, config=None, **_kwargs):
        assert config == {
            "metadata": {"v8_model_scope": "runtime_internal"},
            "tags": ["v8:provider-internal"],
        }
        for text in ("a", "b", "c"):
            yield AIMessageChunk(content=text)


def test_public_astream_events_emits_model_identity_only_once(monkeypatch) -> None:
    native = _ThreeChunkModel()
    adapter = V8ChatModelAdapter(
        model_id="MiniMax-M3",
        provider_standard="openai",
        role="agent:worker",
        meta={
            "api_standard": "openai",
            "model_ref": "minimax-cn::MiniMax-M3",
        },
        model_kwargs={},
        builder=lambda: native,
    )

    monkeypatch.setattr(
        prompt_cache_gateway,
        "prepare_request",
        lambda **kwargs: PreparedPromptCacheRequest(
            messages=list(kwargs["messages"]),
            kwargs={},
            diagnostics={"cacheKey": "stream-probe"},
        ),
    )

    async def collect_events():
        return [
            event
            async for event in adapter.astream_events(
                [HumanMessage(content="stream")],
                version="v2",
            )
        ]

    events = asyncio.run(collect_events())
    chunks = [
        event["data"]["chunk"]
        for event in events
        if event["event"] == "on_chat_model_stream"
        and event["name"] == "V8ChatModelAdapter"
    ]

    assert len(chunks) >= 3
    assert sum("v8_model_ref" in chunk.response_metadata for chunk in chunks) == 1
    assert sum("v8_prompt_cache" in chunk.response_metadata for chunk in chunks) == 1

    combined = chunks[0]
    for chunk in chunks[1:]:
        combined = combined + chunk

    assert combined.text == "abc"
    assert combined.response_metadata["v8_model_id"] == "MiniMax-M3"
    assert combined.response_metadata["v8_model_ref"] == "minimax-cn::MiniMax-M3"
    assert combined.response_metadata["v8_provider_adapter"] == "openai-compatible"
    assert combined.response_metadata["v8_prompt_cache"] == {"cacheKey": "stream-probe"}


def test_specific_required_tool_choice_limits_provider_phase_to_that_tool():
    def read_file(path: str) -> str:
        return path

    def write_file(path: str, content: str) -> str:
        return path + content

    read_tool = StructuredTool.from_function(
        read_file,
        name="read_native_file",
        description="Read one file.",
    )
    write_tool = StructuredTool.from_function(
        write_file,
        name="write_native_file",
        description="Write one file.",
    )
    adapter = V8ChatModelAdapter(
        model_id="worker",
        provider_standard="openai",
        role="agent:worker",
        meta={"api_standard": "openai", "model_ref": "provider::worker"},
        model_kwargs={},
        builder=lambda: object(),
    )

    bound = adapter.bind_tools(
        [read_tool, write_tool],
        tool_choice={"type": "function", "function": {"name": "write_native_file"}},
    )

    assert bound._runtime_bound_tools() == [write_tool]
    prompt = bound._tool_prompt_messages([HumanMessage(content="write")])[0].content
    assert '"name": "write_native_file"' in prompt
    assert '"name": "read_native_file"' not in prompt
