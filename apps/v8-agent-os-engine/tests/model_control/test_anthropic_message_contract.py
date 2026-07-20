from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_anthropic.chat_models import _format_messages
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from core.llm_chat_adapter import V8ChatModelAdapter, create_provider_surface
from core.prompt_cache_gateway import PreparedPromptCacheRequest


def _adapter() -> V8ChatModelAdapter:
    return V8ChatModelAdapter(
        model_id="anthropic-compatible-model",
        provider_standard="anthropic",
        role="agent:verification-engineer",
        meta={
            "api_standard": "anthropic",
            "wire_protocol": "anthropic.messages",
            "capabilityClass": "chat_tool_calling",
            "capabilities": {"supportsTools": True},
        },
        model_kwargs={},
        builder=lambda: object(),
    )


def _tool_call_message(*, canonical_id: str, provider_id: str) -> AIMessage:
    message = AIMessage(
        content=[
            {
                "type": "tool_use",
                "id": provider_id,
                "name": "read_native_file",
                "input": {"path": "README.md"},
            }
        ],
        tool_calls=[
            {
                "id": canonical_id,
                "name": "read_native_file",
                "args": {"path": "README.md"},
            }
        ],
    )
    message.tool_calls[0]["providerToolCallId"] = provider_id
    return message


def test_anthropic_reprojects_canonical_tool_ids_without_duplicate_tool_use() -> None:
    canonical_id = "call_v8_0123456789abcdef01234567"
    provider_id = "call_00_provider_native"
    assistant = _tool_call_message(canonical_id=canonical_id, provider_id=provider_id)
    result = ToolMessage(
        content="README read successfully",
        name="read_native_file",
        tool_call_id=canonical_id,
    )

    normalized = _adapter().normalize_input_for_provider([assistant, result])
    _system, wire_messages = _format_messages(normalized)

    assert assistant.tool_calls[0]["id"] == canonical_id
    assert result.tool_call_id == canonical_id
    assert normalized[0].tool_calls[0]["id"] == provider_id
    assert normalized[1].tool_call_id == provider_id
    assert [
        block["id"]
        for block in wire_messages[0]["content"]
        if block.get("type") == "tool_use"
    ] == [provider_id]
    assert wire_messages[1]["content"][0]["tool_use_id"] == provider_id


def test_anthropic_rejects_incomplete_tool_turn_before_provider_call() -> None:
    assistant = _tool_call_message(
        canonical_id="call_v8_111111111111111111111111",
        provider_id="call_00_missing_result",
    )

    with pytest.raises(ValueError, match="missing immediate tool_result"):
        _adapter().normalize_input_for_provider(
            [assistant, HumanMessage(content="continue without a tool result")]
        )


def test_anthropic_accepts_all_results_for_parallel_tool_calls() -> None:
    first_canonical = "call_v8_222222222222222222222222"
    second_canonical = "call_v8_333333333333333333333333"
    first_provider = "call_00_first"
    second_provider = "call_00_second"
    assistant = AIMessage(
        content=[
            {"type": "tool_use", "id": first_provider, "name": "first", "input": {}},
            {"type": "tool_use", "id": second_provider, "name": "second", "input": {}},
        ],
        tool_calls=[
            {"id": first_canonical, "name": "first", "args": {}},
            {"id": second_canonical, "name": "second", "args": {}},
        ],
    )
    assistant.tool_calls[0]["providerToolCallId"] = first_provider
    assistant.tool_calls[1]["providerToolCallId"] = second_provider

    normalized = _adapter().normalize_input_for_provider(
        [
            assistant,
            ToolMessage(content="one", name="first", tool_call_id=first_canonical),
            ToolMessage(content="two", name="second", tool_call_id=second_canonical),
        ]
    )
    _system, wire_messages = _format_messages(normalized)

    assert [block["id"] for block in wire_messages[0]["content"]] == [
        first_provider,
        second_provider,
    ]
    assert [block["tool_use_id"] for block in wire_messages[1]["content"]] == [
        first_provider,
        second_provider,
    ]


def test_anthropic_coalesces_system_messages_before_user_history() -> None:
    normalized = create_provider_surface("anthropic").normalize_messages(
        [
            HumanMessage(content="hello"),
            SystemMessage(content="first instruction"),
            SystemMessage(content=[{"type": "text", "text": "second instruction"}]),
        ]
    )
    system, wire_messages = _format_messages(normalized)

    assert len([message for message in normalized if isinstance(message, SystemMessage)]) == 1
    assert isinstance(normalized[0], SystemMessage)
    assert [block["text"] for block in system] == [
        "first instruction",
        "second instruction",
    ]
    assert wire_messages == [{"role": "user", "content": "hello"}]


class _PromptEmulatedModel:
    def __init__(self, response_text: str | None = None) -> None:
        self.invoke_count = 0
        self.ainvoke_count = 0
        self.stream_count = 0
        self.astream_count = 0
        self.response_text = response_text or '{"tool_name":"read_native_file","arguments":{"path":"README.md"}}'

    def invoke(self, _messages, **_kwargs):
        self.invoke_count += 1
        return AIMessage(content=self.response_text)

    async def ainvoke(self, _messages, **_kwargs):
        self.ainvoke_count += 1
        return AIMessage(content=self.response_text)

    def stream(self, _messages, **_kwargs):
        self.stream_count += 1
        raise AssertionError("prompt-emulated tool calls must not stream before JSON parsing")

    async def astream(self, _messages, **_kwargs):
        self.astream_count += 1
        raise AssertionError("prompt-emulated tool calls must not stream before JSON parsing")
        yield  # pragma: no cover


def _prompt_emulated_adapter(model: _PromptEmulatedModel) -> V8ChatModelAdapter:
    adapter = V8ChatModelAdapter(
        model_id="anthropic-compatible-model",
        provider_standard="anthropic",
        role="agent:verification-engineer",
        meta={
            "api_standard": "anthropic",
            "wire_protocol": "anthropic.messages",
            "effective_capability_matrix": {
                "supports_streaming": True,
                "supports_native_tools": False,
                "supports_prompt_emulated_tools": True,
                "supports_native_structured_output": True,
                "supports_prompt_fallback_structured_output": True,
            },
        },
        model_kwargs={},
        builder=lambda: model,
    )
    adapter._bound_tools = [SimpleNamespace(name="read_native_file")]
    return adapter


@pytest.fixture
def _without_prompt_cache_side_effects(monkeypatch):
    monkeypatch.setattr(
        "core.llm_chat_adapter.prompt_cache_gateway.prepare_request",
        lambda **kwargs: PreparedPromptCacheRequest(
            messages=list(kwargs["messages"]),
            kwargs={},
            diagnostics={},
        ),
    )
    monkeypatch.setattr("core.llm_chat_adapter.prompt_cache_gateway.decorate_response", lambda *_args: None)
    monkeypatch.setattr("core.llm_chat_adapter.prompt_cache_gateway.store_response", lambda *_args: None)


def test_prompt_emulated_tool_call_buffers_sync_stream_until_json_can_be_parsed(
    _without_prompt_cache_side_effects,
) -> None:
    model = _PromptEmulatedModel()
    chunks = list(_prompt_emulated_adapter(model)._stream([HumanMessage(content="read README")]))

    assert model.invoke_count == 1
    assert model.stream_count == 0
    assert chunks[0].message.tool_calls[0]["name"] == "read_native_file"
    assert chunks[0].message.tool_calls[0]["args"] == {"path": "README.md"}


def test_prompt_emulated_tool_call_buffers_async_stream_until_json_can_be_parsed(
    _without_prompt_cache_side_effects,
) -> None:
    async def collect():
        model = _PromptEmulatedModel()
        chunks = [
            chunk
            async for chunk in _prompt_emulated_adapter(model)._astream([HumanMessage(content="read README")])
        ]
        return model, chunks

    model, chunks = asyncio.run(collect())

    assert model.ainvoke_count == 1
    assert model.astream_count == 0
    assert chunks[0].message.tool_calls[0]["name"] == "read_native_file"
    assert chunks[0].message.tool_calls[0]["args"] == {"path": "README.md"}


def test_prompt_emulated_tool_call_accepts_exact_legacy_bracket_shape(
    _without_prompt_cache_side_effects,
) -> None:
    model = _PromptEmulatedModel(
        '[Tool Call: read_native_file]\n{"path":"README.md"}'
    )

    chunks = list(_prompt_emulated_adapter(model)._stream([HumanMessage(content="read README")]))

    assert chunks[0].message.content == ""
    assert chunks[0].message.tool_calls[0]["name"] == "read_native_file"
    assert chunks[0].message.tool_calls[0]["args"] == {"path": "README.md"}


def test_prompt_emulated_tool_call_does_not_promote_unknown_legacy_tool(
    _without_prompt_cache_side_effects,
) -> None:
    model = _PromptEmulatedModel('[Tool Call: delete_everything]\n{"path":"README.md"}')

    chunks = list(_prompt_emulated_adapter(model)._stream([HumanMessage(content="read README")]))

    assert chunks[0].message.tool_calls == []
    assert "delete_everything" in str(chunks[0].message.content)


def test_prompt_emulated_tool_call_enforces_mapping_schema_allowlist(
    _without_prompt_cache_side_effects,
) -> None:
    model = _PromptEmulatedModel(
        '{"tool_name":"fetch_skill_instructions","arguments":{"skill_name":""}}'
    )
    adapter = _prompt_emulated_adapter(model)
    adapter._bound_tools = [
        {
            "type": "function",
            "function": {
                "name": "run_system_command",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    chunks = list(adapter._stream([HumanMessage(content="run verification")]))

    assert chunks[0].message.tool_calls == []
    assert "fetch_skill_instructions" in str(chunks[0].message.content)


def test_provider_native_tool_call_cannot_escape_bound_tool_surface() -> None:
    adapter = _prompt_emulated_adapter(_PromptEmulatedModel())
    response = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-provider-unknown",
                "name": "fetch_skill_instructions",
                "args": {"skill_name": ""},
            },
            {
                "id": "call-provider-allowed",
                "name": "read_native_file",
                "args": {"path": "README.md"},
            },
        ],
    )

    normalized = adapter._decorate_message(response, tool_mode="prompt_emulated")

    assert [call["name"] for call in normalized.tool_calls] == ["read_native_file"]
    assert normalized.response_metadata["v8_rejected_unbound_tool_calls"] == [
        "fetch_skill_instructions"
    ]


def test_prompt_emulated_anthropic_history_contains_no_native_tool_use_contract() -> None:
    model = _PromptEmulatedModel()
    adapter = _prompt_emulated_adapter(model)
    tool_request = adapter._apply_prompt_emulated_tool_calls(
        AIMessage(content='{"tool_name":"read_native_file","arguments":{"path":"README.md"}}'),
        force=True,
    )

    normalized = adapter.normalize_input_for_provider(
        [
            tool_request,
            ToolMessage(
                content="README lines 1-40",
                name="read_native_file",
                tool_call_id=tool_request.tool_calls[0]["id"],
            ),
        ]
    )
    system, wire_messages = _format_messages(normalized)

    assert system is None
    assert normalized[0].tool_calls == []
    assert "[Previously Executed Tool Request: read_native_file]" in str(normalized[0].content)
    assert wire_messages[0]["role"] == "assistant"
    assert wire_messages[1] == {
        "role": "user",
        "content": f"[Tool Result: read_native_file]\nREADME lines 1-40",
    }
