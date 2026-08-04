from __future__ import annotations

import os
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from core import workbench_files


@dataclass
class _Authority:
    workspace_root: str
    workspace_id: str = "workspace-1"
    project_id: str = "project-1"


@pytest.fixture()
def scoped_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        workbench_files.workspace_authority_service,
        "resolve",
        lambda **_: _Authority(str(workspace)),
    )
    return workbench_files.WorkbenchFileService(), workspace


def test_resolve_and_read_returns_workspace_relative_metadata(scoped_service):
    service, workspace = scoped_service
    source = workspace / "src" / "hello.ts"
    source.parent.mkdir()
    source.write_text("const title = '你好';\nexport default title;\n", encoding="utf-8")

    resolved = service.resolve(session_id="session-1", requested_path=str(source))
    payload = service.read(session_id="session-1", requested_path="src/hello.ts", line_count=1)

    assert resolved.workspace_relative_path == "src/hello.ts"
    assert payload["workspacePath"] == "src/hello.ts"
    assert payload["language"] == "typescript"
    assert payload["encoding"] == "utf-8"
    assert payload["totalLines"] == 2
    assert payload["lines"] == [{"number": 1, "text": "const title = '你好';"}]
    assert payload["hasMore"] is True
    assert str(workspace) not in repr(payload)


def test_parent_segments_and_workspace_escape_are_rejected(scoped_service, tmp_path: Path):
    service, workspace = scoped_service
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(PermissionError, match="Parent path"):
        service.resolve(session_id="session-1", requested_path="../outside.txt")
    with pytest.raises(PermissionError, match="outside"):
        service.resolve(session_id="session-1", requested_path=str(outside))


def test_missing_file_error_does_not_echo_host_path(scoped_service):
    service, workspace = scoped_service
    missing = workspace / "private" / "missing.txt"

    with pytest.raises(FileNotFoundError) as exc:
        service.resolve(session_id="session-1", requested_path=str(missing))

    assert str(workspace) not in str(exc.value)


def test_symlink_or_junction_escape_is_rejected(scoped_service, tmp_path: Path):
    service, workspace = scoped_service
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    with pytest.raises(PermissionError, match="outside"):
        service.resolve(session_id="session-1", requested_path="linked.txt")


def test_binary_file_returns_metadata_without_content(scoped_service):
    service, workspace = scoped_service
    source = workspace / "asset.bin"
    source.write_bytes(b"\x00\x01\x02\x03")

    payload = service.read(session_id="session-1", requested_path="asset.bin")

    assert payload["binary"] is True
    assert payload["content"] is None
    assert payload["lines"] == []
    assert payload["downloadable"] is True


def test_line_and_byte_limits_are_enforced(scoped_service):
    service, workspace = scoped_service
    source = workspace / "large.txt"
    source.write_text("\n".join(f"line {index}" for index in range(2505)), encoding="utf-8")

    payload = service.read(
        session_id="session-1",
        requested_path="large.txt",
        start_line=2,
        line_count=5000,
    )

    assert payload["requestedLineCount"] == 2000
    assert payload["lineCount"] == 2000
    assert payload["startLine"] == 2
    assert payload["endLine"] == 2001
    assert payload["totalLines"] == 2505


def test_catalog_is_session_scoped_searchable_and_paginated(scoped_service):
    service, workspace = scoped_service
    (workspace / "src").mkdir()
    (workspace / "src" / "alpha.ts").write_text("export const alpha = true;", encoding="utf-8")
    (workspace / "src" / "beta.ts").write_text("export const beta = true;", encoding="utf-8")
    (workspace / "README.md").write_text("# Project", encoding="utf-8")

    first = service.list_files(session_id="session-1", limit=2)
    filtered = service.list_files(session_id="session-1", query="beta")
    second = service.list_files(session_id="session-1", cursor=int(first["nextCursor"]), limit=2)

    assert first["sessionId"] == "session-1"
    assert len(first["items"]) == 2
    assert first["hasMore"] is True
    assert filtered["items"][0]["workspacePath"] == "src/beta.ts"
    assert len(second["items"]) == 1
    assert second["hasMore"] is False
    assert str(workspace) not in repr(first)


def test_catalog_query_benchmark_materializes_only_matching_pages_and_reuses_scan(
    scoped_service,
    monkeypatch: pytest.MonkeyPatch,
):
    _, workspace = scoped_service
    service = workbench_files.WorkbenchFileService(catalog_cache_ttl_seconds=60)
    bulk = workspace / "bulk"
    bulk.mkdir()
    for index in range(120):
        (bulk / f"file-{index:03d}.txt").write_text(str(index), encoding="utf-8")
    needle = workspace / "docs" / "README.md"
    needle.parent.mkdir()
    needle.write_text("# Needle", encoding="utf-8")

    scan_count = 0
    materialized_paths: list[str] = []
    original_scan = service._scan_catalog_paths
    original_catalog_item = service._catalog_item

    def counted_scan(root: Path):
        nonlocal scan_count
        scan_count += 1
        return original_scan(root)

    def counted_catalog_item(*, root: Path, workspace_path: str, session_id: str):
        materialized_paths.append(workspace_path)
        return original_catalog_item(root=root, workspace_path=workspace_path, session_id=session_id)

    monkeypatch.setattr(service, "_scan_catalog_paths", counted_scan)
    monkeypatch.setattr(service, "_catalog_item", counted_catalog_item)

    filtered = service.list_files(session_id="session-1", query="README.md", limit=10)

    assert filtered["scanned"] == 121
    assert [item["workspacePath"] for item in filtered["items"]] == ["docs/README.md"]
    assert materialized_paths == ["docs/README.md"]

    materialized_paths.clear()
    first = service.list_files(session_id="session-1", limit=10)
    second = service.list_files(session_id="session-1", cursor=int(first["nextCursor"]), limit=10)

    assert scan_count == 1
    assert len(first["items"]) == 10
    assert len(second["items"]) == 10
    assert len(materialized_paths) == 20


def test_catalog_cache_expires_while_file_reads_remain_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "note.txt"
    source.write_text("version one", encoding="utf-8")
    monkeypatch.setattr(
        workbench_files.workspace_authority_service,
        "resolve",
        lambda **_: _Authority(str(workspace)),
    )
    now = [100.0]
    service = workbench_files.WorkbenchFileService(
        catalog_cache_ttl_seconds=3,
        clock=lambda: now[0],
    )
    scan_count = 0
    original_scan = service._scan_catalog_paths

    def counted_scan(root: Path):
        nonlocal scan_count
        scan_count += 1
        return original_scan(root)

    monkeypatch.setattr(service, "_scan_catalog_paths", counted_scan)

    initial = service.list_files(session_id="session-1")
    source.write_text("version two is longer", encoding="utf-8")
    added = workspace / "added.txt"
    added.write_text("new", encoding="utf-8")
    cached = service.list_files(session_id="session-1")
    live_read = service.read(session_id="session-1", requested_path="note.txt")

    assert [item["workspacePath"] for item in initial["items"]] == ["note.txt"]
    assert [item["workspacePath"] for item in cached["items"]] == ["note.txt"]
    assert cached["items"][0]["size"] == len("version two is longer")
    assert live_read["content"] == "version two is longer"
    assert scan_count == 1

    now[0] += 3.01
    refreshed = service.list_files(session_id="session-1")

    assert {item["workspacePath"] for item in refreshed["items"]} == {"added.txt", "note.txt"}
    assert scan_count == 2


def test_catalog_cache_is_workspace_scoped_session_projected_and_lru_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    roots = {name: tmp_path / name for name in ("workspace-a", "workspace-b", "workspace-c")}
    for name, root in roots.items():
        root.mkdir()
        (root / f"{name}.txt").write_text(name, encoding="utf-8")
    authorities = {
        "session-a1": _Authority(str(roots["workspace-a"]), "workspace-a1", "project-a1"),
        "session-a2": _Authority(str(roots["workspace-a"]), "workspace-a2", "project-a2"),
        "session-b": _Authority(str(roots["workspace-b"]), "workspace-b", "project-b"),
        "session-c": _Authority(str(roots["workspace-c"]), "workspace-c", "project-c"),
    }
    monkeypatch.setattr(
        workbench_files.workspace_authority_service,
        "resolve",
        lambda *, session_id, **_: authorities[session_id],
    )
    service = workbench_files.WorkbenchFileService(
        catalog_cache_ttl_seconds=60,
        catalog_cache_limit=2,
    )
    scan_counts = {name: 0 for name in roots}
    original_scan = service._scan_catalog_paths

    def counted_scan(root: Path):
        scan_counts[root.name] += 1
        return original_scan(root)

    monkeypatch.setattr(service, "_scan_catalog_paths", counted_scan)

    first_a = service.list_files(session_id="session-a1")
    second_a = service.list_files(session_id="session-a2")
    first_b = service.list_files(session_id="session-b")
    service.list_files(session_id="session-a1")
    service.list_files(session_id="session-c")
    second_b = service.list_files(session_id="session-b")

    assert first_a["items"][0]["sessionId"] == "session-a1"
    assert second_a["items"][0]["sessionId"] == "session-a2"
    assert second_a["workspaceId"] == "workspace-a2"
    assert second_a["projectId"] == "project-a2"
    assert first_b["items"][0]["workspacePath"] == "workspace-b.txt"
    assert second_b["items"][0]["workspacePath"] == "workspace-b.txt"
    assert scan_counts == {"workspace-a": 1, "workspace-b": 2, "workspace-c": 1}


def test_catalog_snapshot_build_is_singleflight_across_queries_and_pages(
    scoped_service,
    monkeypatch: pytest.MonkeyPatch,
):
    _, workspace = scoped_service
    for name in ("alpha.txt", "beta.txt", "gamma.txt"):
        (workspace / name).write_text(name, encoding="utf-8")
    service = workbench_files.WorkbenchFileService(catalog_cache_ttl_seconds=60)
    original_scan = service._scan_catalog_paths
    scan_entered = threading.Event()
    release_scan = threading.Event()
    scan_lock = threading.Lock()
    scan_count = 0

    def blocked_scan(root: Path):
        nonlocal scan_count
        with scan_lock:
            scan_count += 1
        scan_entered.set()
        assert release_scan.wait(timeout=5)
        return original_scan(root)

    monkeypatch.setattr(service, "_scan_catalog_paths", blocked_scan)
    requests = [
        ("", 0, 1),
        ("a", 0, 2),
        ("beta", 0, 1),
        ("", 1, 1),
        ("gamma", 0, 1),
        ("txt", 2, 1),
    ]
    barrier = threading.Barrier(len(requests))

    def invoke(options: tuple[str, int, int]):
        barrier.wait(timeout=5)
        query, cursor, limit = options
        return service.list_files(
            session_id="session-1",
            query=query,
            cursor=cursor,
            limit=limit,
        )

    with ThreadPoolExecutor(max_workers=len(requests)) as executor:
        futures = [executor.submit(invoke, options) for options in requests]
        assert scan_entered.wait(timeout=5)
        release_scan.set()
        results = [future.result(timeout=10) for future in futures]

    assert scan_count == 1
    assert all(result["sessionId"] == "session-1" for result in results)


def test_catalog_snapshot_singleflight_failure_releases_waiters_and_retries(
    scoped_service,
    monkeypatch: pytest.MonkeyPatch,
):
    _, workspace = scoped_service
    (workspace / "retry.txt").write_text("retry", encoding="utf-8")
    service = workbench_files.WorkbenchFileService(catalog_cache_ttl_seconds=60)
    original_scan = service._scan_catalog_paths
    first_scan_entered = threading.Event()
    release_first_scan = threading.Event()
    scan_lock = threading.Lock()
    scan_count = 0

    def fail_once(root: Path):
        nonlocal scan_count
        with scan_lock:
            scan_count += 1
            current_scan = scan_count
        if current_scan == 1:
            first_scan_entered.set()
            assert release_first_scan.wait(timeout=5)
            raise OSError("catalog scan failed")
        return original_scan(root)

    monkeypatch.setattr(service, "_scan_catalog_paths", fail_once)
    barrier = threading.Barrier(4)

    def invoke():
        barrier.wait(timeout=5)
        return service.list_files(session_id="session-1")

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(invoke) for _ in range(4)]
        assert first_scan_entered.wait(timeout=5)
        threading.Event().wait(0.05)
        release_first_scan.set()
        for future in futures:
            with pytest.raises(OSError, match="catalog scan failed"):
                future.result(timeout=10)

    assert service._catalog_flights == {}
    recovered = service.list_files(session_id="session-1")

    assert [item["workspacePath"] for item in recovered["items"]] == ["retry.txt"]
    assert scan_count == 2


def test_catalog_omits_dependencies_secrets_and_unsupported_files(scoped_service):
    service, workspace = scoped_service
    (workspace / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (workspace / "credentials.json").write_text("{}", encoding="utf-8")
    (workspace / "cache.db").write_bytes(b"sqlite")
    (workspace / "archive.zip").write_bytes(b"zip")
    dependencies = workspace / "node_modules" / "pkg"
    dependencies.mkdir(parents=True)
    (dependencies / "index.js").write_text("module.exports = {};", encoding="utf-8")
    generated = workspace / ".next-codex-validation" / "server"
    generated.mkdir(parents=True)
    (generated / "bundle.js").write_text("generated", encoding="utf-8")
    packaged_python = workspace / ".python" / "Lib"
    packaged_python.mkdir(parents=True)
    (packaged_python / "vendor.py").write_text("generated", encoding="utf-8")
    (workspace / "preview.png").write_bytes(b"not-a-real-png")

    payload = service.list_files(session_id="session-1")
    paths = {item["workspacePath"] for item in payload["items"]}

    assert paths == {"preview.png"}


def test_catalog_does_not_follow_symlink_escape(scoped_service, tmp_path: Path):
    service, workspace = scoped_service
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = workspace / "linked.md"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    outside_directory = tmp_path / "outside-directory"
    outside_directory.mkdir()
    (outside_directory / "nested.md").write_text("secret", encoding="utf-8")
    directory_link = workspace / "linked-directory"
    try:
        directory_link.symlink_to(outside_directory, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink unavailable: {exc}")

    payload = service.list_files(session_id="session-1", query="linked")

    assert payload["items"] == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junction semantics")
def test_catalog_does_not_follow_windows_junction(scoped_service, tmp_path: Path):
    service, workspace = scoped_service
    (workspace / "inside.md").write_text("inside", encoding="utf-8")
    outside = tmp_path / "junction-target"
    outside.mkdir()
    (outside / "outside.md").write_text("outside", encoding="utf-8")
    junction = workspace / "linked-junction"
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode != 0:
        pytest.skip(f"junction unavailable: {completed.stderr or completed.stdout}")
    try:
        assert workbench_files._is_catalog_link(junction) is True
        payload = service.list_files(session_id="session-1")
        paths = {item["workspacePath"] for item in payload["items"]}

        assert paths == {"inside.md"}
        assert payload["scanned"] == 1
    finally:
        junction.rmdir()


def test_binary_preview_resolve_reuses_session_authority(scoped_service):
    service, workspace = scoped_service
    media = workspace / "preview.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")

    resolved = service.resolve(session_id="session-1", requested_path="preview.png")

    assert resolved.binary is True
    assert resolved.mime_type == "image/png"
    assert resolved.metadata()["previewable"] is True
    assert resolved.workspace_relative_path == "preview.png"
    assert str(workspace) not in repr(resolved.metadata())
