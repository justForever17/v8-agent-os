from __future__ import annotations

from types import SimpleNamespace

import pytest

from erc.command_router import RuntimeCommandRouter
from erc.models import RuntimeCommand


def test_manual_resume_builds_spec_continuation_request(monkeypatch):
    router = RuntimeCommandRouter()
    monkeypatch.setattr(
        router,
        "_scope_payload_for_session",
        lambda _session_id: {
            "workspace_path": "E:/Projects/test2",
            "scope_hint": "workspace",
            "scope_mode": "explicit",
        },
    )
    monkeypatch.setattr(
        "erc.command_router.spec_service.list_specs",
        lambda **_kwargs: {"specs": [{"specId": "spec_demo"}]},
    )
    monkeypatch.setattr(
        "erc.command_router.spec_service.build_brief",
        lambda **_kwargs: {
            "specId": "spec_demo",
            "featureName": "Demo Skill",
            "currentStage": "requirements",
            "approvedStages": ["requirements"],
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "",
                "nextStage": "design",
            },
        },
    )

    request = router._build_manual_resume_chat_request(
        {
            "id": "run_demo",
            "session_id": "session_demo",
            "conversation_id": "session_demo",
            "user_id": "user_demo",
            "metadata": {"provider": "doubao", "model": "doubao-seed-2.0-pro"},
        }
    )

    assert request.data is not None
    assert request.data.spec_mode is True
    assert request.data.spec_id == "spec_demo"
    assert request.resume_value is not None
    assert request.resume_value["specContinuation"]["nextStage"] == "design"
    assert request.messages
    assert "not Supervisor self-approval" in request.messages[0].content
    assert "Engine, not the model, created and bound the canonical specId" in request.messages[0].content
    assert "spec_broker(mode='write_stage', spec_id='spec_demo', stage='design'" in request.messages[0].content


def test_scope_payload_preserves_original_scope_hint(monkeypatch):
    router = RuntimeCommandRouter()
    monkeypatch.setattr(
        "erc.command_router.session_scope_binding_service.get_binding",
        lambda _session_id: SimpleNamespace(
            project_id="test2",
            workspace_id="test2",
            workspace_path="E:/Projects/test2",
            scope_hint=None,
            resolved_scope="project:test2",
        ),
    )

    payload = router._scope_payload_for_session("session_scope")

    assert payload["project_id"] == "test2"
    assert payload["workspace_id"] == "test2"
    assert payload["workspace_path"] == "E:/Projects/test2"
    assert payload["scope_hint"] is None
    assert payload["scope_mode"] == "explicit"


def test_manual_resume_builds_spec_continuation_from_run_metadata_without_scope_binding(monkeypatch):
    router = RuntimeCommandRouter()
    monkeypatch.setattr(router, "_scope_payload_for_session", lambda _session_id: {})
    monkeypatch.setattr(
        "erc.command_router.spec_service.build_brief",
        lambda **_kwargs: {
            "specId": "spec_meta",
            "featureName": "Metadata Spec",
            "currentStage": "requirements",
            "approvedStages": ["requirements"],
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "",
                "nextStage": "design",
            },
        },
    )

    request = router._build_manual_resume_chat_request(
        {
            "id": "run_meta",
            "session_id": "session_meta",
            "conversation_id": "session_meta",
            "user_id": "user_demo",
            "metadata": {
                "provider": "doubao",
                "model": "doubao-seed-2.0-pro",
                "engineeringContextPack": {
                    "workspace": {
                        "workspaceRoot": "E:/Projects/test2",
                    }
                },
            },
        },
        spec_hint={"specId": "spec_meta", "stage": "requirements"},
    )

    assert request.workspace_path is None
    assert request.data is not None
    assert request.data.spec_mode is True
    assert request.data.spec_id == "spec_meta"
    assert request.resume_value is not None
    assert request.resume_value["specContinuation"]["workspacePath"] == "E:/Projects/test2"


def test_spec_continuation_prompt_locks_next_stage_to_tasks():
    content = RuntimeCommandRouter._spec_continuation_prompt(
        {
            "specId": "spec_tasks",
            "approvedStages": ["requirements", "design"],
            "nextStage": "tasks",
            "detailRef": "spec://spec_tasks/design",
            "runtimeExecutionAllowed": False,
        }
    )

    assert "nextStage: tasks" in content
    assert "previous chat history only as background" in content
    assert "stage='tasks'" in content
    assert "stage exactly equals nextStage" in content


def test_spec_continuation_prompt_routes_runtime_execution_with_current_spec():
    content = RuntimeCommandRouter._spec_continuation_prompt(
        {
            "specId": "spec_ready",
            "approvedStages": ["requirements", "design", "tasks"],
            "nextStage": "runtime_execution",
            "detailRef": "spec://spec_ready/tasks",
            "runtimeExecutionAllowed": True,
        }
    )

    assert "runtime_broker(mode='route'" in content
    assert "'specId':'spec_ready'" in content
    assert "wait for the runtime episode handoff" in content
    assert "Do not rewrite requirements/design/tasks" in content
    assert "do not call spec_broker(stage='runtime_execution')" in content
    assert "not approve anything yourself" in content
    assert "do not call memory_broker/web_broker/research_broker" in content
    assert "no drafting detour is needed" in content


def test_spec_approval_resume_schedules_same_run(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []

    def fake_schedule(request, *, transport, run_id=None):
        scheduled.append({"request": request, "transport": transport, "run_id": run_id})
        return run_id or request.resume_run_id

    router.configure(schedule_chat_run=fake_schedule)
    approval = {
        "id": "approval_spec",
        "approval_kind": "spec_stage_approval",
        "run_id": "run_spec",
        "request": {"approvalKind": "spec_stage_approval", "specId": "spec_demo", "stage": "requirements"},
    }
    run_record = {
        "id": "run_spec",
        "session_id": "session_spec",
        "conversation_id": "session_spec",
        "user_id": "user_demo",
        "run_type": "chat",
        "status": "running",
        "metadata": {"provider": "doubao", "model": "doubao-seed-2.0-pro"},
    }

    monkeypatch.setattr("erc.command_router.erc_kernel.approve", lambda *_args, **_kwargs: {"approval": approval})
    monkeypatch.setattr("erc.command_router.db.get_run_record", lambda _run_id: run_record)
    approved_stages = []
    monkeypatch.setattr(
        "erc.command_router.spec_service.approve_stage",
        lambda **kwargs: approved_stages.append(kwargs) or {"ok": True, "nextStage": "design"},
    )
    monkeypatch.setattr(
        router,
        "_scope_payload_for_session",
        lambda _session_id: {
            "workspace_path": "E:/Projects/test2",
            "scope_hint": "workspace",
            "scope_mode": "explicit",
        },
    )
    monkeypatch.setattr(
        "erc.command_router.spec_service.build_brief",
        lambda **_kwargs: {
            "specId": "spec_demo",
            "featureName": "Demo Skill",
            "currentStage": "requirements",
            "approvedStages": ["requirements"],
            "pipelineControl": {
                "runtimeExecutionAllowed": False,
                "blockedByApproval": "",
                "nextStage": "design",
            },
        },
    )

    result = router.dispatch_approval_command(
        RuntimeCommand(
            topic="approval.approve",
            approval_id="approval_spec",
            response={"decision": "approved"},
        )
    )

    assert result is not None
    assert result["resume_scheduled"] is True
    assert result["resumed_run_id"] == "run_spec"
    assert result["spec_continuation"] is True
    assert scheduled and scheduled[0]["transport"] == "system_resume"
    assert scheduled[0]["run_id"] == "run_spec"
    assert scheduled[0]["request"].resume_run_id == "run_spec"
    assert scheduled[0]["request"].data.spec_id == "spec_demo"
    assert approved_stages and approved_stages[0]["spec_id"] == "spec_demo"
    assert approved_stages[0]["stage"] == "requirements"


def test_spec_approval_preflight_keeps_invalid_document_pending(monkeypatch):
    router = RuntimeCommandRouter()
    approval = {
        "id": "approval_tasks_invalid",
        "approval_kind": "spec_stage_approval",
        "run_id": "run_tasks_invalid",
        "status": "pending",
        "request": {
            "approvalKind": "spec_stage_approval",
            "specId": "spec_tasks_invalid",
            "stage": "tasks",
            "workspacePath": "E:/Projects/test2",
        },
    }
    run_record = {
        "id": "run_tasks_invalid",
        "session_id": "session_tasks_invalid",
        "run_type": "chat",
        "status": "waiting_approval",
        "metadata": {},
    }
    approvals = []
    emitted = []
    monkeypatch.setattr("erc.command_router.db.get_pending_approval", lambda _approval_id: approval)
    monkeypatch.setattr("erc.command_router.db.get_run_record", lambda _run_id: run_record)
    monkeypatch.setattr(
        "erc.command_router.spec_service.validate_stage_approval",
        lambda **_kwargs: {
            "ok": False,
            "kind": "spec_stage_analysis_blocked",
            "stage": "tasks",
            "hardBlockers": [{"code": "large_task_missing_mvp_slice"}],
        },
    )
    monkeypatch.setattr(
        "erc.command_router.erc_kernel.approve",
        lambda *_args, **_kwargs: approvals.append(True) or {"approval": approval},
    )
    monkeypatch.setattr(router, "_emit_resume_event", lambda *args, **kwargs: emitted.append((args, kwargs)))

    result = router.dispatch_approval_command(
        RuntimeCommand(
            topic="approval.approve",
            approval_id="approval_tasks_invalid",
            response={"decision": "approved"},
        )
    )

    assert result is not None
    assert result["resume_scheduled"] is False
    assert result["resume_error"] == "spec_stage_approval_preflight_failed"
    assert result["approval"]["status"] == "pending"
    assert approvals == []
    assert emitted and emitted[0][0][1] == "approval.blocked"


@pytest.mark.parametrize("terminal_state", ["completed", "degraded", "failed", "cancelled"])
def test_runtime_episode_handoff_resume_schedules_same_run(monkeypatch, terminal_state):
    router = RuntimeCommandRouter()
    scheduled = []

    def fake_schedule(request, *, transport, run_id=None):
        scheduled.append({"request": request, "transport": transport, "run_id": run_id})
        return run_id or request.resume_run_id

    router.configure(schedule_chat_run=fake_schedule)
    run_record = {
        "id": "run_runtime",
        "session_id": "session_runtime",
        "conversation_id": "session_runtime",
        "user_id": "user_demo",
        "run_type": "chat",
        "status": "running",
        "metadata": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "runtimeEpisodeResume": {"state": "waiting", "reason": "runtime_episode_active_at_stream_end"},
        },
    }
    monkeypatch.setattr("erc.command_router.db.get_run_record", lambda _run_id: run_record)
    monkeypatch.setattr(
        "erc.command_router.db.list_runtime_episodes",
        lambda **_kwargs: [
            {
                "episodeId": "episode_runtime",
                "kind": "engineering",
                "state": terminal_state,
                "sessionId": "session_runtime",
                "runId": "run_runtime",
            }
        ],
    )
    claim_updates = []

    def fake_claim(run_id, **kwargs):
        claim_updates.append({"run_id": run_id, **kwargs})
        return {
            "claimed": True,
            "run_record": {
                **run_record,
                "metadata": {
                    **run_record["metadata"],
                    "runtimeEpisodeResume": kwargs["next_marker"],
                },
            },
        }

    monkeypatch.setattr(
        "erc.command_router.run_service.claim_runtime_episode_resume_schedule",
        fake_claim,
    )
    monkeypatch.setattr(
        router,
        "_scope_payload_for_session",
        lambda _session_id: {
            "workspace_path": "E:/Projects/test3",
            "scope_hint": "workspace",
            "scope_mode": "explicit",
        },
    )

    result = router.schedule_runtime_episode_handoff_resume(
        {
            "episodeId": "episode_runtime",
            "kind": "engineering",
            "state": terminal_state,
            "sessionId": "session_runtime",
            "runId": "run_runtime",
            "inputs": {"specId": "spec_runtime"},
        }
    )

    assert result["resume_scheduled"] is True
    assert result["resumed_run_id"] == "run_runtime"
    assert scheduled and scheduled[0]["transport"] == "system_resume"
    assert scheduled[0]["run_id"] == "run_runtime"
    request = scheduled[0]["request"]
    assert request.resume_run_id == "run_runtime"
    assert request.data.spec_mode is True
    assert request.data.spec_id == "spec_runtime"
    assert request.resume_value["runtimeEpisodeHandoff"]["episodeId"] == "episode_runtime"
    assert request.resume_value["runtimeEpisodeHandoff"]["episodeState"] == terminal_state
    assert "Runtime Episode Terminal" in request.messages[0].content
    assert claim_updates[0]["next_marker"]["state"] == "scheduled"


def test_runtime_episode_handoff_resume_requires_completion_gate_wait_marker(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []
    router.configure(schedule_chat_run=lambda *args, **kwargs: scheduled.append((args, kwargs)) or "run_runtime")
    monkeypatch.setattr(
        "erc.command_router.db.get_run_record",
        lambda _run_id: {
            "id": "run_runtime",
            "session_id": "session_runtime",
            "run_type": "chat",
            "status": "running",
            "metadata": {},
        },
    )

    result = router.schedule_runtime_episode_handoff_resume(
        {
            "episodeId": "episode_runtime",
            "kind": "engineering",
            "state": "completed",
            "sessionId": "session_runtime",
            "runId": "run_runtime",
        }
    )

    assert result["resume_scheduled"] is False
    assert result["resume_error"] == "run_not_waiting_for_runtime_resume"
    assert scheduled == []


def test_supervisor_native_tool_correction_is_system_resume_and_single_use(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []
    run_record = {
        "id": "run_native_correction",
        "session_id": "session_native_correction",
        "conversation_id": "session_native_correction",
        "user_id": "user_demo",
        "run_type": "chat",
        "status": "running",
        "metadata": {"provider": "minimax", "model": "MiniMax-M3"},
    }

    def _schedule(request, *, transport, run_id):
        scheduled.append({"request": request, "transport": transport, "run_id": run_id})
        return run_id

    def _claim(run_id, *, key, expected_state, next_value, expected_status):
        marker = run_record["metadata"].get(key)
        current_state = str(marker.get("state") or "") if isinstance(marker, dict) else ""
        if current_state != expected_state:
            return {"updated": False, "reason": f"metadata_state_mismatch:{current_state or 'missing'}"}
        run_record["metadata"][key] = dict(next_value)
        return {"updated": True, "run_record": dict(run_record)}

    router.configure(schedule_chat_run=_schedule)
    monkeypatch.setattr("erc.command_router.db.get_run_record", lambda _run_id: dict(run_record))
    monkeypatch.setattr("erc.command_router.run_service.update_metadata_key_if_state", _claim)
    monkeypatch.setattr(router, "_emit_resume_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        router,
        "_scope_payload_for_session",
        lambda _session_id: {
            "workspace_path": "E:/Projects/test3",
            "scope_hint": "workspace",
            "scope_mode": "explicit",
        },
    )

    first = router.schedule_supervisor_native_tool_correction(
        "run_native_correction",
        tool_names=["run_system_command", "http_request"],
    )
    second = router.schedule_supervisor_native_tool_correction(
        "run_native_correction",
        tool_names=["run_system_command"],
    )

    assert first["resume_scheduled"] is True
    assert second["resume_scheduled"] is False
    assert second["resume_error"] == "supervisor_native_tool_correction_already_used"
    assert len(scheduled) == 1
    request = scheduled[0]["request"]
    assert scheduled[0]["transport"] == "system_resume"
    assert request.resume_run_id == "run_native_correction"
    assert request.messages[0].role == "system"
    assert "textual pseudo tool markup" in request.messages[0].content
    assert "only corrective continuation" in request.messages[0].content
    assert request.resume_value["supervisorNativeToolCorrection"]["attempt"] == 1


def test_runtime_episode_handoff_resume_waits_for_other_top_level_episode(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []
    router.configure(schedule_chat_run=lambda *args, **kwargs: scheduled.append((args, kwargs)) or "run_runtime")
    monkeypatch.setattr(
        "erc.command_router.db.get_run_record",
        lambda _run_id: {
            "id": "run_runtime",
            "session_id": "session_runtime",
            "run_type": "chat",
            "status": "running",
            "metadata": {"runtimeEpisodeResume": {"state": "waiting"}},
        },
    )
    monkeypatch.setattr(
        "erc.command_router.db.list_runtime_episodes",
        lambda **_kwargs: [
            {"episodeId": "episode_done", "state": "completed", "runId": "run_runtime"},
            {"episodeId": "episode_active", "state": "active", "runId": "run_runtime"},
        ],
    )
    monkeypatch.setattr(
        "erc.command_router.run_service.claim_runtime_episode_resume_schedule",
        lambda *_args, **_kwargs: {"claimed": False, "reason": "top_level_runtime_episode_still_active"},
    )

    result = router.schedule_runtime_episode_handoff_resume(
        {
            "episodeId": "episode_done",
            "kind": "engineering",
            "state": "completed",
            "sessionId": "session_runtime",
            "runId": "run_runtime",
        }
    )

    assert result["resume_scheduled"] is False
    assert result["resume_error"] == "top_level_runtime_episode_still_active"
    assert scheduled == []


def test_runtime_episode_handoff_resume_is_not_scheduled_twice(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []
    router.configure(schedule_chat_run=lambda *args, **kwargs: scheduled.append((args, kwargs)) or "run_runtime")
    monkeypatch.setattr(
        "erc.command_router.db.get_run_record",
        lambda _run_id: {
            "id": "run_runtime",
            "session_id": "session_runtime",
            "run_type": "chat",
            "status": "running",
            "metadata": {
                "runtimeEpisodeResume": {
                    "state": "scheduled",
                    "episodeId": "episode_done",
                }
            },
        },
    )

    result = router.schedule_runtime_episode_handoff_resume(
        {
            "episodeId": "episode_done",
            "kind": "engineering",
            "state": "completed",
            "sessionId": "session_runtime",
            "runId": "run_runtime",
        }
    )

    assert result["resume_scheduled"] is False
    assert result["resume_error"] == "runtime_episode_resume_already_scheduled"
    assert scheduled == []


def test_runtime_episode_handoff_resume_claim_blocks_duplicate_scheduler(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []
    router.configure(schedule_chat_run=lambda *args, **kwargs: scheduled.append((args, kwargs)) or "run_runtime")
    monkeypatch.setattr(
        "erc.command_router.db.get_run_record",
        lambda _run_id: {
            "id": "run_runtime",
            "session_id": "session_runtime",
            "run_type": "chat",
            "status": "running",
            "metadata": {"runtimeEpisodeResume": {"state": "waiting"}},
        },
    )
    monkeypatch.setattr(
        "erc.command_router.run_service.claim_runtime_episode_resume_schedule",
        lambda *_args, **_kwargs: {"claimed": False, "reason": "runtime_episode_resume_already_scheduled"},
    )

    result = router.schedule_runtime_episode_handoff_resume(
        {
            "episodeId": "episode_done",
            "kind": "engineering",
            "state": "completed",
            "sessionId": "session_runtime",
            "runId": "run_runtime",
        }
    )

    assert result["resume_scheduled"] is False
    assert result["resume_error"] == "runtime_episode_resume_already_scheduled"
    assert scheduled == []


def test_runtime_episode_worker_failure_resets_marker_and_reschedules(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []

    def fake_schedule(request, *, transport, run_id=None):
        scheduled.append({"request": request, "transport": transport, "run_id": run_id})
        return run_id or request.resume_run_id

    router.configure(schedule_chat_run=fake_schedule)
    marker = {
        "state": "scheduled",
        "resumeKind": "runtime_episode_terminal",
        "episodeId": "episode_runtime",
        "episodeState": "completed",
    }
    run_record = {
        "id": "run_runtime",
        "session_id": "session_runtime",
        "conversation_id": "session_runtime",
        "user_id": "user_demo",
        "run_type": "chat",
        "status": "running",
        "metadata": {"runtimeEpisodeResume": marker},
    }
    episode = {
        "episodeId": "episode_runtime",
        "kind": "engineering",
        "state": "completed",
        "sessionId": "session_runtime",
        "runId": "run_runtime",
    }
    updates = []
    claims = []

    def fake_get_run_record(_run_id):
        return run_record

    def fake_update_key(run_id, **kwargs):
        updates.append({"run_id": run_id, **kwargs})
        run_record["metadata"]["runtimeEpisodeResume"] = kwargs["next_value"]
        return {"updated": True, "run_record": run_record}

    def fake_claim(run_id, **kwargs):
        claims.append({"run_id": run_id, **kwargs})
        run_record["metadata"]["runtimeEpisodeResume"] = kwargs["next_marker"]
        return {"claimed": True, "run_record": run_record}

    monkeypatch.setattr("erc.command_router.db.get_run_record", fake_get_run_record)
    monkeypatch.setattr("erc.command_router.db.get_runtime_episode", lambda _episode_id: episode)
    monkeypatch.setattr("erc.command_router.run_service.update_metadata_key_if_state", fake_update_key)
    monkeypatch.setattr("erc.command_router.run_service.claim_runtime_episode_resume_schedule", fake_claim)

    result = router.recover_runtime_episode_resume_worker_failure(
        "run_runtime",
        error_message="RuntimeError: worker crashed",
    )

    assert result["resume_scheduled"] is True
    assert result["worker_recovery"] is True
    assert result["worker_crash_count"] == 1
    assert updates[0]["expected_state"] == "scheduled"
    assert updates[0]["next_value"]["state"] == "waiting"
    assert updates[0]["next_value"]["workerCrashCount"] == 1
    assert claims[0]["next_marker"]["state"] == "scheduled"
    assert claims[0]["next_marker"]["workerCrashCount"] == 1
    assert scheduled and scheduled[0]["transport"] == "system_resume"


def test_runtime_episode_worker_failure_stops_after_retry_limit(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []
    router.configure(schedule_chat_run=lambda *args, **kwargs: scheduled.append((args, kwargs)) or "run_runtime")
    run_record = {
        "id": "run_runtime",
        "session_id": "session_runtime",
        "run_type": "chat",
        "status": "running",
        "metadata": {
            "runtimeEpisodeResume": {
                "state": "scheduled",
                "resumeKind": "runtime_episode_terminal",
                "episodeId": "episode_runtime",
                "workerCrashCount": 2,
            }
        },
    }
    updates = []
    emitted = []
    monkeypatch.setattr("erc.command_router.db.get_run_record", lambda _run_id: run_record)
    monkeypatch.setattr(
        "erc.command_router.run_service.update_metadata_key_if_state",
        lambda run_id, **kwargs: updates.append({"run_id": run_id, **kwargs}) or {"updated": True, "run_record": run_record},
    )
    monkeypatch.setattr(router, "_emit_resume_event", lambda *args, **kwargs: emitted.append((args, kwargs)))

    result = router.recover_runtime_episode_resume_worker_failure("run_runtime", error_message="boom")

    assert result["resume_scheduled"] is False
    assert result["resume_error"] == "chat_resume_worker_retry_limit_exceeded"
    assert updates[0]["next_value"]["state"] == "failed"
    assert updates[0]["next_value"]["workerCrashCount"] == 3
    assert scheduled == []
    assert emitted


def test_spec_approval_resume_failure_restores_waiting_approval(monkeypatch):
    router = RuntimeCommandRouter()
    approval = {
        "id": "approval_spec",
        "approval_kind": "spec_stage_approval",
        "run_id": "run_spec",
        "request": {"approvalKind": "spec_stage_approval", "specId": "spec_demo", "stage": "requirements"},
    }
    run_record = {
        "id": "run_spec",
        "session_id": "session_spec",
        "conversation_id": "session_spec",
        "user_id": "user_demo",
        "run_type": "chat",
        "status": "running",
        "metadata": {"provider": "doubao", "model": "doubao-seed-2.0-pro"},
    }
    transitions = []
    workflow_updates = []

    monkeypatch.setattr("erc.command_router.erc_kernel.approve", lambda *_args, **_kwargs: {"approval": approval})
    monkeypatch.setattr("erc.command_router.db.get_run_record", lambda _run_id: run_record)
    monkeypatch.setattr(
        router,
        "_scope_payload_for_session",
        lambda _session_id: {
            "workspace_path": "E:/Projects/test2",
            "scope_hint": "workspace",
            "scope_mode": "explicit",
        },
    )
    monkeypatch.setattr(
        "erc.command_router.spec_service.approve_stage",
        lambda **_kwargs: {"ok": True, "nextStage": "design"},
    )
    monkeypatch.setattr(
        "erc.command_router.run_service.transition_run_if_status",
        lambda run_id, **kwargs: transitions.append({"run_id": run_id, **kwargs}) or {"updated": True, "run_record": run_record},
    )
    monkeypatch.setattr(
        "erc.command_router.workflow_ledger_service.sync_run_status",
        lambda run_id, **kwargs: workflow_updates.append({"run_id": run_id, **kwargs}),
    )

    result = router.dispatch_approval_command(
        RuntimeCommand(
            topic="approval.approve",
            approval_id="approval_spec",
            response={"decision": "approved"},
        )
    )

    assert result is not None
    assert result["resume_scheduled"] is False
    assert result["resume_error"] == "spec_approval_resume_not_scheduled"
    assert transitions and transitions[0]["status"] == "waiting_approval"
    assert transitions[0]["metadata"]["approval_resume_scheduled"] is False
    assert workflow_updates and workflow_updates[0]["run_status"] == "waiting_approval"


def test_spec_approval_resume_failure_restore_is_conditional(monkeypatch):
    router = RuntimeCommandRouter()
    approval = {
        "id": "approval_spec",
        "approval_kind": "spec_stage_approval",
        "run_id": "run_spec",
    }
    run_record = {
        "id": "run_spec",
        "session_id": "session_spec",
        "conversation_id": "session_spec",
        "run_type": "chat",
        "status": "running",
        "metadata": {},
    }
    transitions = []
    workflow_updates = []
    emitted = []

    monkeypatch.setattr("erc.command_router.db.get_run_record", lambda _run_id: run_record)
    monkeypatch.setattr(
        "erc.command_router.run_service.transition_run_if_status",
        lambda run_id, **kwargs: transitions.append({"run_id": run_id, **kwargs})
        or {"updated": False, "reason": "status_mismatch:completed", "currentStatus": "completed"},
    )
    monkeypatch.setattr(
        "erc.command_router.workflow_ledger_service.sync_run_status",
        lambda run_id, **kwargs: workflow_updates.append({"run_id": run_id, **kwargs}),
    )
    monkeypatch.setattr(router, "_emit_resume_event", lambda *args, **kwargs: emitted.append((args, kwargs)))

    router._restore_waiting_approval_after_resume_failure(approval, reason="scheduler_failed")

    assert transitions[0]["expected_statuses"] == {"running"}
    assert transitions[0]["status"] == "waiting_approval"
    assert workflow_updates == []
    assert emitted == []


def test_spec_rejection_with_feedback_resumes_same_run_for_revision(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []

    def fake_schedule(request, *, transport, run_id=None):
        scheduled.append({"request": request, "transport": transport, "run_id": run_id})
        return run_id

    router.configure(schedule_chat_run=fake_schedule)
    approval = {
        "id": "approval_spec_revision",
        "approval_kind": "spec_stage_approval",
        "run_id": "run_spec_revision",
        "request": {
            "approvalKind": "spec_stage_approval",
            "specId": "spec_revision",
            "stage": "requirements",
            "detailRef": "spec://spec_revision/requirements",
        },
    }
    run_record = {
        "id": "run_spec_revision",
        "session_id": "session_spec_revision",
        "conversation_id": "session_spec_revision",
        "user_id": "user_demo",
        "run_type": "chat",
        "status": "waiting_input",
        "metadata": {"provider": "doubao", "model": "doubao-seed-2.0-pro"},
    }
    monkeypatch.setattr(
        "erc.command_router.erc_kernel.reject",
        lambda *_args, **_kwargs: {"approval": approval, "transition_event": {}, "command_event": {}},
    )
    monkeypatch.setattr("erc.command_router.db.get_run_record", lambda _run_id: run_record)
    monkeypatch.setattr(
        "erc.command_router.erc_kernel.resume_run",
        lambda *_args, **_kwargs: {"transition_event": {"topic": "run.state.changed"}, "command_event": {"topic": "run.resumed"}},
    )
    monkeypatch.setattr(
        router,
        "_scope_payload_for_session",
        lambda _session_id: {
            "workspace_path": "E:/Projects/test2",
            "scope_hint": "workspace",
            "scope_mode": "explicit",
        },
    )

    result = router.dispatch_approval_command(
        RuntimeCommand(
            topic="approval.reject",
            approval_id="approval_spec_revision",
            response={"answer": "删除内部路径和工具语法。", "approved": False},
        )
    )

    assert result is not None
    assert result["resume_scheduled"] is True
    assert result["spec_revision"] is True
    assert result["resumed_run_id"] == "run_spec_revision"
    assert scheduled and scheduled[0]["transport"] == "system_resume"
    assert scheduled[0]["run_id"] == "run_spec_revision"
    request = scheduled[0]["request"]
    assert request.resume_run_id == "run_spec_revision"
    assert request.data is not None
    assert request.data.spec_mode is True
    assert request.data.spec_id == "spec_revision"
    assert request.resume_value["specRevision"]["feedback"] == "删除内部路径和工具语法。"
    assert "do not call ask_user again" in request.messages[0].content
    assert "mode='rewrite_stage'" in request.messages[0].content
    assert "absolute local paths" in request.messages[0].content


def test_spec_rejection_without_feedback_stays_waiting_for_user(monkeypatch):
    router = RuntimeCommandRouter()
    scheduled = []
    router.configure(schedule_chat_run=lambda *args, **kwargs: scheduled.append((args, kwargs)) or "run_spec")
    approval = {
        "id": "approval_spec",
        "approval_kind": "spec_stage_approval",
        "run_id": "run_spec",
        "request": {"approvalKind": "spec_stage_approval", "specId": "spec_demo", "stage": "requirements"},
    }
    monkeypatch.setattr(
        "erc.command_router.erc_kernel.reject",
        lambda *_args, **_kwargs: {"approval": approval, "transition_event": {}, "command_event": {}},
    )

    result = router.dispatch_approval_command(
        RuntimeCommand(
            topic="approval.reject",
            approval_id="approval_spec",
            response={"answer": "", "approved": False},
        )
    )

    assert result is not None
    assert "resume_scheduled" not in result
    assert scheduled == []
