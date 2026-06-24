from __future__ import annotations

import json

from runtimes.chat.runtime import ChatRuntime


def test_web_broker_event_agent_visible_result_is_text_surface():
    payload = {
        "ok": True,
        "mode": "search",
        "query": "V8OS tool output surface",
        "summary": "Found relevant docs.",
        "results": [
            {
                "title": "Tool Output Surface Contract",
                "url": "https://example.com/tool-surface",
                "snippet": "Agent-visible output should be clean Markdown.",
                "sourceQualityHints": {"diagnostic": "runtime-only"},
            }
        ],
        "trace": {"provider": "internal-only"},
    }

    visible = ChatRuntime._agent_visible_tool_result_for_event(
        "web_broker",
        json.dumps(payload, ensure_ascii=False),
        payload,
    )

    assert visible.startswith("Web broker (search)")
    assert "Tool Output Surface Contract" in visible
    assert "https://example.com/tool-surface" in visible
    assert not visible.lstrip().startswith("{")
    assert "sourceQualityHints" not in visible
    assert '"trace"' not in visible


def test_memory_broker_event_agent_visible_result_is_text_surface():
    payload = {
        "ok": True,
        "mode": "recall",
        "query": "previous task",
        "summary": "No matching prior memory.",
        "metrics": {"candidateCount": 0, "cacheHit": False},
    }

    visible = ChatRuntime._agent_visible_tool_result_for_event(
        "memory_broker",
        json.dumps(payload, ensure_ascii=False),
        payload,
    )

    assert visible == "Memory: no matching prior evidence.\nContinue with the current request."
    assert not visible.lstrip().startswith("{")
    assert "candidateCount" not in visible
    assert "Query:" not in visible
    assert "Next:" not in visible


def test_runtime_broker_event_agent_visible_result_is_text_surface():
    payload = {
        "ok": True,
        "mode": "route",
        "state": "queued",
        "queuedEpisodeId": "episode_demo",
        "nextAction": "wait_episode",
        "runtimeRegistry": {"internal": "do-not-show"},
    }

    visible = ChatRuntime._agent_visible_tool_result_for_event(
        "runtime_broker",
        json.dumps(payload, ensure_ascii=False),
        payload,
    )

    assert visible.startswith("Runtime route menu")
    assert "episode_demo" in visible
    assert not visible.lstrip().startswith("{")
    assert "runtimeRegistry" not in visible
