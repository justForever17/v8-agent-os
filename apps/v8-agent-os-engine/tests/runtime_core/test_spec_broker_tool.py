import json

from core.tools.native.spec import spec_broker
from erc.runtime_context import bind_runtime_context
from langgraph.types import Command


def _payload(raw: str) -> dict:
    return json.loads(raw)


def test_spec_broker_description_lists_write_edit_read_modes():
    description = str(getattr(spec_broker, "description", "") or "")

    assert "write_stage" in description
    assert "edit" in description
    assert "write" in description
    assert "update" in description
    assert "read_stage" in description
    assert "It does not write final project deliverables" in description
    assert "runtime lane" in description
    assert "runtime_broker" in description


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
    payload = _payload(result.update["messages"][0].content)
    assert payload["approvalId"] == "approval_spec_demo"
    assert payload["approvalKind"] == "spec_stage_approval"
    assert payload["approvalStatus"] == "pending"


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
            content="# Tasks\n\n- [ ] TASK-001: 生成 SKILL.md。 Links: REQ-001, DES-001\n",
        )
    )

    assert tasks["ok"] is True
    assert tasks["kind"] == "spec_stage_edited"
    assert tasks["specId"] == spec_id
    assert tasks["stage"] == "tasks"
    assert tasks["pipelineControl"]["blockedByApproval"] == "tasks"
    assert tasks["pipelineControl"]["runtimeExecutionAllowed"] is False
    assert tasks["tasksPipeline"]["valid"] is False
    assert "runtimeLane" in tasks["tasksPipeline"]["missingFields"]


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
            content="# Tasks\n\n- [ ] TASK-001: 生成 SKILL.md。 Links: REQ-001, DES-001\n",
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
