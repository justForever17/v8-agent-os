from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.agents import build_subagent_registry_snapshot  # noqa: E402
from core.delegation_broker import reveal_subagent_family  # noqa: E402
from core.tools.native import delegation as delegation_tools  # noqa: E402


REPO_ROOT = ENGINE_ROOT.parents[1]
OUTPUT_ROOT = REPO_ROOT / "docs" / "chatruntime" / "runtime_deep_observation_reports"


def _agent(agent_id: str, family: str | None, *, ops: list[str], description: str = "") -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "agentClass": "executor",
        "domainTags": [family or "general"],
        "operationCapabilities": ops,
    }
    if family is not None:
        snapshot["specialistFamily"] = family
    return {
        "id": agent_id,
        "name": agent_id,
        "description": description or f"{agent_id} dry-run specialist",
        "isEnabled": True,
        "capabilitySnapshot": snapshot,
    }


def _payload(command: Any) -> dict[str, Any]:
    message = command.update["messages"][0]
    return json.loads(str(message.content or "{}"))


def _has_direct_target(value: dict[str, Any]) -> bool:
    child_brief = value.get("childTaskBrief") if isinstance(value.get("childTaskBrief"), dict) else {}
    for container in (value, child_brief):
        for key in ("targetAgentId", "childAgentId", "preferredAgentId"):
            if str(container.get(key) or "").strip():
                return True
    return False


def _dispatch_with_storage(agents: list[dict[str, Any]], *, state: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    class _Storage:
        def get_all_agents(self) -> list[dict[str, Any]]:
            return list(agents)

        def get_supervisor_config(self) -> dict[str, Any]:
            return {}

    original_storage = delegation_tools.storage
    native_tools = sys.modules.get("core.native_tools")
    original_native_storage = getattr(native_tools, "storage", None) if native_tools is not None else None
    delegation_tools.storage = _Storage()
    if native_tools is not None and hasattr(native_tools, "storage"):
        native_tools.storage = delegation_tools.storage
    try:
        command = delegation_tools.delegation_broker.func(
            mode="dispatch",
            tasks=[task],
            state=state,
            tool_call_id="dry-run-dispatch",
        )
        return _payload(command)
    finally:
        delegation_tools.storage = original_storage
        if native_tools is not None and hasattr(native_tools, "storage"):
            native_tools.storage = original_native_storage


def build_report() -> dict[str, Any]:
    engineering = _agent("implementation-engineer", "engineering", ops=["implement", "debug", "test"])
    freelancer = _agent("free-helper", None, ops=["triage", "summarize"])
    researcher = _agent("web-research-architect", "research", ops=["research", "source_quality", "cite"])

    initial_agents = [engineering, freelancer]
    next_agents = [engineering, freelancer, researcher]
    initial_snapshot = build_subagent_registry_snapshot(initial_agents)
    next_snapshot = build_subagent_registry_snapshot(next_agents)

    reveal_engineering = reveal_subagent_family("engineering", initial_agents)
    reveal_freelancers = reveal_subagent_family("freelancers", initial_agents)
    stale_dispatch = _dispatch_with_storage(
        next_agents,
        state={"subagent_registry_snapshot": initial_snapshot},
        task={
            "taskBriefId": "task-stale-target",
            "goal": "Use the new research worker.",
            "executionLaneHint": "subagent",
            "preferredAgentId": "web-research-architect",
            "requiredCapabilities": ["research"],
        },
    )
    next_run_dispatch = _dispatch_with_storage(
        next_agents,
        state={"subagent_registry_snapshot": next_snapshot},
        task={
            "taskBriefId": "task-next-run",
            "goal": "Use the new research worker.",
            "executionLaneHint": "subagent",
            "preferredAgentId": "web-research-architect",
            "requiredCapabilities": ["research"],
        },
    )
    peer_help = delegation_tools.request_peer_help.func(
        needed_capabilities=["research", "citations"],
        reason="Need source-backed verification before final handoff.",
        context="Check official docs and return cited evidence.",
        preferred_family="research",
        state={
            "parallel_branch": {
                "agentId": "implementation-engineer",
                "agentName": "Implementation Engineer",
                "invocationId": "invoke-parent",
                "delegationId": "delegation-parent",
                "taskBriefId": "task-parent",
                "allowChildDelegation": True,
                "delegationDepth": 1,
            }
        },
        tool_call_id="dry-run-peer-help",
    )
    pending_help = peer_help.update["pending_child_delegations"][0]

    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "snapshots": {
            "initial": {
                "version": initial_snapshot["version"],
                "hash": initial_snapshot["hash"],
                "agentIds": initial_snapshot["agentIds"],
            },
            "nextRun": {
                "version": next_snapshot["version"],
                "hash": next_snapshot["hash"],
                "agentIds": next_snapshot["agentIds"],
            },
            "hashChangedAfterAdd": initial_snapshot["hash"] != next_snapshot["hash"],
        },
        "reveal": {
            "engineeringMembers": [item["agentId"] for item in reveal_engineering["members"]],
            "freelancerMembers": [item["agentId"] for item in reveal_freelancers["members"]],
        },
        "dispatch": {
            "staleSnapshotStatus": stale_dispatch["items"][0]["status"],
            "staleSnapshotError": stale_dispatch["items"][0].get("error"),
            "staleSnapshotRegistryVersion": stale_dispatch.get("registryVersion"),
            "nextRunStatus": next_run_dispatch["items"][0]["status"],
            "nextRunTarget": next_run_dispatch["items"][0]["targetId"],
            "nextRunRegistryVersion": next_run_dispatch.get("registryVersion"),
        },
        "peerHelp": {
            "requestKind": pending_help["requestKind"],
            "neededCapabilities": pending_help["neededCapabilities"],
            "preferredFamily": pending_help["preferredFamily"],
            "hasDirectTargetAgent": _has_direct_target(pending_help),
            "brokerDecisionRequired": True,
        },
        "acceptance": {
            "newAgentVisibleNextRun": next_run_dispatch["items"][0]["status"] == "queued",
            "staleRunDoesNotSelectNewAgent": stale_dispatch["items"][0].get("error") == "no_matching_target",
            "freelancersIsolated": [item["agentId"] for item in reveal_freelancers["members"]] == ["free-helper"],
            "peerHelpHasNoDirectTarget": not _has_direct_target(pending_help),
        },
    }


def main() -> None:
    report = build_report()
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    json_path = OUTPUT_ROOT / f"{stamp}_subagent_registry_snapshot_dry_run.json"
    md_path = OUTPUT_ROOT / f"{stamp}_subagent_registry_snapshot_dry_run.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(
        "# Subagent Registry Snapshot Dry Run\n\n"
        f"- initial version: `{report['snapshots']['initial']['version']}`\n"
        f"- next-run version: `{report['snapshots']['nextRun']['version']}`\n"
        f"- hash changed after add: `{report['snapshots']['hashChangedAfterAdd']}`\n"
        f"- stale dispatch: `{report['dispatch']['staleSnapshotStatus']}` / `{report['dispatch']['staleSnapshotError']}`\n"
        f"- next-run dispatch target: `{report['dispatch']['nextRunTarget']}`\n"
        f"- freelancers reveal: `{', '.join(report['reveal']['freelancerMembers'])}`\n"
        f"- peer help direct target present: `{report['peerHelp']['hasDirectTargetAgent']}`\n",
        encoding="utf-8",
    )
    print(json.dumps({"json": str(json_path), "markdown": str(md_path), "acceptance": report["acceptance"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
