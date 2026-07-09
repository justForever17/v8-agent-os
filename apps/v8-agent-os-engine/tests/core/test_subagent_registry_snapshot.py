from __future__ import annotations

import json
import sys

from core.agents import (
    agents_from_subagent_registry_snapshot,
    build_subagent_registry_snapshot,
    build_specialist_family_registry,
    ensure_specialist_family,
)
from core.delegation_broker import choose_best_local_agent_with_diagnostics, reveal_subagent_family
from core.tools.native import delegation as delegation_tools


def _agent(agent_id: str, family: str | None, *, ops: list[str] | None = None, enabled: bool = True) -> dict:
    snapshot = {
        "agentClass": "executor",
        "operationCapabilities": ops or ["implement"],
        "domainTags": [family or "general"],
    }
    if family is not None:
        snapshot["specialistFamily"] = family
    return {
        "id": agent_id,
        "name": agent_id,
        "description": f"{agent_id} specialist",
        "isEnabled": enabled,
        "capabilitySnapshot": snapshot,
    }


def _payload_from_command(command) -> dict:
    message = command.update["messages"][0]
    return json.loads(message.content)


def _patch_delegation_storage(monkeypatch, storage) -> None:
    monkeypatch.setattr(delegation_tools, "storage", storage)
    native_tools = sys.modules.get("core.native_tools")
    if native_tools is not None and hasattr(native_tools, "storage"):
        monkeypatch.setattr(native_tools, "storage", storage)


def test_missing_family_defaults_to_freelancers_not_engineering() -> None:
    snapshot = ensure_specialist_family({"agentClass": "executor", "domainTags": ["code", "docs"]})

    assert snapshot["specialistFamily"] == "freelancers"

    registry = build_specialist_family_registry([_agent("free-1", None), _agent("eng-1", "engineering")])
    counts = {item["familyId"]: item["memberCount"] for item in registry}
    assert counts["freelancers"] == 1
    assert counts["engineering"] == 1


def test_registry_snapshot_hash_changes_when_agent_is_added() -> None:
    first = build_subagent_registry_snapshot([_agent("eng-1", "engineering")])
    second = build_subagent_registry_snapshot([_agent("eng-1", "engineering"), _agent("free-1", None)])

    assert first["schemaVersion"] == "v8.subagent_registry_snapshot.v1"
    assert first["hash"] != second["hash"]
    assert first["version"] != second["version"]
    assert "free-1" in second["agentIds"]


def test_reveal_freelancers_and_explicit_family_are_isolated() -> None:
    agents = [_agent("eng-1", "engineering"), _agent("free-1", None)]

    engineering = reveal_subagent_family("engineering", agents)
    freelancers = reveal_subagent_family("freelancers", agents)

    assert [item["agentId"] for item in engineering["members"]] == ["eng-1"]
    assert [item["agentId"] for item in freelancers["members"]] == ["free-1"]


def test_dispatch_uses_run_snapshot_not_new_storage_agent(monkeypatch) -> None:
    old_agent = _agent("eng-1", "engineering", ops=["implement"])
    new_agent = _agent("new-1", "engineering", ops=["implement"])
    run_snapshot = build_subagent_registry_snapshot([old_agent])

    class _Storage:
        def get_all_agents(self):
            return [old_agent, new_agent]

        def get_supervisor_config(self):
            return {}

    _patch_delegation_storage(monkeypatch, _Storage())
    command = delegation_tools.delegation_broker.func(
        mode="dispatch",
        tasks=[{"taskBriefId": "task-1", "goal": "Use the new worker", "preferredAgentId": "new-1", "executionLaneHint": "subagent"}],
        state={"subagent_registry_snapshot": run_snapshot},
        tool_call_id="call-test",
    )

    payload = _payload_from_command(command)
    assert payload["registryVersion"] == run_snapshot["version"]
    assert payload["items"][0]["status"] == "error"
    assert payload["items"][0]["error"] == "no_matching_target"
    assert payload["items"][0]["targetId"] == "new-1"


def test_next_run_snapshot_can_dispatch_new_agent(monkeypatch) -> None:
    new_agent = _agent("new-1", "engineering", ops=["implement"])

    class _Storage:
        def get_all_agents(self):
            return [new_agent]

        def get_supervisor_config(self):
            return {}

    _patch_delegation_storage(monkeypatch, _Storage())
    command = delegation_tools.delegation_broker.func(
        mode="dispatch",
        tasks=[{"taskBriefId": "task-1", "goal": "Use the new worker", "preferredAgentId": "new-1", "executionLaneHint": "subagent"}],
        state={},
        tool_call_id="call-test",
    )

    payload = _payload_from_command(command)
    assert payload["items"][0]["status"] == "queued"
    assert payload["items"][0]["targetId"] == "new-1"
    assert payload["items"][0]["registryVersion"] == payload["registryVersion"]


def test_choose_best_agent_accepts_snapshot_members() -> None:
    snapshot = build_subagent_registry_snapshot([_agent("research-1", "research", ops=["research", "cite"])])
    selected, diagnostics = choose_best_local_agent_with_diagnostics(
        {"goal": "Find cited sources", "familyHint": "research", "requiredCapabilities": ["research"]},
        agents_from_subagent_registry_snapshot(snapshot),
    )

    assert selected is not None
    assert selected["id"] == "research-1"
    assert diagnostics["targetFamily"] == "research"


def test_request_peer_help_only_records_capability_handoff() -> None:
    command = delegation_tools.request_peer_help.func(
        needed_capabilities=["research", "citations"],
        reason="Need source-backed verification.",
        context="Check current docs and return cited evidence.",
        preferred_family="research",
        state={
            "parallel_branch": {
                "agentId": "eng-1",
                "agentName": "Engineering Agent",
                "invocationId": "invoke-1",
                "delegationId": "delegation-1",
                "taskBriefId": "task-parent",
                "allowChildDelegation": True,
                "delegationDepth": 1,
            }
        },
        tool_call_id="call-peer",
    )

    pending = command.update["pending_child_delegations"][0]
    assert pending["requestKind"] == "handoff_request"
    assert pending["neededCapabilities"] == ["research", "citations"]
    assert pending["preferredFamily"] == "research"
    assert "childAgentId" not in pending
    assert "targetAgentId" not in pending["childTaskBrief"]
    assert not pending["childTaskBrief"].get("preferredAgentId")
    assert pending["childTaskBrief"]["familyHint"] == "research"


def test_request_peer_help_without_child_budget_is_blocked_not_dispatched() -> None:
    command = delegation_tools.request_peer_help.func(
        needed_capabilities="research",
        reason="Need another worker.",
        state={"parallel_branch": {"agentId": "eng-1", "allowChildDelegation": False}},
        tool_call_id="call-peer",
    )

    payload = _payload_from_command(command)
    assert payload["ok"] is False
    assert payload["error"] == "child_delegation_not_allowed"
    assert "pending_child_delegations" not in command.update
