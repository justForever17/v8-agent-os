from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from types import SimpleNamespace

import pytest

from core import workspace_state_digest as digest

from core.workspace_state_digest import (
    build_workspace_state_digest_context,
    command_may_change_workspace,
    mark_workspace_state_stale,
)


@pytest.fixture(autouse=True)
def isolated_digest_and_git(monkeypatch):
    # These are cache/contract tests; OS Git process startup is not their subject.
    monkeypatch.setattr(digest, "_DIGEST_CACHE", {})
    monkeypatch.setattr(digest, "_run_git", lambda *_args, **_kwargs: (128, "fatal: not a git repository"))
    yield
    _settle_git_refresh()


def _settle_git_refresh():
    with digest._DIGEST_LOCK:
        worker = digest._GIT_REFRESH_THREAD
    if worker is not None:
        worker.join(timeout=3)
        assert not worker.is_alive(), "test Git probe was not released"


def _state(root, run="digest-run"):
    return {"session_id": "digest-session", "run_id": run, "workspace_path": str(root)}


def test_plain_workspace_never_launches_git(tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    monkeypatch.setattr(digest, "_run_git", lambda *_a, **_kw: pytest.fail("Plain directory launched Git"))
    assert digest._collect_git_summary(tmp_path) == {"repoDetected": False}


@pytest.mark.parametrize("layout", ["directory", "worktree_file", "bare", "environment"])
def test_git_discovery_preserves_supported_layouts(tmp_path, monkeypatch, layout):
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    if layout == "directory":
        (tmp_path / ".git").mkdir()
    elif layout == "worktree_file":
        (tmp_path / ".git").write_text("gitdir: ../repository/.git/worktrees/a", encoding="utf-8")
    elif layout == "bare":
        (tmp_path / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
        (tmp_path / "objects").mkdir()
    else:
        monkeypatch.setenv("GIT_DIR", str(tmp_path / "separate-git"))
    nested = tmp_path / "src"
    nested.mkdir()
    calls = []

    def probe(root, *args):
        calls.append((root, args))
        return 0, str(tmp_path) if args[0] == "rev-parse" else ""

    monkeypatch.setattr(digest, "_run_git", probe)
    assert digest._collect_git_summary(nested)["repoDetected"] is True
    assert calls[0] == (nested, ("rev-parse", "--show-toplevel"))


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
    assert diagnostics and diagnostics[0]["repoDetected"] is None
    assert diagnostics[0]["gitProbeStatus"] == "pending"
    assert diagnostics[0]["physicalPathPresent"] is True
    _settle_git_refresh()
    _, refreshed = build_workspace_state_digest_context(state=state, session_id="session-digest")
    assert refreshed[0]["repoDetected"] is False


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


def test_cold_marks_do_not_probe_and_next_read_coalesces_mutations(tmp_path, monkeypatch):
    calls = []
    original_markers = digest._collect_project_markers
    monkeypatch.setattr(digest, "_collect_project_markers", lambda root: calls.append(root) or original_markers(root))
    state = _state(tmp_path)
    for index in range(3):
        (tmp_path / f"file-{index}.txt").write_text(str(index))
        assert mark_workspace_state_stale(state, reason="file_write", subject=f"file-{index}.txt") is None
    assert calls == []
    (tmp_path / "package.json").write_text("{}")
    text, rows = build_workspace_state_digest_context(state=state)
    assert len(calls) == 1 and "package.json" in text
    assert "file-2.txt" in text and "stale=true" in text
    assert rows[0]["mutationVersion"] == rows[0]["snapshotVersion"] == 3
    assert rows[0]["refreshPending"] is False
    _settle_git_refresh()
    cached = build_workspace_state_digest_context(state=state)
    assert build_workspace_state_digest_context(state=state) == cached
    assert len(calls) == 1, "a hot unchanged read must not scan native markers again"


def test_slow_snapshot_does_not_block_or_erase_a_concurrent_write_marker(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    calls = []
    def slow_markers(root):
        calls.append(root)
        entered.set()
        assert release.wait(5), "isolated slow probe was not released"
        return []
    monkeypatch.setattr(digest, "_collect_project_markers", slow_markers)
    state = _state(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reading = pool.submit(build_workspace_state_digest_context, state=state)
        try:
            assert entered.wait(3)
            marking = pool.submit(mark_workspace_state_stale, state, reason="file_write", subject="late.txt")
            assert marking.result(timeout=2) is None
        finally:
            release.set()
        _, rows = reading.result(timeout=3)
    assert rows[0]["refreshPending"] is True
    assert rows[0]["snapshotVersion"] == 0 and rows[0]["mutationVersion"] == 1
    text, refreshed = build_workspace_state_digest_context(state=state)
    assert len(calls) == 2 and "late.txt" in text
    assert refreshed[0]["refreshPending"] is False
    assert refreshed[0]["mutationVersion"] == refreshed[0]["snapshotVersion"] == 1


def test_one_workspaces_slow_probe_does_not_serialize_other_workspaces(tmp_path, monkeypatch):
    blocked_root, other_root = tmp_path / "blocked", tmp_path / "other"
    blocked_root.mkdir()
    other_root.mkdir()
    entered, release = Event(), Event()
    def probe(root):
        if root == blocked_root:
            entered.set()
            assert release.wait(5)
        return []
    monkeypatch.setattr(digest, "_collect_project_markers", probe)
    with ThreadPoolExecutor(max_workers=2) as pool:
        blocked = pool.submit(build_workspace_state_digest_context, state=_state(blocked_root))
        try:
            assert entered.wait(3)
            independent = pool.submit(build_workspace_state_digest_context, state=_state(other_root))
            text, _ = independent.result(timeout=2)
            assert str(other_root) in text
        finally:
            release.set()
        blocked.result(timeout=3)


def test_deferred_marks_keep_cache_and_reason_history_bounded(tmp_path):
    for index in range(130):
        mark_workspace_state_stale(_state(tmp_path, str(index)), reason="file_write", subject="item.txt")
    assert len(digest._DIGEST_CACHE) == 128
    state = _state(tmp_path, "latest")
    for index in range(12):
        mark_workspace_state_stale(state, reason="file_write", subject=str(index))
    key, _ = digest._cache_key(state)
    assert len(digest._DIGEST_CACHE) == 128
    assert [item["subject"] for item in digest._DIGEST_CACHE[key].stale_reasons] == [str(index) for index in range(4, 12)]
    assert digest._DIGEST_CACHE[key].mutation_version == 12


def test_cold_snapshot_cannot_publish_fresh_after_dirty_entry_is_evicted(tmp_path, monkeypatch):
    state = _state(tmp_path, "cold-aba")
    key, _ = digest._cache_key(state)
    calls = []
    original_markers = digest._collect_project_markers
    def probe(root):
        calls.append(root)
        if len(calls) == 1:
            # Force the exact cold-read ABA ordering while collection is outside
            # the lock: a real file change, its marker, then bounded eviction.
            (tmp_path / "package.json").write_text("{}")
            mark_workspace_state_stale(state, reason="file_write", subject="package.json")
            for index in range(128):
                mark_workspace_state_stale(_state(tmp_path, f"evict-{index}"), reason="file_write")
            assert key not in digest._DIGEST_CACHE
            assert len(digest._DIGEST_CACHE) == 128
        return original_markers(root)
    monkeypatch.setattr(digest, "_collect_project_markers", probe)
    text, rows = build_workspace_state_digest_context(state=state)
    assert "stale=true" in text and rows[0]["refreshPending"] is True
    assert rows[0]["snapshotVersion"] == 0 and rows[0]["mutationVersion"] == 1
    assert key not in digest._DIGEST_CACHE, "an obsolete cold probe must not replace the evicted dirty entry"
    text, rows = build_workspace_state_digest_context(state=state)
    assert len(calls) == 2 and "package.json" in text
    assert rows[0]["refreshPending"] is False
    assert len(digest._DIGEST_CACHE) == 128


def test_slow_git_never_blocks_native_prompt_and_has_one_global_sampler(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    calls = []
    (tmp_path / "package.json").write_text("{}")

    def probe(root):
        calls.append(root)
        entered.set()
        assert release.wait(3)
        return {"repoDetected": True, "branch": "main", "changedCount": 1, "topChangedFiles": [" M package.json"]}

    monkeypatch.setattr(digest, "_collect_git_summary", probe)
    state = _state(tmp_path)
    try:
        started = time.perf_counter()
        text, rows = build_workspace_state_digest_context(state=state)
        assert time.perf_counter() - started < 0.5
        assert "package.json" in text and "repoDetected=unknown; status=pending" in text
        assert rows[0]["repoDetected"] is None and rows[0]["changedCount"] is None
        assert entered.wait(1)
        with ThreadPoolExecutor(max_workers=8) as pool:
            snapshots = list(pool.map(lambda _: build_workspace_state_digest_context(state=state), range(24)))
        assert all(item[1][0]["gitProbeStatus"] == "pending" for item in snapshots)
        other = tmp_path / "other"
        other.mkdir()
        other_text, _ = build_workspace_state_digest_context(state=_state(other, "other-run"))
        assert "status=pending" in other_text
        assert calls == [tmp_path]
    finally:
        release.set()
        _settle_git_refresh()
    text, rows = build_workspace_state_digest_context(state=state)
    assert "repoDetected=true branch=main changed=1" in text
    assert rows[0]["gitProbeStatus"] == "ready" and rows[0]["gitSnapshotAt"]
    assert calls == [tmp_path]


@pytest.mark.parametrize("invalidate", ["write", "eviction"])
def test_late_git_result_cannot_overwrite_newer_or_evicted_snapshot(tmp_path, monkeypatch, invalidate):
    entered, release = Event(), Event()

    def probe(_root):
        entered.set()
        assert release.wait(3)
        return {"repoDetected": True, "branch": "old", "changedCount": 99}

    monkeypatch.setattr(digest, "_collect_git_summary", probe)
    state = _state(tmp_path)
    key, _ = digest._cache_key(state)
    try:
        build_workspace_state_digest_context(state=state)
        assert entered.wait(1)
        if invalidate == "write":
            (tmp_path / "package.json").write_text("{}")
            mark_workspace_state_stale(state, reason="file_write", subject="package.json")
            text, rows = build_workspace_state_digest_context(state=state)
            assert "package.json" in text
            assert rows[0]["mutationVersion"] == rows[0]["snapshotVersion"] == 1
        else:
            for index in range(128):
                mark_workspace_state_stale(_state(tmp_path, f"evict-{index}"))
            assert key not in digest._DIGEST_CACHE
    finally:
        release.set()
        _settle_git_refresh()
    if invalidate == "write":
        assert digest._DIGEST_CACHE[key].diagnostics["gitProbeStatus"] == "pending"
        assert "branch=old" not in digest._DIGEST_CACHE[key].text
    else:
        assert key not in digest._DIGEST_CACHE
        assert len(digest._DIGEST_CACHE) == 128
    monkeypatch.setattr(digest, "_collect_git_summary", lambda _root: {"repoDetected": False})
    build_workspace_state_digest_context(state=state)
    _settle_git_refresh()
    assert digest._DIGEST_CACHE[key].diagnostics["repoDetected"] is False
    assert len(digest._DIGEST_CACHE) <= 128


def test_git_discovery_exception_is_unverified_with_bounded_retry(tmp_path, monkeypatch):
    clock, calls = [100.0], []

    def probe(_root):
        calls.append(True)
        raise PermissionError("metadata path unavailable")

    monkeypatch.setattr(digest, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(digest, "_collect_git_summary", probe)
    state = _state(tmp_path)
    build_workspace_state_digest_context(state=state)
    _settle_git_refresh()
    for _ in range(10):
        text, rows = build_workspace_state_digest_context(state=state)
        assert "repoDetected=unknown; status=unverified" in text
        assert rows[0]["repoDetected"] is None and rows[0]["changedCount"] is None
    assert len(calls) == 1
    clock[0] += 6
    build_workspace_state_digest_context(state=state)
    _settle_git_refresh()
    assert len(calls) == 2


def test_failed_git_status_is_not_a_clean_worktree(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()

    def probe(_root, *args):
        return (999, "probe timed out") if args[0] == "status" else (0, str(tmp_path))

    monkeypatch.setattr(digest, "_run_git", probe)
    summary = digest._collect_git_summary(tmp_path)
    assert summary["repoDetected"] is True and summary["gitProbeStatus"] == "unverified"
    assert "changedCount" not in summary
    assert "changed=0" not in digest._git_summary_line(summary)
