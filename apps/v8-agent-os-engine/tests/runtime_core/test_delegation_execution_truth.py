import asyncio
from copy import deepcopy
from contextlib import nullcontext
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from core.delegation_broker import normalize_task_brief
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_episodes import build_runtime_episode
from core.tool_surface import apply_tool_surface_budget
from graph.parallel_support import (
    _run_parallel_agent_branch,
    _validate_required_verification_evidence,
    _verification_expectations,
)


def task(*, capsule=False):
    value = {"taskBriefId": "verify", "goal": "Verify the assigned input",
        "context": {"runSystemCommandParams": {"command": "python -B -u verify.py",
            "mode": "session", "terminal_mode": "pipe", "timeout_seconds": 210}},
        "expectedOutputs": ["Execution evidence"], "acceptanceContract": ["Return the observed outcome"],
        "toolPolicy": {"mode": "allowlist", "allowedTools": ["run_system_command", "command_session_broker"]}}
    if capsule:
        value.update(readOnly=True, writeRequired=False, readSet=["verify.py"], writeSet=[])
    return normalize_task_brief(value)


def call(name, call_id, args):
    return AIMessage(content="", tool_calls=[{"name": name, "id": call_id, "args": args}])


def result(name, call_id, payload):
    return ToolMessage(name=name, tool_call_id=call_id, content=json.dumps(payload))


def test_context_command_is_required_even_when_typed_capsule_was_omitted():
    brief = task()
    original = deepcopy(brief)
    expectations = _verification_expectations({"taskBrief": brief})
    assert expectations["requiredCommands"] == ["python -B -u verify.py"]
    failure = _validate_required_verification_evidence(branch={"taskBrief": brief}, delta_messages=[])
    assert failure["missingVerificationTools"] == ["run_system_command"]
    assert failure["verificationEvidence"]["passed"] is False
    assert brief == original and "engineeringTaskCapsule" not in brief


@pytest.mark.parametrize("text", ["Everything is complete", "No execution is possible", "Status: BLOCKER"])
def test_missing_command_never_settles_success_based_on_answer_wording(text):
    brief = task()
    branch = {"agentId": "verifier", "agentName": "Verifier", "delegationId": "fixture-delegation",
        "taskBriefId": "verify", "taskBrief": brief, "reason": brief["goal"]}
    def node(_state):
        return Command(goto="supervisor", update={"messages": [HumanMessage(content=text,
            additional_kwargs={"v8_subagent_result_text": text, "v8_governance_type": "delegation_result",
                "v8_owner_agent_id": "verifier", "v8_owner_delegation_id": "fixture-delegation",
                "v8_tool_surface": ["delegation_broker"]})]})
    _, _, summary, _ = asyncio.run(_run_parallel_agent_branch(
        {"messages": [], "todos": [], "parallel_branch": branch},
        agent_data={"node_func": node, "tool_mode": "contextual_auto", "tools": []}))
    assert summary["status"] in {"failed", "blocked"}
    assert summary["missingVerificationTools"] == ["run_system_command"]


def test_non_command_advice_still_finishes_without_shell_evidence():
    brief = normalize_task_brief({"taskBriefId": "advice", "readOnly": True,
        "expectedOutputs": ["Review"], "acceptanceContract": ["Explain the tradeoffs"]})
    assert _validate_required_verification_evidence(branch={"taskBrief": brief}, delta_messages=[]) is None


@pytest.mark.parametrize("surface", [False, True])
def test_command_session_requires_matched_terminal_observation(surface):
    brief = task()
    launch = call("run_system_command", "launch", brief["context"]["runSystemCommandParams"])
    started = result("run_system_command", "launch", {"ok": True, "kind": "command_session",
        "commandId": "session-one", "state": "running", "command": "python -B -u verify.py"})
    observe = call("command_session_broker", "observe", {"mode": "observe", "command_id": "session-one"})
    ended = result("command_session_broker", "observe", {"ok": True, "kind": "command_session",
        "commandId": "session-one", "state": "completed", "returnCode": 0,
        "command": "python -B -u verify.py", "finalPreview": "verified"})
    wrong = result("command_session_broker", "observe", {"ok": True, "kind": "command_session",
        "commandId": "another-session", "state": "completed", "returnCode": 0})
    if surface:
        started, ended, wrong = [apply_tool_surface_budget(m, {"agentVisibleBudget": 4000}) for m in (started, ended, wrong)]
    check = lambda messages: _validate_required_verification_evidence(branch={"taskBrief": brief}, delta_messages=messages)
    assert check([launch, started]) is not None
    assert check([launch, started, observe, wrong]) is not None
    assert check([observe, ended]) is not None
    assert check([launch, started, observe, ended.model_copy(update={"status": "error"})]) is not None
    assert check([launch, started, ended, observe]) is not None
    assert check([launch, started, observe, ended]) is None


def test_explicit_session_parameters_are_preserved_before_toolnode():
    brief = task()
    branch = {"agentId": "verifier", "agentName": "Verifier", "delegationId": "fixture-session",
        "taskBriefId": "verify", "taskBrief": brief, "reason": brief["goal"]}
    seen = []
    def node(state):
        if not seen:
            return Command(goto="verifier_tools", update={"messages": [call("run_system_command", "run", brief["context"]["runSystemCommandParams"])]})
        return Command(goto="supervisor", update={"messages": [AIMessage(content="Verification returned.")]})
    async def tool_node(state, **_kwargs):
        seen.append(deepcopy(state["messages"][-1].tool_calls[0]["args"]))
        return Command(goto="verifier", update={"messages": [result("run_system_command", "run", {"ok": True, "kind": "command_result", "returnCode": 0})]})
    asyncio.run(_run_parallel_agent_branch({"messages": [], "todos": [], "parallel_branch": branch},
        agent_data={"node_func": node, "tool_node_func": tool_node, "tools": [], "tool_mode": "contextual_auto"}))
    assert seen == [brief["context"]["runSystemCommandParams"]]


def test_missing_read_allowlist_returns_parent_repair_without_granting_it():
    brief = task(capsule=True)
    before = deepcopy(brief)
    failure = _validate_required_verification_evidence(branch={"taskBrief": brief}, delta_messages=[])
    assert failure["status"] == "blocked"
    assert failure["executionContractRepair"] == {
        "required": True, "capsuleMissing": False, "unavailableTools": ["read_native_file"]}
    assert brief == before


def test_parent_inspect_keeps_execution_gap_through_delegation_renderer(monkeypatch):
    import core.runtime_episode_control as control
    gap = _validate_required_verification_evidence(branch={"taskBrief": task(capsule=True)}, delta_messages=[])
    monkeypatch.setattr(control, "db", SimpleNamespace(
        get_runtime_episode=lambda _id: {"id": "fixture", "session_id": "s", "run_id": "r", "state": "degraded"},
        list_runtime_episode_messages=lambda **_kw: [],
        get_connection=lambda: nullcontext(SimpleNamespace(execute=lambda *_a: SimpleNamespace(fetchone=lambda: None))),
        list_runtime_episode_handoffs=lambda _id: [{"payload": {"handoffRefId": "h", "status": "degraded", "results": [gap]}}]))
    payload = {"ok": True, "mode": "inspect", **control.inspect_episode("fixture", session_id="s", run_id="r")}
    rendered = apply_tool_surface_budget(result("delegation_broker", "inspect", payload), {"agentVisibleBudget": 4000})
    assert "executionContractRepair" in rendered.content
    assert '"unavailableTools":["read_native_file"]' in rendered.content
    assert "Supervisor must repair the typed Capsule/toolPolicy" in rendered.content


def test_worker_return_does_not_auto_pass_acceptance_tiers(monkeypatch):
    from core.native_tools import delegation_broker
    episode = build_runtime_episode(need={"kind": "delegation", "source": "test", "reason": "Review"},
        kind="delegation", state="queued", continuation_target="runtime_episode_runner",
        extra={"inputs": {"workerBriefs": [{"taskBriefId": "advice", "goal": "Review",
            "acceptanceTiers": {"must": ["Sound reasoning"], "should": ["Limitations"], "nice": ["Examples"]}}]}})
    monkeypatch.setattr(delegation_broker, "func", lambda **_kwargs: Command(update={
        "parallel_results": [{"status": "ok", "taskBriefId": "advice", "delegationId": "result-1",
            "supervisorAcceptance": {"status": "pending"}, "resultText": "Review is ready."}]}))
    handoff = asyncio.run(RuntimeEpisodeRunner()._execute_delegation(episode))
    assert handoff["status"] == "ready"
    assert all(tier["passed"] is None and tier["status"] == "pending" for tier in handoff["acceptanceCheck"].values())
