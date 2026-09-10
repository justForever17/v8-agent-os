from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, message_chunk_to_message
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
        with pytest.raises(V8LLMError) as error:
            _collect_stream(adapter, asynchronous=asynchronous)
        assert error.value.code == "model_output_incomplete"
    assert native.fallback_calls == 0


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_protocol_fragment_never_enters_public_stream(asynchronous):
    class BrokenNative(_RequiredToolModel):
        def stream(self, _messages, **_kwargs):
            text = '正常的已完成内容。]<]minimax[>[<tool_call><invoke name="lookup">PRIVATE-ARGS</invoke></tool_call>'
            for character in text:
                yield AIMessageChunk(content=character)
    native, seen = BrokenNative(), []
    adapter = _required_tool_adapter(native)
    async def collect():
        async for chunk in adapter.astream([HumanMessage(content="test")]):
            seen.append(chunk.content)
    with pytest.raises(V8LLMError) as error:
        if asynchronous:
            asyncio.run(collect())
        else:
            for chunk in adapter.stream([HumanMessage(content="test")]):
                seen.append(chunk.content)
    assert error.value.code == "model_output_incomplete"
    assert ''.join(seen) == '正常的已完成内容。'
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
    assert combined.response_metadata["v8_prompt_cache"] == {
        "cacheKey": "stream-probe",
        "outputTokenBudget": {"mode": "auto", "maxTokens": None, "source": "provider_default"},
    }


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


class _NativeFileResponse:
    """Provider-boundary fixture; LangChain assembles the real JSON fragments."""

    def __init__(self, arguments, *, finish="tool_calls"):
        self.arguments = arguments
        self.finish = finish
        self.requests = []

    def bind_tools(self, *_args, **_kwargs):
        return self

    def stream(self, _messages, **kwargs):
        self.requests.append(kwargs)
        for offset in range(0, len(self.arguments), 4093):
            fragment = self.arguments[offset:offset + 4093]
            yield AIMessageChunk(content="", tool_call_chunks=[{
                "index": 0, "id": "provider-write" if offset == 0 else None,
                "name": "write_result" if offset == 0 else None, "args": fragment,
            }])
        yield AIMessageChunk(content="", response_metadata={"finish_reason": self.finish})

    async def astream(self, messages, **kwargs):
        for chunk in self.stream(messages, **kwargs):
            yield chunk

    def invoke(self, messages, **kwargs):
        chunks = list(self.stream(messages, **kwargs))
        combined = chunks[0]
        for chunk in chunks[1:]:
            combined += chunk
        message = message_chunk_to_message(combined)
        # Nonstream SDK output retains the raw function arguments even though
        # its convenience tool_calls may already contain repaired JSON.
        message.additional_kwargs["tool_calls"] = [{
            "id": "provider-write", "type": "function",
            "function": {"name": "write_result", "arguments": self.arguments},
        }]
        return message

    async def ainvoke(self, messages, **kwargs):
        return self.invoke(messages, **kwargs)


def _file_response_adapter(native, target, *, role="agent:worker", mode="auto", standard="openai"):
    def write_result(content: str) -> str:
        target.write_text(content, encoding="utf-8", newline="")
        return hashlib.sha256(target.read_bytes()).hexdigest()

    tool = StructuredTool.from_function(write_result, description="Write the fixture result.")
    adapter = V8ChatModelAdapter(
        model_id="fixture-model", provider_standard=standard, role=role,
        meta={"model_record": {"outputTokenMode": mode, "maxTokens": 4096},
              "capabilityClass": "chat_tool_calling",
              "capabilities": {"supportsTools": True, "supportsStreaming": True}},
        model_kwargs={}, builder=lambda: native,
    ).bind_tools([tool])
    return adapter, tool


def _collect_file_response(adapter, *, asynchronous, streaming):
    messages = [HumanMessage(content="Write the requested result.")]
    async def call():
        if streaming:
            return [chunk async for chunk in adapter.astream(messages)]
        return await adapter.ainvoke(messages)
    result = asyncio.run(call()) if asynchronous else list(adapter.stream(messages)) if streaming else adapter.invoke(messages)
    if not streaming:
        return result
    combined = result[0]
    for chunk in result[1:]:
        combined += chunk
    return message_chunk_to_message(combined)


@pytest.mark.parametrize("asynchronous,streaming", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("arguments,finish", [
    ('{"content":"the provider stopped inside this value', "tool_calls"),
    ('{"content":"the provider stopped inside this value', ""),
    ('{"content":"apparently complete"}', "length"),
])
def test_incomplete_tool_arguments_cannot_reach_file_executor(tmp_path, asynchronous, streaming, arguments, finish):
    target = tmp_path / "result.txt"
    native = _NativeFileResponse(arguments, finish=finish)
    adapter, tool = _file_response_adapter(native, target)
    with pytest.raises(V8LLMError) as failure:
        response = _collect_file_response(adapter, asynchronous=asynchronous, streaming=streaming)
        for call in response.tool_calls:
            tool.invoke(call["args"])
    assert failure.value.code == "model_output_incomplete"
    assert not target.exists()
    assert len(native.requests) == 1
    assert arguments not in str(failure.value)


@pytest.mark.parametrize("role", ["supervisor", "agent:worker", "reviewer:worker", "vision"])
@pytest.mark.parametrize("standard", ["openai", "anthropic", "gemini"])
def test_complete_large_tool_arguments_reach_executor_unchanged(tmp_path, role, standard):
    content = "<html><body>" + ("完整片段 \\\"引号\\\" {JSON} 🌼\n" * 4096) + "CANARY-END</body></html>"
    target = tmp_path / "result.html"
    native = _NativeFileResponse(json.dumps({"content": content}, ensure_ascii=False))
    adapter, tool = _file_response_adapter(native, target, role=role, standard=standard)
    response = _collect_file_response(adapter, asynchronous=False, streaming=True)
    assert len(response.tool_calls) == 1
    proof = tool.invoke(response.tool_calls[0]["args"])
    assert proof == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert target.read_text(encoding="utf-8") == content
    assert len(native.requests) == 1
    request = native.requests[0]
    if standard == "anthropic":
        assert request["max_tokens"] == 32768  # Required protocol field, not a model cap.
    else:
        assert not any(key in request for key in ("max_tokens", "max_output_tokens", "max_completion_tokens"))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("with_raw_delta", [False, True])
def test_native_sdk_raw_tool_deltas_remain_complete_through_adapter(tmp_path, asynchronous, with_raw_delta):
    from langchain_openai.chat_models.base import _convert_delta_to_message_chunk

    content = "完整工具参数，第二分片含实际正文与末尾 canary。"
    arguments = json.dumps({"content": content}, ensure_ascii=False)

    class NativeSdkResponse(_NativeFileResponse):
        def stream(self, _messages, **kwargs):
            self.requests.append(kwargs)
            for index, fragment in enumerate([arguments[:18], arguments[18:]]):
                function = {"arguments": fragment, **({"name": "write_result"} if index == 0 else {})}
                delta = {"role": "assistant", "tool_calls": [
                    {"index": 0, "function": function, **({"id": "provider-write", "type": "function"} if index == 0 else {})},
                ]}
                chunk = _convert_delta_to_message_chunk(delta, AIMessageChunk)
                if with_raw_delta:
                    # Some native SDKs preserve raw OpenAI deltas alongside
                    # tool_call_chunks; current langchain-openai omits them.
                    chunk.additional_kwargs["tool_calls"] = delta["tool_calls"]
                yield chunk
            yield AIMessageChunk(content="", response_metadata={"finish_reason": "tool_calls"})

    native = NativeSdkResponse(arguments)
    original = list(native.stream([]))
    assembled = original[0] + original[1] + original[2]
    if with_raw_delta:
        assert assembled.additional_kwargs["tool_calls"][0]["function"]["arguments"] == arguments
    assert assembled.tool_call_chunks[0]["args"] == arguments
    target = tmp_path / "result.txt"
    adapter, executor = _file_response_adapter(native, target)
    response = _collect_file_response(adapter, asynchronous=asynchronous, streaming=True)
    assert response.tool_calls[0]["args"] == {"content": content}
    executor.invoke(response.tool_calls[0]["args"])
    assert target.read_text(encoding="utf-8") == content


def test_complete_sdk_chunks_do_not_hide_incomplete_original_raw_arguments(tmp_path):
    target = tmp_path / "result.txt"
    adapter, _executor = _file_response_adapter(object(), target)
    raw = '{"content":"unfinished'
    chunk = AIMessageChunk(content="", tool_call_chunks=[{
        "index": 0, "id": "provider-write", "name": "write_result", "args": '{"content":"repaired"}',
    }], additional_kwargs={"tool_calls": [{"index": 0, "id": "provider-write", "type": "function",
        "function": {"name": "write_result", "arguments": raw}}]})
    decorated = adapter._coerce_chunk(chunk)
    assert decorated.additional_kwargs["tool_calls"][0]["function"]["arguments"] == raw
    with pytest.raises(V8LLMError) as failure:
        adapter._coerce_ai_message(decorated)
    assert failure.value.details["reason"] == "incomplete_tool_arguments"
    assert failure.value.details["argumentSource"] == "raw_tool_calls"
    assert failure.value.details["argumentSha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert not target.exists()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("with_raw_delta", [False, True])
@pytest.mark.parametrize("truncated_tool", [None, 0, 1])
def test_interleaved_two_tool_sdk_stream_survives_factory_patch_and_bound_surface(asynchronous, with_raw_delta, truncated_tool):
    from copy import deepcopy
    from core.provider_compatibility import install_provider_compatibility_patches
    import langchain_openai.chat_models.base as openai_base

    # This is the same SDK patch installed by LLMFactory, before creating its
    # ChatOpenAI backend. Both tool indexes must survive normalization.
    install_provider_compatibility_patches()
    first = json.dumps({"mode": "dispatch", "tasks": [{"title": "只读验证", "paths": ["a.json", "b.json"]}]},
                       ensure_ascii=False, separators=(",", ":"))
    second = json.dumps({"action": "observe", "target": "owned-page"}, separators=(",", ":"))
    if truncated_tool == 0:
        first = first[:18]
    elif truncated_tool == 1:
        second = second[:-1]
    fragments = [(0, first[:18]), (1, second[:12]), (0, first[18:33]), (1, second[12:]), (0, first[33:])]
    tool_names = ["delegation_broker", "browser_broker"]
    seen = set()
    deltas = []
    for index, fragment in fragments:
        raw = {"index": index, "function": {"arguments": fragment}}
        if index not in seen:
            raw.update(id=f"provider-tool-{index}", type="function")
            raw["function"]["name"] = tool_names[index]
            seen.add(index)
        deltas.append({"role": "assistant", "tool_calls": [raw]})

    class InterleavedSdkModel:
        def bind_tools(self, tools, **_kwargs):
            assert {tool["function"]["name"] for tool in tools} == set(tool_names)
            return self

        def stream(self, _messages, **_kwargs):
            for delta in deltas:
                chunk = openai_base._convert_delta_to_message_chunk(deepcopy(delta), AIMessageChunk)
                if with_raw_delta:
                    chunk.additional_kwargs["tool_calls"] = deepcopy(delta["tool_calls"])
                yield chunk
            yield AIMessageChunk(content="", response_metadata={"finish_reason": "tool_calls"})

        async def astream(self, messages, **kwargs):
            for chunk in self.stream(messages, **kwargs):
                yield chunk

    tools = [{"type": "function", "function": {"name": name, "description": "Fixture only; never execute.",
              "parameters": {"type": "object", "properties": {}}}} for name in tool_names]
    adapter = V8ChatModelAdapter(model_id="fixture-model", provider_standard="openai", role="supervisor",
        meta={"model_record": {"outputTokenMode": "auto"}, "capabilityClass": "chat_tool_calling",
              "capabilities": {"supportsTools": True, "supportsStreaming": True}},
        model_kwargs={}, builder=InterleavedSdkModel).bind_tools(tools)
    if truncated_tool is not None:
        with pytest.raises(V8LLMError) as failure:
            _collect_file_response(adapter, asynchronous=asynchronous, streaming=True)
        assert failure.value.code == "model_output_incomplete"
        assert failure.value.details["argumentSource"] == "tool_call_chunks"
        assert failure.value.details["argumentSha256"] == hashlib.sha256((first if truncated_tool == 0 else second).encode()).hexdigest()
        if truncated_tool == 0:
            assert failure.value.details["argumentsChars"] == 18
            assert failure.value.details["argumentSha256"] == "ff1b37e176f211c0f73bf01032925c4578c55a269449469e6b34da7d53979a37"
    else:
        response = _collect_file_response(adapter, asynchronous=asynchronous, streaming=True)
        assert [(call["name"], call["args"]) for call in response.tool_calls] == [
            (tool_names[0], json.loads(first)), (tool_names[1], json.loads(second)),
        ]


@pytest.mark.parametrize("finish_key,finish", [("finish_reason", "MAX_TOKENS"), ("stop_reason", "max_tokens"), ("status", "incomplete")])
def test_provider_output_limit_blocks_complete_looking_tool_arguments(tmp_path, finish_key, finish):
    adapter, _tool = _file_response_adapter(object(), tmp_path / "result.txt")
    response = AIMessage(content="", tool_calls=[{
        "id": "write", "name": "write_result", "args": {"content": "looks complete"},
    }], response_metadata={finish_key: finish})
    with pytest.raises(V8LLMError) as failure:
        adapter._coerce_ai_message(response)
    assert failure.value.code == "model_output_incomplete"


def test_complete_first_tool_does_not_hide_truncated_second_tool(tmp_path):
    adapter, _tool = _file_response_adapter(object(), tmp_path / "result.txt")
    response = AIMessageChunk(content="", tool_call_chunks=[
        {"index": 0, "id": "first", "name": "write_result", "args": '{"content":"valid"}'},
        {"index": 1, "id": "second", "name": "write_result", "args": '{"content":"partial'},
    ])
    assert len(response.tool_calls) == 2  # SDK repaired the second call.
    with pytest.raises(V8LLMError) as failure:
        adapter._coerce_ai_message(response)
    assert failure.value.details["reason"] == "incomplete_tool_arguments"


def test_incomplete_stream_layout_records_indexes_without_private_argument_values(tmp_path):
    adapter, _tool = _file_response_adapter(object(), tmp_path / "unused.txt")
    response = AIMessageChunk(content="", tool_call_chunks=[
        {"index": 0, "id": "old", "name": "write_result", "args": '{"content":"PRIVATE-CANARY'},
        {"index": 1, "id": "new", "name": "write_result", "args": '{"content":"complete"}'},
    ])
    with pytest.raises(V8LLMError) as failure:
        adapter._validate_complete_tool_response(response)
    layout = json.loads(failure.value.details["toolArgumentLayout"])
    assert [(item["index"], item["completeObject"]) for item in layout] == [(0, False), (1, True)]
    assert "PRIVATE-CANARY" not in str(failure.value.details)
    assert not (tmp_path / "unused.txt").exists()


def test_prompt_emulated_tool_does_not_erase_output_limit_metadata(tmp_path):
    adapter, _tool = _file_response_adapter(object(), tmp_path / "result.txt")
    response = AIMessage(
        content='{"tool_name":"write_result","arguments":{"content":"partial result"}}',
        response_metadata={"finish_reason": "length"},
    )
    with pytest.raises(V8LLMError) as failure:
        adapter._coerce_ai_message(response, force_prompt_emulated_tools=True)
    assert failure.value.code == "model_output_incomplete"


def test_incomplete_plain_text_remains_visible_without_authorizing_any_tool(tmp_path):
    adapter, _tool = _file_response_adapter(object(), tmp_path / "result.txt")
    response = AIMessage(content="Partial explanation", response_metadata={"finish_reason": "length"})
    result = adapter._coerce_ai_message(response)
    assert result.content == "Partial explanation"
    assert result.response_metadata["finish_reason"] == "length"
    assert result.tool_calls == []
