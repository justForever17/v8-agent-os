from __future__ import annotations

from pathlib import Path

import pytest

from runtimes.rpa.default_templates import GITHUB_STAR_TEMPLATE_ID, ensure_system_rpa_seed_templates
from runtimes.rpa.runtime import RPARuntime
from runtimes.rpa.store import RPAScriptStore
from runtimes.rpa.template_service import RPATemplateService


class _FakeAdapter:
    def availability(self):
        return {"robotFramework": True, "rpaFramework": True}

    def export_script(self, *, script, output_dir=None):
        return {
            "path": str(Path(output_dir or ".") / f"{script['id']}.robot"),
            "scriptId": script["id"],
            "taskName": script.get("name"),
        }

    def build_command(self, *, robot_file, variables=None, output_dir=None, dry_run=False):
        return ["python", "-m", "robot", str(robot_file), str(sorted((variables or {}).items()))]


def _runtime(tmp_path: Path, *, approve: bool) -> RPARuntime:
    store = RPAScriptStore(root_dir=tmp_path)
    ensure_system_rpa_seed_templates(store)
    service = RPATemplateService(script_store=store)
    if approve:
        service.approve_template(GITHUB_STAR_TEMPLATE_ID, reviewer="test")
    return RPARuntime(adapter=_FakeAdapter(), script_store=store, template_service=service)


def test_run_approved_template_projects_template_truth_into_execution(monkeypatch, tmp_path: Path):
    runtime = _runtime(tmp_path, approve=True)
    prepared = runtime.prepare_template_run(
        template_id=GITHUB_STAR_TEMPLATE_ID,
        variables={"repo_url": "https://github.com/TurixAI/TuriX-CUA", "desired_state": "starred"},
    )
    policy = runtime._resolve_template_execution_policy(mode="template", prepared=prepared)

    assert policy["hasComputerUseSource"] is True
    assert policy["executionPath"] == "computer_use_first"
    assert policy["bypassCompileBlock"] is True
    assert prepared["export"] == {}
    assert prepared["command"] == []

    captured = {}

    def fake_execute_prepared(**kwargs):
        captured.update(kwargs)
        return {"status": "completed", "mode": kwargs["mode"]}

    monkeypatch.setattr(runtime, "_execute_prepared", fake_execute_prepared)
    result = runtime.run_template(
        template_id=GITHUB_STAR_TEMPLATE_ID,
        variables={"repo_url": "https://github.com/TurixAI/TuriX-CUA", "desired_state": "starred"},
        trigger_source="rpa_web",
        non_chat_run=True,
    )

    assert result == {"status": "completed", "mode": "template"}
    assert captured["mode"] == "template"
    assert captured["prepared"]["script"]["source"]["templateId"] == GITHUB_STAR_TEMPLATE_ID
    assert captured["prepared"]["script"]["metadata"]["templateStatus"] == "approved"
    assert captured["prepared"]["script"]["metadata"]["templateGovernance"]["stage"] == "approved_live"
    assert captured["variables"]["desired_state"] == "starred"
    assert captured["non_chat_run"] is True


def test_unapproved_template_cannot_run_from_user_entry(tmp_path: Path):
    runtime = _runtime(tmp_path, approve=False)

    with pytest.raises(ValueError, match="尚未批准"):
        runtime.prepare_template_run(template_id=GITHUB_STAR_TEMPLATE_ID, variables={})
