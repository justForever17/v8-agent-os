from types import SimpleNamespace
from unittest.mock import Mock, patch

from graph.supervisor_turn import (
    _coerce_recoverable_failure_response,
    _filter_tool_names,
    _runtime_episode_handoff_ready,
    _runtime_episode_recoverable_failure,
    _runtime_handoff_final_response,
    _runtime_handoff_final_text,
    _runtime_handoff_final_message,
    _runtime_recoverable_failure_final_response,
    _runtime_recoverable_failure_final_text,
    _runtime_recoverable_failure_message,
    _should_hide_todo_tools_for_direct_writing,
)
from runtimes.chat.supervisor_completion_gate import evaluate_supervisor_completion
from runtimes.chat.runtime import ChatRuntime


def test_runtime_episode_handoff_ready_requires_resume_terminal_state():
    assert _runtime_episode_handoff_ready(
        {
            "planner_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "handoff_ready",
                "handoffCount": 2,
            }
        }
    )

    assert not _runtime_episode_handoff_ready(
        {
            "planner_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "wait_episode",
                "state": "handoff_ready",
                "handoffCount": 2,
            }
        }
    )

    assert not _runtime_episode_handoff_ready(
        {
            "planner_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "episode_terminal",
                "handoffCount": 0,
            }
        }
    )


def test_runtime_episode_degraded_handoff_ready_is_terminal():
    assert _runtime_episode_handoff_ready(
        {
            "planner_dispatch_status": {
                "mode": "runtime_episode",
                "nextAction": "resume_supervisor",
                "state": "degraded_handoff_ready",
                "handoffCount": 1,
            }
        }
    )


def test_runtime_handoff_final_message_blocks_post_handoff_tool_loop():
    message = _runtime_handoff_final_message()
    content = str(message.content)
    assert "Do not call tools" in content
    assert "produce one concise user-facing completion/status summary" in content


def test_runtime_handoff_final_response_is_deterministic_from_handoff_refs():
    state = {
        "planner_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "resume_supervisor",
            "state": "handoff_ready",
            "handoffCount": 1,
        },
        "current_route_context": {
            "handoffRefs": [
                {
                    "kind": "engineering_patch_bundle",
                    "status": "ready",
                    "compactSummary": "Engineering Runtime 已产出 work_plan_ready。",
                }
            ]
        },
    }
    text = _runtime_handoff_final_text(state)
    response = _runtime_handoff_final_response(state)
    assert "运行时链路已经完成并回流" in text
    assert "engineering_patch_bundle / ready" in text
    assert "work_plan_ready" in str(response.content)


def test_runtime_recoverable_failure_message_blocks_false_completion():
    state = {
        "planner_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "recoverable_failure",
            "state": "episode_failed",
            "reason": "artifact_acceptance_failed",
        }
    }
    assert _runtime_episode_recoverable_failure(state)
    message = _runtime_recoverable_failure_message(state)
    content = str(message.content)
    assert "MUST NOT claim" in content
    assert "artifact_acceptance_failed" in content


def test_runtime_recoverable_failure_final_response_is_deterministic():
    state = {
        "planner_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "recoverable_failure",
            "state": "episode_failed",
            "reason": "worker_acceptance_failed",
            "failedEpisodeCount": 1,
            "failedHandoffCount": 1,
        }
    }
    text = _runtime_recoverable_failure_final_text(state)
    response = _runtime_recoverable_failure_final_response(state)
    assert "还没有真正完成" in text
    assert "worker_acceptance_failed" in text
    assert "失败 episode 数：1" in str(response.content)


def test_runtime_recoverable_failure_response_is_coerced_when_model_claims_success():
    state = {
        "planner_dispatch_status": {
            "mode": "runtime_episode",
            "nextAction": "recoverable_failure",
            "state": "episode_failed",
            "reason": "artifact_acceptance_failed",
        }
    }
    response = SimpleNamespace(content="已完整生成并交付。", additional_kwargs={})
    coerced = _coerce_recoverable_failure_response(response, state)
    assert "还没有真正完成" in coerced.content
    assert "artifact_acceptance_failed" in coerced.content


def test_direct_writing_skill_plan_hides_supervisor_todo_tools():
    state = {
        "task_shape_hint": {
            "primaryTaskShape": "writing",
            "writingRoute": {
                "present": True,
                "mode": "direct_supervisor",
                "requiresSkillExecution": True,
                "requiresArtifact": False,
                "requiresResearch": False,
                "skillName": "huashu-nuwa",
            },
        }
    }

    assert _should_hide_todo_tools_for_direct_writing(
        state,
        "使用 huashu-nuwa 给我做执行计划，只输出计划，不写文件、不创建 skill。",
    )
    tools = [SimpleNamespace(name="fetch_skill_instructions"), SimpleNamespace(name="write_todos"), SimpleNamespace(name="update_todo")]
    filtered = _filter_tool_names(tools, {"write_todos", "update_todo"})
    assert [tool.name for tool in filtered] == ["fetch_skill_instructions"]


def test_runtime_or_artifact_writing_keeps_supervisor_todo_tools_available():
    state = {
        "task_shape_hint": {
            "primaryTaskShape": "writing",
            "writingRoute": {
                "present": True,
                "mode": "skill_subagent",
                "requiresSkillExecution": True,
                "requiresArtifact": True,
                "requiresResearch": True,
                "recommendedFamily": "writing",
            },
        }
    }

    assert not _should_hide_todo_tools_for_direct_writing(state, "调研后生成 skill 并保存到工作区。")


def test_completion_gate_blocks_active_runtime_episode():
    decision = evaluate_supervisor_completion(
        episodes=[{"episodeId": "episode_research", "state": "active", "kind": "research"}],
        final_text="开始并行搜索~",
    )

    assert decision.action == "fail"
    assert decision.reason == "runtime_episode_active_at_stream_end"


def test_completion_gate_blocks_research_plan_claimed_as_ready_evidence():
    decision = evaluate_supervisor_completion(
        episodes=[{"episodeId": "episode_research", "state": "completed", "kind": "research"}],
        handoffs_by_episode={
            "episode_research": [
                {
                    "handoffRefId": "handoff_research",
                    "kind": "research_evidence_bundle",
                    "status": "ready",
                    "runMode": "plan",
                }
            ]
        },
        final_text="现在我重新启动调研。",
    )

    assert decision.action == "fail"
    assert decision.reason == "research_plan_only_claimed_evidence_ready"


def test_completion_gate_accepts_failed_episode_with_degraded_handoff():
    decision = evaluate_supervisor_completion(
        episodes=[{"episodeId": "episode_engineering", "state": "failed", "kind": "engineering"}],
        handoffs_by_episode={
            "episode_engineering": [
                {
                    "handoffRefId": "handoff_engineering_degraded",
                    "kind": "engineering",
                    "status": "degraded",
                    "engineeringState": "recoverable_failed",
                    "errorCode": "skill_artifact_validation_failed",
                }
            ]
        },
        final_text="技能产物没有通过验证，需要继续修复。",
    )

    assert decision.action == "complete"
    assert decision.reason == "eligible"


def test_completion_gate_keeps_spec_stage_waiting_for_approval():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={
            "specId": "spec_demo",
            "currentStage": "requirements",
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "requirements",
                "blockedReason": "approval_required",
            },
        },
        final_text="需求文档已生成，请审批。",
    )

    assert decision.action == "waiting_approval"
    assert decision.reason == "approval_required"


def test_completion_gate_allows_fast_approval_continuation_window():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={
            "specId": "spec_demo",
            "currentStage": "requirements",
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "",
                "blockedReason": "",
                "nextStage": "design",
            },
        },
        final_text="需求已经审批，后面继续。",
    )

    assert decision.action == "complete"
    assert decision.reason == "eligible"


def test_completion_gate_blocks_spec_mode_without_created_stage():
    decision = evaluate_supervisor_completion(
        spec_mode=True,
        spec_brief={},
        final_text="接下来我会编写需求文档。",
    )

    assert decision.action == "fail"
    assert decision.reason == "spec_stage_not_created"


def test_completion_spec_brief_uses_current_run_spec_tool_result():
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_brief={}, spec_id=""),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
    )
    expected = {
        "specId": "spec_live",
        "currentStage": "requirements",
        "pipelineControl": {"blockedByApproval": "requirements", "blockedReason": "approval_required"},
    }

    with (
        patch(
            "runtimes.chat.runtime.db.get_runtime_events",
            return_value=[
                {
                    "run_id": "run-spec",
                    "topic": "tool.finished",
                    "payload": {"tool": {"toolName": "spec_broker", "result": {"specId": "spec_live"}}},
                }
            ],
        ),
        patch("runtimes.chat.runtime.db.get_chat_canonical_messages", return_value=[]),
        patch("runtimes.chat.runtime.spec_service.build_brief", return_value=expected) as build_brief,
    ):
        brief = ChatRuntime._completion_spec_brief(chat_run)

    assert brief == expected
    build_brief.assert_called_once_with(workspace_path="E:/Projects/test3", spec_id="spec_live")


def test_completion_spec_brief_extracts_spec_id_from_command_tool_message():
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_brief={}, spec_id=""),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
    )
    expected = {
        "specId": "spec_command_live",
        "currentStage": "requirements",
        "pipelineControl": {"blockedByApproval": "requirements", "blockedReason": "approval_required"},
    }

    with (
        patch(
            "runtimes.chat.runtime.db.get_runtime_events",
            return_value=[
                {
                    "run_id": "run-spec",
                    "topic": "tool.finished",
                    "payload": {
                        "tool": {
                            "toolName": "spec_broker",
                            "result": {
                                "graph": None,
                                "update": {
                                    "messages": [
                                        {
                                            "content": (
                                                '{\n'
                                                '  "ok": true,\n'
                                                '  "kind": "spec_stage_waiting_user_approval",\n'
                                                '  "specId": "spec_command_live"\n'
                                                '}'
                                            )
                                        }
                                    ]
                                },
                            },
                        }
                    },
                }
            ],
        ),
        patch("runtimes.chat.runtime.db.get_chat_canonical_messages", return_value=[]),
        patch("runtimes.chat.runtime.spec_service.build_brief", return_value=expected) as build_brief,
    ):
        brief = ChatRuntime._completion_spec_brief(chat_run)

    assert brief == expected
    build_brief.assert_called_once_with(workspace_path="E:/Projects/test3", spec_id="spec_command_live")


def test_finalize_success_waits_for_spec_stage_approval_from_command_tool_message():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    completion_brief = {
        "specId": "spec_command_live",
        "currentStage": "requirements",
        "pipelineControl": {"blockedByApproval": "requirements", "blockedReason": "approval_required"},
    }

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="需求文档已准备好，等待审批。"),
        patch.object(ChatRuntime, "_completion_spec_brief", return_value=completion_brief),
        patch("runtimes.chat.runtime.spec_service.mark_delivered") as mark_delivered,
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "waiting_approval"
    run_handle.transition.assert_called_once_with(
        "waiting_approval",
        reason="approval_required",
        node="completion_gate",
    )
    run_handle.complete.assert_not_called()
    mark_delivered.assert_not_called()
    assert emitted[0][0] == "run.completion.waiting_for_spec_approval"
    assert emitted[0][1]["specId"] == "spec_command_live"


def test_finalize_success_marks_runtime_allowed_spec_delivered():
    emitted = []
    run_handle = SimpleNamespace(
        complete=Mock(),
        transition=Mock(),
        fail=Mock(),
    )
    chat_run = SimpleNamespace(
        prepared=SimpleNamespace(spec_mode=True, spec_id="spec_ready", spec_brief={}),
        scope_result=SimpleNamespace(binding=SimpleNamespace(workspace_path="E:/Projects/test3")),
        session_id="session-spec",
        active_run_id="run-spec",
        run_handle=run_handle,
        emit_runtime_event=lambda topic, payload, **kwargs: emitted.append((topic, payload, kwargs)),
    )
    completion_brief = {
        "specId": "spec_ready",
        "currentStage": "tasks",
        "pipelineControl": {"runtimeExecutionAllowed": True},
    }

    with (
        patch("runtimes.chat.runtime.db.list_runtime_episodes", return_value=[]),
        patch("runtimes.chat.runtime.db.list_runtime_episode_handoffs", return_value=[]),
        patch.object(ChatRuntime, "_completion_final_text", return_value="交付完成。"),
        patch.object(ChatRuntime, "_completion_spec_brief", return_value=completion_brief),
        patch(
            "runtimes.chat.runtime.spec_service.mark_delivered",
            return_value={"ok": True, "lifecycle": "delivered", "deliveredAt": "2026-06-15T00:00:00Z"},
        ) as mark_delivered,
    ):
        result = ChatRuntime().finalize_success_run(chat_run)

    assert result["status"] == "finished"
    mark_delivered.assert_called_once_with(
        workspace_path="E:/Projects/test3",
        spec_id="spec_ready",
        run_id="run-spec",
        session_id="session-spec",
    )
    run_handle.complete.assert_called_once_with(reason="stream_finished", node="run_manager")
    assert emitted[0][0] == "spec.lifecycle.delivered"
    assert emitted[0][1]["specId"] == "spec_ready"
