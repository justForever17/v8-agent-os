from __future__ import annotations

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
