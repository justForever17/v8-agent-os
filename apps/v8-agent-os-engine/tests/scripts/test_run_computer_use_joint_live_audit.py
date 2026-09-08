from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location(
    "computer_use_joint_audit_test", Path(__file__).with_name("run_computer_use_joint_live_audit.py"))
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.fixture(autouse=True)
def isolated_probe_scope(monkeypatch):
    from runtimes.memory.scope_resolution import scope_resolution_service
    calls = []
    monkeypatch.setattr(scope_resolution_service, "resolve", lambda **kwargs: calls.append(kwargs))
    return calls


def custom_brief():
    return {"taskBriefId": "owned-window", "goal": "在专用验收窗口点击按钮，确认状态变成 approved。",
            "writeSet": [], "acceptanceContract": ["当前专用窗口显示 approved"]}


def forbidden(*_args, **_kwargs):
    pytest.fail("custom direct case must not invoke a built-in case, global cleanup, or Supervisor")


def test_no_live_refuses_before_reading_custom_input_or_loading_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["audit", "--phase", "direct", "--case", "custom",
                                      "--workspace", str(tmp_path), "--task-brief-file", "missing.json"])
    monkeypatch.setattr(audit, "_load_custom_task_brief", forbidden)
    monkeypatch.setattr(audit, "_ensure_workspace_binding", forbidden)
    monkeypatch.setitem(sys.modules, "core.runtime.startup_profile",
                        SimpleNamespace(get_runtime_registry_state=forbidden))
    assert audit.main() == 2


@pytest.mark.parametrize("extra", [["--cleanup-test-processes"], ["--phase", "all"], ["--phase", "supervisor"]])
def test_custom_forbids_global_cleanup_and_non_direct_phases_before_input_read(monkeypatch, tmp_path, extra):
    monkeypatch.setattr(sys, "argv", ["audit", "--live", "--phase", "direct", "--case", "custom",
                                      "--workspace", str(tmp_path), "--task-brief-file", "missing.json", *extra])
    monkeypatch.setattr(audit, "_load_custom_task_brief", forbidden)
    with pytest.raises(SystemExit) as caught:
        audit.main()
    assert caught.value.code == 2


@pytest.mark.parametrize("change", [{"goal": ""}, {"acceptanceContract": []}, {"writeSet": "*"}])
def test_custom_brief_requires_explicit_goal_acceptance_and_write_scope(tmp_path, change):
    path = tmp_path / "brief.json"
    path.write_text(json.dumps({**custom_brief(), **change}), encoding="utf-8")
    with pytest.raises(ValueError):
        audit._load_custom_task_brief(str(path))


def test_custom_direct_main_uses_exact_brief_and_own_lineage_without_qq_or_browser_cleanup(monkeypatch, tmp_path, isolated_probe_scope):
    from core.database import DatabaseManager

    database = DatabaseManager(tmp_path / "state.db")
    brief = custom_brief()
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding="utf-8")
    workspace = tmp_path / "workspace"
    report = tmp_path / "report"
    calls = []

    def execute(**kwargs):
        calls.append(kwargs)
        assert database.get_session(kwargs["session_id"])["metadata"]["hiddenFromHistory"] is True
        record = database.get_run_record(kwargs["run_id"])
        assert record["session_id"] == kwargs["session_id"] and record["status"] == "running"
        assert kwargs["task_brief"] == brief and kwargs["max_rounds"] == 6
        return {"ok": True, "status": "completed", "verification": {"passed": True}, "actions": []}

    for name in ("_clean_test_processes", "_qqmusic_brief", "_qqmusic_processes", "_metaso_brief",
                 "_submit_supervisor_case", "_qqmusic_shortcut_replay_evidence"):
        monkeypatch.setattr(audit, name, forbidden)
    monkeypatch.setattr(audit, "_ensure_workspace_binding", lambda _workspace: {"projectId": "fixture", "workspaceId": "fixture"})
    monkeypatch.setitem(sys.modules, "core.runtime.startup_profile", SimpleNamespace(get_runtime_registry_state=lambda: None))
    monkeypatch.setitem(sys.modules, "core.database", SimpleNamespace(db=database))
    monkeypatch.setitem(sys.modules, "runtimes.computer_use.episode_agent", SimpleNamespace(execute_computer_use_task_brief=execute))
    monkeypatch.setitem(sys.modules, "runtimes.computer_use.runtime", SimpleNamespace(computer_use_runtime=object()))
    monkeypatch.setattr(sys, "argv", ["audit", "--live", "--phase", "direct", "--case", "custom",
                                      "--workspace", str(workspace), "--task-brief-file", str(brief_path),
                                      "--max-rounds", "6", "--report-dir", str(report)])
    assert audit.main() == 0
    assert len(calls) == 1
    result = json.loads((report / "report.json").read_text(encoding="utf-8"))
    assert result["initialCleanup"] == result["finalCleanup"] == {}
    record = database.get_run_record(calls[0]["run_id"])
    assert record["status"] == "completed" and record["metadata"]["internalProbe"] is True
    assert result["results"][0]["sessionId"] == calls[0]["session_id"]
    assert isolated_probe_scope == [{"session_id": calls[0]["session_id"], "run_id": calls[0]["run_id"],
                                    "user_id": "local-owner", "scope_mode": "explicit", "workspace_path": str(workspace),
                                    "workspace_id": "fixture", "project_id": "fixture"}]


@pytest.mark.parametrize("case_id", ["typo", "custom"])
def test_unknown_or_missing_custom_case_cannot_fall_through_to_qqmusic(case_id, monkeypatch, tmp_path):
    monkeypatch.setattr(audit, "_qqmusic_brief", forbidden)
    with pytest.raises(ValueError, match="unsupported_direct_case"):
        audit._run_direct_case(runtime=None, execute_task=forbidden, workspace=tmp_path,
                               binding={}, case_id=case_id, stamp="fixture", db=None)


def test_blocked_direct_case_is_durable_failure_not_success(tmp_path):
    from core.database import DatabaseManager

    database = DatabaseManager(tmp_path / "state.db")
    result = audit._run_direct_case(
        runtime=None, execute_task=lambda **_kwargs: {"ok": False, "status": "blocked", "verification": {"passed": False}},
        workspace=tmp_path, binding={"projectId": "fixture", "workspaceId": "fixture"},
        case_id="custom", stamp="fixture", db=database, custom_brief=custom_brief())
    record = database.get_run_record(result["runId"])
    assert result["passed"] is False and record["status"] == "failed"
    assert record["metadata"]["computerUseStatus"] == "blocked"
