from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from erc.runtime_context import bind_runtime_context
from core.tool_surface import apply_tool_surface_budget, record_raw_observation


def test_tool_surface_generic_json_preserves_refs_and_next_action_without_raw_json():
    payload = {
        "ok": True,
        "runId": "run_123",
        "recommendedNextAction": "inspect raw evidence",
        "items": [{"text": "x" * 2000} for _ in range(20)],
    }
    message = ToolMessage(
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id="call_123",
        name="experimental_json_tool",
    )

    result = apply_tool_surface_budget(
        message,
        {
            "runId": "run_123",
            "agentVisibleBudget": 1400,
            "hardMaxChars": 15000,
            "targetChars": 12000,
            "budgetSource": "unit_test",
        },
    )

    rendered = str(result.content)
    assert rendered.startswith("experimental json tool result")
    assert "_v8ToolSurface" not in rendered
    assert not rendered.lstrip().startswith("{")
    assert "Items:" in rendered
    assert "inspect raw evidence" in rendered
    assert "tool_observation_detail(raw_ref='toolobs://" in rendered
    assert result.response_metadata["v8_tool_output_budget"]["semanticTruncationStrategy"] == "decision_summary_surface"


def test_plugin_status_surface_keeps_on_demand_cli_help_and_real_extension_summaries():
    message = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "mode": "status",
                "items": [
                    {
                        "pluginId": "github",
                        "name": "GitHub CLI",
                        "status": "ready",
                        "authorized": False,
                        "components": [
                            {"id": "gh", "type": "cli"},
                            {"id": "github-cli-skill", "type": "skill"},
                        ],
                        "usage": {
                            "transport": "cli",
                            "cli": [
                                {
                                    "componentId": "gh",
                                    "command": "gh",
                                    "help": "Work seamlessly with GitHub from the command line.",
                                    "available": True,
                                }
                            ],
                            "skills": [
                                {
                                    "componentId": "github-cli-skill",
                                    "name": "gh",
                                    "summary": "Official GitHub CLI usage patterns.",
                                }
                            ],
                            "mcpTools": [],
                        },
                    }
                ],
                "nextAction": "Authorize component gh, then call plugin_cli with an actionId and typed parameters.",
            },
            ensure_ascii=False,
        ),
        tool_call_id="call-plugin-status",
        name="plugin_broker",
    )

    result = apply_tool_surface_budget(message, {"agentVisibleBudget": 4000})
    rendered = str(result.content)

    assert "Command: gh" in rendered
    assert "authorize the smallest component scope" in rendered
    assert "plugin_cli" in rendered
    assert "without a plugin grant" not in rendered
    assert "Work seamlessly with GitHub" in rendered
    assert "Grant component IDs (not runtime names)" in rendered
    assert "Skill name=gh; grant component=github-cli-skill" in rendered
    assert "Official GitHub CLI usage patterns." in rendered
    assert "run_system_command" not in rendered
    assert not rendered.lstrip().startswith("{")


def test_tool_observation_detail_json_is_readable_without_recursive_raw_ref():
    message = ToolMessage(
        content=json.dumps(
            {
                "ok": False,
                "error": "raw_ref_not_found",
                "rawRef": "toolobs://missing",
            }
        ),
        tool_call_id="call_detail",
        name="tool_observation_detail",
    )

    result = apply_tool_surface_budget(message, {"agentVisibleBudget": 1000})

    rendered = str(result.content)
    assert rendered.startswith("tool observation detail result")
    assert "Status: failed" in rendered
    assert "raw_ref_not_found" in rendered
    assert not rendered.lstrip().startswith("{")
    assert "tool_observation_detail(raw_ref=" not in rendered
    assert "rawRef" not in result.response_metadata["v8_tool_output_budget"]


def test_tool_observation_detail_long_text_stays_text_when_truncated():
    message = ToolMessage(
        content="Tool observation detail\nrawRef: toolobs://source\n\n" + ("readable line\n" * 200),
        tool_call_id="call_detail_long",
        name="tool_observation_detail",
    )

    result = apply_tool_surface_budget(message, {"agentVisibleBudget": 500})

    rendered = str(result.content)
    metadata = result.response_metadata["v8_tool_output_budget"]
    assert not rendered.lstrip().startswith("{")
    assert "tool observation detail truncated" in rendered
    assert "tool_observation_detail(raw_ref=" not in rendered
    assert "rawRef" not in metadata
    assert metadata["semanticTruncationStrategy"] == "tool_observation_detail_surface"


def test_delegation_surface_keeps_worker_result_and_hides_runtime_observation_ref():
    message = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "mode": "observe",
                "summary": "Collected one local subagent result.",
                "items": [
                    {
                        "taskBriefId": "task-1",
                        "targetLabel": "Reviewer",
                        "status": "ok",
                        "toolPolicy": {"mode": "none", "allowedTools": [], "forbiddenTools": []},
                        "resultText": "OWNER_ISOLATION_OK",
                        "summary": "Review complete.",
                        "localSelfCheck": "Verified the requested slice.",
                        "acceptanceHint": "Accept, retry, or ignore.",
                    }
                ],
                "recommendedNextAction": "accept_retry_or_ignore",
            },
            ensure_ascii=False,
        ),
        tool_call_id="call-delegation",
        name="delegation_broker",
    )

    result = apply_tool_surface_budget(message, {"agentVisibleBudget": 3000})

    rendered = str(result.content)
    assert "Reviewer" in rendered
    assert "Tool authority: none" in rendered
    assert "Exact result: OWNER_ISOLATION_OK" in rendered
    assert "Review complete." in rendered
    assert "Verified the requested slice." in rendered
    assert "Accept, retry, or ignore." in rendered
    assert "tool_observation_detail(raw_ref=" not in rendered
    assert "toolobs://" not in rendered


def test_session_coordination_surface_preserves_exact_ask_user_authorization_args():
    message = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "tool": "session_message_broker",
                "authorizationRequired": True,
                "summary": "Send a correction to the target task.",
                "message": {
                    "messageId": "coord-001",
                    "targetSessionId": "session-target-001",
                    "state": "awaiting_authorization",
                    "intent": "correct",
                    "hopCount": 1,
                    "maxHops": 2,
                },
                "askUserRequest": {
                    "question": "Allow this cross-task message?",
                    "details": "Send a correction to the target task.",
                    "questions": [
                        {
                            "id": "session_coordination_authorization",
                            "options": [
                                {"id": "approve_send", "title": "Allow"},
                                {"id": "reject_send", "title": "Cancel"},
                            ],
                        }
                    ],
                    "selection_mode": "single",
                    "coordinationContext": {
                        "kind": "session_coordination_authorization",
                        "draftMessageId": "coord-001",
                        "messageDigest": "digest-001",
                        "oneShot": True,
                    },
                },
            },
            ensure_ascii=False,
        ),
        tool_call_id="call-session-message",
        name="session_message_broker",
    )

    result = apply_tool_surface_budget(message, {"agentVisibleBudget": 5000})
    rendered = str(result.content)
    assert rendered.startswith("Cross-session Supervisor coordination")
    assert "Required ask_user arguments:" in rendered
    assert '"coordinationContext"' in rendered
    assert '"draftMessageId":"coord-001"' in rendered
    assert "do not claim the message was sent yet" in rendered


def test_record_raw_observation_inherits_runtime_context_metadata(monkeypatch):
    captured: list[dict] = []

    class _FakeObservabilityDb:
        @staticmethod
        def add_tool_observation_record(record: dict) -> None:
            captured.append(record)

    monkeypatch.setattr("core.observability_db.observability_db", _FakeObservabilityDb())

    with bind_runtime_context(
        session_id="session_surface",
        run_id="run_surface",
        workspace_path="E:/Projects/test2",
    ):
        raw_ref = record_raw_observation(
            tool_name="web_broker",
            tool_call_id="call_surface",
            runtime_kind="native",
            surface="tool_node",
            raw_content="raw",
            visible_content="visible",
            budget_meta={},
            metadata={},
        )

    assert raw_ref.startswith("toolobs://")
    metadata = captured[0]["metadata"]
    assert metadata["sessionId"] == "session_surface"
    assert metadata["runId"] == "run_surface"
    assert metadata["workspacePath"] == "E:/Projects/test2"


def test_tool_surface_persists_agent_visible_markdown_with_session_identity(tmp_path, monkeypatch):
    from core.observability_db import ObservabilityDatabaseManager

    observation_db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    monkeypatch.setattr("core.observability_db.observability_db", observation_db)
    message = ToolMessage(
        content=json.dumps(
            {
                "ok": True,
                "mode": "read",
                "title": "Official runtime guide",
                "finalUrl": "https://example.com/runtime",
                "text": "The durable episode returns a typed handoff with proof.",
                "providerAttemptMatrix": [{"provider": "internal-only"}],
            },
            ensure_ascii=False,
        ),
        name="web_broker",
        tool_call_id="call-research-surface",
    )

    visible = apply_tool_surface_budget(
        message,
        {
            "sessionId": "session-research-surface",
            "runId": "run-research-surface",
            "workspacePath": "E:/Projects/test2",
            "agentVisibleBudget": 4000,
            "hardMaxChars": 60000,
        },
    )

    records = observation_db.list_tool_observation_records(session_id="session-research-surface", limit=5)
    assert len(records["items"]) == 1
    record = records["items"][0]
    assert record["runId"] == "run-research-surface"
    assert "Web broker (read)" in record["agentVisiblePreview"]
    assert "typed handoff with proof" in record["agentVisiblePreview"]
    assert "providerAttemptMatrix" not in record["agentVisiblePreview"]
    assert str(visible.content) == record["agentVisiblePreview"]


def test_raw_observation_does_not_return_unreadable_ref_when_persistence_fails(monkeypatch):
    class _BrokenObservabilityDb:
        @staticmethod
        def add_tool_observation_record(_record: dict) -> None:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr("core.observability_db.observability_db", _BrokenObservabilityDb())

    raw_ref = record_raw_observation(
        tool_name="research_broker",
        tool_call_id="call-broken-observation",
        runtime_kind="research",
        surface="tool_node",
        raw_content="raw result",
        budget_meta={"sessionId": "session-broken", "runId": "run-broken"},
    )

    assert raw_ref == ""
