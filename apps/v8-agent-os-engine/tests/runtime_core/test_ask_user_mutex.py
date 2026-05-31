from __future__ import annotations

import json
from unittest.mock import patch

from core import native_tools


def test_ask_user_blocks_when_same_session_has_pending_interaction() -> None:
    pending = {
        "id": "ask_active",
        "session_id": "session-1",
        "run_id": "run-1",
        "tool_call_id": "call_existing",
        "question": "先回答这个问题",
        "status": "pending",
    }

    with patch.object(native_tools, "get_runtime_context", return_value={"session_id": "session-1", "run_id": "run-2", "agent_id": "subagent-a", "runtime_kind": "delegation"}), patch.object(
        native_tools.db,
        "list_ask_user_interactions",
        return_value=[pending],
    ), patch.object(native_tools, "interrupt") as interrupt_mock:
        result = native_tools.ask_user.func("新的澄清问题", tool_call_id="call_new")

    payload = json.loads(result)
    assert payload["ok"] is False
    assert payload["error"] == "ask_user_blocked_by_active_interaction"
    assert payload["activeInteractionId"] == "ask_active"
    assert payload["requester"]["agentId"] == "subagent-a"
    interrupt_mock.assert_not_called()


def test_ask_user_reports_existing_pending_for_same_tool_call() -> None:
    pending = {
        "id": "ask_same",
        "session_id": "session-1",
        "run_id": "run-1",
        "tool_call_id": "call_same",
        "question": "这个问题已在等待",
        "status": "pending",
    }

    with patch.object(native_tools, "get_runtime_context", return_value={"session_id": "session-1", "run_id": "run-1"}), patch.object(
        native_tools.db,
        "list_ask_user_interactions",
        return_value=[pending],
    ), patch.object(native_tools, "interrupt") as interrupt_mock:
        result = native_tools.ask_user.func("这个问题已在等待", tool_call_id="call_same")

    payload = json.loads(result)
    assert payload["ok"] is False
    assert payload["error"] == "ask_user_already_pending"
    assert payload["interactionId"] == "ask_same"
    interrupt_mock.assert_not_called()
