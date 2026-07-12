from __future__ import annotations

from api import session_workflow_routes


def test_quick_index_invalidates_when_workspace_presentation_changes(tmp_path, monkeypatch):
    projects_path = tmp_path / "config.json"
    projects_path.write_text('{"version":2,"workspacePresentations":[]}', encoding="utf-8")
    monkeypatch.setattr(session_workflow_routes.storage, "base_dir", tmp_path)
    monkeypatch.setattr(
        session_workflow_routes,
        "_WEB_SESSION_INDEX_PATH",
        tmp_path / "web_session_index.json",
    )

    session_workflow_routes._write_web_session_index([])
    assert session_workflow_routes._read_web_session_index_payload() is not None

    projects_path.write_text(
        '{"version":2,"workspacePresentations":[{"workspacePath":"E:/demo","displayName":"Demo"}]}',
        encoding="utf-8",
    )
    assert session_workflow_routes._read_web_session_index_payload() is None
