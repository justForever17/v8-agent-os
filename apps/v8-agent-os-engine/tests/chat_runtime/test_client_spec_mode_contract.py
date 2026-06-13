from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_admin_bff_preserves_spec_mode_and_disables_legacy_planner_force():
    source = (REPO_ROOT / "apps/v8-agent-os-admin/src/lib/realtime/engine-chat-request.ts").read_text(encoding="utf-8")

    assert "const specMode = data.specMode === true;" in source
    assert "plannerMode: specMode" in source
    assert 'specMode,' in source
    assert "taskPlanningMode: data.taskPlanningMode === true" not in source


def test_phone_and_web_spec_toggle_do_not_force_legacy_task_planner():
    phone_source = (REPO_ROOT / "apps/v8-agent-os-phone/src/lib/phone-api.ts").read_text(encoding="utf-8")
    web_source = (REPO_ROOT / "apps/v8-agent-os-web/src/components/chat/InputArea.tsx").read_text(encoding="utf-8")

    assert 'plannerMode: options.taskPlanningMode ? "off" : undefined' in phone_source
    assert "taskPlanningMode: options.taskPlanningMode ? true : undefined" not in phone_source
    assert 'nextData.plannerMode = "off";' in web_source
    assert "nextData.taskPlanningMode = true;" not in web_source
