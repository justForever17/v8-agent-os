from __future__ import annotations

import json

from core.runtime_route_contract import runtime_route_parameter_guidance
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

    assert visible.startswith("execution runtime queued")
    assert "episode_demo" not in visible
    assert "graph owns waiting" in visible
    assert "Active grants" not in visible
    assert "tool_observation_detail" not in visible
    assert "Raw:" not in visible
    assert not visible.lstrip().startswith("{")
    assert "runtimeRegistry" not in visible


def test_runtime_broker_invalid_contract_surface_teaches_parameter_shape():
    payload = {
        "ok": False,
        "mode": "route",
        "summary": "The route contract is invalid.",
        "error": "typed_need_invalid",
        "routeBriefQuality": {
            "validationErrors": [
                    {"field": "taskBriefs.0.dependencies", "type": "list_type"}
            ]
        },
        "parameterGuidance": runtime_route_parameter_guidance("engineering"),
        "recommendedNextAction": "Repair the same route call once.",
    }

    visible = ChatRuntime._agent_visible_tool_result_for_event(
        "runtime_broker",
        json.dumps(payload, ensure_ascii=False),
        payload,
    )

    assert visible.startswith("Runtime route repair")
    assert "taskBriefs.0.dependencies" in visible
    assert "Canonical task array: taskBriefs" in visible
    assert '"dependency":' not in visible
    assert "dependencies" in visible
    assert "Omit optional arrays when empty" in visible
    assert "Repair the same route call once" in visible


def test_runtime_broker_write_contract_surface_reports_exact_task_failure():
    payload = {
        "ok": False,
        "mode": "route",
        "summary": "The write contract conflicts with its explicit expected artifacts.",
        "error": "write_task_contract_incomplete",
        "routeBriefQuality": {
            "tasks": [
                {
                    "taskBriefId": "baseline-delivery",
                    "missingFields": ["writeSet(expected_artifact_not_declared)"],
                    "undeclaredArtifactPaths": ["reports/evidence.json"],
                }
            ],
            "requiredFields": ["writeSet", "expectedOutputs", "acceptance"],
        },
        "recommendedNextAction": "Repair only the exact reported path and retry the same route once.",
    }

    visible = ChatRuntime._agent_visible_tool_result_for_event(
        "runtime_broker",
        json.dumps(payload, ensure_ascii=False),
        payload,
    )

    assert "Task baseline-delivery needs repair: writeSet(expected_artifact_not_declared)" in visible
    assert "Declared artifacts outside writeSet: reports/evidence.json" in visible
    assert "Missing contract fields: writeSet, expectedOutputs, acceptance" not in visible
