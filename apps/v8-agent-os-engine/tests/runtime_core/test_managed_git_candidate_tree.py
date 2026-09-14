import os
from pathlib import Path

import pytest

from core.engineering_sandbox.git_service import ManagedGitError
from tests.runtime_core.test_engineering_sandbox import _git_service, _cleanup_test_managed_worktrees


def _worktree(tmp_path):
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("base\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)
    worktree = service.create_worktree(repository, worktree_id="candidate", run_id="candidate-run")
    root = Path(worktree.topology.worktree_root)
    (root / "allowed.txt").write_text("candidate\n", encoding="utf-8")
    return service, worktree, root


def test_late_file_before_staging_cannot_enter_a_candidate_or_advance_head(tmp_path, monkeypatch):
    service, worktree, root = _worktree(tmp_path)
    original = service.run

    def run(args, **kwargs):
        if list(args)[:2] == ["add", "-A"]:
            (root / "outside.txt").write_text("late unauthorized change\n", encoding="utf-8")
        return original(args, **kwargs)

    monkeypatch.setattr(service, "run", run)
    with pytest.raises(ManagedGitError) as caught:
        service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate")
    assert caught.value.code == "worktree_write_set_violation"
    assert original(["rev-parse", "HEAD"], cwd=root).stdout.strip() == worktree.base_commit
    assert (root / "outside.txt").exists()
    assert not list(service.index_root.glob("*.index*"))


def test_candidate_receipt_and_blob_follow_frozen_tree_despite_late_working_edit(tmp_path, monkeypatch):
    service, worktree, root = _worktree(tmp_path)
    original = service.run
    changed = []

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if list(args) == ["write-tree"]:
            changed.append(True)
            (root / "allowed.txt").write_text("new work after snapshot\n", encoding="utf-8")
        return result

    monkeypatch.setattr(service, "run", run)
    receipt = service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate")
    assert changed
    assert original(["show", f"{receipt.commit_id}:allowed.txt"], cwd=root).stdout == "candidate\n"
    assert receipt.changed_paths == ("allowed.txt",)
    assert (root / "allowed.txt").read_text(encoding="utf-8") == "new work after snapshot\n"
    assert "allowed.txt" in original(["status", "--porcelain"], cwd=root).stdout


def test_late_large_blob_is_validated_from_staged_tree(tmp_path, monkeypatch):
    service, worktree, root = _worktree(tmp_path)
    original = service.run
    monkeypatch.setattr("core.engineering_sandbox.git_service.MAX_MANAGED_FILE_BYTES", 16)

    def run(args, **kwargs):
        if list(args)[:2] == ["add", "-A"]:
            (root / "allowed.txt").write_text("x" * 32, encoding="utf-8")
        return original(args, **kwargs)

    monkeypatch.setattr(service, "run", run)
    with pytest.raises(ManagedGitError) as caught:
        service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate")
    assert caught.value.code == "managed_git_large_file_blocked"
    assert original(["rev-parse", "HEAD"], cwd=root).stdout.strip() == worktree.base_commit


def test_candidate_rename_delete_and_crash_replay_have_exact_paths(tmp_path):
    service, worktree, root = _worktree(tmp_path)
    (root / "allowed.txt").rename(root / "renamed.txt")
    receipt = service.finalize_worktree(worktree, write_set=("allowed.txt", "renamed.txt"), commit_message="rename")
    assert set(receipt.changed_paths) == {"allowed.txt", "renamed.txt"}
    assert service.run(["show", f"{receipt.commit_id}:renamed.txt"], cwd=root).stdout == "candidate\n"
    assert not service.run(["status", "--porcelain"], cwd=root).stdout.strip()
    replayed = service.finalize_worktree(worktree, write_set=("allowed.txt", "renamed.txt"), commit_message="replay")
    assert replayed.commit_id == receipt.commit_id
    assert set(replayed.changed_paths) == set(receipt.changed_paths)


def test_finalization_does_not_remove_another_git_operations_index_lock(tmp_path):
    service, worktree, root = _worktree(tmp_path)
    raw_index = service.run(["rev-parse", "--git-path", "index"], cwd=root).stdout.strip()
    index = (root / raw_index).resolve()
    lock = index.with_name(index.name + ".lock")
    lock.write_bytes(b"another git writer")
    try:
        with pytest.raises(ManagedGitError) as caught:
            service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate")
        assert caught.value.code == "worktree_index_busy"
        assert lock.read_bytes() == b"another git writer"
    finally:
        lock.unlink()


def test_finalization_cannot_follow_a_changed_head_to_another_branch(tmp_path, monkeypatch):
    service, worktree, root = _worktree(tmp_path)
    original = service.run
    original(["branch", "other", worktree.base_commit], cwd=root)

    def run(args, **kwargs):
        result = original(args, **kwargs)
        if list(args) == ["write-tree"]:
            original(["symbolic-ref", "HEAD", "refs/heads/other"], cwd=root)
        return result

    monkeypatch.setattr(service, "run", run)
    with pytest.raises(ManagedGitError) as caught:
        service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate")
    assert caught.value.code == "worktree_branch_changed"
    assert original(["rev-parse", "other"], cwd=root).stdout.strip() == worktree.base_commit
    assert original(["rev-parse", worktree.branch_name], cwd=root).stdout.strip() == worktree.base_commit


@pytest.mark.parametrize("detached", [False, True])
def test_branch_drift_between_last_check_and_ref_cas_is_reported_without_index_publication(tmp_path, monkeypatch, detached):
    service, worktree, root = _worktree(tmp_path)
    original = service.run
    original(["branch", "other", worktree.base_commit], cwd=root)
    switched = []

    def run(args, **kwargs):
        if list(args)[:2] == ["update-ref", f"refs/heads/{worktree.branch_name}"] and not switched:
            switched.append(True)
            if detached:
                original(["update-ref", "--no-deref", "HEAD", worktree.base_commit], cwd=root)
            else:
                original(["symbolic-ref", "HEAD", "refs/heads/other"], cwd=root)
        return original(args, **kwargs)

    monkeypatch.setattr(service, "run", run)
    with pytest.raises(ManagedGitError) as caught:
        service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate")
    assert caught.value.code == "worktree_branch_changed" and caught.value.details["referenceRestored"]
    assert original(["rev-parse", "other"], cwd=root).stdout.strip() == worktree.base_commit
    assert original(["rev-parse", worktree.branch_name], cwd=root).stdout.strip() == worktree.base_commit
    assert not original(["diff", "--cached", "--name-only"], cwd=root).stdout.strip()


def test_dotfile_is_a_different_permission_from_the_unprefixed_name(tmp_path):
    service, worktree, root = _worktree(tmp_path)
    (root / ".allowed.txt").write_text("not authorized", encoding="utf-8")
    with pytest.raises(ManagedGitError) as caught:
        service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate")
    assert caught.value.code == "worktree_write_set_violation"
    assert caught.value.details["violations"] == [".allowed.txt"]


@pytest.mark.skipif(os.name != "nt", reason="Windows index read-handle sharing semantics")
def test_index_reader_failure_restores_own_ref_and_later_retry_keeps_candidate(tmp_path):
    service, worktree, root = _worktree(tmp_path)
    raw_index = service.run(["rev-parse", "--git-path", "index"], cwd=root).stdout.strip()
    index = (root / raw_index).resolve()
    previous = index.read_bytes()
    with index.open("rb"):
        with pytest.raises(ManagedGitError) as caught:
            service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate")
    assert caught.value.code == "candidate_index_publication_failed"
    assert caught.value.details["referenceRestored"] is True
    assert service.run(["rev-parse", "HEAD"], cwd=root).stdout.strip() == worktree.base_commit
    assert index.read_bytes() == previous
    assert not service.run(["diff", "--cached", "--name-only"], cwd=root).stdout.strip()
    assert (root / "allowed.txt").read_text(encoding="utf-8") == "candidate\n"
    receipt = service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="candidate retry")
    assert service.run(["show", f"{receipt.commit_id}:allowed.txt"], cwd=root).stdout == "candidate\n"
    assert not service.run(["status", "--porcelain"], cwd=root).stdout.strip()
