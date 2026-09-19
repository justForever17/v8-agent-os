from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from core.tools import research_broker


def test_context7_result_is_wrapped_as_research_shard(monkeypatch):
    def fake_run_coro(coro, *, timeout_seconds: float):
        if inspect.iscoroutine(coro):
            coro.close()
        return {
            "ok": True,
            "serverName": "context7",
            "toolName": "get-library-docs",
            "libraryId": "/expo/router",
            "text": "Expo Router official docs: use app routes and layout files.",
            "attempts": [{"tool": "get-library-docs", "ok": True}],
        }

    monkeypatch.setattr(research_broker, "_run_coro_blocking", fake_run_coro)
    shard = research_broker._run_context7_source("How to use Expo Router?", tool_call_id="tool-test")
    assert shard["ok"] is True
    assert shard["provider"] == "context7"
    assert shard["sourceCapability"] == "official_technical_docs"
    assert shard["results"][0]["url"] == "mcp://context7/expo/router"
    assert "official docs" in shard["fetchedTopSources"][0]["text"]


@pytest.mark.parametrize("outcome", ["success", "docs_error", "cancelled"])
def test_context7_executes_async_resolution_and_read_with_real_local_helpers(monkeypatch, outcome):
    calls = []

    async def initialize():
        calls.append("initialize")

    async def call_tool(*, server_name, tool_name, arguments):
        assert server_name == "context7"
        calls.append((tool_name, arguments))
        if tool_name == "resolve-library-id":
            assert arguments["libraryName"] and arguments["query"]
            return {"content": [{"type": "text", "text": "Context7-compatible library ID: /expo/router"}]}
        assert tool_name == "get-library-docs"
        assert arguments["libraryId"] == "/expo/router"
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return {"isError": outcome == "docs_error", "content": [{"type": "text",
            "text": "MCP error: document unavailable" if outcome == "docs_error"
            else "Expo Router uses app routes and layout files. Preserve this complete source body."}]}

    monkeypatch.setattr(research_broker.mcp_manager, "initialize", initialize)
    monkeypatch.setattr(research_broker.mcp_manager, "call_tool", call_tool)
    monkeypatch.setattr(research_broker.mcp_manager, "_server_tools", {
        "context7": [SimpleNamespace(name="resolve-library-id"), SimpleNamespace(name="get-library-docs")],
    })
    shard = research_broker._run_context7_source("How should I use Expo Router?", tool_call_id="controlled-context7")
    assert calls[0] == "initialize"
    assert [item[0] for item in calls[1:]] == ["resolve-library-id", "get-library-docs"]
    if outcome == "success":
        assert shard["ok"] is True
        assert shard["results"][0]["url"] == "mcp://context7/expo/router"
        assert shard["fetchedTopSources"][0]["text"].endswith("Preserve this complete source body.")
        assert all(item["ok"] for item in shard["providerAttemptMatrix"])
    else:
        assert shard["ok"] is False
        assert shard["results"] == shard["fetchedTopSources"] == []
        assert shard["errors"]
