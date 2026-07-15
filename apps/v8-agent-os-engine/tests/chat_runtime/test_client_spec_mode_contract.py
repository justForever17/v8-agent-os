from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]


def test_admin_bff_preserves_spec_mode_without_planner_fields():
    source = (REPO_ROOT / "apps/v8-agent-os-admin/src/lib/realtime/engine-chat-request.ts").read_text(encoding="utf-8")

    assert "const specMode = data.specMode === true" in source
    assert 'specMode,' in source
    assert "plannerMode" not in source


def test_phone_and_web_spec_toggle_send_only_spec_mode():
    phone_source = (REPO_ROOT / "apps/v8-agent-os-phone/src/lib/phone-api.ts").read_text(encoding="utf-8")
    web_source = (REPO_ROOT / "apps/v8-agent-os-web/src/components/chat/InputArea.tsx").read_text(encoding="utf-8")

    assert "plannerMode" not in phone_source
    assert "plannerMode" not in web_source
    assert "specMode:" in phone_source
    assert "nextData.specMode = true;" in web_source
