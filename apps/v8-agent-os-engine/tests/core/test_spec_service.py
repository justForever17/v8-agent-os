from __future__ import annotations

from pathlib import Path

from core.spec_service import _task_is_large, _task_slices, spec_service


def test_single_nested_directory_is_not_counted_as_multiple_task_outputs():
    assert _task_is_large(
        {
            "runtimeLane": "Engineering",
            "title": "Confirm deliverable directory and baseline",
            "expectedOutput": ".v8/live-audit/spec-mode-v2/demo/ ready with a baseline note.",
            "taskExcerpt": "Confirm one target directory before writing files.",
        }
    ) is False

    assert _task_is_large(
        {
            "runtimeLane": "Engineering",
            "title": "Implement runtime and tests",
            "expectedOutput": "src/runtime.ts and tests/runtime.test.ts",
            "taskExcerpt": "Implement two independently reviewable files.",
        }
    ) is True


def test_verbose_single_artifact_task_is_not_large_without_explicit_complexity_signal():
    assert _task_is_large(
        {
            "runtimeLane": "Engineering",
            "title": "Build one static page",
            "expectedOutput": "`.v8/demo/index.html`",
            "taskExcerpt": (
                "Describe the HTML structure, accessibility checks, static inspection, and acceptance evidence. "
                * 80
            ),
        }
    ) is False


def test_last_heading_task_does_not_absorb_following_document_sections():
    tasks = _task_slices(
        """
### TASK-003: Verify delivery
- runtimeLane: Engineering
- dependsOn: [TASK-001, TASK-002]
- specRefs: REQ-001, DES-001
- expectedOutput: `.v8/demo/_audit/acceptance.md`
- acceptance: Record pass or blocked for each requirement.
- proofRequired: The report itself.

## Execution order

TASK-001 and TASK-002 may run in parallel before TASK-003.
""",
        {"REQ-001": {"summary": "Requirement", "detailRef": "spec://requirements#REQ-001"}},
        [],
    )

    assert len(tasks) == 1
    assert "Execution order" not in tasks[0]["taskExcerpt"]
    assert "parallel" not in tasks[0]["taskExcerpt"]
    assert _task_is_large(tasks[0]) is False


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


def test_spec_brief_projects_explicit_target_output_directory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request=(
            "实现一个静态页面。\n"
            "目标输出目录：`.v8/live-audit/spec-mode-v2/demo`"
        ),
        feature_name="Static Page",
        stage="requirements",
    )

    brief = spec_service.build_brief(workspace_path=str(workspace), spec_id=created["specId"])

    assert brief["targetOutputDirectories"] == [".v8/live-audit/spec-mode-v2/demo"]


def test_spec_tasks_cannot_add_unapproved_final_deliverable_file(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request=(
            "实现一个静态页面。\n"
            "目标输出目录：`.v8/live-audit/spec-mode-v2/demo`\n"
            "最终交付文件必须是：index.html 和 README.md"
        ),
        feature_name="Static Page",
        stage="requirements",
    )
    spec_id = created["specId"]
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content=(
            "# Requirements\n\n- REQ-001: Deliver index.html and README.md under "
            "`.v8/live-audit/spec-mode-v2/demo`.\n"
        ),
    )
    assert spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")["ok"]
    spec_service.create_stage(workspace_path=str(workspace), user_request="continue", spec_id=spec_id, stage="design")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content=(
            "# Design\n\n- DES-001: Build index.html and README.md under "
            "`.v8/live-audit/spec-mode-v2/demo`.\n"
        ),
    )
    assert spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")["ok"]
    spec_service.create_stage(workspace_path=str(workspace), user_request="continue", spec_id=spec_id, stage="tasks")
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# Tasks\n\n"
            "### TASK-001: Build page\n"
            "- runtimeLane: Engineering\n"
            "- specRefs: REQ-001, DES-001\n"
            "- expectedOutput: `.v8/live-audit/spec-mode-v2/demo/index.html`\n"
            "- acceptance: page exists\n"
            "- proofRequired: file proof\n\n"
            "### TASK-002: Write docs\n"
            "- runtimeLane: Engineering\n"
            "- specRefs: REQ-001, DES-001\n"
            "- expectedOutput: `.v8/live-audit/spec-mode-v2/demo/README.md`\n"
            "- acceptance: docs exist\n"
            "- proofRequired: file proof\n\n"
            "### TASK-003: Write audit\n"
            "- runtimeLane: Engineering\n"
            "- specRefs: REQ-001, DES-001\n"
            "- expectedOutput: `.v8/live-audit/spec-mode-v2/demo/acceptance.md`\n"
            "- acceptance: audit exists\n"
            "- proofRequired: file proof\n"
        ),
    )

    review = spec_service.validate_stage_approval(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")

    assert review["ok"] is False
    assert review["kind"] == "spec_stage_contract_drift"
    assert review["allowedFinalDeliverables"] == ["index.html", "README.md"]
    assert review["unexpectedOutputs"] == [{"kind": "final_deliverable_file", "value": "acceptance.md"}]


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
    assert "TASK-001" in section["content"]


def test_spec_service_tasks_template_is_pipeline_ready(tmp_path: Path):
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

    assert tasks["ok"] is True
    assert tasks["tasksPipeline"]["valid"] is True
    assert tasks["tasksPipeline"]["taskCount"] >= 3
    assert not tasks["tasksPipeline"]["missingFields"]
    assert tasks["specBrief"]["documents"]["tasks"]["pipelineDiagnostics"]["valid"] is True
    content = (workspace / ".v8" / "specs" / Path(tasks["specDir"]).name / "tasks.md").read_text(encoding="utf-8")
    assert "| Task ID | Runtime lane | Goal | Depends on | Spec refs | Expected output | Acceptance / proof | MVP slice | Independent acceptance |" in content
    assert "runtimeLane:" in content
    assert "mvpSlice:" in content
    assert "independentAcceptance:" in content
    analysis = spec_service.analyze_spec(workspace_path=str(workspace), spec_id=spec_id)
    assert analysis["hardBlockers"] == []


def test_spec_service_tasks_fallback_scaffold_includes_mvp_and_independent_acceptance(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个计数器。",
        feature_name="Counter Fallback",
        stage="requirements",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现一个计数器。", spec_id=spec_id, stage="design")
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现一个计数器。", spec_id=spec_id, stage="tasks")

    edited = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content="Implement the approved counter work.",
    )

    assert edited["ok"] is True
    assert edited["tasksPipeline"]["valid"] is True
    content = spec_service.read_section(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")["content"]
    assert "TASK-001" in content
    assert "mvpSlice:" in content
    assert "independentAcceptance:" in content
    analysis = spec_service.analyze_spec(workspace_path=str(workspace), spec_id=spec_id)
    assert analysis["hardBlockers"] == []


def test_spec_service_delivered_spec_exits_active_list_but_remains_readable(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request=(
            "生成第一份已交付 Spec。\n"
            "目标输出目录：`.v8/output/demo`\n"
            "最终交付文件必须是：index.html 和 README.md"
        ),
        feature_name="Delivered Spec",
        stage="requirements",
    )
    delivered_id = first["specId"]
    delivered = spec_service.mark_delivered(
        workspace_path=str(workspace),
        spec_id=delivered_id,
        run_id="run-delivered",
        session_id="session-delivered",
    )
    assert delivered["ok"] is True
    assert delivered["lifecycle"] == "delivered"

    second = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="生成第二份当前 Spec。",
        feature_name="Current Spec",
        stage="requirements",
    )
    current_id = second["specId"]

    active_listing = spec_service.list_specs(workspace_path=str(workspace), include_archived=False)
    assert [item["specId"] for item in active_listing["specs"]] == [current_id]

    full_listing = spec_service.list_specs(workspace_path=str(workspace), include_archived=True)
    assert {item["specId"] for item in full_listing["specs"]} == {delivered_id, current_id}
    delivered_summary = next(item for item in full_listing["specs"] if item["specId"] == delivered_id)
    assert delivered_summary["targetOutputDirectories"] == [".v8/output/demo"]
    assert delivered_summary["explicitDeliverableFiles"] == ["index.html", "README.md"]
    brief = spec_service.build_brief(workspace_path=str(workspace), spec_id=delivered_id)
    assert brief["lifecycle"] == "delivered"
    assert brief["specId"] == delivered_id


def test_spec_service_starting_same_feature_without_spec_id_creates_new_spec(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="第一次创建同名功能。",
        feature_name="Spec Mode Live Counter",
        stage="requirements",
    )
    second = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="第二次创建同名功能。",
        feature_name="Spec Mode Live Counter",
        stage="requirements",
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["specId"] != second["specId"]
    assert Path(first["specDir"]) != Path(second["specDir"])
    listing = spec_service.list_specs(workspace_path=str(workspace), include_archived=False)
    assert {item["specId"] for item in listing["specs"]} == {first["specId"], second["specId"]}


def test_spec_service_tasks_pipeline_diagnostics_flags_weak_tasks(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个计数器。",
        feature_name="Counter Weak Tasks",
        stage="requirements",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现一个计数器。", spec_id=spec_id, stage="design")
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现一个计数器。", spec_id=spec_id, stage="tasks")

    edited = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content="# Tasks\n\n- [ ] TASK-001: 做完。\n",
    )

    assert edited["tasksPipeline"]["valid"] is False
    assert edited["tasksPipeline"]["taskIds"] == ["TASK-001"]
    assert set(edited["tasksPipeline"]["missingFields"]) >= {"runtimeLane", "dependsOn", "specRefs", "expectedOutput", "acceptanceProof"}
    brief = spec_service.build_brief(workspace_path=str(workspace), spec_id=spec_id)
    assert brief["pipelineControl"]["blockedReason"] == "stage_format_invalid"
    assert not brief["pipelineControl"]["blockedByApproval"]

    approval = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")
    assert approval["ok"] is False
    assert approval["kind"] == "spec_stage_format_invalid"
    assert approval["pipelineControl"]["blockedReason"] == "stage_format_invalid"
    assert not approval["pipelineControl"]["blockedByApproval"]


def test_spec_service_tasks_pipeline_accepts_chinese_runtime_channel(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个计数器。",
        feature_name="Chinese Runtime Channel",
        stage="requirements",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现一个计数器。", spec_id=spec_id, stage="design")
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(workspace_path=str(workspace), user_request="实现一个计数器。", spec_id=spec_id, stage="tasks")

    edited = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# Tasks\n\n"
            "| 任务ID | 任务名称 | 执行通道 | 依赖 | 预期输出 |\n"
            "| --- | --- | --- | --- | --- |\n"
            "| TASK-001 | 编写 index.html | engineering | 无 | index.html |\n\n"
            "### TASK-001: 编写 index.html\n"
            "- 执行通道: engineering\n"
            "- 依赖: 无\n"
            "- 需求引用: REQ-001\n"
            "- 设计引用: DES-001\n"
            "- 预期输出路径: index.html\n"
            "- 验收检查: 页面可用\n"
            "- 证明方式: grep marker\n"
        ),
    )

    assert edited["tasksPipeline"]["valid"] is True
    assert "runtimeLane" not in edited["tasksPipeline"]["missingFields"]


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

    locked = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="replace_section",
        section_ref="REQ-001",
        content="- REQ-001: Supervisor should not rewrite approved stages directly.",
        reason="agent_attempted_rewrite_after_approval",
    )

    assert locked["ok"] is False
    assert locked["kind"] == "spec_stage_locked"
    assert locked["nextStage"] == "design"

    revision = spec_service.request_revision(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        comment="user requested a requirement change",
        section_ref="REQ-001",
    )
    assert revision["ok"] is True

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

    locked = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design: Login Fix\n\n## Architecture\n\n- DES-001: Supervisor cannot rewrite approved design.",
        reason="agent_attempted_rewrite_after_approval",
    )
    assert locked["ok"] is False
    assert locked["kind"] == "spec_stage_locked"

    revision = spec_service.request_revision(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        comment="user requested design rework",
        section_ref="DES-001",
    )
    assert revision["ok"] is True

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


def test_spec_service_generates_quality_checklist_and_complex_annex(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="对接第三方 HTTP API，并给出 quickstart smoke 验收。",
        feature_name="Provider API Spec",
        stage="requirements",
    )
    spec_dir = Path(created["specDir"])
    created = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=created["specId"],
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: Integrate provider HTTP API and document smoke acceptance.\n",
    )

    assert created["ok"] is True
    brief = created["specBrief"]
    checklist = brief["qualityEvidence"]["checklists"]["requirements"]
    assert checklist["kind"] == "checklist"
    assert checklist["relativePath"].endswith("checklists/requirements.md")
    assert any(item.get("kind") == "checklist" for item in brief["linkedSections"])
    annex = brief["annexDocuments"]
    assert {"contracts", "quickstart"}.issubset(set(annex))
    assert any(item.get("kind") == "annex" and item.get("title") == "Contracts" for item in brief["linkedSections"])
    assert (spec_dir / "checklists" / "requirements.md").exists()


def test_spec_service_tasks_analysis_blocks_large_task_without_mvp_and_independent_acceptance(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个跨 runtime 的交付闭环。",
        feature_name="Large Spec Task",
        stage="requirements",
    )
    created = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=created["specId"],
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: Deliver the runtime workflow with proof.\n",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个跨 runtime 的交付闭环。",
        spec_id=spec_id,
        stage="design",
    )
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design\n\n- DES-001: Engineering coordinates subagent execution and proof reconciliation.\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个跨 runtime 的交付闭环。",
        spec_id=spec_id,
        stage="tasks",
    )
    tasks = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# Tasks\n\n"
            "### TASK-001: Implement parallel subagent delivery\n\n"
            "- runtimeLane: Engineering + subagent worker\n"
            "- dependsOn: []\n"
            "- specRefs: REQ-001, DES-001\n"
            "- expectedOutput: `src/runtime.ts`, `tests/runtime.test.ts`\n"
            "- acceptance: typecheck and targeted pytest pass\n"
            "- proofRequired: changed files and test output\n"
        ),
    )

    assert tasks["tasksPipeline"]["valid"] is True
    analysis = spec_service.analyze_spec(workspace_path=str(workspace), spec_id=spec_id)
    codes = {item["code"] for item in analysis["hardBlockers"]}
    assert {"large_task_missing_mvp_slice", "large_task_missing_independent_acceptance"}.issubset(codes)
    approval = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")
    assert approval["ok"] is False
    assert approval["kind"] == "spec_stage_analysis_blocked"


def test_spec_stage_review_preserves_explicit_target_output_directory(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = ".v8/live-audit/spec-mode-v2/example"
    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request=f"Target output directory: {target}. Deliver index.html.",
        feature_name="Explicit target contract",
        stage="requirements",
    )
    spec_id = created["specId"]
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: Deliver index.html at the workspace root.\n",
    )

    blocked = spec_service.validate_stage_approval(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
    )

    assert blocked["ok"] is False
    assert blocked["kind"] == "spec_stage_contract_drift"
    assert blocked["missingConstraints"] == [{"kind": "target_output_directory", "value": target}]

    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
        action="rewrite_stage",
        content=f"# Requirements\n\n- REQ-001: Deliver `{target}/index.html`.\n",
    )
    assert spec_service.validate_stage_approval(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="requirements",
    )["ok"] is True


def test_spec_service_tasks_analysis_accepts_task_level_mvp_and_acceptance_annex(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个跨 runtime 的交付闭环。",
        feature_name="Large Spec Task",
        stage="requirements",
    )
    created = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=created["specId"],
        stage="requirements",
        action="rewrite_stage",
        content="# Requirements\n\n- REQ-001: Deliver the runtime workflow with proof.\n",
    )
    spec_id = created["specId"]
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="requirements")
    spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个跨 runtime 的交付闭环。",
        spec_id=spec_id,
        stage="design",
    )
    spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="design",
        action="rewrite_stage",
        content="# Design\n\n- DES-001: Engineering coordinates subagent execution and proof reconciliation.\n",
    )
    spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="design")
    spec_service.create_stage(
        workspace_path=str(workspace),
        user_request="实现一个跨 runtime 的交付闭环。",
        spec_id=spec_id,
        stage="tasks",
    )
    tasks = spec_service.edit_stage(
        workspace_path=str(workspace),
        spec_id=spec_id,
        stage="tasks",
        action="rewrite_stage",
        content=(
            "# Tasks\n\n"
            "### TASK-001: Implement parallel subagent delivery\n\n"
            "- runtimeLane: Engineering + subagent worker\n"
            "- dependsOn: []\n"
            "- specRefs: REQ-001, DES-001\n"
            "- expectedOutput: `src/runtime.ts`, `tests/runtime.test.ts`\n"
            "- acceptance: typecheck and targeted pytest pass\n"
            "- proofRequired: changed files and test output\n\n"
            "### mvpSlice annotation (per task)\n\n"
            "| Task ID | Part of mvpSlice? | Why |\n"
            "| --- | --- | --- |\n"
            "| TASK-001 | Yes | First independently runnable runtime handoff. |\n\n"
            "### independentAcceptance (per task)\n\n"
            "- TASK-001 independentAcceptance: reviewer can inspect the proof and rerun the targeted test without trusting the worker summary.\n"
        ),
    )

    paths = spec_service.resolve_paths(str(workspace), spec_id=spec_id)
    manifest = spec_service._load_manifest(paths)
    task_slice = spec_service._traceability_index(paths, manifest)["tasks"][0]
    assert task_slice["mvpSlice"] == "First independently runnable runtime handoff."
    assert task_slice["independentAcceptance"].startswith("reviewer can inspect")
    analysis = spec_service.analyze_spec(workspace_path=str(workspace), spec_id=spec_id)
    assert analysis["hardBlockers"] == []
    approval = spec_service.approve_stage(workspace_path=str(workspace), spec_id=spec_id, stage="tasks")
    assert approval["ok"] is True
    assert approval["pipelineControl"]["runtimeExecutionAllowed"] is True
