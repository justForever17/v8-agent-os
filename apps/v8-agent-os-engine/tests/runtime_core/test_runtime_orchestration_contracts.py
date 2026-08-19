from __future__ import annotations

import json
import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import ValidationError

import core.runtime_episode_runner as runner_module
import core.tools.native.command as command_module
import runtimes.chat.supervisor_completion_gate as completion_gate_module
from core.delegation_broker import normalize_task_brief
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.runtime_episodes import ACTIVE_EPISODE_STATES
from core.tools.native.delegation import _inject_inherited_handoffs_into_tasks
from core.tools.native.command_governance import _windows_shell_syntax_violation_payload
from core.tools.native.runtime import RuntimeRouteTaskBrief
from graph.parallel_support import (
    _repeat_sensitive_tool_call_signature,
    _subagent_timeline_nodes_from_message,
    build_parallel_delegate_task_node,
)
from graph.workflow_assembly import _route_runtime_tool_commands, _workflow_entry_command
from runtimes.chat.supervisor_completion_gate import evaluate_supervisor_completion


def test_subagent_timeline_projection_keeps_readable_activity_and_redacts_secrets() -> None:
    assistant = AIMessage(
        id="child-message-1",
        content="已核对 README，并准备读取配置。",
        additional_kwargs={"reasoning_content": "先比较两个文件的公开字段。"},
        tool_calls=[{
            "id": "child-tool-1",
            "name": "read_native_file",
            "args": {"path": "README.md", "api_key": "sk-private-test-value"},
        }],
    )

    nodes = _subagent_timeline_nodes_from_message(assistant)

    assert [node["topic"] for node in nodes] == [
        "subagent.reasoning.delta",
        "subagent.text.delta",
        "subagent.tool.started",
    ]
    assert nodes[0]["content"] == "先比较两个文件的公开字段。"
    assert nodes[1]["content"] == "已核对 README，并准备读取配置。"
    assert nodes[2]["args"] == {"path": "README.md", "api_key": "<redacted>"}

    tool_result = ToolMessage(
        id="child-tool-result-1",
        name="read_native_file",
        tool_call_id="child-tool-1",
        content="authorization: Bearer abcdefghijklmnop; title=V8 Agent OS",
    )
    result_nodes = _subagent_timeline_nodes_from_message(tool_result)
    assert len(result_nodes) == 1
    assert result_nodes[0]["topic"] == "subagent.tool.finished"
    assert "abcdefghijklmnop" not in result_nodes[0]["agentVisibleResult"]
    assert "<redacted>" in result_nodes[0]["agentVisibleResult"]


def _dependent_episode() -> dict:
    return {
        "episodeId": "episode-dependent",
        "runId": "run-shared",
        "kind": "engineering",
        "state": "active",
        "inputs": {
            "workerBriefs": [
                {
                    "taskBriefId": "TASK-B",
                    "goal": "Use the upstream evidence and write result.md.",
                    "dependency": ["TASK-A"],
                    "context": {},
                }
            ]
        },
    }


def _upstream_episode(state: str) -> dict:
    return {
        "episodeId": "episode-upstream",
        "runId": "run-shared",
        "kind": "research",
        "state": state,
        "inputs": {
            "workerBriefs": [
                {
                    "taskBriefId": "TASK-A",
                    "goal": "Collect evidence.",
                    "readOnly": True,
                    "context": {},
                }
            ]
        },
    }


def test_runtime_route_task_brief_normalizes_explicit_expected_output_map() -> None:
    brief = RuntimeRouteTaskBrief.model_validate(
        {
            "taskBriefId": "research-1",
            "goal": "Collect evidence.",
            "readOnly": True,
            "writeRequired": False,
            "writeSet": "",
            "expectedOutputs": {
                "item": "limitations",
                "reuseDecision": "",
                "detailRef": "",
            },
            "acceptanceContract": {"mustCiteSources": True},
        }
    )

    assert brief.writeSet == []
    assert brief.expectedOutputs == ["item: limitations", "reuseDecision", "detailRef"]


def test_runtime_route_task_brief_preserves_supported_snake_case_contract() -> None:
    typed = RuntimeRouteTaskBrief.model_validate(
        {
            "task_brief_id": "snake-contract",
            "goal": "Preserve the complete route contract.",
            "read_only": True,
            "write_required": False,
            "read_set": ["README.md"],
            "tool_policy": {"mode": "allowlist", "allowedTools": ["read_native_file"]},
            "side_effect_policy": {"mode": "none"},
            "execution_budget": {"maxTokens": 1200},
            "failure_policy": {"onError": "return_diagnostic"},
            "acceptance_tiers": {"must": ["cite README.md"]},
            "critical_files": ["README.md"],
            "verification_matrix": ["inspect README.md"],
            "proof_expectations": ["line reference"],
            "delegation_policy": {
                "allow_child_delegation": False,
                "selectionRationale": "Keep the review local.",
            },
        }
    )

    normalized = normalize_task_brief(typed.model_dump(exclude_none=True))

    assert normalized["taskBriefId"] == "snake-contract"
    assert normalized["readOnly"] is True
    assert normalized["writeRequired"] is False
    assert normalized["readSet"] == ["README.md"]
    assert normalized["toolPolicy"]["mode"] == "allowlist"
    assert normalized["toolPolicy"]["allowedTools"] == ["read_native_file"]
    assert normalized["sideEffectPolicy"] == {"mode": "none"}
    assert normalized["budget"] == {"maxTokens": 1200}
    assert normalized["failurePolicy"] == {"onError": "return_diagnostic"}
    assert normalized["criticalFiles"] == ["README.md"]
    assert normalized["verificationMatrix"] == ["inspect README.md"]
    assert normalized["proofExpectations"] == ["line reference"]
    assert normalized["delegationPolicy"]["allowChildDelegation"] is False
    assert normalized["extensions"]["delegationPolicy.selectionRationale"] == (
        "Keep the review local."
    )
    assert "delegationPolicy.selectionRationale" in normalized["unsupportedFields"]
    assert normalized["acceptanceTiers"] == {
        "must": ["cite README.md"],
        "should": [],
        "nice": [],
    }


def test_runtime_route_task_brief_rejects_conflicting_aliases_with_typed_diagnostic() -> None:
    with pytest.raises(ValidationError) as exc_info:
        RuntimeRouteTaskBrief.model_validate(
            {
                "taskBriefId": "canonical-id",
                "task_brief_id": "snake-id",
                "goal": "Reject ambiguous identity before routing.",
                "criticalFiles": ["canonical.py"],
                "critical_files": ["snake.py"],
            }
        )

    error = exc_info.value.errors(include_url=False, include_input=False)[0]
    assert error["type"] == "task_brief_alias_conflict"
    assert set(str(error["ctx"]["fields"]).split(", ")) == {
        "taskBriefId",
        "criticalFiles",
    }
    conflicts = json.loads(error["ctx"]["aliasConflicts"])
    assert all("values" not in item for item in conflicts)


def test_command_list_runtime_route_enters_runtime_episode() -> None:
    commands = [
        Command(
            goto="supervisor",
            update={
                "messages": [ToolMessage(content="queued", tool_call_id="runtime-route")],
                "current_route_context": {"capabilityEpisodes": [{"episodeId": "episode-1"}]},
            },
        ),
        Command(
            goto="supervisor",
            update={
                "runtime_dispatch_status": {
                    "mode": "runtime_broker_route",
                    "nextAction": "wait_episode",
                    "episodeId": "episode-1",
                }
            },
        ),
    ]

    routed = _route_runtime_tool_commands(commands)

    assert isinstance(routed, Command)
    assert routed.goto == "runtime_episode"
    assert routed.update["runtime_dispatch_status"]["episodeId"] == "episode-1"
    assert routed.update["current_route_context"]["capabilityEpisodes"][0]["episodeId"] == "episode-1"
    assert len(routed.update["messages"]) == 1


def test_mixed_runtime_route_results_prefer_queued_episode_without_concurrent_state_writes() -> None:
    commands = [
        Command(
            goto="supervisor",
            update={
                "messages": [ToolMessage(content="queued", tool_call_id="runtime-route")],
                "current_route_context": {"capabilityEpisodes": [{"episodeId": "episode-1"}]},
                "runtime_dispatch_status": {
                    "mode": "runtime_broker_route",
                    "dispatched": True,
                    "nextAction": "wait_episode",
                    "episodeId": "episode-1",
                },
            },
        ),
        Command(
            goto="supervisor",
            update={
                "messages": [ToolMessage(content="repair", tool_call_id="runtime-repair")],
                "runtime_dispatch_status": {
                    "mode": "runtime_broker_route",
                    "dispatched": False,
                    "blocked": True,
                    "nextAction": "repair_task_contract",
                },
            },
        ),
    ]

    routed = _route_runtime_tool_commands(commands)

    assert isinstance(routed, Command)
    assert routed.goto == "runtime_episode"
    assert routed.update["runtime_dispatch_status"]["episodeId"] == "episode-1"
    assert routed.update["runtime_dispatch_status"]["nextAction"] == "wait_episode"
    assert [message.content for message in routed.update["messages"]] == ["queued", "repair"]


def test_multiple_blocked_runtime_results_collapse_to_one_supervisor_transition() -> None:
    commands = [
        Command(
            goto="supervisor",
            update={
                "runtime_dispatch_status": {
                    "mode": "runtime_broker_route",
                    "blocked": True,
                    "nextAction": "repair_task_contract",
                }
            },
        ),
        Command(
            goto="supervisor",
            update={
                "runtime_dispatch_status": {
                    "mode": "runtime_broker_route",
                    "blocked": True,
                    "nextAction": "report_runtime_blocker",
                }
            },
        ),
    ]

    routed = _route_runtime_tool_commands(commands)

    assert isinstance(routed, Command)
    assert routed.goto == "supervisor"
    assert routed.update["runtime_dispatch_status"]["nextAction"] == "report_runtime_blocker"


def test_non_runtime_command_list_remains_unmodified() -> None:
    commands = [Command(goto="supervisor", update={"messages": []})]
    assert _route_runtime_tool_commands(commands) is commands


def test_workflow_entry_routes_pending_runtime_handoff_to_episode_node() -> None:
    command = _workflow_entry_command(
        {
            "runtime_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "wait_episode",
                "state": "handoff_resume_requested",
            }
        }
    )
    assert command.goto == "runtime_episode"
    assert _workflow_entry_command({}).goto == "supervisor"


def test_waiting_dependency_is_an_active_episode_state() -> None:
    assert "waiting_dependency" in ACTIVE_EPISODE_STATES


def test_cross_episode_active_dependency_blocks_before_executor(monkeypatch) -> None:
    fake_db = SimpleNamespace(
        list_runtime_episodes=lambda **_kwargs: [_upstream_episode("active")],
        list_runtime_episode_handoffs=lambda _episode_id: [],
    )
    monkeypatch.setattr(runner_module, "db", fake_db)

    updated, gate = RuntimeEpisodeRunner._prepare_cross_episode_dependencies(_dependent_episode())

    assert updated["episodeId"] == "episode-dependent"
    assert gate == {
        "state": "waiting_dependency",
        "taskBriefIds": ["TASK-A"],
        "producerEpisodeIds": ["episode-upstream"],
        "summary": "Waiting for required upstream runtime episode handoff before execution.",
    }


def test_cross_episode_handoff_is_injected_without_runtime_noise(monkeypatch) -> None:
    handoff = {
        "handoffId": "handoff-upstream",
        "payload": {
            "status": "ready",
            "compactSummary": "Evidence collected.",
            "results": [
                {
                    "taskBriefId": "TASK-A",
                    "status": "completed",
                    "resultText": "Three primary sources agree.",
                    "artifactRefs": ["workspace://evidence.md"],
                    "proofRefs": ["proof://source-check"],
                    "rawToolPayload": {"secret": "must-not-cross"},
                }
            ],
            "rawReasoning": "must-not-cross",
        },
    }
    fake_db = SimpleNamespace(
        list_runtime_episodes=lambda **_kwargs: [_upstream_episode("completed")],
        list_runtime_episode_handoffs=lambda _episode_id: [handoff],
    )
    monkeypatch.setattr(runner_module, "db", fake_db)

    updated, gate = RuntimeEpisodeRunner._prepare_cross_episode_dependencies(_dependent_episode())

    assert gate is None
    injected = updated["inputs"]["workerBriefs"][0]["context"]["dependencyResults"][0]
    assert injected["taskBriefId"] == "TASK-A"
    assert injected["status"] == "ok"
    assert injected["summary"] == "Three primary sources agree."
    assert injected["artifacts"] == ["workspace://evidence.md"]
    assert injected["proofRefs"] == ["proof://source-check"]
    serialized = json.dumps(injected, ensure_ascii=False)
    assert "rawToolPayload" not in serialized
    assert "rawReasoning" not in serialized
    assert "must-not-cross" not in serialized


def test_cross_episode_failed_dependency_blocks_dependent(monkeypatch) -> None:
    fake_db = SimpleNamespace(
        list_runtime_episodes=lambda **_kwargs: [_upstream_episode("failed")],
        list_runtime_episode_handoffs=lambda _episode_id: [
            {
                "handoffId": "handoff-failed",
                "payload": {"status": "failed", "compactSummary": "Source validation failed."},
            }
        ],
    )
    monkeypatch.setattr(runner_module, "db", fake_db)

    _updated, gate = RuntimeEpisodeRunner._prepare_cross_episode_dependencies(_dependent_episode())

    assert gate["state"] == "failed"
    assert gate["errorCode"] == "cross_episode_dependency_failed"
    assert gate["taskBriefIds"] == ["TASK-A"]


def test_direct_delegation_injects_upstream_handoff_content_instead_of_fake_file_paths(monkeypatch) -> None:
    monkeypatch.setattr("core.tools.native.delegation.sys.platform", "win32")
    tasks = [
        {
            "taskBriefId": "risk-review",
            "goal": "Review research://episode_research/evidence and engineering://episode_engineering/plan.",
            "context": "Read-only review.",
            "evidenceRefs": [
                "research://episode_research/evidence",
                "engineering://episode_engineering/plan",
            ],
        }
    ]
    inherited = {
        "handoffRefs": [
            {
                "handoffRefId": "handoff-research",
                "producerEpisodeId": "episode_research",
                "kind": "research_evidence_bundle",
                "status": "ready",
                "compactSummary": "Three sources agree on the bottleneck.",
                "rawReasoning": "must-not-cross",
            },
            {
                "handoffRefId": "handoff-engineering",
                "producerEpisodeId": "episode_engineering",
                "kind": "engineering_patch_bundle",
                "status": "ready",
                "compactSummary": "The execution plan has three bounded phases.",
                "childHandoffs": [
                    {
                        "producerEpisodeId": "subagent::plan",
                        "kind": "subagent_result",
                        "status": "ready",
                        "compactSummary": "Phase 1 narrows routing; phase 2 verifies handoffs.",
                        "rawToolPayload": {"secret": "must-not-cross"},
                    }
                ],
            },
        ]
    }

    injected = _inject_inherited_handoffs_into_tasks(tasks, inherited)[0]
    context = injected["context"]

    assert context["notes"] == "Read-only review."
    assert context["shellDialect"] == "powershell"
    assert [item["producerEpisodeId"] for item in context["upstreamHandoffs"]] == [
        "episode_research",
        "episode_engineering",
    ]
    assert context["upstreamHandoffs"][1]["childResults"][0]["summary"].startswith("Phase 1")
    assert "not filesystem paths" in context["handoffUsage"]
    serialized = json.dumps(injected, ensure_ascii=False)
    assert "rawReasoning" not in serialized
    assert "rawToolPayload" not in serialized
    assert "must-not-cross" not in serialized


def test_direct_delegation_keeps_unresolved_explicit_evidence_without_unrelated_fallback() -> None:
    injected = _inject_inherited_handoffs_into_tasks(
        [
            {
                "taskBriefId": "missing-evidence",
                "goal": "Review the specifically requested evidence.",
                "evidenceRefs": ["research://episode_missing/evidence"],
            }
        ],
        {
            "handoffRefs": [
                {
                    "handoffRefId": "handoff-unrelated",
                    "producerEpisodeId": "episode_unrelated",
                    "status": "ready",
                    "compactSummary": "This belongs to another task.",
                }
            ]
        },
    )[0]

    context = injected["context"]
    assert "upstreamHandoffs" not in context
    assert context["requestedEvidenceRefs"] == ["research://episode_missing/evidence"]
    assert context["unresolvedEvidenceRefs"] == ["research://episode_missing/evidence", "episode_missing"]
    assert context["evidenceResolutionDiagnostics"]["mode"] == "explicit_refs"
    assert context["evidenceResolutionDiagnostics"]["fallbackUsed"] is False
    assert "episode_unrelated" not in json.dumps(injected, ensure_ascii=False)


def test_direct_delegation_uses_exact_reference_tokens_without_prefix_collision() -> None:
    injected = _inject_inherited_handoffs_into_tasks(
        [
            {
                "taskBriefId": "exact-evidence",
                "goal": "Review episode 10 only.",
                "evidenceRefs": ["research://episode_10_branch/evidence"],
            }
        ],
        {
            "handoffRefs": [
                {
                    "handoffRefId": "handoff-episode-1",
                    "producerEpisodeId": "episode_1",
                    "status": "ready",
                    "compactSummary": "Wrong prefix match.",
                },
                {
                    "handoffRefId": "handoff-episode-10",
                    "producerEpisodeId": "episode_10_branch",
                    "status": "ready",
                    "compactSummary": "Exact requested evidence.",
                },
            ]
        },
    )[0]

    handoffs = injected["context"]["upstreamHandoffs"]
    assert [item["producerEpisodeId"] for item in handoffs] == ["episode_10_branch"]
    assert injected["context"].get("unresolvedEvidenceRefs") in (None, [])
    assert "Wrong prefix match" not in json.dumps(injected, ensure_ascii=False)


def test_direct_delegation_does_not_match_generic_uri_segments_as_evidence_identity() -> None:
    injected = _inject_inherited_handoffs_into_tasks(
        [
            {
                "taskBriefId": "generic-segment-collision",
                "goal": "Review the requested episode only.",
                "evidenceRefs": ["research://episode_target/evidence"],
            }
        ],
        {
            "handoffRefs": [
                {
                    "handoffRefId": "handoff-wrong",
                    "producerEpisodeId": "episode_wrong",
                    "status": "ready",
                    "compactSummary": "Unrelated evidence with a generic ref segment.",
                    "refs": ["evidence"],
                }
            ]
        },
    )[0]

    context = injected["context"]
    assert "upstreamHandoffs" not in context
    assert context["unresolvedEvidenceRefs"] == [
        "research://episode_target/evidence",
        "episode_target",
    ]
    assert "episode_wrong" not in json.dumps(injected, ensure_ascii=False)


def test_direct_delegation_resolves_handoff_reference_aliases_and_evidence_refs() -> None:
    inherited = {
        "handoffRefs": [
            {
                "handoffId": "handoff-current",
                "handoffRefId": "handoff-current",
                "identityAliases": ["handoff-legacy"],
                "producerEpisodeId": "episode_reference_bundle",
                "status": "ready",
                "compactSummary": "Requested evidence bundle.",
                "detailRef": "detail://bundle-42",
                "refs": ["research://source-42"],
                "proofRefs": ["proof://claim-42"],
            }
        ]
    }

    for requested_ref in (
        "handoff://handoff-legacy/result",
        "detail://bundle-42",
        "research://source-42",
        "proof://claim-42",
    ):
        injected = _inject_inherited_handoffs_into_tasks(
            [
                {
                    "taskBriefId": f"resolve-{requested_ref}",
                    "goal": "Review the exact referenced evidence.",
                    "evidenceRefs": [requested_ref],
                }
            ],
            inherited,
        )[0]

        context = injected["context"]
        assert context["upstreamHandoffs"][0]["summary"] == "Requested evidence bundle."
        assert context.get("unresolvedEvidenceRefs") in (None, [])
        assert context["evidenceResolutionDiagnostics"]["fallbackUsed"] is False


def test_direct_delegation_marks_latest_handoff_fallback_when_no_refs_are_supplied() -> None:
    injected = _inject_inherited_handoffs_into_tasks(
        [{"taskBriefId": "context-only", "goal": "Review the current upstream result."}],
        {
            "handoffRefs": [
                {
                    "handoffRefId": "handoff-latest",
                    "producerEpisodeId": "episode_latest",
                    "status": "ready",
                    "compactSummary": "Latest available context.",
                }
            ]
        },
    )[0]

    context = injected["context"]
    assert context["upstreamHandoffs"][0]["producerEpisodeId"] == "episode_latest"
    assert context["evidenceResolutionDiagnostics"] == {
        "mode": "latest_available_fallback",
        "fallbackUsed": True,
        "reason": "no_explicit_evidence_refs",
        "selectedHandoffRefIds": ["handoff-latest"],
    }
    assert "latest-available context" in context["handoffUsage"]


def test_runtime_handoff_file_hunting_uses_one_semantic_repeat_signature() -> None:
    read_signature = _repeat_sensitive_tool_call_signature(
        {"name": "read_native_file", "args": {"path": "E:/repo/research_evidence_bundle_episode_a.md"}}
    )
    command_signature = _repeat_sensitive_tool_call_signature(
        {"name": "run_system_command", "args": {"command": "dir /s /b E:\\repo\\episode_b 2>nul"}}
    )

    assert read_signature == command_signature == (
        "runtime_handoff_lookup",
        "runtime_handoff_identifier_is_not_a_file",
    )


def test_creative_media_repair_loops_use_bounded_repeat_signatures() -> None:
    create_signature = _repeat_sensitive_tool_call_signature(
        {
            "name": "creative_media_jobs",
            "args": {
                "action": "create",
                "request": {"modality": "image", "operationKind": "image.edit", "sourceId": "src-a"},
            },
        }
    )
    reordered_signature = _repeat_sensitive_tool_call_signature(
        {
            "name": "creative_media_jobs",
            "args": {
                "request": {"sourceId": "src-a", "operationKind": "image.edit", "modality": "image"},
                "action": "create",
            },
        }
    )
    poll_signature = _repeat_sensitive_tool_call_signature(
        {"name": "creative_media_jobs", "args": {"action": "get", "request": {"jobId": "cm-a"}}}
    )

    assert create_signature == reordered_signature
    assert create_signature and create_signature[0] == "creative_media_jobs"
    assert poll_signature is None


def test_parallel_delegation_publishes_denoised_progress_and_terminal_handoff(monkeypatch) -> None:
    events: list[dict] = []
    heartbeats: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "graph.parallel_support.emit_runtime_episode_event",
        lambda topic, payload, source=None: events.append({"topic": topic, "payload": payload, "source": source}),
    )
    monkeypatch.setattr(
        "graph.parallel_support.heartbeat_runtime_episode",
        lambda episode_id, progress="": heartbeats.append((episode_id, progress)),
    )

    def worker_node(_state):
        return Command(goto="supervisor", update={"messages": [AIMessage(content="Risk review complete.")]})

    node = build_parallel_delegate_task_node(
        {
            "worker": {
                "node_func": worker_node,
                "tool_mode": "none",
            }
        }
    )
    result = asyncio.run(
        node(
            {
                "session_id": "session-progress",
                "run_id": "run-progress",
                "messages": [],
                "todos": [],
                "parallel_branch": {
                    "invocationId": "delegation-progress",
                    "delegationId": "subagent::delegation-progress::worker",
                    "taskBriefId": "TASK-PROGRESS",
                    "taskBrief": {"taskBriefId": "TASK-PROGRESS", "goal": "Review risk."},
                    "agentId": "worker",
                    "agentName": "Risk Reviewer",
                    "reason": "Review risk.",
                    "initialMessageCount": 0,
                    "initialTodoCount": 0,
                },
            }
        )
    )

    assert result.goto == "parallel_delegate_join"
    stages = [item["payload"]["progress"]["stage"] for item in events]
    assert stages == ["started", "working", "responding", "handoff_ready"]
    assert events[2]["payload"]["progress"]["timelineNode"]["topic"] == "subagent.text.delta"
    assert all(item["topic"] == "runtime.episode.progress" for item in events)
    assert heartbeats[-1][0] == "subagent::delegation-progress::worker"


def test_progress_event_drops_raw_payload_and_reasoning(monkeypatch) -> None:
    recorded: list[dict] = []

    class FakeDb:
        def add_runtime_episode_event_record(self, **kwargs):
            recorded.append(dict(kwargs))

        def add_runtime_event(self, payload):
            recorded.append(dict(payload))

        def get_next_runtime_seq(self, _session_id):
            return 1

    monkeypatch.setattr(runner_module, "db", FakeDb())
    runner = RuntimeEpisodeRunner()
    runner._emit(
        "runtime.episode.progress",
        episode={"episodeId": "episode-progress", "kind": "delegation", "state": "active"},
        session_id="session-progress",
        run_id="run-progress",
        progress={
            "stage": "tool_execution",
            "status": "running",
            "summary": "正在验证产物。",
            "toolName": "run_system_command",
            "rawOutput": "SECRET_OUTPUT",
            "reasoning": "SECRET_REASONING",
        },
        rawToolPayload={"secret": "SECRET_PAYLOAD"},
    )

    serialized = json.dumps(recorded, ensure_ascii=False)
    assert "tool_execution" in serialized
    assert "run_system_command" in serialized
    assert "SECRET_OUTPUT" not in serialized
    assert "SECRET_REASONING" not in serialized
    assert "SECRET_PAYLOAD" not in serialized


def test_windows_shell_dialect_is_deterministic(monkeypatch) -> None:
    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(command_module, "default_shell_dialect", lambda: "powershell")

    assert command_module._resolve_shell_dialect("$env:MODE='test'; Get-ChildItem -Force") == "powershell"
    assert command_module._resolve_shell_dialect("set MODE=test && dir /b") == "powershell"
    assert command_module._resolve_shell_dialect("set MODE=test && dir /b", "cmd") == "cmd"
    assert command_module._resolve_shell_dialect("Get-ChildItem", "pwsh") == "pwsh"

    violation = _windows_shell_syntax_violation_payload(
        "set MODE=test && dir /b",
        shell_dialect="powershell",
    )
    assert violation is not None
    assert "cmd_syntax_in_powershell" in violation["violations"]

    chained = _windows_shell_syntax_violation_payload(
        'cd "C:\\workspace" && dir',
        shell_dialect=command_module._resolve_shell_dialect('cd "C:\\workspace" && dir'),
    )
    assert chained is not None
    assert "powershell_5_chain_operator" in chained["violations"]


def test_windows_shell_argv_uses_one_explicit_shell(monkeypatch) -> None:
    monkeypatch.setattr(command_module.sys, "platform", "win32")
    monkeypatch.setattr(command_module.shutil, "which", lambda name: f"C:/shells/{name}")

    argv = command_module._shell_command_argv("Write-Output 'ok'", "powershell")

    assert argv[0] == "C:/shells/powershell.exe"
    assert argv[-2:] == ["-Command", "Write-Output 'ok'"]
    assert argv.count("powershell.exe") == 0


def _required_write_episode(workspace: str, *, state: str = "completed") -> dict:
    return {
        "episodeId": "episode-write",
        "kind": "engineering",
        "state": state,
        "inputs": {
            "workspacePath": workspace,
            "taskBriefs": [
                {
                    "taskBriefId": "TASK-WRITE",
                    "goal": "Write result.md.",
                    "writeRequired": True,
                    "writeSet": ["result.md"],
                    "expectedOutputs": ["result.md"],
                    "acceptanceContract": {"must": ["result.md exists"]},
                }
            ],
        },
    }


def _creative_artifact_episode(workspace: str) -> dict:
    contract = {
        "schema": "v8.creative_canvas_task.v1",
        "canvasOperationId": "canvas-op-proof",
        "actionId": "creative_media.edit_image_region",
        "output": {"kind": "artifact", "slot": "image_derivative"},
        "resources": {
            "sourceIds": ["source-proof"],
            "maskSourceId": "mask-proof",
        },
        "execution": {
            "tool": "creative_media_jobs",
            "arguments": {
                "action": "create",
                "request": {
                    "modality": "image",
                    "operationKind": "image.edit",
                    "canvasOperationId": "canvas-op-proof",
                    "sourceId": "source-proof",
                    "maskSourceId": "mask-proof",
                    "prompt": "Replace only the masked region.",
                },
            },
        },
    }
    return {
        "episodeId": "episode-creative-artifact",
        "kind": "creative_media",
        "state": "completed",
        "sessionId": "session-creative-artifact",
        "runId": "run-creative-artifact",
        "inputs": {
            "workspacePath": workspace,
            "workspaceId": "workspace-creative-artifact",
            "projectId": "project-creative-artifact",
            "taskBriefs": [
                {
                    "taskBriefId": "TASK-CREATIVE-ARTIFACT",
                    "goal": "Execute the exact Canvas edit.",
                    "writeRequired": True,
                    "writeSet": [".v8/creative-media/"],
                    "expectedOutputs": ["One governed Creative Media artifact."],
                    "context": {"canvasExecutionContract": contract},
                }
            ],
        },
    }


def _creative_source_record(workspace: str) -> dict:
    return {
        "sessionId": "session-creative-artifact",
        "resourceRef": {
            "workspaceId": "workspace-creative-artifact",
            "projectId": "project-creative-artifact",
            "workspaceRoot": workspace,
        },
        "metadata": {},
    }


def test_non_spec_required_write_degraded_cannot_complete(tmp_path) -> None:
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path), state="degraded")],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "degraded",
                    "compactSummary": "Worker returned without a file.",
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.reason == "required_write_runtime_degraded"
    assert decision.details["nextAction"] == "repair_or_retry_required_write_episode"


def test_non_spec_completed_repair_supersedes_degraded_same_task_contract(tmp_path) -> None:
    (tmp_path / "result.md").write_text("done", encoding="utf-8")
    failed = _required_write_episode(str(tmp_path), state="degraded")
    failed["episodeId"] = "episode-write-failed"
    failed["inputs"]["taskBriefs"][0]["writeSet"] = ["drafts/"]

    repaired = _required_write_episode(str(tmp_path), state="completed")
    repaired["episodeId"] = "episode-write-repaired"

    decision = evaluate_supervisor_completion(
        episodes=[failed, repaired],
        handoffs_by_episode={
            "episode-write-failed": [{"status": "degraded", "compactSummary": "The first contract was invalid."}],
            "episode-write-repaired": [
                {
                    "status": "ready",
                    "changedPaths": ["result.md"],
                    "acceptanceCheck": {"must": {"passed": True, "items": ["result.md exists"]}},
                }
            ],
        },
        final_text="验收决定：ACCEPT — repaired task contract is verified.",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_non_spec_completed_other_task_does_not_hide_degraded_write(tmp_path) -> None:
    (tmp_path / "new.md").write_text("done", encoding="utf-8")
    failed = _required_write_episode(str(tmp_path), state="degraded")
    failed["episodeId"] = "episode-old-task"
    failed["inputs"]["taskBriefs"][0]["taskBriefId"] = "TASK-OLD"
    failed["inputs"]["taskBriefs"][0]["writeSet"] = ["old.md"]

    unrelated = _required_write_episode(str(tmp_path), state="completed")
    unrelated["episodeId"] = "episode-new-task"
    unrelated["inputs"]["taskBriefs"][0]["taskBriefId"] = "TASK-NEW"
    unrelated["inputs"]["taskBriefs"][0]["writeSet"] = ["new.md"]
    unrelated["inputs"]["taskBriefs"][0]["expectedOutputs"] = ["new.md"]

    decision = evaluate_supervisor_completion(
        episodes=[failed, unrelated],
        handoffs_by_episode={
            "episode-old-task": [{"status": "degraded", "compactSummary": "old task failed"}],
            "episode-new-task": [
                {
                    "status": "ready",
                    "changedPaths": ["new.md"],
                    "acceptanceCheck": {"must": {"passed": True, "items": ["new.md exists"]}},
                }
            ],
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.reason == "required_write_runtime_degraded"
    assert decision.details["episodeId"] == "episode-old-task"


def test_non_spec_required_write_without_file_cannot_complete(tmp_path) -> None:
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path))],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "proofRefs": ["proof://verification"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.reason == "required_write_files_missing"


def test_non_spec_direct_write_accepts_exact_authoritative_run_artifact(tmp_path, monkeypatch) -> None:
    target = tmp_path / "result.md"
    target.write_text("done", encoding="utf-8")
    episode = _required_write_episode(str(tmp_path))
    episode.update({"sessionId": "session-direct-write", "runId": "run-direct-write"})
    monkeypatch.setattr(
        completion_gate_module.db,
        "list_runtime_artifacts",
        lambda **_kwargs: [
            {
                "resourceRole": "artifact",
                "sessionId": "session-direct-write",
                "runId": "run-direct-write",
                "origin": "agent_file_write",
                "sourceComponent": "write_native_file",
                "sourcePath": str(target),
                "metadata": {
                    "storageClass": "workspace",
                    "pathPlane": "workspace_artifact",
                    "deliveryState": "authoritative",
                    "managedExecution": False,
                    "workspaceRelativePath": "result.md",
                },
            }
        ],
    )

    decision = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode={
            "episode-write": [{"status": "ready", "proofRefs": ["proof://direct-write-validation"]}]
        },
        final_text="验收决定：ACCEPT — direct write and validation proof verified.",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_non_spec_managed_candidate_artifact_cannot_bypass_merge_handoff(tmp_path, monkeypatch) -> None:
    target = tmp_path / "result.md"
    target.write_text("candidate only", encoding="utf-8")
    episode = _required_write_episode(str(tmp_path))
    episode.update({"sessionId": "session-managed-write", "runId": "run-managed-write"})
    monkeypatch.setattr(
        completion_gate_module.db,
        "list_runtime_artifacts",
        lambda **_kwargs: [
            {
                "resourceRole": "artifact",
                "sessionId": "session-managed-write",
                "runId": "run-managed-write",
                "origin": "agent_file_write",
                "sourceComponent": "write_native_file",
                "sourcePath": str(target),
                "metadata": {
                    "storageClass": "workspace",
                    "pathPlane": "workspace_artifact",
                    "deliveryState": "candidate",
                    "managedExecution": True,
                    "workspaceRelativePath": "result.md",
                },
            }
        ],
    )

    decision = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode={
            "episode-write": [{"status": "ready", "proofRefs": ["proof://candidate-validation"]}]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.reason == "required_write_files_missing"


def test_non_spec_typed_creative_artifact_satisfies_artifact_delivery_without_workspace_file(
    tmp_path,
    monkeypatch,
) -> None:
    artifact_path = tmp_path / "creative_media" / "cm-proof" / "image-proof.png"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"creative-artifact-proof")
    episode = _creative_artifact_episode(str(tmp_path))

    monkeypatch.setattr(
        completion_gate_module.db,
        "get_session_scope_binding",
        lambda _session_id: {
            "session_id": "session-creative-artifact",
            "workspace_id": "workspace-creative-artifact",
            "project_id": "project-creative-artifact",
            "workspace_path": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        completion_gate_module.db,
        "get_session_source",
        lambda *, session_id, source_id: (
            _creative_source_record(str(tmp_path))
            if session_id == "session-creative-artifact" and source_id in {"source-proof", "mask-proof"}
            else None
        ),
    )
    monkeypatch.setattr(
        completion_gate_module.db,
        "get_runtime_artifact",
        lambda artifact_id: {
            "artifactId": artifact_id,
            "sessionId": "session-creative-artifact",
            "runId": "run-creative-artifact",
            "resourceRole": "artifact",
            "sourcePath": str(artifact_path),
            "metadata": {
                "storageClass": "runtime_artifact",
                "creativeMediaJobId": "cm-proof",
                "workspaceId": "workspace-creative-artifact",
                "projectId": "project-creative-artifact",
                "workspacePath": str(tmp_path),
                "modality": "image",
                "operationKind": "image.edit",
                "outputKind": "artifact",
                "outputSlot": "image_derivative",
                "canvasOperationId": "canvas-op-proof",
                "sourceId": "source-proof",
                "maskSourceId": "mask-proof",
            },
        }
        if artifact_id == "art_proof"
        else None,
    )

    decision = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode={
            "episode-creative-artifact": [
                {
                    "status": "ready",
                    "artifactRefs": ["art_proof"],
                    "proofRefs": ["creative-media-job://cm-proof"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_canvas_graph_artifact_bundle_satisfies_outer_delivery_contract(
    tmp_path,
    monkeypatch,
) -> None:
    artifact_path = tmp_path / "creative_media" / "inner-frame" / "frame.png"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"canvas-graph-frame")
    episode = _creative_artifact_episode(str(tmp_path))
    brief = episode["inputs"]["taskBriefs"][0]
    contract = brief["context"]["canvasExecutionContract"]
    contract["actionId"] = "canvas.graph.run_to_here"
    contract["output"] = {"kind": "artifacts", "slot": "canvas_graph"}
    contract["resources"] = {"workspaceAssetIds": ["wma-video"]}
    contract["execution"]["arguments"]["request"] = {
        "modality": "workflow",
        "operationKind": "canvas.graph.execute",
        "canvasOperationId": "canvas-op-proof",
        "graphId": "canvas-graph-proof",
        "graphRevision": 4,
        "targetNodeIds": ["frame-action"],
    }

    monkeypatch.setattr(
        completion_gate_module.db,
        "get_session_scope_binding",
        lambda _session_id: {
            "session_id": "session-creative-artifact",
            "workspace_id": "workspace-creative-artifact",
            "project_id": "project-creative-artifact",
            "workspace_path": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        completion_gate_module.db,
        "get_runtime_artifact",
        lambda artifact_id: {
            "artifactId": artifact_id,
            "sessionId": "session-creative-artifact",
            "runId": "run-creative-artifact",
            "resourceRole": "artifact",
            "sourcePath": str(artifact_path),
            "metadata": {
                "storageClass": "runtime_artifact",
                "creativeMediaJobId": "cm-inner-frame",
                "workspaceId": "workspace-creative-artifact",
                "projectId": "project-creative-artifact",
                "workspacePath": str(tmp_path),
                "modality": "video",
                "operationKind": "video.extract_frame_exact",
                "outputKind": "artifact",
                "outputSlot": "image_derivative",
                "canvasOperationId": "canvas-op-proof",
            },
        }
        if artifact_id == "art_frame"
        else None,
    )
    handoff = {
        "status": "ready",
        "artifactRefs": ["art_frame"],
        "proofRefs": ["creative-media-job://cm-outer-graph"],
        "creativeExecutionEvidence": {
            "schemaVersion": "creative-execution-evidence/v1",
            "records": [{
                "tool": "creative_media_jobs",
                "operationKind": "canvas.graph.execute",
                "outputKind": "artifacts",
                "outputSlot": "canvas_graph",
                "jobId": "cm-outer-graph",
                "status": "succeeded",
                "artifactRefs": ["art_frame"],
            }],
            "artifactRefs": ["art_frame"],
            "proofRefs": ["creative-media-job://cm-outer-graph"],
        },
    }

    decision = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode={"episode-creative-artifact": [handoff]},
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_non_spec_typed_creative_artifact_rejects_source_lineage_drift(
    tmp_path,
    monkeypatch,
) -> None:
    artifact_path = tmp_path / "creative_media" / "cm-proof" / "image-proof.png"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(b"creative-artifact-proof")
    episode = _creative_artifact_episode(str(tmp_path))
    monkeypatch.setattr(
        completion_gate_module.db,
        "get_session_scope_binding",
        lambda _session_id: {
            "session_id": "session-creative-artifact",
            "workspace_id": "workspace-creative-artifact",
            "project_id": "project-creative-artifact",
            "workspace_path": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        completion_gate_module.db,
        "get_session_source",
        lambda **_kwargs: _creative_source_record(str(tmp_path)),
    )
    monkeypatch.setattr(
        completion_gate_module.db,
        "get_runtime_artifact",
        lambda _artifact_id: {
            "artifactId": "art_proof",
            "sessionId": "session-creative-artifact",
            "runId": "run-creative-artifact",
            "resourceRole": "artifact",
            "sourcePath": str(artifact_path),
            "metadata": {
                "storageClass": "runtime_artifact",
                "creativeMediaJobId": "cm-proof",
                "workspaceId": "workspace-creative-artifact",
                "projectId": "project-creative-artifact",
                "workspacePath": str(tmp_path),
                "modality": "image",
                "operationKind": "image.edit",
                "outputKind": "artifact",
                "outputSlot": "image_derivative",
                "canvasOperationId": "canvas-op-proof",
                "sourceId": "source-from-another-contract",
                "maskSourceId": "mask-proof",
            },
        },
    )

    decision = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode={
            "episode-creative-artifact": [
                {
                    "status": "ready",
                    "artifactRefs": ["art_proof"],
                    "proofRefs": ["creative-media-job://cm-proof"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.reason == "creative_artifact_lineage_mismatch"


def test_non_spec_engineering_cannot_use_bare_runtime_artifact_as_workspace_file(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        completion_gate_module.db,
        "get_runtime_artifact",
        lambda _artifact_id: (_ for _ in ()).throw(AssertionError("engineering must not resolve bare art refs")),
    )
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path))],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "artifactRefs": ["art_unrelated"],
                    "proofRefs": ["proof://verification"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.reason == "required_write_files_missing"


def test_non_spec_required_write_resolves_delivered_file_from_original_workspace(tmp_path) -> None:
    original_workspace = tmp_path / "original"
    original_workspace.mkdir()
    (original_workspace / "result.md").write_text("done", encoding="utf-8")
    episode = _required_write_episode(str(tmp_path / "cleaned-worktree"))
    episode["inputs"]["originalWorkspacePath"] = str(original_workspace)

    decision = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "changedPaths": ["result.md"],
                    "proofRefs": ["proof://verification"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_non_spec_readonly_engineering_family_delegation_does_not_require_written_file(tmp_path) -> None:
    episode = {
        "episodeId": "episode-readonly-delegation",
        "kind": "delegation",
        "state": "completed",
        "inputs": {
            "workspacePath": str(tmp_path),
            "workerBriefs": [
                {
                    "taskBriefId": "TASK-READ",
                    "goal": "Read README.md and return its first heading.",
                    "familyHint": "engineering",
                    "readSet": ["README.md"],
                    "toolPolicy": {"mode": "allowlist", "allowedTools": ["read_native_file"]},
                }
            ],
        },
    }
    decision = evaluate_supervisor_completion(
        episodes=[episode],
        handoffs_by_episode={
            "episode-readonly-delegation": [
                {
                    "status": "ready",
                    "compactSummary": "README.md first heading verified.",
                }
            ]
        },
        final_text="验收决定：ACCEPT\n依据：README.md 首个标题已经由只读 worker 验证。",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_non_spec_required_write_rejects_unresolved_workspace_artifact_ref(tmp_path) -> None:
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path))],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "artifactRefs": ["workspace://result.md"],
                    "proofRefs": ["proof://verification"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.reason == "required_write_files_missing"


def test_non_spec_required_write_without_proof_cannot_complete(tmp_path) -> None:
    artifact = tmp_path / "result.md"
    artifact.write_text("done", encoding="utf-8")
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path))],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "changedFiles": ["result.md"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.reason == "required_write_proof_missing"


def test_non_spec_required_write_with_file_and_proof_can_complete(tmp_path) -> None:
    artifact = tmp_path / "result.md"
    artifact.write_text("done", encoding="utf-8")
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path))],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "changedFiles": ["result.md"],
                    "proofRefs": ["proof://verification"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_proven_retry_supersedes_older_failed_attempt_for_same_write_set(tmp_path) -> None:
    artifact = tmp_path / "result.md"
    artifact.write_text("done", encoding="utf-8")
    failed = {
        **_required_write_episode(str(tmp_path), state="failed"),
        "episodeId": "episode-write-failed",
        "created_at": "2026-07-20T10:00:00.000Z",
        "updated_at": "2026-07-20T10:01:00.000Z",
    }
    proven = {
        **_required_write_episode(str(tmp_path), state="completed"),
        "episodeId": "episode-write-proven",
        "created_at": "2026-07-20T10:02:00.000Z",
        "updated_at": "2026-07-20T10:03:00.000Z",
    }
    decision = evaluate_supervisor_completion(
        episodes=[failed, proven],
        handoffs_by_episode={
            "episode-write-failed": [{"status": "failed", "errorCode": "first_attempt_failed"}],
            "episode-write-proven": [
                {
                    "status": "ready",
                    "changedFiles": ["result.md"],
                    "proofRefs": ["proof://verified-retry"],
                    "verificationResults": [
                        {
                            "status": "verified",
                            "passed": True,
                            "observations": [
                                {"command": "python result.md", "returnCode": 0, "stdout": "done"}
                            ],
                        }
                    ],
                }
            ],
        },
        final_text="验收决定: ACCEPT\n新尝试已覆盖同一交付范围。",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_proven_retry_does_not_hide_failed_disjoint_write_obligation(tmp_path) -> None:
    artifact = tmp_path / "result.md"
    artifact.write_text("done", encoding="utf-8")
    failed = {
        **_required_write_episode(str(tmp_path), state="failed"),
        "episodeId": "episode-other-failed",
        "created_at": "2026-07-20T10:00:00.000Z",
        "updated_at": "2026-07-20T10:01:00.000Z",
    }
    failed["inputs"]["taskBriefs"][0]["writeSet"] = ["other.md"]
    proven = {
        **_required_write_episode(str(tmp_path), state="completed"),
        "episodeId": "episode-result-proven",
        "created_at": "2026-07-20T10:02:00.000Z",
        "updated_at": "2026-07-20T10:03:00.000Z",
    }
    decision = evaluate_supervisor_completion(
        episodes=[failed, proven],
        handoffs_by_episode={
            "episode-other-failed": [{"status": "failed", "errorCode": "other_obligation_failed"}],
            "episode-result-proven": [
                {
                    "status": "ready",
                    "changedFiles": ["result.md"],
                    "proofRefs": ["proof://verified-retry"],
                    "verificationResults": [{"status": "verified", "passed": True}],
                }
            ],
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "fail"
    assert decision.details["episodeId"] == "episode-other-failed"


def test_non_spec_required_write_accepts_managed_git_changed_paths(tmp_path) -> None:
    artifact = tmp_path / "result.md"
    artifact.write_text("done", encoding="utf-8")
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path))],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "delegationHandoff": {
                        "status": "ready",
                        "results": [
                            {
                                "status": "ok",
                                "gitChangeSet": {"changedPaths": ["result.md"]},
                            }
                        ],
                        "acceptanceCheck": {"must": {"passed": True}},
                    },
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_non_spec_required_write_accepts_nested_runtime_acceptance_proof(tmp_path) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("done", encoding="utf-8")
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path))],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "delegationHandoff": {
                        "status": "ready",
                        "results": [
                            {
                                "status": "ok",
                                "artifactRefs": [
                                    {"path": str(artifact), "kind": "workspace_artifact"}
                                ],
                            }
                        ],
                        "acceptanceCheck": {"must": {"passed": True, "items": ["file exists"]}},
                    },
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_non_spec_required_write_uses_parent_handoff_as_completion_boundary(tmp_path) -> None:
    artifact = tmp_path / "result.txt"
    artifact.write_text("done", encoding="utf-8")
    parent = _required_write_episode(str(tmp_path))
    child = {
        **_required_write_episode(str(tmp_path)),
        "episodeId": "episode-child",
        "kind": "delegation",
        "parentEpisodeId": "episode-write",
    }
    decision = evaluate_supervisor_completion(
        episodes=[parent, child],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "delegationHandoff": {
                        "artifactRefs": [{"path": str(artifact), "kind": "workspace_artifact"}],
                        "acceptanceCheck": {"must": {"passed": True}},
                    },
                }
            ],
            "episode-child": [
                {
                    "status": "ready",
                    "artifactRefs": [{"path": str(artifact), "kind": "workspace_artifact"}],
                }
            ],
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "complete"


def test_non_spec_required_write_resolves_workspace_artifact_ref_to_real_file(tmp_path) -> None:
    artifact = tmp_path / "result.md"
    artifact.write_text("done", encoding="utf-8")
    decision = evaluate_supervisor_completion(
        episodes=[_required_write_episode(str(tmp_path))],
        handoffs_by_episode={
            "episode-write": [
                {
                    "status": "ready",
                    "artifactRefs": ["workspace://result.md"],
                    "proofRefs": ["proof://verification"],
                }
            ]
        },
        final_text="Done",
        spec_mode=False,
    )

    assert decision.action == "complete"
