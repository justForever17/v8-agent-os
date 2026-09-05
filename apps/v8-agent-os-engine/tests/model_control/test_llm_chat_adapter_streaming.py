from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.tools import StructuredTool

from core.llm_chat_adapter import V8ChatModelAdapter
from core.llm_exceptions import V8LLMError
from core.prompt_cache_gateway import PreparedPromptCacheRequest, prompt_cache_gateway


class _RequiredToolModel:
    def __init__(self, *, error_after_chunk=False, error_before_chunk=False):
        self.error_after_chunk = error_after_chunk
        self.error_before_chunk = error_before_chunk
        self.fallback_calls = 0

    def bind_tools(self, *_args, **_kwargs):
        return self

    def invoke(self, messages, **_kwargs):
        if any("工具调用兼容模式" in str(message.content) for message in messages):
            self.fallback_calls += 1
            return AIMessage(content='{"tool_name":"lookup","arguments":{"query":"test"}}')
        return AIMessage(content="I will look it up.")

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)

    def stream(self, _messages, **_kwargs):
        if self.error_before_chunk:
            raise ValueError("tool_choice is not supported")
        yield AIMessageChunk(content='<tool_call><invoke name="lookup">test</invoke></tool_call>')
        if self.error_after_chunk:
            raise ValueError("tool_choice is not supported")

    async def astream(self, messages, **kwargs):
        for chunk in self.stream(messages, **kwargs):
            yield chunk


def _required_tool_adapter(native):
    def lookup(query: str) -> str:
        return query
    return V8ChatModelAdapter(
        model_id="test-model", provider_standard="openai", role="supervisor",
        meta={
            "api_standard": "openai", "model_ref": "configured::test-model",
            "capabilityClass": "chat_tool_calling",
            "capabilities": {"supportsTools": True, "supportsStreaming": True},
        },
        model_kwargs={}, builder=lambda: native,
    ).bind_tools([StructuredTool.from_function(lookup, description="Look up a value.")], tool_choice="required")


def _collect_stream(adapter, *, asynchronous):
    messages = [HumanMessage(content="Look up test.")]
    if not asynchronous:
        return list(adapter.stream(messages))
    async def collect():
        return [chunk async for chunk in adapter.astream(messages)]
    return asyncio.run(collect())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("error_after_chunk", [False, True])
def test_emitted_native_stream_never_starts_hidden_prompt_retry(asynchronous, error_after_chunk):
    native = _RequiredToolModel(error_after_chunk=error_after_chunk)
    adapter = _required_tool_adapter(native)
    assert adapter.effective_capability_matrix()["supports_native_tools"] is True
    if error_after_chunk:
        with pytest.raises(V8LLMError):
            _collect_stream(adapter, asynchronous=asynchronous)
    else:
        chunks = _collect_stream(adapter, asynchronous=asynchronous)
        assert "<tool_call>" in "".join(str(chunk.content) for chunk in chunks)
        assert not any(chunk.tool_calls for chunk in chunks)
    assert native.fallback_calls == 0


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_capability_error_before_stream_still_allows_configured_fallback(asynchronous):
    native = _RequiredToolModel(error_before_chunk=True)
    chunks = _collect_stream(_required_tool_adapter(native), asynchronous=asynchronous)
    assert native.fallback_calls == 1
    assert any(call["name"] == "lookup" for chunk in chunks for call in chunk.tool_calls)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_nonstream_required_tool_fallback_remains_available(asynchronous):
    native = _RequiredToolModel()
    adapter = _required_tool_adapter(native)
    messages = [HumanMessage(content="Look up test.")]
    response = asyncio.run(adapter.ainvoke(messages)) if asynchronous else adapter.invoke(messages)
    assert native.fallback_calls == 1
    assert response.tool_calls[0]["name"] == "lookup"


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
