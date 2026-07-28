from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessageChunk, HumanMessage

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
