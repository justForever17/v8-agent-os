import asyncio
import sqlite3
import threading

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.llm_chat_adapter import V8ChatModelAdapter
from erc.checkpoint_store import CheckpointStore, V8AsyncSqliteSaver


class _NativeModel:
    def __init__(self) -> None:
        self.last_config = None

    def bind_tools(self, _tools, **_kwargs):
        return self

    def invoke(self, _messages, *, config=None, **_kwargs):
        self.last_config = config
        return AIMessage(content="ok")


class _PromptFallbackNativeModel:
    def __init__(self) -> None:
        self.calls = 0
        self.messages = []
        self.bind_kwargs = []

    def bind_tools(self, _tools, **kwargs):
        self.bind_kwargs.append(dict(kwargs))
        return self

    def invoke(self, messages, *, config=None, **_kwargs):
        self.calls += 1
        self.messages.append(list(messages))
        if self.calls == 1:
            return AIMessage(content="我会先调用 runtime_broker。")
        return AIMessage(
            content=(
                '{"tool_name":"runtime_broker","arguments":'
                '{"mode":"route","need":{"kind":"engineering"}}}'
            )
        )


class _FlakySaver:
    def __init__(self) -> None:
        self.aput_calls = 0
        self.aput_writes_calls = 0

    async def aput(self, *_args, **_kwargs):
        self.aput_calls += 1
        if self.aput_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"ok": True, "operation": "aput"}

    async def aput_writes(self, *_args, **_kwargs):
        self.aput_writes_calls += 1
        if self.aput_writes_calls == 1:
            raise sqlite3.OperationalError("database is locked")
        return {"ok": True, "operation": "aput_writes"}


class _BrokenSaver:
    async def aput(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("no such table: checkpoints")

    async def aput_writes(self, *_args, **_kwargs):
        raise sqlite3.OperationalError("no such table: checkpoint_writes")


def test_bind_tools_does_not_deepcopy_runtime_client_locks():
    adapter = V8ChatModelAdapter(
        model_id="test-model",
        provider_standard="openai",
        role="supervisor",
        meta={"api_standard": "openai"},
        model_kwargs={},
        builder=lambda: _NativeModel(),
    )
    adapter._base_model = {"lock": threading.RLock()}

    bound = adapter.bind_tools([{"name": "demo_tool"}])

    assert bound._base_model is None
    assert len(bound._bound_tools or []) == 1


def test_adapter_marks_nested_provider_events_runtime_internal():
    native = _NativeModel()
    adapter = V8ChatModelAdapter(
        model_id="test-model",
        provider_standard="openai",
        role="supervisor",
        meta={"api_standard": "openai"},
        model_kwargs={},
        builder=lambda: native,
    )

    response = adapter.invoke([HumanMessage(content="hello")])

    assert response.content == "ok"
    assert native.last_config == {
        "metadata": {"v8_model_scope": "runtime_internal"},
        "tags": ["v8:provider-internal"],
    }


def test_required_native_tool_call_falls_back_to_strict_prompt_emulation():
    native = _PromptFallbackNativeModel()
    adapter = V8ChatModelAdapter(
        model_id="test-model",
        provider_standard="openai",
        role="supervisor",
        meta={
            "api_standard": "openai",
            "capabilityClass": "chat_tool_calling",
            "capabilities": {"supportsTools": True},
        },
        model_kwargs={},
        builder=lambda: native,
    )
    runtime_broker_schema = {
        "type": "function",
        "function": {
            "name": "runtime_broker",
            "description": "Route a typed runtime episode.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string"},
                    "need": {"type": "object"},
                },
                "required": ["mode", "need"],
            },
        },
    }

    response = adapter.bind_tools(
        [runtime_broker_schema],
        tool_choice="runtime_broker",
    ).invoke([HumanMessage(content="route engineering")])

    assert native.calls == 2
    assert native.bind_kwargs == [{"tool_choice": "runtime_broker"}]
    assert response.content == ""
    assert response.tool_calls[0]["name"] == "runtime_broker"
    assert response.tool_calls[0]["args"] == {
        "mode": "route",
        "need": {"kind": "engineering"},
    }
    assert "本次必须调用工具 runtime_broker" in native.messages[1][0].content


def test_checkpoint_store_isolates_savers_by_event_loop(tmp_path):
    store = CheckpointStore(tmp_path / "checkpoints.sqlite3")

    async def _open_and_close():
        saver = await store.get_async_sqlite_saver()
        assert saver is not None
        await store.close()

    asyncio.run(_open_and_close())
    asyncio.run(_open_and_close())


def test_checkpoint_store_retries_transient_sqlite_write_locks():
    saver = _FlakySaver()

    async def _run():
        assert await V8AsyncSqliteSaver._call_with_lock_retry(
            saver.aput,
            "config",
            "checkpoint",
            "metadata",
            "versions",
        ) == {"ok": True, "operation": "aput"}
        assert await V8AsyncSqliteSaver._call_with_lock_retry(
            saver.aput_writes,
            "config",
            "writes",
            "task-id",
            "task-path",
        ) == {
            "ok": True,
            "operation": "aput_writes",
        }

    asyncio.run(_run())
    assert saver.aput_calls == 2
    assert saver.aput_writes_calls == 2


def test_checkpoint_store_does_not_retry_non_lock_sqlite_errors():
    saver = _BrokenSaver()

    async def _run():
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            await V8AsyncSqliteSaver._call_with_lock_retry(
                saver.aput,
                "config",
                "checkpoint",
                "metadata",
                "versions",
            )

    asyncio.run(_run())
