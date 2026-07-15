from __future__ import annotations

from pathlib import Path

from core.workspace_state_digest import (
    build_workspace_state_digest_context,
    command_may_change_workspace,
    mark_workspace_state_stale,
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

    assert "[WORKSPACE FACTS]" in text
    assert str(tmp_path) in text
    assert "package.json" in text
    assert "Physical Path Present: true" in text
    assert diagnostics and diagnostics[0]["repoDetected"] is False
    assert diagnostics[0]["physicalPathPresent"] is True


def test_workspace_state_digest_marks_mutated_snapshot_stale(tmp_path: Path):
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


def test_workspace_state_digest_does_not_cache_a_physically_deleted_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = {
        "session_id": "session-deleted",
        "run_id": "run-deleted",
        "workspace_path": str(workspace),
        "workspace_id": "workspace-deleted",
    }

    _text, diagnostics = build_workspace_state_digest_context(state=context, session_id="session-deleted")
    assert diagnostics[0]["physicalPathPresent"] is True

    workspace.rmdir()
    text, diagnostics = build_workspace_state_digest_context(state=context, session_id="session-deleted")
    assert "Physical Path Present: false" in text
    assert diagnostics[0]["physicalPathPresent"] is False


def test_command_may_change_workspace_heuristic():
    assert command_may_change_workspace("npm install")
    assert command_may_change_workspace("git checkout -b feature")
    assert command_may_change_workspace("New-Item -ItemType Directory src")
    assert not command_may_change_workspace("git status --short")
    assert not command_may_change_workspace("python -m pytest -q")
