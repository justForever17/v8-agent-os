from __future__ import annotations

from types import SimpleNamespace

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
        "erc.command_router.run_service.transition_run",
        lambda run_id, **kwargs: transitions.append({"run_id": run_id, **kwargs}),
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
