from __future__ import annotations

from pathlib import Path

from core.spec_service import spec_service


def test_spec_service_blocks_downstream_until_approved(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个可审批的 Spec Mode。",
        feature_name="Spec Mode",
        stage="requirements",
        kind="feature",
    )
    assert first["ok"] is True
    assert first["pipelineControl"]["currentStage"] == "requirements"
    assert first["pipelineControl"]["runtimeExecutionAllowed"] is False
    spec_id = first["specId"]

    blocked = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个可审批的 Spec Mode。",
        spec_id=spec_id,
        stage="design",
    )
    assert blocked["ok"] is False
    assert blocked["requiredApproval"] == "requirements"

    approved = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    assert approved["ok"] is True
    assert approved["nextStage"] == "design"

    design = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个可审批的 Spec Mode。",
        spec_id=spec_id,
        stage="design",
    )
    assert design["ok"] is True
    assert design["linkedSections"]
    assert design["specBrief"]["linkedSections"]
    assert design["specBrief"]["documents"]["design"]["detailRef"] == f"spec://{spec_id}/design"
    assert (workspace / ".v8" / "specs" / Path(design["specDir"]).name / "design.md").exists()


def test_spec_service_read_section_returns_detail_ref(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="修复登录按钮点击无反馈。",
        feature_name="Login Bug",
        kind="bugfix",
    )
    spec_id = created["specId"]

    section = spec_service.read_section(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="bugfix",
        section_ref="BFIX-002",
        max_chars=800,
    )

    assert section["ok"] is True
    assert section["documentRef"] == f"spec://{spec_id}/bugfix#BFIX-002"
    assert "BFIX-002" in section["content"]


def test_spec_service_normalizes_tsk_task_ids(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个计数器。",
        feature_name="Counter",
        stage="requirements",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现一个计数器。", spec_id=spec_id, stage="design")
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")

    tasks = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个计数器。",
        spec_id=spec_id,
        stage="tasks",
    )
    edited = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content="# Tasks\n\n### TSK-001: Create the counter\n\nLinks: REQ-001, DES-001\n",
    )

    assert tasks["ok"] is True
    assert edited["specBrief"]["documents"]["tasks"]["ids"] == ["TASK-001"]
    section = spec_service.read_section(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        section_ref="TASK-001",
    )
    assert "TSK-001" in section["content"]


def test_spec_service_accepts_fr_nfr_requirement_ids(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个可验收的小项目。",
        feature_name="FR Requirements",
        stage="requirements",
    )
    spec_id = created["specId"]
    edited = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- FR-001: The page shows the core user flow.\n- NFR-001: The page remains simple and local-only.\n",
    )

    assert edited["specBrief"]["documents"]["requirements"]["ids"] == ["FR-001", "NFR-001"]
    section = spec_service.read_section(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        section_ref="FR-001",
    )
    assert "core user flow" in section["content"]


def test_spec_service_stage_edit_records_history_and_stales_downstream(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现稳定的 Spec Mode。",
        feature_name="Spec Mode v2",
        stage="requirements",
        kind="feature",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现稳定的 Spec Mode。", spec_id=spec_id, stage="design")
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现稳定的 Spec Mode。", spec_id=spec_id, stage="tasks")
    approved_tasks = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")
    assert approved_tasks["pipelineControl"]["runtimeExecutionAllowed"] is True

    edited = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="replace_section",
        section_ref="REQ-001",
        content="- REQ-001: Updated requirement that must be re-approved.",
        reason="user_revised_requirement",
    )

    assert edited["ok"] is True
    assert edited["pipelineControl"]["runtimeExecutionAllowed"] is False
    assert set(edited["pipelineControl"]["staleStages"]) >= {"design", "tasks"}
    assert edited["specBrief"]["approvalState"]["requirements"] is False
    assert edited["specBrief"]["approvalState"]["design"] is False
    assert edited["specBrief"]["approvalState"]["tasks"] is False
    assert edited["specBrief"]["versionHistory"]
    section = spec_service.read_section(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        section_ref="REQ-001",
    )
    assert "Updated requirement" in section["content"]


def test_spec_service_rewrite_stage_marks_downstream_stale(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="修复登录异常。",
        feature_name="Login Fix",
        kind="bugfix",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="bugfix")
    spec_service.create_stage(workspace_path=str(workspace), user_request="修复登录异常。", spec_id=spec_id, stage="design")
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="修复登录异常。", spec_id=spec_id, stage="tasks")
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")

    rewritten = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design: Login Fix\n\n## Architecture\n\n- DES-001: New scoped design.",
        reason="design_reworked_after_review",
    )

    assert rewritten["pipelineControl"]["runtimeExecutionAllowed"] is False
    assert rewritten["specBrief"]["approvalState"]["design"] is False
    assert rewritten["specBrief"]["approvalState"]["tasks"] is False
    assert "tasks" in rewritten["pipelineControl"]["staleStages"]


def test_spec_service_list_and_read_spec_for_client_approval(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="做一个简易番茄钟页面。",
        feature_name="Pomodoro Page",
        stage="requirements",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements", comment="ok")

    listing = spec_service.list_specs(workspace_path=str(workspace))
    assert listing["ok"] is True
    assert listing["specs"][0]["specId"] == spec_id
    assert listing["specs"][0]["documents"]["requirements"]["ids"]

    detail = spec_service.read_spec(workspace_path=str(workspace), spec_id=spec_id, max_chars=20000)
    assert detail["ok"] is True
    assert detail["spec"]["pipelineControl"]["approvedStages"] == ["requirements"]
    assert "requirements" in detail["stages"]
    assert "REQ-001" in detail["stages"]["requirements"]["content"]
    assert detail["specBrief"]["documents"]["requirements"]["detailRef"] == f"spec://{spec_id}/requirements"
