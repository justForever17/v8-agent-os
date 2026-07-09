from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.scripts import run_spec_mode_project_live_audit as audit


def test_submit_omits_model_profile_for_admin_configured_model(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        captured["url"] = url
        captured["method"] = method
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {"accepted": True, "runId": "run-admin-default"}

    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    run_id, _latency, response = audit._submit(
        "http://127.0.0.1:9530",
        session_id="session-1",
        workspace=tmp_path,
        model_profile="",
        safety_approval_mode="reduced",
        prompt="hello",
        client_tag="client-1",
    )

    payload = captured["payload"]
    assert run_id == "run-admin-default"
    assert response["accepted"] is True
    assert payload["workspacePath"] == str(tmp_path)
    assert payload["data"]["specMode"] is True
    assert payload["data"]["specCommand"] == {"action": "new"}
    assert payload["data"]["safetyApprovalMode"] == "reduced"
    assert "modelProfile" not in payload["data"]
    assert audit._effective_model_label("") == "admin-configured supervisor model"


def test_submit_keeps_explicit_model_override(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        captured["payload"] = payload
        return {"accepted": True, "run_id": "run-explicit"}

    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    run_id, _latency, _response = audit._submit(
        "http://127.0.0.1:9530/v1",
        session_id="session-2",
        workspace=tmp_path,
        model_profile="deepseek-v4-flash",
        safety_approval_mode="minimal",
        prompt="hello",
        client_tag="client-2",
    )

    assert run_id == "run-explicit"
    assert captured["payload"]["data"]["specCommand"] == {"action": "new"}
    assert captured["payload"]["data"]["modelProfile"] == "deepseek-v4-flash"
    assert captured["payload"]["data"]["safetyApprovalMode"] == "minimal"
    assert audit._uses_model_override("deepseek-v4-flash") is True
    assert audit._uses_model_override("engine-default") is False


def test_workspace_preflight_reports_missing_workspace_without_creating_it(tmp_path: Path) -> None:
    missing = tmp_path / "missing-project"

    findings = audit._workspace_preflight(missing)

    assert findings == [
        {
            "severity": "P0",
            "code": "workspace_missing",
            "summary": f"工作区不存在：{missing}。请先创建/选择并信任项目工作区后再运行 live 闭环。",
        }
    ]
    assert not missing.exists()


def test_workspace_blocker_detection_reads_nested_http_payload() -> None:
    payload = {
        "status": 400,
        "body": {
            "detail": {
                "error": "workspace_trust_required",
                "summary": "先选择并信任项目工作区",
            }
        },
    }

    assert audit._find_workspace_blocker_code(payload) == "workspace_trust_required"
    assert audit._find_workspace_blocker_code({"payload": "workspace_side_effect_blocked by authority"}) == "workspace_side_effect_blocked"
    assert audit._find_workspace_blocker_code({"detail": {"error": "unrelated"}}) == ""


def test_live_workspace_trust_preflight_registers_trusted_project(monkeypatch: Any, tmp_path: Path) -> None:
    saved_payloads: list[dict[str, Any]] = []

    class FakeRegistry:
        def find_project_for_workspace(self, *, workspace_path: str) -> None:
            return None

        def save_project(self, payload: dict[str, Any]) -> SimpleNamespace:
            saved_payloads.append(payload)
            return SimpleNamespace(
                project_id="spec-live-project",
                workspace_id="spec-live-project",
                workspace_trust_state="trusted",
                workspace_trust_source="user_confirmed",
            )

    class FakeAuthority:
        def resolve(self, **_: Any) -> SimpleNamespace:
            return SimpleNamespace(
                as_dict=lambda: {
                    "sideEffectsAllowed": True,
                    "trustState": "trusted",
                    "trustSource": "user_confirmed",
                    "source": "explicit_workspace_path",
                    "projectId": "spec-live-project",
                    "workspaceId": "spec-live-project",
                }
            )

    monkeypatch.setattr("runtimes.memory.project_registry.project_registry_service", FakeRegistry())
    monkeypatch.setattr("core.workspace_authority.workspace_authority_service", FakeAuthority())

    findings, event = audit._ensure_live_workspace_trusted(tmp_path)

    assert findings == []
    assert saved_payloads[0]["workspacePath"] == str(tmp_path)
    assert saved_payloads[0]["workspaceTrustState"] == "trusted"
    assert saved_payloads[0]["workspaceTrustSource"] == "user_confirmed"
    assert event["action"] == "registered_trusted_project"
    assert event["authority"]["sideEffectsAllowed"] is True


def test_record_workspace_blocker_is_deduped() -> None:
    result = audit.SpecLiveResult(session_id="s")

    audit._record_workspace_blocker(result, "workspace_binding_required", {"detail": "first"})
    audit._record_workspace_blocker(result, "workspace_binding_required", {"detail": "second"})

    assert [item["code"] for item in result.findings] == ["workspace_binding_required"]
    assert len(result.key_events) == 2


def test_pseudo_tool_call_detection_records_actionable_failure() -> None:
    result = audit.SpecLiveResult(session_id="s")

    assert audit._contains_pseudo_tool_call({"payload": "before <invoke name=\"spec_broker\"> after"})
    audit._record_pseudo_tool_call_observed(result, {"content": '<tool_call><invoke name="spec_broker"></invoke></tool_call>'})
    audit._maybe_record_pseudo_tool_call_failure(result, {"topic": "extension.execution.completed"}, {"hasToolCalls": True})
    assert result.findings == []

    audit._maybe_record_pseudo_tool_call_failure(result, {"topic": "extension.execution.completed"}, {"hasToolCalls": False})
    audit._maybe_record_pseudo_tool_call_failure(result, {"topic": "extension.execution.completed"}, {"hasToolCalls": False})

    assert [item["code"] for item in result.findings] == ["model_pseudo_tool_call_not_executed"]
    assert len([item for item in result.key_events if "modelPseudoToolCall" in item]) == 1


def test_pseudo_tool_call_detection_ignores_user_prompt_markers() -> None:
    result = audit.SpecLiveResult(session_id="session-live")
    event = {
        "seq": 1,
        "session_id": "session-live",
        "run_id": "run-live",
        "topic": "message.user.recorded",
        "payload": {
            "role": "user",
            "content": "禁止输出 `<tool_call>` 或 `<invoke name=...>` 伪工具块。",
        },
    }

    assert audit._contains_pseudo_tool_call(event)
    assert audit._event_can_contain_model_pseudo_tool_call(event, event["payload"]) is False
    if audit._event_can_contain_model_pseudo_tool_call(event, event["payload"]):
        audit._record_pseudo_tool_call_observed(result, event)
    audit._maybe_record_pseudo_tool_call_failure(result, {"topic": "extension.execution.completed"}, {"hasToolCalls": False})

    assert result.findings == []
    assert result.key_events == []


def test_pseudo_tool_call_detection_ignores_context_policy_markers() -> None:
    event = {
        "topic": "context.prepared",
        "payload": {
            "recall_audit": {
                "query": "Do not emit textual pseudo tool syntax such as <tool_call> or <invoke name=...>.",
            },
        },
    }

    assert audit._contains_pseudo_tool_call(event)
    assert audit._event_can_contain_model_pseudo_tool_call(event, event["payload"]) is False


def test_collect_durable_dedupes_seen_runtime_events(monkeypatch: Any) -> None:
    event = {
        "seq": 1,
        "session_id": "session-live",
        "run_id": "run-live",
        "topic": "extension.execution.completed",
        "payload": {
            "hasToolCalls": False,
            "messagePreview": "<invoke name=\"spec_broker\">pseudo</invoke>",
        },
    }

    class FakeDb:
        def list_runtime_episodes(self, **_: Any) -> list[dict[str, Any]]:
            return []

        def list_runtime_episode_handoffs(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            return []

        def get_runtime_events(self, session_id: str) -> list[dict[str, Any]]:
            assert session_id == "session-live"
            return [event]

        def get_runtime_events_for_run(self, *_: Any, **__: Any) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr("core.database.db", FakeDb())

    result = audit.SpecLiveResult(session_id="session-live")
    result.run_ids.append("run-live")
    audit._collect_durable(result)
    assert audit._finding_exists(result, "model_pseudo_tool_call_not_executed")

    audit._clear_findings(result, "model_pseudo_tool_call_not_executed")
    audit._collect_durable(result)

    assert not audit._finding_exists(result, "model_pseudo_tool_call_not_executed")
    assert len(result.seen_runtime_event_keys) == 1


def test_auto_respond_pending_spec_ask_user_uses_real_response_endpoint(monkeypatch: Any) -> None:
    posted: dict[str, Any] = {}

    def fake_list_ask_user_interactions(session_id: str, status: str) -> list[dict[str, Any]]:
        assert session_id == "session-live"
        assert status == "pending"
        return [
            {
                "id": "ask-1",
                "question": "请确认需求边界",
                "request": {
                    "interactionKind": "ask_user",
                    "question": "请确认需求边界",
                    "specContext": {
                        "kind": "spec_clarification",
                        "stage": "requirements",
                        "featureName": "spec-live",
                    },
                },
            }
        ]

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        posted["url"] = url
        posted["method"] = method
        posted["payload"] = payload
        return {
            "resume_scheduled": True,
            "resumed_run_id": "run-1",
            "spec_clarification": {"recorded": True, "stage": "requirements"},
        }

    monkeypatch.setattr("core.database.db.list_ask_user_interactions", fake_list_ask_user_interactions)
    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    result = audit.SpecLiveResult(session_id="session-live")
    responses = audit._auto_respond_pending_spec_ask_user(
        "http://127.0.0.1:9530",
        result,
        stage="requirements",
        marker="SPEC_LIVE_TEST",
        target_rel=".v8/live-audit/spec-mode-v2/test",
        workspace=Path("E:/Projects/test3"),
    )

    answer = posted["payload"]["response"]["answer"]
    assert posted["url"].endswith("/v1/ask-user/ask-1/respond")
    assert posted["method"] == "POST"
    assert "SPEC_LIVE_TEST" in answer
    assert ".v8/live-audit/spec-mode-v2/test" in answer
    assert "REQ-###" in answer
    assert responses[0]["status"] == "responded"
    assert result.ask_user_responses[0]["stage"] == "requirements"
    assert "run-1" in result.run_ids


def test_approve_stage_reports_spec_analysis_blockers(monkeypatch: Any) -> None:
    def fake_list_pending_approvals(session_id: str, status: str) -> list[dict[str, Any]]:
        assert session_id == "session-live"
        assert status == "pending"
        return [
            {
                "id": "approval-tasks",
                "approval_kind": "spec_stage_approval",
                "request": {"specId": "spec-live", "stage": "tasks"},
            }
        ]

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        assert url.endswith("/v1/approvals/approval-tasks/approve")
        assert method == "POST"
        return {
            "resume_scheduled": False,
            "resume_error": "spec_stage_approval_apply_failed",
            "spec_stage_approval": {
                "ok": False,
                "kind": "spec_stage_analysis_blocked",
                "summary": "Spec tasks cannot be approved until blockers are resolved.",
                "analysis": {
                    "hardBlockers": [
                        {"code": "large_task_missing_mvp_slice", "taskId": "TASK-001"},
                    ],
                },
            },
        }

    monkeypatch.setattr("core.database.db.list_pending_approvals", fake_list_pending_approvals)
    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    result = audit._approve_stage(
        "http://127.0.0.1:9530",
        session_id="session-live",
        spec_id="spec-live",
        stage="tasks",
        comment="approve tasks",
        timeout_s=1,
    )

    assert result["ok"] is False
    assert result["error"] == "spec_stage_analysis_blocked"
    assert result["hardBlockers"][0]["code"] == "large_task_missing_mvp_slice"


def test_approve_stage_prefers_latest_pending_approval(monkeypatch: Any) -> None:
    approved: list[str] = []

    def fake_list_pending_approvals(session_id: str, status: str) -> list[dict[str, Any]]:
        assert session_id == "session-live"
        assert status == "pending"
        return [
            {
                "id": "approval-old",
                "approval_kind": "spec_stage_approval",
                "created_at": "2026-07-09 20:00:00",
                "request": {"specId": "spec-live", "stage": "tasks", "workspacePath": "E:/Projects/test3"},
            },
            {
                "id": "approval-new",
                "approval_kind": "spec_stage_approval",
                "created_at": "2026-07-09 20:01:00",
                "request": {"specId": "spec-live", "stage": "tasks", "workspacePath": "E:/Projects/test3"},
            },
        ]

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        approved.append(url.rsplit("/", 2)[-2])
        return {"resume_scheduled": True, "resumed_run_id": "run-resumed", "spec_stage_approval": {"ok": True}}

    monkeypatch.setattr("core.database.db.list_pending_approvals", fake_list_pending_approvals)
    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    result = audit._approve_stage(
        "http://127.0.0.1:9530",
        session_id="session-live",
        spec_id="spec-live",
        stage="tasks",
        comment="approve tasks",
        timeout_s=1,
    )

    assert result["ok"] is True
    assert result["approvalId"] == "approval-new"
    assert approved == ["approval-new"]


def test_auto_respond_patches_missing_spec_context_for_spec_question(monkeypatch: Any) -> None:
    patched: dict[str, Any] = {}

    def fake_list_ask_user_interactions(session_id: str, status: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "ask-missing-context",
                "question": "Spec 模式已开启，准备写 requirements.md。请确认需求边界。",
                "request": {
                    "interactionKind": "ask_user",
                    "question": "Spec 模式已开启，准备写 requirements.md。请确认需求边界。",
                },
            }
        ]

    def fake_update_ask_user_interaction(interaction_id: str, *, status: str, request: dict[str, Any], **_: Any) -> None:
        patched["interactionId"] = interaction_id
        patched["status"] = status
        patched["request"] = request

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        return {"resume_scheduled": True, "resumed_run_id": "run-patched"}

    monkeypatch.setattr("core.database.db.list_ask_user_interactions", fake_list_ask_user_interactions)
    monkeypatch.setattr("core.database.db.update_ask_user_interaction", fake_update_ask_user_interaction)
    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    result = audit.SpecLiveResult(session_id="session-live")
    responses = audit._auto_respond_pending_spec_ask_user(
        "http://127.0.0.1:9530",
        result,
        stage="requirements",
        marker="SPEC_LIVE_TEST",
        target_rel=".v8/live-audit/spec-mode-v2/test",
        workspace=Path("E:/Projects/test3"),
    )

    assert patched["interactionId"] == "ask-missing-context"
    assert patched["request"]["specContext"] == {
        "kind": "spec_clarification",
        "featureName": "spec-mode-live-counter",
        "stage": "requirements",
        "workspacePath": "E:\\Projects\\test3",
    }
    assert responses[0]["status"] == "responded"
    assert result.key_events[0]["askUserSpecContextPatched"]["interactionId"] == "ask-missing-context"


def test_auto_respond_patches_stage_clarification_without_spec_keyword(monkeypatch: Any) -> None:
    patched: dict[str, Any] = {}
    posted: dict[str, Any] = {}

    def fake_list_ask_user_interactions(session_id: str, status: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "ask-design",
                "question": "design 阶段有三个微决策想先跟你对齐，确认后我立刻写 design.md。",
                "request": {
                    "interactionKind": "ask_user",
                    "question": "design 阶段有三个微决策想先跟你对齐，确认后我立刻写 design.md。",
                },
            }
        ]

    def fake_update_ask_user_interaction(interaction_id: str, *, status: str, request: dict[str, Any], **_: Any) -> None:
        patched["interactionId"] = interaction_id
        patched["status"] = status
        patched["request"] = request

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        posted["url"] = url
        posted["payload"] = payload
        return {"resume_scheduled": True, "resumed_run_id": "run-design"}

    monkeypatch.setattr("core.database.db.list_ask_user_interactions", fake_list_ask_user_interactions)
    monkeypatch.setattr("core.database.db.update_ask_user_interaction", fake_update_ask_user_interaction)
    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    result = audit.SpecLiveResult(session_id="session-live", spec_id="spec_live")
    responses = audit._auto_respond_pending_spec_ask_user(
        "http://127.0.0.1:9530",
        result,
        stage="design",
        marker="SPEC_LIVE_TEST",
        target_rel=".v8/live-audit/spec-mode-v2/test",
        workspace=Path("E:/Projects/test3"),
    )

    assert patched["interactionId"] == "ask-design"
    assert patched["request"]["specContext"]["stage"] == "design"
    assert patched["request"]["specContext"]["specId"] == "spec_live"
    assert posted["url"].endswith("/v1/ask-user/ask-design/respond")
    assert "设计阶段" in posted["payload"]["response"]["answer"]
    assert responses[0]["stage"] == "design"


def test_auto_respond_answers_new_interaction_for_same_stage(monkeypatch: Any) -> None:
    posted: list[str] = []

    def fake_list_ask_user_interactions(session_id: str, status: str) -> list[dict[str, Any]]:
        return [
            {
                "id": "ask-design-2",
                "question": "design 阶段请再次确认实现取向。",
                "request": {
                    "interactionKind": "ask_user",
                    "question": "design 阶段请再次确认实现取向。",
                    "specContext": {
                        "kind": "spec_clarification",
                        "specId": "spec_live",
                        "featureName": "spec-live",
                        "stage": "design",
                        "workspacePath": "E:/Projects/test3",
                    },
                },
            }
        ]

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        posted.append(url)
        return {"resume_scheduled": True, "resumed_run_id": "run-design-2"}

    monkeypatch.setattr("core.database.db.list_ask_user_interactions", fake_list_ask_user_interactions)
    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    result = audit.SpecLiveResult(session_id="session-live")
    result.ask_user_responses.append({"interactionId": "ask-design-1", "stage": "design", "status": "responded"})
    responses = audit._auto_respond_pending_spec_ask_user(
        "http://127.0.0.1:9530",
        result,
        stage="design",
        marker="SPEC_LIVE_TEST",
        target_rel=".v8/live-audit/spec-mode-v2/test",
        workspace=Path("E:/Projects/test3"),
    )

    assert posted and posted[0].endswith("/v1/ask-user/ask-design-2/respond")
    assert responses[0]["interactionId"] == "ask-design-2"
    assert len([item for item in result.ask_user_responses if item.get("stage") == "design"]) == 2


def test_bootstrap_live_spec_shell_records_clarification_without_stage_doc(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = audit._bootstrap_live_spec_shell(
        workspace,
        marker="SPEC_LIVE_TEST",
        target_rel=".v8/live-audit/spec-mode-v2/test",
        session_id="session-bootstrap",
    )

    spec_id = result["specId"]
    spec_dir = Path(result["specDir"])
    manifest = spec_dir / "spec.json"
    requirements = spec_dir / "requirements.md"
    assert spec_id.startswith("spec_")
    assert manifest.exists()
    assert not requirements.exists()
    assert result["clarification"]["kind"] == "spec_clarification_recorded"


def test_ensure_spec_clarification_creates_pending_ask_user_and_responds(monkeypatch: Any, tmp_path: Path) -> None:
    created: dict[str, Any] = {}
    posted: dict[str, Any] = {}

    def fake_add_ask_user_interaction(**kwargs: Any) -> None:
        created.update(kwargs)

    def fake_list_ask_user_interactions(session_id: str, status: str) -> list[dict[str, Any]]:
        assert session_id == "session-live"
        assert status == "pending"
        return [
            {
                "id": created["interaction_id"],
                "question": created["question"],
                "request": created["request"],
            }
        ]

    def fake_json_request(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        posted["url"] = url
        posted["method"] = method
        posted["payload"] = payload
        return {
            "resume_scheduled": True,
            "resumed_run_id": "run-resumed",
            "spec_clarification": {"recorded": True, "stage": "requirements", "specId": "spec-live"},
        }

    monkeypatch.setattr("core.database.db.add_ask_user_interaction", fake_add_ask_user_interaction)
    monkeypatch.setattr("core.database.db.list_ask_user_interactions", fake_list_ask_user_interactions)
    monkeypatch.setattr(audit, "_json_request", fake_json_request)

    result = audit.SpecLiveResult(session_id="session-live", spec_id="spec-live")
    result.run_ids.append("run-current")
    result.key_events.append({"specClarificationRequired": {"stage": "requirements"}})

    responses = audit._ensure_spec_clarification_via_ask_user(
        "http://127.0.0.1:9530",
        result,
        workspace=tmp_path,
        marker="SPEC_LIVE_TEST",
        target_rel=".v8/live-audit/spec-mode-v2/test",
        stage="requirements",
    )

    assert created["session_id"] == "session-live"
    assert created["run_id"] == "run-current"
    assert created["request"]["specContext"]["specId"] == "spec-live"
    assert created["request"]["specContext"]["stage"] == "requirements"
    assert posted["url"].endswith(f"/v1/ask-user/{created['interaction_id']}/respond")
    assert posted["method"] == "POST"
    assert responses[0]["status"] == "responded"
    assert result.ask_user_responses[0]["specClarification"]["recorded"] is True


def test_retry_stage_after_pseudo_tool_call_recovers_spec_id_before_submit(monkeypatch: Any, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_find_spec_by_marker_or_target(workspace: Path, marker: str, target_rel: str, spec_id: str = "") -> dict[str, Any]:
        return {
            "manifest": {"specId": "spec-recovered"},
            "specDir": str(workspace / ".v8" / "specs" / "spec-recovered"),
        }

    def fake_submit(*args: Any, **kwargs: Any) -> tuple[str, int, dict[str, Any]]:
        captured.update(kwargs)
        return "run-retry", 12, {"accepted": True, "runId": "run-retry"}

    monkeypatch.setattr(audit, "_find_spec_by_marker_or_target", fake_find_spec_by_marker_or_target)
    monkeypatch.setattr(audit, "_submit", fake_submit)

    result = audit.SpecLiveResult(session_id="session-live")
    result.findings.append(
        {
            "severity": "P0",
            "code": "model_pseudo_tool_call_not_executed",
            "summary": "pseudo",
        }
    )

    ok = audit._retry_stage_after_pseudo_tool_call(
        "http://127.0.0.1:9530",
        result,
        workspace=tmp_path,
        marker="SPEC_LIVE_TEST",
        target_rel=".v8/live-audit/spec-mode-v2/test",
        stage="requirements",
        model_profile="",
        safety_approval_mode="reduced",
    )

    assert ok is True
    assert result.spec_id == "spec-recovered"
    assert captured["spec_id"] == "spec-recovered"
    assert any("activeSpecRecovered" in item for item in result.key_events)
