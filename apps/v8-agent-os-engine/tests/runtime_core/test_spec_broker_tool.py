import json

from core.tools.native.spec import spec_broker
from core.spec_service import spec_service
from erc.runtime_context import bind_runtime_context
from langgraph.types import Command


def _payload(raw: str) -> dict:
    return json.loads(raw)


def _resolved_spec_clarification(*, workspace_path: str, feature_name: str, stage: str) -> list[dict]:
    return [
        {
            "id": f"ask_spec_clarify_{stage}",
            "tool_call_id": f"call_spec_clarify_{stage}",
            "question": f"请确认 {feature_name} 的 {stage} 边界。",
            "answer_text": "确认按当前边界继续。",
            "status": "resolved",
            "resolved_at": "2026-07-09T00:00:00Z",
            "request": {
                "interactionKind": "ask_user",
                "question": f"请确认 {feature_name} 的 {stage} 边界。",
                "specContext": {
                    "kind": "spec_clarification",
                    "featureName": feature_name,
                    "stage": stage,
                    "workspacePath": workspace_path,
                },
            },
        }
    ]


def test_spec_broker_description_lists_write_edit_read_modes():
    description = str(getattr(spec_broker, "description", "") or "")

    assert "write_stage" in description
    assert "edit" in description
    assert "write" in description
    assert "update" in description
    assert "read_stage" in description
    assert "It never writes final" in description
    assert "mode='brief'" in description
    assert "`mode='approve'` is reserved for user/client approval continuations" in description
    assert "Approved stages are locked" in description
    assert "runtime lane" in description
    assert "runtime_broker" in description


def test_spec_brief_traceability_links_kiro_style_requirements_design_and_tasks(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    requirements = """# 需求文档

## 需求

### 需求 1：PDF文件上传功能

#### 验收标准

1. WHEN 用户点击上传按钮 THEN 系统 SHALL 打开文件选择器，仅允许选择PDF格式文件
2. WHEN 用户选择的文件大小超过20MB THEN 系统 SHALL 显示错误提示

### 需求 6：配置管理

#### 验收标准

1. WHEN 项目初始化 THEN 系统 SHALL 提供.env.template模板文件
2. WHEN 应用启动 THEN 系统 SHALL 从.env文件读取MinerU API和腾讯云COS配置
5. IF .env文件不存在 THEN 系统 SHALL 提示开发者复制.env.template并填写配置

### 需求 8：性能与兼容性

#### 验收标准

3. WHEN 应用运行 THEN 系统 SHALL 兼容微信小程序基础库版本2.0.0及以上
"""
    design = """# 设计文档

## 概述

本文档描述基于uni-app框架的微信小程序PDF转DOCX转换器。全员必须使用uni-app/Vue/JavaScript小程序工程，不得改成Python脚本或普通Node CLI。

## 架构

### 整体架构

微信小程序前端采用uni-app框架，服务层包含cosService.js和mineruService.js。

## 配置管理设计

- 需求: 6.1, 6.2, 6.5
- 使用utils/config.js读取.env文件并做启动期校验。
"""
    tasks = """# 实施计划

- [x] 1. 初始化uni-app项目结构
  - 创建uni-app微信小程序项目基础结构
  - _需求: 6.1, 6.2, 8.3_

- [ ] 2. 配置环境变量和配置管理
  - [x] 2.1 创建环境变量模板文件
    - 创建.env.template文件，包含所有必需配置项的说明
    - _需求: 6.1, 6.5_

  - [x] 2.2 实现配置加载工具
    - 编写utils/config.js实现loadConfig函数
    - _需求: 6.2, 6.3, 6.4_
"""
    created = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="创建PDF转DOCX小程序",
            feature_name="pdf-to-docx-converter",
            content=requirements,
        )
    )
    spec_id = created["specId"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="requirements"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
            content=design,
        )
    )["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="design"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="tasks",
            content=tasks,
        )
    )["ok"]

    brief = spec_service.build_brief(workspace_path=str(workspace), spec_id=spec_id)
    traceability = brief["traceability"]
    assert "uni-app" in traceability["frameworkDigest"]
    assert "Python" in traceability["frameworkDigest"]
    assert traceability["distributionChecks"]["taskCount"] >= 3
    assert traceability["distributionChecks"]["tasksWithRequirementRefs"] >= 3
    assert traceability["distributionChecks"]["tasksWithDesignRefs"] >= 3
    first_task = traceability["tasks"][0]
    assert first_task["taskId"] == "TASK-1"
    assert {"6.1", "6.2", "8.3"}.issubset(set(first_task["requirementRefs"]))
    config_template_task = next(item for item in traceability["tasks"] if item["taskId"] == "TASK-2.1")
    assert "2.1" not in config_template_task["requirementRefs"]
    assert any("env.template" in (item.get("summary") or "") for item in config_template_task["requirementSnippets"])
    assert any("uni-app" in (item.get("summary") or "") for item in first_task["designSnippets"])


def test_spec_brief_traceability_links_heading_style_tasks(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    requirements = """# Requirements

## REQ-001 Counter

The page SHALL show a counter.

## REQ-002 Documentation

The delivery SHALL include a README.
"""
    design = """# Design

## DES-001 Browser implementation

Use one HTML file with inline CSS and JavaScript. REQ-001

## DES-002 Documentation

Document how to open the page. REQ-002
"""
    tasks = """# Tasks

### TASK-001: Build the counter

- **runtimeLane**: Engineering
- **specRefs**: REQ-001, DES-001
- **expectedOutput**: `.v8/demo/index.html`

### TASK-002: Write documentation

- **runtimeLane**: Engineering
- **specRefs**: REQ-002, DES-002
- **expectedOutput**: `.v8/demo/README.md`
"""
    created = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="Create a counter.",
            feature_name="heading-task-contract",
            content=requirements,
        )
    )
    spec_id = created["specId"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="requirements"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
            content=design,
        )
    )["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="design"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="tasks",
            content=tasks,
        )
    )["ok"]

    traceability = spec_service.build_brief(workspace_path=str(workspace), spec_id=spec_id)["traceability"]

    assert traceability["distributionChecks"]["taskCount"] == 2
    assert traceability["distributionChecks"]["tasksWithRequirementRefs"] == 2
    assert traceability["distributionChecks"]["tasksWithDesignRefs"] == 2
    by_id = {item["taskId"]: item for item in traceability["tasks"]}
    assert by_id["TASK-001"]["requirementRefs"] == ["REQ-001"]
    assert by_id["TASK-001"]["designRefs"] == ["DES-001"]
    assert by_id["TASK-002"]["requirementRefs"] == ["REQ-002"]
    assert "DES-002" in by_id["TASK-002"]["designRefs"]
    assert "DES-001" in by_id["TASK-002"]["designRefs"]  # Framework/design baseline is shared across tasks.


def test_spec_broker_rewrite_approved_stage_returns_locked(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="创建一个计数器。",
            feature_name="Counter",
            content="# Requirements\n\n- REQ-001: Show a counter.\n",
        )
    )
    spec_id = started["specId"]
    assert _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            comment="approved",
        )
    )["ok"]

    locked = _payload(
        spec_broker.func(
            mode="rewrite_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            content="# Requirements\n\n- REQ-001: Rewrite should be blocked.\n",
        )
    )

    assert locked["ok"] is False
    assert locked["kind"] == "spec_stage_locked"
    assert locked["stage"] == "requirements"
    assert locked["nextStage"] == "design"
    assert "stage='design'" in locked["recommendedNextAction"]
    assert locked["transitionHint"]["nextStage"] == "design"
    assert "write stage design" in locked["transitionHint"]["whenReady"].lower()


def test_spec_broker_normalizes_short_ids_and_reports_format_diagnostics(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="Normalize short ids",
            feature_name="normalize-short-ids",
            content="# Requirements\n\n- REQ-1: Show a counter.\n\n## Acceptance Criteria\n\n- AC-REQ-1: WHEN done THEN counter works.\n",
        )
    )

    assert started["ok"] is True
    assert "REQ-001" in started["document"]["ids"]
    assert "AC-REQ-001" in _payload(
        spec_broker.func(
            mode="read",
            workspace_path=str(workspace),
            spec_id=started["specId"],
            stage="requirements",
        )
    )["content"]
    assert started["formatDiagnostics"]["valid"] is True


def test_spec_broker_approval_allows_loose_requirements_with_diagnostics(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="Bad requirements format",
            feature_name="bad-requirements-format",
            content="# Requirements\n\nThis is prose without stable requirement ids.\n",
        )
    )
    approved = _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=started["specId"],
            stage="requirements",
            comment="try approve",
        )
    )

    assert approved["ok"] is True
    diagnostics = approved["specBrief"]["documents"]["requirements"].get("formatDiagnostics") or {}
    assert "requirementIds" not in diagnostics.get("missingFields", [])
    assert started["idAllocation"]["allocatedIds"] == ["REQ-001"]
    assert diagnostics.get("approvalBlocking") == []
    assert "requirements" in set(
        spec_service.build_brief(workspace_path=str(workspace), spec_id=started["specId"]).get("approvedStages") or []
    )


def test_spec_broker_rejects_supervisor_self_approval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    created = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="需要用户审批的 Spec",
            feature_name="approval-guard",
            content="# Requirements\n\n- REQ-001: 只能由用户审批。\n",
        )
    )
    spec_id = created["specId"]

    with bind_runtime_context(
        runtime_kind="chat",
        session_id="session_self_approval_guard",
        run_id="run_self_approval_guard",
        workspace_path=str(workspace),
    ):
        blocked = _payload(
            spec_broker.func(
                mode="approve",
                workspace_path=str(workspace),
                spec_id=spec_id,
                stage="requirements",
                comment="Supervisor tries to approve its own draft",
            )
        )

    assert blocked["ok"] is False
    assert blocked["kind"] == "spec_user_approval_required"
    assert "cannot approve" in blocked["summary"]
    brief = spec_service.build_brief(workspace_path=str(workspace), spec_id=spec_id)
    assert "requirements" not in set(brief.get("approvedStages") or [])


def test_spec_broker_runtime_stage_creates_governance_approval(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    approvals = []

    def fake_request_approval(request):
        approvals.append(request)
        return {
            "approval_id": "approval_spec_demo",
            "approval_kind": request.approval_kind,
            "status": "pending",
        }

    monkeypatch.setattr("core.tools.native.spec.command_service.request_approval", fake_request_approval)

    with bind_runtime_context(session_id="session_spec", run_id="run_spec", workspace_path=str(workspace)):
        blocked = _payload(spec_broker.func(
            mode="write_stage",
            stage="requirements",
            feature_name="demo-spec",
            user_request="生成一个 demo skill",
            content="# Requirements\n\n- REQ-1: demo.\n",
            tool_call_id="call_demo",
        ))
    assert blocked["ok"] is False
    assert blocked["kind"] == "spec_clarification_required"
    assert blocked["askUserTemplate"]["specContext"]["stage"] == "requirements"

    monkeypatch.setattr(
        "core.tools.native.spec.db.list_ask_user_interactions",
        lambda **_kwargs: _resolved_spec_clarification(
            workspace_path=str(workspace),
            feature_name="demo-spec",
            stage="requirements",
        ),
    )
    with bind_runtime_context(session_id="session_spec", run_id="run_spec", workspace_path=str(workspace)):
        result = spec_broker.func(
            mode="write_stage",
            stage="requirements",
            feature_name="demo-spec",
            user_request="生成一个 demo skill",
            content="# Requirements\n\n- REQ-1: demo.\n",
            tool_call_id="call_demo",
        )

    assert isinstance(result, Command)
    assert approvals
    assert approvals[0].approval_kind == "spec_stage_approval"
    assert approvals[0].session_id == "session_spec"
    assert approvals[0].run_id == "run_spec"
    assert approvals[0].request["specId"]
    assert approvals[0].request["specBrief"]["featureName"] == "demo-spec"
    payload = _payload(result.update["messages"][0].content)
    assert payload["approvalId"] == "approval_spec_demo"
    assert payload["approvalKind"] == "spec_stage_approval"
    assert payload["approvalStatus"] == "pending"
    assert payload["specBrief"]["clarificationSummary"]["count"] == 1


def test_spec_broker_edit_alias_rewrites_stage_with_inferred_spec_id(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成绝区零角色玲视角 skill",
            feature_name="zzz-ling-perspective",
            content="# Requirements\n\n- REQ-1: 生成可加载的角色视角 skill。\n",
        )
    )
    assert started["ok"] is True
    spec_id = started["specId"]

    approved = _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            comment="approved in test",
        )
    )
    assert approved["ok"] is True

    edited = _payload(
        spec_broker.func(
            mode="edit",
            workspace_path=str(workspace),
            kind="design",
            content="# Design\n\n- DES-1: 先读技能，再调研，再生成文件。\n",
        )
    )

    assert edited["ok"] is True
    assert edited["kind"] == "spec_stage_edited"
    assert edited["specId"] == spec_id
    assert edited["stage"] == "design"
    assert edited["action"] == "rewrite_stage"


def test_spec_broker_list_and_missing_spec_id_use_latest_active_spec(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="第一份需求",
            feature_name="first-spec",
            content="# Requirements\n\n- REQ-1: first.\n",
        )
    )
    assert first["ok"] is True

    second = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="第二份需求",
            feature_name="second-spec",
            content="# Requirements\n\n- REQ-1: second.\n",
        )
    )
    assert second["ok"] is True
    second_id = second["specId"]

    listing = _payload(spec_broker.func(mode="list", workspace_path=str(workspace)))
    assert listing["ok"] is True
    assert listing["kind"] == "spec_list"
    assert listing["specs"][0]["specId"] == second_id

    approved = _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            stage="requirements",
            comment="approve latest active spec",
        )
    )

    assert approved["ok"] is True
    assert approved["specId"] == second_id


def test_spec_broker_read_missing_next_stage_returns_write_guidance(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成玲的 skill",
            feature_name="ling-skill",
            content="# Requirements\n\n- REQ-001: deliver skill.\n",
        )
    )
    spec_id = created["specId"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="requirements"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
            content="# Design\n\n- DES-001: design.\n",
        )
    )["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="design"))["ok"]

    missing = _payload(
        spec_broker.func(
            mode="read_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="tasks",
        )
    )

    assert missing["ok"] is False
    assert missing["kind"] == "spec_stage_missing"
    assert missing["stage"] == "tasks"
    assert missing["nextStage"] == "tasks"
    assert missing["pipelineControl"]["nextStage"] == "tasks"
    assert missing["transitionHint"]["state"] == "stage_ready_to_write"
    assert "mode='write_stage'" in missing["recommendedNextAction"]
    assert "stage='tasks'" in missing["recommendedNextAction"]


def test_spec_broker_read_existing_stage_includes_pipeline_transition_hint(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成玲的 skill",
            feature_name="ling-skill",
            content="# Requirements\n\n- REQ-001: deliver skill.\n",
        )
    )
    spec_id = created["specId"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="requirements"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
            content="# Design\n\n- DES-001: design.\n",
        )
    )["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="design"))["ok"]

    read = _payload(
        spec_broker.func(
            mode="read",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
        )
    )

    assert read["ok"] is True
    assert read["kind"] == "spec_section"
    assert read["pipelineControl"]["nextStage"] == "tasks"
    assert read["transitionHint"]["state"] == "stage_ready_to_write"
    assert "stage='tasks'" in read["recommendedNextAction"]


def test_spec_broker_start_same_feature_creates_new_current_spec(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="第一次创建 live counter",
            feature_name="spec-mode-v2-live-counter",
            content="# Requirements\n\n- REQ-001: first.\n",
        )
    )
    second = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="第二次创建 live counter",
            feature_name="spec-mode-v2-live-counter",
            content="# Requirements\n\n- REQ-001: second.\n",
        )
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert first["specId"] != second["specId"]
    listing = _payload(spec_broker.func(mode="list", workspace_path=str(workspace)))
    assert listing["specs"][0]["specId"] == second["specId"]
    assert {item["specId"] for item in listing["specs"]} == {first["specId"], second["specId"]}


def test_spec_broker_blocks_bugfix_restart_when_current_spec_ready_for_execution(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成玲的 skill",
            feature_name="ling-skill",
            content="# Requirements\n\n- REQ-001: deliver skill.\n",
        )
    )
    spec_id = created["specId"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="requirements"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            spec_id=spec_id,
            kind="design",
            content="# Design\n\n- DES-001: design.\n",
        )
    )["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="design"))["ok"]
    assert _payload(
            spec_broker.func(
                mode="start",
                workspace_path=str(workspace),
                spec_id=spec_id,
                kind="tasks",
                content=(
                    "# Tasks\n\n"
                    "| Task ID | runtimeLane | dependsOn | specRefs | expectedOutput | acceptance | proofRequired |\n"
                    "| --- | --- | --- | --- | --- | --- | --- |\n"
                    "| TASK-001 | Engineering | - | REQ-001, DES-001 | Skill files written | SkillLoader can load skill | file manifest + validation |\n\n"
                    "### TASK-001: Deliver skill\n\n"
                    "- runtimeLane: Engineering\n"
                    "- dependsOn: -\n"
                    "- specRefs: REQ-001, DES-001\n"
                    "- expectedOutput: SKILL.md and research references\n"
                    "- acceptance: SkillLoader can load the generated skill\n"
                    "- proofRequired: generated file manifest and validation result\n"
                ),
            )
        )["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="tasks"))["pipelineControl"]["runtimeExecutionAllowed"] is True

    blocked = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            kind="bugfix",
            feature_name="ling-skill-fix",
            user_request="SKILL.md 为空，需要修复失败的执行结果。",
            content="# Bugfix\n\n- BFIX-001: repair empty skill.\n",
        )
    )

    assert blocked["ok"] is False
    assert blocked["kind"] == "spec_runtime_execution_active"
    assert blocked["specId"] == spec_id


def test_spec_broker_runtime_execution_is_not_writable_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成一个可加载 skill",
            feature_name="runtime-execution-not-stage",
            content="# Requirements\n\n- REQ-001: deliver a loadable skill.\n",
        )
    )
    spec_id = created["specId"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="requirements"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
            content="# Design\n\n- DES-001: Use the skill template and write files through Engineering Runtime.\n",
        )
    )["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="design"))["ok"]
    assert _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="tasks",
            content=(
                "# Tasks\n\n"
                "| Task ID | runtimeLane | dependsOn | specRefs | expectedOutput | acceptance | proofRequired |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
                "| TASK-001 | Engineering | - | REQ-001, DES-001 | SKILL.md | Skill loads | file proof |\n\n"
                "### TASK-001: Deliver skill\n\n"
                "- runtimeLane: Engineering\n"
                "- dependsOn: []\n"
                "- specRefs: REQ-001, DES-001\n"
                "- expectedOutput: SKILL.md\n"
                "- acceptance: SkillLoader can load the generated skill\n"
                "- proofRequired: file proof\n"
            ),
        )
    )["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=spec_id, stage="tasks"))["ok"]

    correction = _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="runtime_execution",
            content="# Runtime Execution\n\nDo the work.",
        )
    )

    assert correction["ok"] is False
    assert correction["kind"] == "spec_runtime_execution_not_stage"
    assert correction["state"] == "runtime_execution_ready"
    assert correction["requiredNextTool"] == "runtime_broker"
    assert "runtime_broker(mode='route'" in correction["recommendedNextAction"]
    assert "stage='runtime_execution'" in "\n".join(correction["doNot"])


def test_spec_broker_default_resolution_ignores_delivered_specs(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="同题旧 Spec",
            feature_name="duplicate-topic",
            content="# Requirements\n\n- REQ-001: old.\n",
        )
    )
    second = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="同题当前 Spec",
            feature_name="duplicate-topic-current",
            content="# Requirements\n\n- REQ-001: current.\n",
        )
    )
    assert first["ok"] and second["ok"]
    first_id = first["specId"]
    second_id = second["specId"]

    # Mark the old spec delivered after the current one was created. Even if
    # its updatedAt becomes newer, it must not be an automatic active candidate.
    spec_service.mark_delivered(workspace_path=str(workspace), spec_id=first_id, run_id="run-old")

    listing = _payload(spec_broker.func(mode="list", workspace_path=str(workspace)))
    assert [item["specId"] for item in listing["specs"]] == [second_id]

    approved = _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            stage="requirements",
            comment="approve the current active spec",
        )
    )

    assert approved["ok"] is True
    assert approved["specId"] == second_id


def test_spec_broker_allows_new_requirements_with_validation_words_when_old_spec_ready(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    old_req = _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            user_request="旧的 live counter",
            feature_name="spec-mode-v2",
            stage="requirements",
            content="# Requirements\n\n- REQ-001: old counter.\n",
        )
    )
    old_id = old_req["specId"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=old_id, stage="requirements"))["ok"]
    old_design = _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=old_id,
            stage="design",
            content="# Design\n\n## DES-001\n\nUse plain HTML.\n",
        )
    )
    assert old_design["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=old_id, stage="design"))["ok"]
    old_tasks = _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            spec_id=old_id,
            stage="tasks",
            content=(
                "# Tasks\n\n"
                "## Task Pipeline\n\n"
                "| Task ID | Runtime lane | Goal | Depends on | Spec refs | Expected output | Acceptance / proof |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
                "| TASK-001 | Engineering | Build old counter. | - | REQ-001, DES-001 | index.html | file exists |\n\n"
                "### TASK-001: Build old counter\n\n"
                "- runtimeLane: Engineering\n"
                "- dependsOn: []\n"
                "- specRefs: REQ-001, DES-001\n"
                "- expectedOutput: index.html\n"
                "- acceptance: file exists\n"
                "- proofRequired: file proof\n"
            ),
        )
    )
    assert old_tasks["ok"]
    assert _payload(spec_broker.func(mode="approve", workspace_path=str(workspace), spec_id=old_id, stage="tasks"))["ok"]

    new_req = _payload(
        spec_broker.func(
            mode="write_stage",
            workspace_path=str(workspace),
            user_request="新建一个带验收与验证步骤的计数器 Spec",
            feature_name="spec-mode-v2",
            stage="requirements",
            content="# Requirements\n\n- REQ-001: new counter with 验收 and 验证 wording.\n",
        )
    )

    assert new_req["ok"] is True
    assert new_req["kind"] in {"spec_stage_ready", "spec_stage_edited"}
    assert new_req["specId"] != old_id


def test_spec_broker_runtime_context_spec_id_wins_over_feature_match(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    old = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="旧的 live counter 需求",
            feature_name="spec-mode-v2-live-counter",
            content="# Requirements\n\n- REQ-001: old live counter.\n",
        )
    )
    current = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="当前 run 的 Spec",
            feature_name="spec-mode-v2-current-run",
            content="# Requirements\n\n- REQ-001: current run.\n",
        )
    )
    old_id = old["specId"]
    current_id = current["specId"]
    assert old_id != current_id

    for spec_id in (old_id, current_id):
        assert _payload(
            spec_broker.func(
                mode="approve",
                workspace_path=str(workspace),
                spec_id=spec_id,
                stage="requirements",
                comment="requirements approved",
            )
        )["ok"]
        assert _payload(
            spec_broker.func(
                mode="start",
                workspace_path=str(workspace),
                spec_id=spec_id,
                kind="design",
                content="# Design\n\n- DES-001: approved design.\n",
            )
        )["ok"]
        assert _payload(
            spec_broker.func(
                mode="approve",
                workspace_path=str(workspace),
                spec_id=spec_id,
                stage="design",
                comment="design approved",
            )
        )["ok"]

    with bind_runtime_context(
        workspace_path=str(workspace),
        spec_id=current_id,
        specId=current_id,
    ):
        tasks = _payload(
            spec_broker.func(
                mode="start",
                workspace_path=str(workspace),
                feature_name="spec-mode-v2-live-counter",
                kind="tasks",
                content="# Tasks\n\n- [ ] TASK-001: runtime lane Engineering; Links: REQ-001, DES-001\n",
            )
        )

    assert tasks["ok"] is True
    assert tasks["specId"] == current_id
    assert tasks["stage"] == "tasks"

    old_brief = spec_service.build_brief(workspace_path=str(workspace), spec_id=old_id)
    old_tasks_doc = (old_brief.get("documents") or {}).get("tasks") or {}
    assert old_tasks_doc.get("status") in {None, "missing", "stale"}


def test_spec_broker_continuation_rejects_wrong_stage_without_new_spec(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="Spec continuation demo",
            feature_name="continuation-demo",
            content="# Requirements\n\n- REQ-001: demo.\n",
        )
    )
    spec_id = started["specId"]
    assert _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            comment="requirements approved",
        )
    )["ok"]

    with bind_runtime_context(
        workspace_path=str(workspace),
        specContinuation={
            "kind": "spec_approval_continuation",
            "specId": spec_id,
            "nextStage": "design",
            "approvedStages": ["requirements"],
        },
    ):
        rejected = _payload(
            spec_broker.func(
                mode="write_stage",
                workspace_path=str(workspace),
                stage="requirements",
                feature_name="accidental-new-requirements",
                content="# Requirements\n\n- REQ-001: should not create a new spec.\n",
            )
        )

    assert rejected["ok"] is False
    assert rejected["kind"] == "spec_stage_mismatch"
    assert rejected["specId"] == spec_id
    assert rejected["expectedStage"] == "design"
    listing = _payload(spec_broker.func(mode="list", workspace_path=str(workspace)))
    assert listing["count"] == 1
    assert listing["specs"][0]["specId"] == spec_id
    assert "mode='approve'" not in listing["recommendedNextAction"]
    assert "user/client approval event" in listing["recommendedNextAction"]


def test_spec_broker_continuation_rejects_wrong_spec_id(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    current = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="Current spec",
            feature_name="current-spec",
            content="# Requirements\n\n- REQ-001: current.\n",
        )
    )
    other = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="Other spec",
            feature_name="other-spec",
            content="# Requirements\n\n- REQ-001: other.\n",
        )
    )

    with bind_runtime_context(
        workspace_path=str(workspace),
        specContinuation={
            "kind": "spec_approval_continuation",
            "specId": current["specId"],
            "nextStage": "design",
            "approvedStages": ["requirements"],
        },
    ):
        rejected = _payload(
            spec_broker.func(
                mode="write_stage",
                workspace_path=str(workspace),
                spec_id=other["specId"],
                stage="design",
                content="# Design\n\n- DES-001: wrong spec.\n",
            )
        )

    assert rejected["ok"] is False
    assert rejected["kind"] == "spec_id_mismatch"
    assert rejected["expectedSpecId"] == current["specId"]
    assert rejected["attemptedSpecId"] == other["specId"]


def test_spec_broker_continuation_defaults_missing_stage_to_next_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="Spec continuation stage default demo",
            feature_name="continuation-stage-default",
            content="# Requirements\n\n- REQ-001: demo.\n",
        )
    )
    spec_id = started["specId"]
    assert _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            comment="requirements approved",
        )
    )["ok"]

    with bind_runtime_context(
        workspace_path=str(workspace),
        specContinuation={
            "kind": "spec_approval_continuation",
            "specId": spec_id,
            "nextStage": "design",
            "approvedStages": ["requirements"],
        },
    ):
        design = _payload(
            spec_broker.func(
                mode="write_stage",
                workspace_path=str(workspace),
                content="# Design\n\n- DES-001: design generated from continuation.\n",
            )
        )

    assert design["ok"] is True
    assert design["specId"] == spec_id
    assert design["stage"] == "design"


def test_spec_broker_stage_alias_reuses_active_spec_instead_of_creating_default(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成绝区零角色玲视角 skill",
            feature_name="zzz-ling-perspective-skill",
        )
    )
    assert started["ok"] is True
    spec_id = started["specId"]

    staged = _payload(
        spec_broker.func(
            mode="stage",
            workspace_path=str(workspace),
            stage="requirements",
            content="# Requirements\n\n- REQ-1: 继续写入 zzz-ling-perspective-skill 需求。\n",
        )
    )
    assert staged["ok"] is True
    assert staged["specId"] == spec_id
    assert staged["kind"] == "spec_stage_edited"

    listing = _payload(spec_broker.func(mode="list", workspace_path=str(workspace)))
    assert listing["count"] == 1
    assert listing["specs"][0]["specId"] == spec_id


def test_spec_broker_accepts_feature_slug_in_spec_id_for_downstream_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    feature_slug = "zzz-ling-perspective-skill-generation"
    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成绝区零角色玲视角 skill",
            feature_name=feature_slug,
            content="# Requirements\n\n- REQ-1: 生成可加载的角色视角 skill。\n",
        )
    )
    assert started["ok"] is True
    spec_id = started["specId"]

    approved = _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=feature_slug,
            stage="requirements",
            comment="model supplied feature slug as spec_id",
        )
    )
    assert approved["ok"] is True
    assert approved["specId"] == spec_id

    design = _payload(
        spec_broker.func(
            mode="write",
            workspace_path=str(workspace),
            spec_id=feature_slug,
            stage="design",
            content="# Design\n\n- DES-1: 先调研，再按 skill 模板生成。\n",
        )
    )

    assert design["ok"] is True
    assert design["kind"] == "spec_stage_edited"
    assert design["specId"] == spec_id
    assert design["stage"] == "design"


def test_spec_broker_start_kind_design_edits_active_design_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    feature_slug = "zzz-ling-perspective"
    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成绝区零角色玲视角 skill",
            feature_name=feature_slug,
            content="# Requirements\n\n- REQ-001: 生成可加载的角色视角 skill。\n",
        )
    )
    assert started["ok"] is True
    spec_id = started["specId"]

    approved = _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            comment="requirements approved",
        )
    )
    assert approved["ok"] is True

    design = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            spec_id=f"{feature_slug}-design",
            feature_name=feature_slug,
            kind="design",
            content="# Design\n\n- DES-001: 先调研角色资料，再按 skill 模板生成。\n",
        )
    )

    assert design["ok"] is True
    assert design["kind"] == "spec_stage_edited"
    assert design["specId"] == spec_id
    assert design["stage"] == "design"


def test_spec_broker_start_kind_tasks_edits_active_tasks_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    feature_slug = "zzz-ling-perspective"
    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成绝区零角色玲视角 skill",
            feature_name=feature_slug,
            content="# Requirements\n\n- REQ-001: 生成可加载的角色视角 skill。\n",
        )
    )
    assert started["ok"] is True
    spec_id = started["specId"]
    assert _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            comment="requirements approved",
        )
    )["ok"]

    design = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            spec_id=feature_slug,
            feature_name=feature_slug,
            kind="design",
            content="# Design\n\n- DES-001: 先调研角色资料，再按 skill 模板生成。\n",
        )
    )
    assert design["ok"] is True
    assert _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
            comment="design approved",
        )
    )["ok"]

    tasks = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            spec_id=feature_slug,
            feature_name=feature_slug,
            kind="tasks",
            content=(
                "# Tasks\n\n"
                "## Task Pipeline\n\n"
                "| Task ID | Runtime lane | Goal | Depends on | Spec refs | Expected output | Acceptance / proof |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
                "| TASK-001 | Engineering | 生成 SKILL.md。 | - | REQ-001, DES-001 | SKILL.md | 文件存在且可加载。 |\n\n"
                "## Task Details\n\n"
                "### TASK-001: 生成 SKILL.md\n\n"
                "- runtimeLane: Engineering\n"
                "- dependsOn: []\n"
                "- specRefs: REQ-001, DES-001\n"
                "- inputRefs: approved requirements/design\n"
                "- expectedOutput: SKILL.md\n"
                "- acceptance: 文件存在且可加载\n"
                "- proofRequired: file manifest + validation\n"
            ),
        )
    )

    assert tasks["ok"] is True
    assert tasks["kind"] == "spec_stage_edited"
    assert tasks["specId"] == spec_id
    assert tasks["stage"] == "tasks"
    assert tasks["pipelineControl"]["blockedByApproval"] == "tasks"
    assert tasks["pipelineControl"]["runtimeExecutionAllowed"] is False
    assert tasks["tasksPipeline"]["valid"] is True


def test_spec_broker_read_stage_alias_reads_current_stage(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成绝区零角色玲视角 skill",
            feature_name="zzz-ling-perspective",
            content="# Requirements\n\n- REQ-001: 生成可加载的角色视角 skill。\n",
        )
    )
    assert started["ok"] is True

    read = _payload(
        spec_broker.func(
            mode="read_stage",
            workspace_path=str(workspace),
            feature_name="zzz-ling-perspective",
            stage="requirements",
        )
    )

    assert read["ok"] is True
    assert read["kind"] == "spec_section"
    assert "REQ-001" in read["content"]


def test_spec_broker_write_stage_stops_live_turn_for_user_approval(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "core.tools.native.spec.command_service.request_approval",
        lambda request: {
            "approval_id": "approval_spec_stage",
            "approval_kind": request.approval_kind,
            "status": "pending",
        },
    )
    monkeypatch.setattr(
        "core.tools.native.spec.db.list_ask_user_interactions",
        lambda **_kwargs: _resolved_spec_clarification(
            workspace_path=str(workspace),
            feature_name="zzz-ling-perspective",
            stage="requirements",
        ),
    )

    with bind_runtime_context(
        runtime_kind="chat",
        session_id="session_spec_approval_gate",
        run_id="run_spec_approval_gate",
        workspace_path=str(workspace),
    ):
        result = spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成绝区零角色玲视角 skill",
            feature_name="zzz-ling-perspective",
            content="# Requirements\n\n- REQ-001: 生成可加载的角色视角 skill。\n",
            tool_call_id="call_spec_stage",
        )

    assert isinstance(result, Command)
    assert getattr(result, "goto", None) == "__end__"
    messages = list((result.update or {}).get("messages") or [])
    assert messages
    payload = _payload(messages[0].content)
    assert payload["kind"] == "spec_stage_waiting_user_approval"
    assert payload["stage"] == "requirements"
    assert payload["specId"].startswith("spec_")
    assert payload["approvalKind"] == "spec_stage_approval"
    assert payload["recommendedNextAction"].startswith("Wait for the Spec approval gate")
    assert payload["transitionHint"]["state"] == "waiting_user_approval"
    assert payload["transitionHint"]["nextStageAfterApproval"] == "design"
    assert "approves requirements" in payload["transitionHint"]["ifApproved"]


def test_spec_broker_tasks_stage_stops_live_turn_for_user_approval(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "core.tools.native.spec.command_service.request_approval",
        lambda request: {
            "approval_id": f"approval_{request.request.get('stage')}",
            "approval_kind": request.approval_kind,
            "status": "pending",
        },
    )
    monkeypatch.setattr(
        "core.tools.native.spec.db.list_ask_user_interactions",
        lambda **_kwargs: _resolved_spec_clarification(
            workspace_path=str(workspace),
            feature_name="zzz-ling-perspective",
            stage="tasks",
        ),
    )

    started = _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            user_request="生成绝区零角色玲视角 skill",
            feature_name="zzz-ling-perspective",
            content="# Requirements\n\n- REQ-001: 生成可加载的角色视角 skill。\n",
        )
    )
    spec_id = started["specId"]
    assert _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="requirements",
            comment="requirements approved",
        )
    )["ok"]
    _payload(
        spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            spec_id=spec_id,
            kind="design",
            content="# Design\n\n- DES-001: 按 skill 模板生成并验证。\n",
        )
    )
    assert _payload(
        spec_broker.func(
            mode="approve",
            workspace_path=str(workspace),
            spec_id=spec_id,
            stage="design",
            comment="design approved",
        )
    )["ok"]

    with bind_runtime_context(
        runtime_kind="chat",
        session_id="session_spec_tasks_gate",
        run_id="run_spec_tasks_gate",
        workspace_path=str(workspace),
    ):
        result = spec_broker.func(
            mode="start",
            workspace_path=str(workspace),
            spec_id=spec_id,
            kind="tasks",
            content=(
                "# Tasks\n\n"
                "## Task Pipeline\n\n"
                "| Task ID | Runtime lane | Goal | Depends on | Spec refs | Expected output | Acceptance / proof |\n"
                "| --- | --- | --- | --- | --- | --- | --- |\n"
                "| TASK-001 | Engineering | 生成 SKILL.md。 | - | REQ-001, DES-001 | SKILL.md | 文件存在且可加载。 |\n\n"
                "## Task Details\n\n"
                "### TASK-001: 生成 SKILL.md\n\n"
                "- runtimeLane: Engineering\n"
                "- dependsOn: []\n"
                "- specRefs: REQ-001, DES-001\n"
                "- inputRefs: approved requirements/design\n"
                "- expectedOutput: SKILL.md\n"
                "- acceptance: 文件存在且可加载\n"
                "- proofRequired: file manifest + validation\n"
            ),
            tool_call_id="call_spec_tasks",
        )

    assert isinstance(result, Command)
    assert getattr(result, "goto", None) == "__end__"
    messages = list((result.update or {}).get("messages") or [])
    assert messages
    payload = _payload(messages[0].content)
    assert payload["kind"] == "spec_stage_waiting_user_approval"
    assert payload["stage"] == "tasks"
    assert payload["approvalId"] == "approval_tasks"
    assert payload["pipelineControl"]["blockedByApproval"] == "tasks"
    assert payload["pipelineControl"]["runtimeExecutionAllowed"] is False
    assert payload["transitionHint"]["state"] == "waiting_user_approval"
    assert payload["transitionHint"]["nextStageAfterApproval"] == "runtime_execution"
    assert "runtime_broker" in payload["transitionHint"]["ifApproved"]
