from __future__ import annotations

import os
from pathlib import Path

import pytest

from erc.runtime_context import bind_runtime_context
from tests.runtime_core.test_workspace_capability import _patch_descriptor


@pytest.mark.parametrize("replacement", ["directory_symlink", "same_content_file"])
def test_native_write_rejects_identity_change_after_safety_preflight(tmp_path, monkeypatch, replacement):
    from core.tools.native import workspace_file as files

    root = tmp_path / "workspace"
    inside, alternate = root / "inside", root / "alternate"
    inside.mkdir(parents=True)
    alternate.mkdir()
    target = inside / "target.txt"
    target.write_text("base", encoding="utf-8")
    (alternate / target.name).write_text("base", encoding="utf-8")
    _patch_descriptor(monkeypatch, active_root=root, main_root=root)
    enforce = files._enforce_safety_decision
    changed = False

    def replace_after_preflight(*args, **kwargs):
        nonlocal changed
        result = enforce(*args, **kwargs)
        if result[0]:
            if replacement == "directory_symlink":
                inside.rename(root / "original-inside")
                inside.symlink_to(alternate, target_is_directory=True)
            else:
                target.rename(inside / "original.txt")
                target.write_text("base", encoding="utf-8")
            changed = True
        return result

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(root), agent_id="identity-test"):
        files.read_native_file.func(str(target))
        monkeypatch.setattr(files, "_enforce_safety_decision", replace_after_preflight)
        result = files.write_native_file.func(str(target), "changed", allow_full_replace=True)
    assert changed, result
    assert "file_identity_changed_before_commit" in result, result
    assert target.read_text(encoding="utf-8") == "base"
    assert (alternate / target.name).read_text(encoding="utf-8") == "base"
    original = root / "original-inside" / target.name if replacement == "directory_symlink" else inside / "original.txt"
    assert original.read_text(encoding="utf-8") == "base"
    assert not list(root.rglob("*.v8os-tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows directory sharing semantics")
@pytest.mark.parametrize("level", ["parent", "ancestor"])
def test_windows_commit_pins_actual_directory_handles_and_releases_them(tmp_path, monkeypatch, level):
    from core.tools.native import workspace_file as files

    ancestor = tmp_path / "ancestor"
    parent = ancestor / "inside"
    parent.mkdir(parents=True)
    target = parent / "target.txt"
    target.write_text("base", encoding="utf-8")
    directory = parent if level == "parent" else ancestor
    original_identity = directory.stat().st_ino
    replacement = tmp_path / "moved"
    replace = files.os.replace
    attempted = False

    def attempt_directory_rename(source, destination):
        nonlocal attempted
        with pytest.raises(PermissionError) as failure:
            directory.rename(replacement)
        assert failure.value.winerror in {5, 32}
        assert directory.stat().st_ino == original_identity
        attempted = True
        replace(source, destination)

    monkeypatch.setattr(files.os, "replace", attempt_directory_rename)
    files._atomic_write_text(target, "written", expected_version=files._file_state_fingerprint(target))
    assert attempted
    assert target.read_text(encoding="utf-8") == "written"
    directory.rename(replacement)
    assert replacement.stat().st_ino == original_identity


@pytest.mark.parametrize("failure", [PermissionError, KeyboardInterrupt])
def test_commit_failure_releases_directory_handles_and_removes_staging(tmp_path, monkeypatch, failure):
    from core.tools.native import workspace_file as files

    parent = tmp_path / "parent"
    parent.mkdir()
    target = parent / "target.txt"
    target.write_text("base", encoding="utf-8")

    def fail_replace(*args, **kwargs):
        raise failure("injected commit failure")

    monkeypatch.setattr(files.os, "replace", fail_replace)
    with pytest.raises(failure):
        files._atomic_write_text(target, "lost", expected_version=files._file_state_fingerprint(target))
    assert target.read_text(encoding="utf-8") == "base"
    assert list(parent.iterdir()) == [target]
    parent.rename(tmp_path / "released")


def test_safety_review_does_not_hold_directory_handles(tmp_path, monkeypatch):
    from core.tools.native import workspace_file as files

    root = tmp_path / "workspace"
    parent = root / "inside"
    parent.mkdir(parents=True)
    target = parent / "target.txt"
    target.write_text("base", encoding="utf-8")
    _patch_descriptor(monkeypatch, active_root=root, main_root=root)
    enforce = files._enforce_safety_decision
    moved = False

    def review(*args, **kwargs):
        nonlocal moved
        temporary = root / "temporarily-moved"
        parent.rename(temporary)
        temporary.rename(parent)
        moved = True
        return enforce(*args, **kwargs)

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(root), agent_id="review-test"):
        files.read_native_file.func(str(target))
        monkeypatch.setattr(files, "_enforce_safety_decision", review)
        result = files.write_native_file.func(str(target), "written", allow_full_replace=True)
    assert moved
    assert "Successfully" in result, result
    assert target.read_text(encoding="utf-8") == "written"


def test_native_write_can_create_multiple_missing_parent_directories(tmp_path, monkeypatch):
    from core.tools.native import workspace_file as files

    root = tmp_path / "workspace"
    root.mkdir()
    _patch_descriptor(monkeypatch, active_root=root, main_root=root)
    target = root / "new" / "nested" / "target.txt"
    with bind_runtime_context(runtime_kind="chat", workspace_path=str(root), agent_id="create-test"):
        result = files.write_native_file.func(str(target), "created")
    assert "Successfully" in result, result
    assert target.read_text(encoding="utf-8") == "created"
    assert not list(root.rglob("*.v8os-tmp"))


def test_new_directories_do_not_follow_ancestor_swapped_during_safety(tmp_path, monkeypatch):
    from core.tools.native import workspace_file as files

    root = tmp_path / "workspace"
    inside, alternate = root / "inside", root / "alternate"
    inside.mkdir(parents=True)
    alternate.mkdir()
    _patch_descriptor(monkeypatch, active_root=root, main_root=root)
    enforce = files._enforce_safety_decision

    def swap(*args, **kwargs):
        result = enforce(*args, **kwargs)
        if result[0]:
            inside.rename(root / "original")
            inside.symlink_to(alternate, target_is_directory=True)
        return result

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(root), agent_id="create-test"):
        monkeypatch.setattr(files, "_enforce_safety_decision", swap)
        result = files.write_native_file.func(str(inside / "new" / "nested" / "target.txt"), "lost")
    assert "file_identity_changed_before_commit" in result, result
    assert list(alternate.iterdir()) == []
    assert list((root / "original").iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX dir_fd rename semantics")
def test_posix_rename_at_replace_keeps_write_bound_to_original_directory(tmp_path, monkeypatch):
    from core.tools.native import workspace_file as files
    from core.tools.native.workspace_file_identity import FileCommitPathChanged

    parent, alternate = tmp_path / "inside", tmp_path / "alternate"
    parent.mkdir()
    alternate.mkdir()
    target = parent / "target.txt"
    target.write_text("base", encoding="utf-8")
    (alternate / target.name).write_text("base", encoding="utf-8")
    replace = files.os.replace
    used_descriptor = None

    def move_at_replace(source, destination, **kwargs):
        nonlocal used_descriptor
        used_descriptor = kwargs["dst_dir_fd"]
        parent.rename(tmp_path / "original")
        parent.symlink_to(alternate, target_is_directory=True)
        replace(source, destination, **kwargs)

    monkeypatch.setattr(files.os, "replace", move_at_replace)
    with pytest.raises(FileCommitPathChanged):
        files._atomic_write_text(target, "written", expected_version=files._file_state_fingerprint(target))
    assert (alternate / target.name).read_text(encoding="utf-8") == "base"
    assert (tmp_path / "original" / target.name).read_text(encoding="utf-8") == "written"
    assert not list(tmp_path.rglob("*.v8os-tmp"))
    with pytest.raises(OSError):
        os.fstat(used_descriptor)
