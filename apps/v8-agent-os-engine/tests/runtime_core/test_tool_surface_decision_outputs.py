from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from core.tool_surface import apply_tool_surface_budget


def _visible(tool_name: str, payload: dict, *, budget: int = 2500) -> str:
    message = ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        name=tool_name,
        tool_call_id=f"call-{tool_name}",
    )
    return str(
        apply_tool_surface_budget(
            message,
            {"agentVisibleBudget": budget},
            tool_name=tool_name,
        ).content
    )


def _assert_not_json_wrapper(text: str) -> None:
    assert not text.lstrip().startswith("{")
    assert "_v8ToolSurface" not in text
    assert '"ok"' not in text
    assert "recommendedNextAction" not in text


def test_runtime_broker_default_is_decision_summary():
    visible = _visible(
        "runtime_broker",
        {
            "mode": "list",
            "ok": True,
            "availableGroups": [
                {"group": "research.core", "kind": "research", "label": "Research core"},
                {"group": "creative_media.core", "kind": "creative_media", "label": "Creative Media core"},
            ],
            "recommendedNextAction": "Grant one needed group.",
            "omitted": {"toolNames": 48},
        },
    )

    assert visible.startswith("Runtime broker")
    assert "research.core" in visible
    assert "Grantable groups" in visible
    _assert_not_json_wrapper(visible)


def test_workspace_broker_default_hides_binding_and_tree():
    visible = _visible(
        "workspace_broker",
        {
            "ok": True,
            "kind": "workspace_inventory",
            "workspaceRoot": "E:\\Projects\\demo",
            "token": "abc1234567890",
            "nonEmpty": True,
            "topDirs": ["src", "tests", "node_modules"],
            "projectMarkers": [{"path": "package.json", "kind": "package.json"}],
            "workspaceBinding": {"large": "diagnostic"},
            "tree": [{"name": "src"}],
            "recommendedNextAction": "Continue existing project.",
        },
    )

    assert "Workspace inventory" in visible
    assert "E:\\Projects\\demo" in visible
    assert "package.json" in visible
    assert "workspaceBinding" not in visible
    assert '"tree"' not in visible
    _assert_not_json_wrapper(visible)


def test_research_plan_hides_shard_defaults():
    visible = _visible(
        "research_broker",
        {
            "ok": True,
            "mode": "plan",
            "kind": "research_plan",
            "question": "Compare official docs",
            "researchIntent": "source quality",
            "experienceFirstPolicy": {"summary": "Search reusable packs first."},
            "shardDefaults": {"allowedTools": ["web_search", "web_read"], "deadlineMs": 45000},
            "limits": {"effectiveMaxShards": 2, "effectiveMaxRounds": 1},
            "shards": [
                {"shardId": "shard_1", "kind": "baseline", "query": "Compare official docs", "reason": "baseline"},
                {"shardId": "shard_2", "kind": "official_docs", "query": "Compare official docs API", "reason": "official"},
            ],
        },
    )

    assert "Research plan" in visible
    assert "Shard briefs" in visible
    assert "allowedTools" not in visible
    assert "deadlineMs" not in visible
    _assert_not_json_wrapper(visible)


def test_computer_use_route_hides_matches_and_manual_controls():
    visible = _visible(
        "computer_use_resolve_execution_route",
        {
            "ok": True,
            "recommendedMode": "hybrid_mode",
            "recommendedTool": "computer_use_execute_task",
            "recommendedAction": "run_hybrid_with_computer_use",
            "recommendedMatch": {
                "id": "system.github.star_repository",
                "name": "GitHub Star Repository",
                "score": 0.41,
                "confidence": 0.81,
            },
            "matches": [{"id": "too much"}],
            "manualControls": {"humanCanApprove": True},
        },
    )

    assert "Computer Use route" in visible
    assert "system.github.star_repository" in visible
    assert "computer_use_execute_task" in visible
    assert "manualControls" not in visible
    assert '"matches"' not in visible
    _assert_not_json_wrapper(visible)


def test_creative_media_list_jobs_is_short_queue_surface():
    visible = _visible(
        "creative_media_list_jobs",
        {
            "ok": True,
            "statusCounts": {"failed": 8, "succeeded": 9},
            "jobs": [
                {
                    "jobId": "cm_15ff203da04c46b0a39506b5a9ade2c2",
                    "operationKind": "image.generate",
                    "status": "failed",
                    "providerId": "openai",
                    "model": "gpt-image-2",
                    "error": "RemoteProtocolError: Server disconnected without sending a response.",
                    "providerResponse": {"large": "raw"},
                }
            ],
            "detailTool": "creative_media_get_job(job_id=...)",
        },
    )

    assert "Creative Media jobs" in visible
    assert "Status: failed=8, succeeded=9" in visible
    assert "RemoteProtocolError" in visible
    assert "providerResponse" not in visible
    _assert_not_json_wrapper(visible)


def test_computer_use_list_apps_limits_aliases_and_windows():
    visible = _visible(
        "computer_use_list_apps",
        {
            "ok": True,
            "count": 20,
            "apps": [
                {
                    "appId": "vscode",
                    "displayName": "Visual Studio Code",
                    "isRunning": True,
                    "launchable": True,
                    "topWindowTitle": "Codex",
                    "aliases": ["VS Code", "vscode", "visual studio code"],
                    "windows": [{"handle": 1}],
                }
            ],
        },
    )

    assert "Computer Use apps" in visible
    assert "vscode" in visible
    assert "VS Code" in visible
    assert "visual studio code" not in visible
    assert "windows" not in visible
    _assert_not_json_wrapper(visible)
