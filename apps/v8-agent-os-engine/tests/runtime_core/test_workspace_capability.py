from __future__ import annotations

from pathlib import Path

from core import workspace_capability as workspace_capability_module
from core.workspace_capability import preflight_command_workspace, resolve_workspace_tool_path
from erc.runtime_context import bind_runtime_context


def _patch_descriptor(monkeypatch, *, active_root: Path, main_root: Path) -> None:
    def _descriptor(**_kwargs):
        return {
            "runtimeKind": "chat",
            "projectId": "test2",
            "workspaceId": "test2",
            "workspaceRoot": str(active_root),
            "source": "explicit_workspace_path",
            "usesScopedWorkspace": True,
            "isScopedOverride": active_root != main_root,
            "mainWorkspacePath": str(main_root),
        }

    monkeypatch.setattr(
        workspace_capability_module.workspace_resolution_service,
        "resolve_workspace_descriptor",
        _descriptor,
    )


def test_relative_tool_path_resolves_inside_active_workspace(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    result = resolve_workspace_tool_path("src/app.ts", runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is True
    assert Path(result["resolvedPath"]) == active_root / "src" / "app.ts"
    assert result["relation"] == "inside_active_workspace"


def test_absolute_default_workspace_path_is_blocked_when_scoped(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    result = resolve_workspace_tool_path(str(main_root / "projects" / "wrong" / "package.json"), runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is False
    assert result["error"] == "workspace_boundary_violation"


def test_command_preflight_blocks_absolute_path_outside_active_workspace(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    command = f'cd /d "{main_root}\\projects\\wrong" && npm install'
    result = preflight_command_workspace(command, runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is False
    assert result["error"] == "workspace_command_path_violation"
    assert result["violations"]


def test_command_preflight_expands_userprofile_paths(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    monkeypatch.setenv("USERPROFILE", str(main_root))

    result = preflight_command_workspace(r"cd %USERPROFILE%\\.v8-agent-os\\workspace && npm install", runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is False
    assert result["error"] == "workspace_command_path_violation"


def test_write_native_file_blocks_default_workspace_when_scoped(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        allowed_result = native_tools.write_native_file.func("src/ok.txt", "ok")
        blocked_result = native_tools.write_native_file.func(str(main_root / "projects" / "wrong.txt"), "bad")

    assert "Successfully Created/Overwritten" in allowed_result
    assert (active_root / "src" / "ok.txt").read_text(encoding="utf-8") == "ok"
    assert "workspace_boundary_block" in blocked_result
    assert not (main_root / "projects" / "wrong.txt").exists()


def test_write_native_file_supports_line_scoped_patch(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    monkeypatch.setattr(
        native_tools,
        "_workspace_inventory_status",
        lambda _context: {"hasInventoryToken": True, "workspaceRoot": str(active_root)},
    )
    target = active_root / "src" / "longish.txt"
    target.parent.mkdir(parents=True)
    target.write_text("one\nold-a\nold-b\nfour\n", encoding="utf-8")

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        native_tools.read_native_file.func("src/longish.txt")
        result = native_tools.write_native_file.func("src/longish.txt", "new-a\nnew-b", line_start=2, line_end=3)

    assert "scoped_file_patch" in result
    assert target.read_text(encoding="utf-8") == "one\nnew-a\nnew-b\nfour\n"


def test_write_native_file_blocks_existing_long_file_full_overwrite_without_anchor(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    monkeypatch.setattr(
        native_tools,
        "_workspace_inventory_status",
        lambda _context: {"hasInventoryToken": True, "workspaceRoot": str(active_root)},
    )
    target = active_root / "src" / "very_long.txt"
    target.parent.mkdir(parents=True)
    target.write_text("".join(f"line {index}\n" for index in range(1000)), encoding="utf-8")

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        native_tools.read_native_file.func("src/very_long.txt")
        result = native_tools.write_native_file.func("src/very_long.txt", "replacement")

    assert "long_file_full_overwrite_block" in result
    assert "line 999" in target.read_text(encoding="utf-8")


def test_write_native_file_requires_read_before_modifying_existing_file(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    monkeypatch.setattr(
        native_tools,
        "_workspace_inventory_status",
        lambda _context: {"hasInventoryToken": True, "workspaceRoot": str(active_root)},
    )
    target = active_root / "src" / "existing.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
    ):
        blocked = native_tools.write_native_file.func("src/existing.txt", "after")
        native_tools.read_native_file.func("src/existing.txt")
        allowed = native_tools.write_native_file.func(
            "src/existing.txt",
            "after",
            expected_old_text="before",
        )
        blocked_again = native_tools.write_native_file.func(
            "src/existing.txt",
            "after-again",
            expected_old_text="after",
        )

    assert "read_before_write_required" in blocked
    assert "scoped_file_patch" in allowed
    assert "read_before_write_required" in blocked_again
    assert target.read_text(encoding="utf-8") == "after"


def test_write_native_file_rejects_stale_read_receipt(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    monkeypatch.setattr(
        native_tools,
        "_workspace_inventory_status",
        lambda _context: {"hasInventoryToken": True, "workspaceRoot": str(active_root)},
    )
    target = active_root / "src" / "changed.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
    ):
        native_tools.read_native_file.func("src/changed.txt")
        target.write_text("changed elsewhere with a different size", encoding="utf-8")
        result = native_tools.write_native_file.func("src/changed.txt", "after")

    assert "read_before_write_required" in result
    assert "file_changed_after_read" in result
    assert target.read_text(encoding="utf-8") == "changed elsewhere with a different size"
