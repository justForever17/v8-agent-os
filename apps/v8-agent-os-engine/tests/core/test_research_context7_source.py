from __future__ import annotations

import inspect

from core.tools import research_broker


def test_context7_is_attempted_for_official_docs_policy():
    assert research_broker._should_try_context7_source("How should I use Expo Router?", "official_docs_first")
    assert research_broker._should_try_context7_source("React API 设计", "authoritative")


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
