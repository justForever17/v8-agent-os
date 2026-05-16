from __future__ import annotations

from pathlib import Path

from core.workspace_state_digest import (
    build_workspace_state_digest_context,
    command_may_change_workspace,
    mark_workspace_state_stale,
    record_workspace_inventory_token,
)


def test_workspace_state_digest_includes_snapshot_and_workspace(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    state = {
        "session_id": "session-digest",
        "run_id": "run-digest",
        "workspace_path": str(tmp_path),
        "workspace_id": "workspace-digest",
    }

    text, diagnostics = build_workspace_state_digest_context(state=state, session_id="session-digest")

    assert "[WORKSPACE STATE SNAPSHOT]" in text
    assert str(tmp_path) in text
    assert "package.json" in text
    assert diagnostics and diagnostics[0]["repoDetected"] is False


def test_workspace_state_digest_stale_and_inventory_token(tmp_path: Path):
    context = {
        "session_id": "session-stale",
        "run_id": "run-stale",
        "workspace_path": str(tmp_path),
        "workspace_id": "workspace-stale",
    }
    build_workspace_state_digest_context(state=context, session_id="session-stale")

    mark_workspace_state_stale(context, reason="file_write", subject=str(tmp_path / "a.txt"))
    text, diagnostics = build_workspace_state_digest_context(state=context, session_id="session-stale")
    assert "stale=true" in text
    assert diagnostics[0]["stale"] is True

    record_workspace_inventory_token(context, token="abc123", inspected_path=str(tmp_path))
    _text, diagnostics = build_workspace_state_digest_context(state=context, session_id="session-stale")
    assert diagnostics[0]["inventoryToken"] == "abc123"


def test_command_may_change_workspace_heuristic():
    assert command_may_change_workspace("npm install")
    assert command_may_change_workspace("git checkout -b feature")
    assert command_may_change_workspace("New-Item -ItemType Directory src")
    assert not command_may_change_workspace("git status --short")
    assert not command_may_change_workspace("python -m pytest -q")

