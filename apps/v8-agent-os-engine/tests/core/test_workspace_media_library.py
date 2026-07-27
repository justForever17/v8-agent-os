from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.database import DatabaseManager
from core.workspace_media_library import WorkspaceMediaLibraryError, WorkspaceMediaLibraryService


def _authority(path: Path, workspace_id: str = "workspace-a") -> SimpleNamespace:
    return SimpleNamespace(
        workspace_root=str(path),
        workspace_id=workspace_id,
        project_id="project-a",
        side_effects_allowed=True,
    )


def test_workspace_asset_identity_is_shared_but_session_use_and_workspace_boundary_are_strict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    media = workspace_a / "uploads" / "shot.mp4"
    media.parent.mkdir()
    media.write_bytes(b"governed-media")
    for session_id in ("session-a", "session-b", "session-c"):
        database.create_or_update_session(session_id, session_id, user_id="user")
    database.add_session_source(
        source_id="source-a",
        session_id="session-a",
        source_kind="web_upload",
        mime_type="video/mp4",
        title="shot.mp4",
        workspace_path=str(media),
    )

    monkeypatch.setattr("core.workspace_media_library.db", database)
    monkeypatch.setattr(
        "core.workspace_media_library.workspace_authority_service.resolve",
        lambda **kwargs: _authority(workspace_b, "workspace-b")
        if kwargs.get("session_id") == "session-c"
        else _authority(workspace_a),
    )
    library = WorkspaceMediaLibraryService()

    asset = library.register_source(session_id="session-a", source_id="source-a")
    asset_id = asset["assetId"]
    assert asset["adoptedByCurrentSession"] is True
    assert asset["workspaceRelativePath"] == "uploads/shot.mp4"
    assert "workspace-a" not in asset["contentUrl"]

    session_b_assets = library.list_assets(session_id="session-b")
    assert [item["assetId"] for item in session_b_assets] == [asset_id]
    assert session_b_assets[0]["adoptedByCurrentSession"] is False
    with pytest.raises(PermissionError, match="explicitly adopted"):
        library.resolve_asset_path(session_id="session-b", asset_id=asset_id, require_session_use=True)

    adopted = library.use_asset(session_id="session-b", asset_id=asset_id)
    assert adopted["adoptedByCurrentSession"] is True
    assert library.resolve_asset_path(
        session_id="session-b",
        asset_id=asset_id,
        require_session_use=True,
    ) == media.resolve()

    production = library.create_folder(
        session_id="session-a",
        title="Series A",
        folder_kind="production",
    )
    episode = library.create_folder(
        session_id="session-a",
        title="Episode 01",
        folder_kind="episode",
        parent_folder_id=production["folderId"],
    )
    with pytest.raises(WorkspaceMediaLibraryError, match="already exists"):
        library.create_folder(
            session_id="session-b",
            title="episode 01",
            folder_kind="episode",
            parent_folder_id=production["folderId"],
        )
    placed = library.place_asset(
        session_id="session-a",
        asset_id=asset_id,
        folder_id=episode["folderId"],
    )
    assert placed["folderId"] == episode["folderId"]
    assert {item["folderId"] for item in library.list_folders(session_id="session-b")} == {
        production["folderId"],
        episode["folderId"],
    }

    assert library.list_assets(session_id="session-c") == []
    with pytest.raises(WorkspaceMediaLibraryError, match="not in the current session workspace"):
        library.get_asset(session_id="session-c", asset_id=asset_id)

    database.delete_session("session-a")
    assert library.get_asset(session_id="session-b", asset_id=asset_id)["assetId"] == asset_id


def test_internal_canvas_mask_is_never_registered_as_workspace_media(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    mask = workspace / "mask.png"
    mask.write_bytes(b"mask")
    database.create_or_update_session("session-a", "A", user_id="user")
    database.add_session_source(
        source_id="mask-a",
        session_id="session-a",
        source_kind="canvas_mask",
        mime_type="image/png",
        title="mask.png",
        workspace_path=str(mask),
    )
    monkeypatch.setattr("core.workspace_media_library.db", database)
    monkeypatch.setattr(
        "core.workspace_media_library.workspace_authority_service.resolve",
        lambda **_kwargs: _authority(workspace),
    )
    with pytest.raises(WorkspaceMediaLibraryError, match="masks"):
        WorkspaceMediaLibraryService().register_source(session_id="session-a", source_id="mask-a")
