from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core import native_tools
from core.model_governance_exceptions import ModelGovernanceInterventionRequired


def _list_ask_user_records(records):
    def _list(*, session_id=None, run_id=None, status=None):
        result = []
        for record in records:
            if session_id and record.get("session_id") != session_id:
                continue
            if run_id and record.get("run_id") != run_id:
                continue
            if status and record.get("status") != status:
                continue
            result.append(record)
        return result

    return _list


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
        side_effect=_list_ask_user_records([pending]),
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
        side_effect=_list_ask_user_records([pending]),
    ), patch.object(native_tools, "interrupt") as interrupt_mock:
        result = native_tools.ask_user.func("这个问题已在等待", tool_call_id="call_same")

    payload = json.loads(result)
    assert payload["ok"] is False
    assert payload["error"] == "ask_user_already_pending"
    assert payload["interactionId"] == "ask_same"
    interrupt_mock.assert_not_called()


def test_ask_user_reuses_resolved_answer_for_same_tool_call() -> None:
    resolved = {
        "id": "ask_answered",
        "session_id": "session-1",
        "run_id": "run-1",
        "tool_call_id": "call_same",
        "question": "这个问题已经回答过",
        "answer_text": "使用最小静态页面，只写 index.html 和 README.md。",
        "status": "resolved",
    }

    with patch.object(native_tools, "get_runtime_context", return_value={"session_id": "session-1", "run_id": "run-1"}), patch.object(
        native_tools.db,
        "list_ask_user_interactions",
        side_effect=_list_ask_user_records([resolved]),
    ), patch.object(native_tools, "interrupt") as interrupt_mock:
        result = native_tools.ask_user.func("这个问题已经回答过", tool_call_id="call_same")

    assert result == "使用最小静态页面，只写 index.html 和 README.md。"
    interrupt_mock.assert_not_called()


def test_ask_user_runtime_gate_unavailable_raises_governance_with_spec_context() -> None:
    spec_context = {
        "kind": "spec_clarification",
        "featureName": "spec-mode-live-counter",
        "stage": "requirements",
        "workspacePath": "E:/Projects/test3",
    }

    with patch.object(
        native_tools,
        "get_runtime_context",
        return_value={"session_id": "session-1", "run_id": "run-1", "agent_id": "supervisor", "runtime_kind": "chat", "node": "supervisor"},
    ), patch.object(
        native_tools.db,
        "list_ask_user_interactions",
        return_value=[],
    ), patch.object(
        native_tools,
        "interrupt",
        side_effect=RuntimeError("__pregel_scratchpad missing"),
    ):
        with pytest.raises(ModelGovernanceInterventionRequired) as exc_info:
            native_tools.ask_user.func(
                "请确认需求边界",
                specContext=spec_context,
                tool_call_id="call_ask_spec",
            )

    payload = exc_info.value.to_request_payload()
    assert payload["approvalKind"] == "ask_user"
    assert payload["interactionKind"] == "ask_user"
    assert payload["toolCallId"] == "call_ask_spec"
    assert payload["specContext"] == spec_context
    assert payload["details"]["specContext"] == spec_context
