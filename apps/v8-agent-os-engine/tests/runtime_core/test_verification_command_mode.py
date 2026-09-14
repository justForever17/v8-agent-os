import asyncio
import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command

from core.delegation_broker import normalize_task_brief
from graph.parallel_support import _run_parallel_agent_branch
from graph.tool_routing import create_routed_tool_node


@pytest.mark.parametrize("supplied,declared,expected,backend", [
    ({"mode": "session", "terminal_mode": "pipe", "timeout_seconds": 210}, None,
     {"mode": "session", "terminal_mode": "pipe", "timeout_seconds": 210}, "session"),
    ({"mode": "auto"}, None, {"mode": "auto"}, "sync"),
    ({"mode": "sync", "timeout_seconds": 30}, None, {"mode": "sync", "timeout_seconds": 30}, "sync"),
    ({"mode": "sync", "terminal_mode": "pty", "timeout_seconds": 30},
     {"mode": "session", "terminal_mode": "pipe", "timeout_seconds": 210},
     {"mode": "session", "terminal_mode": "pipe", "timeout_seconds": 210}, "session"),
    ({}, None, {}, "sync"),
])
def test_exact_command_keeps_native_mode_until_explicit_task_params_override(monkeypatch, supplied, declared, expected, backend):
    observed, started, summary = execute_branch(monkeypatch, supplied, declared)
    assert len(observed) == 1
    assert {key: observed[0][key] for key in ("command", "mode", "terminal_mode", "timeout_seconds")} == {
        "command": "python -B -u validate_a.py", "mode": "auto", "terminal_mode": "auto", "timeout_seconds": None, **expected}
    assert started[0]["backend"] == backend
    assert started[0]["timeout_seconds"] == expected.get("timeout_seconds")
    assert summary["status"] == "ok"


def test_actual_timeout_receipt_still_fails_verification(monkeypatch):
    observed, started, summary = execute_branch(monkeypatch, {"mode": "sync", "timeout_seconds": 90}, None, timed_out=True)
    assert observed[0]["timeout_seconds"] == 90 and started[0]["backend"] == "sync"
    assert summary["status"] != "ok"
    assert summary["verificationEvidence"]["passed"] is False


def execute_branch(monkeypatch, supplied, declared, *, timed_out=False):
    import core.native_tools as native
    import core.tools.native.command as command
    observed, started = [], []
    exact = "python -B -u validate_a.py"
    original = command.run_system_command.func
    def invoke(**kwargs):
        observed.append({key: value for key, value in kwargs.items() if key != "tool_call_id"})
        return original(**kwargs)
    monkeypatch.setattr(command.run_system_command, "func", invoke)
    def sync(**kwargs):
        started.append({"backend": "sync", **kwargs})
        return json.dumps({"ok": not timed_out, "kind": "command_result", "returnCode": 1 if timed_out else 0,
                           "error": "Command timed out after 90 seconds." if timed_out else None, "stdout": "verified"})
    monkeypatch.setattr(command.execute_system_command, "func", sync)
    def session(command_text, **kwargs):
        started.append({"backend": "session", "command": command_text, **kwargs})
        return {"commandId": "native-session", "interactive": False, "profile": "auto", "runId": None,
                "status": {"is_running": False, "return_code": 0, "timeout_seconds": kwargs.get("timeout_seconds")}}
    monkeypatch.setattr(command, "_launch_background_command", session)
    monkeypatch.setattr(native, "_launch_background_command", session)
    context = {"verificationCommand": exact}
    if declared:
        context["runSystemCommandParams"] = {"command": exact, **declared}
    brief = normalize_task_brief({"taskBriefId": "verify", "readOnly": True, "writeRequired": False, "writeSet": [],
        "context": context, "toolPolicy": {"mode": "allowlist", "allowedTools": ["run_system_command"]}})
    calls = []
    def model(state):
        calls.append(state)
        if len(calls) == 1:
            return Command(goto="worker_tools", update={"messages": [AIMessage(content="", tool_calls=[{
                "id": "exact", "name": "run_system_command", "args": {"command": exact, **deepcopy(supplied)}}])]})
        return Command(goto="supervisor", update={"messages": [AIMessage(content="Verification returned its observed outcome.")]})
    _, _, summary, _ = asyncio.run(_run_parallel_agent_branch({"messages": [], "todos": [], "parallel_branch": {
        "agentId": "worker", "delegationId": "verify-command-modes", "taskBriefId": "verify", "taskBrief": brief,
        "allowChildDelegation": False}}, {"node_func": model, "tools": [command.run_system_command],
        "tool_node_func": create_routed_tool_node([command.run_system_command], "worker_tools", "worker")}))
    return observed, started, summary
