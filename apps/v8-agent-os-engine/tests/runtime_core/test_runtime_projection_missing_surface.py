from __future__ import annotations

import json

from core.runtime_projection import project_chat_messages_from_events


def test_tool_finished_without_agent_surface_does_not_project_raw_secret_and_keeps_recovery_ref():
    events = [{
        "event_id": "evt-missing-surface",
        "run_id": "run-missing-surface",
        "topic": "tool.finished",
        "payload": {"tool": {
            "toolName": "delegation_broker",
            "toolCallId": "call-recovery-1",
            "result": json.dumps({
                "ok": True,
                "summary": "Child result is ready for acceptance.",
                "secret": "SYNTHETIC_SECRET",
                "delegationId": "delegation-1",
                "handoffId": "handoff-1",
                "detailRef": "toolobs://detail-1",
            }),
        }},
    }]

    messages = project_chat_messages_from_events(events)
    part = messages[0]["parts"][0]
    assert "SYNTHETIC_SECRET" not in str(part)
    assert "Child result is ready for acceptance." in part["result"]
    assert part["toolCallId"] == "call-recovery-1"
    assert part["detailRef"] == "toolobs://detail-1"
    assert part["delegationId"] == "delegation-1"
    assert part["handoffId"] == "handoff-1"


def test_unknown_tool_without_agent_surface_is_explicitly_degraded_without_raw_json():
    events = [{
        "event_id": "evt-unknown-surface",
        "run_id": "run-unknown-surface",
        "topic": "tool.finished",
        "payload": {"tool": {
            "toolName": "fixture_mcp_lookup",
            "toolCallId": "call-unknown-1",
            "result": json.dumps({"ok": True, "value": "SYNTHETIC_SECRET", "detailRef": "toolobs://detail-2"}),
        }},
    }]
    part = project_chat_messages_from_events(events)[0]["parts"][0]
    assert "SYNTHETIC_SECRET" not in str(part)
    assert "no Agent Surface" in part["result"]
    assert part["detailRef"] == "toolobs://detail-2"
