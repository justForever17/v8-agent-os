from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from core.agents import default_subagent_configs
from core.delegation_broker import (
    build_workset_dispatch_decisions,
    choose_best_local_agent_with_diagnostics,
    normalize_task_brief,
)
from core.engineering_capsule import (
    derive_grandchild_engineering_task,
    engineering_capsule_mode,
)
from core.engineering_kernel import build_engineering_kernel_context, detect_command_environment
from core.runtime_tool_access import filter_visible_tools_for_actor
from core.tools.native.command import run_system_command
from core.tools.native import delegation as native_delegation
from core.tools.native.delegation import request_peer_help
from core.tools.native.workspace_file import write_native_file
from erc.runtime_context import bind_runtime_context
from graph.parallel_support import _runtime_context_from_parallel_state


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _write_task(workspace: Path) -> dict:
    return normalize_task_brief(
        {
            "taskBriefId": "task-write",
            "goal": "Implement the requested page.",
            "context": {"workspacePath": str(workspace)},
            "writeSet": ["src/page.tsx"],
            "expectedOutputs": ["src/page.tsx"],
            "acceptanceContract": "The page builds and the requested interaction works.",
            "verificationMatrix": ["run the focused component test"],
            "proofExpectations": ["test output", "file artifact"],
            "writeRequired": True,
        }
    )


def test_engineering_kernel_publishes_workspace_and_detected_shell(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    text, diagnostics = build_engineering_kernel_context(
        state={
            "session_id": "session-kernel",
            "run_id": "run-kernel",
            "workspace_path": str(tmp_path),
            "workspace_id": "workspace-kernel",
        },
        session_id="session-kernel",
    )

    command_environment = detect_command_environment()
    assert "[ENGINEERING KERNEL]" in text
    assert str(tmp_path) in text
    assert f"shellDialect={command_environment['shellDialect']}" in text
    assert "read_only_no_capsule" in text
    assert "workspace_broker" not in text
    assert diagnostics[0]["executionMode"] == "none"


def test_supervisor_prompt_does_not_advertise_direct_write_without_capsule(tmp_path: Path) -> None:
    content = (Path(__file__).parents[2] / "graph" / "supervisor_context.py").read_text(encoding="utf-8")

    assert "read_only_no_capsule" in content
    assert "route one typed Engineering episode" in content
    assert "prefer `write_native_file` directly" not in content


def test_valid_write_contract_derives_capsule_and_invalid_contract_is_blocked(tmp_path: Path) -> None:
    valid = _write_task(tmp_path)
    capsule = valid["engineeringTaskCapsule"]

    assert capsule["executionMode"] == "write"
    assert capsule["contractStatus"] == "valid"
    assert capsule["shellDialect"] == detect_command_environment()["shellDialect"]
    assert capsule["writeSet"] == ["src/page.tsx"]
    assert capsule["expectedOutputs"] == ["src/page.tsx"]
    assert capsule["expectedArtifacts"] == ["src/page.tsx"]

    invalid = normalize_task_brief(
        {
            "taskBriefId": "task-invalid",
            "goal": "Write the page.",
            "writeRequired": True,
            "writeSet": ["src/page.tsx"],
        }
    )
    invalid_capsule = invalid["engineeringTaskCapsule"]
    assert invalid_capsule["executionMode"] == "read_only"
    assert invalid_capsule["contractStatus"] == "invalid"
    assert invalid_capsule["missingContractFields"] == ["expectedOutputs", "acceptanceContract"]

    decision = build_workset_dispatch_decisions([invalid], auto_dispatch=False)[0]
    assert decision["blocked"] is True
    assert decision["reason"] == "engineering_task_capsule_incomplete"


def test_human_readable_expected_outputs_are_not_treated_as_artifact_paths(tmp_path: Path) -> None:
    task = normalize_task_brief(
        {
            "taskBriefId": "task-human-output",
            "goal": "Create and verify result.txt.",
            "context": {"workspacePath": str(tmp_path)},
            "writeRequired": True,
            "writeSet": ["result.txt"],
            "expectedOutputs": [
                "result.txt exists in the workspace root",
                "read-back content exactly matches the requested value",
            ],
            "acceptanceContract": "The file exists and its content matches exactly.",
        }
    )

    capsule = task["engineeringTaskCapsule"]
    assert capsule["contractStatus"] == "valid"
    assert capsule["expectedOutputs"] == [
        "result.txt exists in the workspace root",
        "read-back content exactly matches the requested value",
    ]
    assert capsule["expectedArtifacts"] == ["result.txt"]


def test_declared_file_mutation_tool_requires_a_complete_write_capsule() -> None:
    task = normalize_task_brief(
        {
            "taskBriefId": "implicit-write-tool",
            "goal": "Create the requested result.",
            "allowedTools": ["write_native_file", "read_native_file"],
            "expectedOutputs": ["result.txt"],
            "acceptanceContract": "result.txt exists with the expected content.",
        }
    )

    capsule = task["engineeringTaskCapsule"]
    decision = build_workset_dispatch_decisions([task])[0]
    assert capsule["writeRequired"] is True
    assert capsule["contractStatus"] == "invalid"
    assert capsule["missingContractFields"] == ["writeSet"]
    assert decision["blocked"] is True


def test_write_capsule_routes_away_from_review_and_verification_agents(tmp_path: Path) -> None:
    task = _write_task(tmp_path)
    task["familyHint"] = "engineering"
    task["preferredAgentId"] = "verification-engineer"
    agents = [item.model_dump() for item in default_subagent_configs()]
    agents.sort(key=lambda item: 0 if item["id"] in {"code-review-architect", "verification-engineer"} else 1)

    selected, diagnostics = choose_best_local_agent_with_diagnostics(task, agents)

    assert selected is not None
    assert selected["capabilitySnapshot"]["agentClass"] == "executor"
    assert selected["id"] not in {"code-review-architect", "verification-engineer"}
    assert "preferredAgentId_incompatible_with_write:verification-engineer" in diagnostics["matchSignals"]


def test_verification_signal_does_not_override_write_capsule_target() -> None:
    tasks = native_delegation._apply_delegation_target_defaults(
        [
            normalize_task_brief(
                {
                    "taskBriefId": "write-and-verify",
                    "goal": "Create result.txt and verify the exact contents.",
                    "familyHint": "engineering",
                    "writeRequired": True,
                    "writeSet": ["result.txt"],
                    "expectedOutputs": ["result.txt"],
                    "acceptanceContract": "The file exists and its contents match exactly.",
                }
            )
        ]
    )

    assert tasks[0].get("preferredAgentId") != "verification-engineer"


def test_no_capsule_projects_only_read_tools_for_supervisor_and_subagent(tmp_path: Path) -> None:
    tools = [
        _tool("delegation_broker"),
        _tool("read_native_file"),
        _tool("grep_search"),
        _tool("write_native_file"),
        _tool("run_system_command"),
        _tool("command_session_broker"),
    ]

    supervisor_names = {
        tool.name
        for tool in filter_visible_tools_for_actor(tools, actor="supervisor", route_context={})
    }
    assert supervisor_names == {"delegation_broker", "read_native_file", "grep_search"}

    read_task = normalize_task_brief({"goal": "Inspect the code."})
    subagent_names = {
        tool.name
        for tool in filter_visible_tools_for_actor(
            tools,
            actor="subagent",
            route_context={"taskBrief": read_task},
        )
    }
    assert subagent_names == {"read_native_file", "grep_search"}

    write_task = _write_task(tmp_path)
    write_names = {
        tool.name
        for tool in filter_visible_tools_for_actor(
            tools,
            actor="subagent",
            route_context={"taskBrief": write_task},
        )
    }
    assert {"write_native_file", "run_system_command", "command_session_broker"}.issubset(write_names)


def test_native_guards_reject_subagent_mutation_without_capsule(tmp_path: Path) -> None:
    with bind_runtime_context(
        runtime_kind="subagent",
        session_id="session-read-only",
        run_id="run-read-only",
        workspace_path=str(tmp_path),
        workspace_id="workspace-read-only",
        project_id="project-read-only",
        engineering_capsule_mode="none",
    ):
        write_payload = json.loads(write_native_file.func("new.txt", "blocked"))
        command_payload = json.loads(run_system_command.func("echo should-not-run"))

    assert write_payload["kind"] == "write_set_scope_block"
    assert write_payload["engineeringCapsuleMode"] == "none"
    assert command_payload["kind"] == "engineering_capsule_required"
    assert not (tmp_path / "new.txt").exists()


def test_parallel_runtime_context_projects_capsule_mode_and_write_scope(tmp_path: Path) -> None:
    write_task = _write_task(tmp_path)
    context = _runtime_context_from_parallel_state(
        {
            "session_id": "session-child",
            "run_id": "run-child",
            "workspace_path": str(tmp_path),
        },
        branch={"agentId": "engineer", "taskBrief": write_task},
    )

    assert context["engineering_capsule_mode"] == "write"
    assert context["engineering_capsule_id"].startswith("engcap_")
    assert context["allowed_write_paths"] == ["src/page.tsx"]


def test_grandchild_capsule_preserves_parent_contract_without_write_authority(tmp_path: Path) -> None:
    parent = _write_task(tmp_path)
    child = derive_grandchild_engineering_task(
        parent,
        normalize_task_brief(
            {
                "taskBriefId": "task-grandchild",
                "goal": "Inspect the component contract and return evidence.",
                "readSet": ["src/component.tsx"],
            }
        ),
        shell_dialect=detect_command_environment()["shellDialect"],
    )

    assert engineering_capsule_mode(child) == "read_only"
    assert child["writeSet"] == []
    assert "src/page.tsx" in child["readSet"]
    inherited = child["context"]["inheritedEngineeringContract"]
    assert inherited["writeSet"] == ["src/page.tsx"]
    assert inherited["acceptance"] == "The page builds and the requested interaction works."
    assert inherited["proofExpectations"] == ["test output", "file artifact"]


def test_peer_help_derives_read_only_grandchild_capsule(tmp_path: Path) -> None:
    parent = _write_task(tmp_path)
    command = request_peer_help.func(
        needed_capabilities=["code_review"],
        reason="Review the implementation against the acceptance contract.",
        context="Focus on the changed component.",
        state={
            "parallel_branch": {
                "agentId": "engineer",
                "delegationId": "delegation-parent",
                "taskBriefId": parent["taskBriefId"],
                "taskBrief": parent,
                "allowChildDelegation": True,
                "delegationDepth": 0,
            }
        },
        tool_call_id="call-peer-help",
    )
    pending = command.update["pending_child_delegations"][0]
    child = pending["childTaskBrief"]

    assert child["engineeringTaskCapsule"]["executionMode"] == "read_only"
    assert child["engineeringTaskCapsule"]["parentCapsuleId"] == parent["engineeringTaskCapsule"]["capsuleId"]
    assert child["writeSet"] == []
    assert pending["childBranch"]["runtimeAccess"] == []
