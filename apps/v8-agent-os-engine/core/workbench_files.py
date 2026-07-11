from __future__ import annotations

import codecs
import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import quote

from core.workspace_authority import workspace_authority_service


DEFAULT_LINE_COUNT = 500
MAX_LINE_COUNT = 2000
MAX_FRAGMENT_BYTES = 512 * 1024

_LANGUAGE_BY_SUFFIX = {
    ".c": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".css": "css",
    ".go": "go",
    ".h": "c",
    ".hpp": "cpp",
    ".html": "html",
    ".htm": "html",
    ".java": "java",
    ".js": "javascript",
    ".json": "json",
    ".jsx": "javascript",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".md": "markdown",
    ".mdx": "markdown",
    ".mjs": "javascript",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".scss": "scss",
    ".sh": "shell",
    ".sql": "sql",
    ".swift": "swift",
    ".toml": "toml",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".txt": "text",
    ".vue": "vue",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

_TEXT_SUFFIXES = frozenset(_LANGUAGE_BY_SUFFIX) | {
    ".csv",
    ".env",
    ".gitignore",
    ".ini",
    ".log",
    ".properties",
    ".svg",
}


@dataclass(frozen=True)
class ResolvedWorkbenchFile:
    session_id: str
    absolute_path: Path
    workspace_relative_path: str
    workspace_id: str
    project_id: str
    mime_type: str
    language: str
    encoding: str | None
    binary: bool
    size: int
    mtime: str
    etag: str

    def metadata(self) -> dict[str, Any]:
        encoded_path = quote(self.workspace_relative_path, safe="")
        return {
            "sessionId": self.session_id,
            "workspacePath": self.workspace_relative_path,
            "name": self.absolute_path.name,
            "workspaceId": self.workspace_id or None,
            "projectId": self.project_id or None,
            "mimeType": self.mime_type,
            "language": self.language or None,
            "encoding": self.encoding,
            "binary": self.binary,
            "previewable": not self.binary,
            "downloadable": True,
            "size": self.size,
            "mtime": self.mtime,
            "etag": self.etag,
            "downloadUrl": (
                f"/v1/sessions/{quote(self.session_id, safe='')}/workbench/files/read"
                f"?path={encoded_path}&download=true"
            ),
        }


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_requested_path(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("workspace://"):
        raw = raw[len("workspace://") :].lstrip("/\\")
    if not raw or "\x00" in raw:
        raise ValueError("path is required")
    portable = raw.replace("\\", "/")
    if ".." in PurePosixPath(portable).parts:
        raise PermissionError("Parent path segments are not allowed")
    return raw


def _detect_text_encoding(path: Path, mime_type: str) -> tuple[bool, str | None]:
    with path.open("rb") as handle:
        sample = handle.read(64 * 1024)
    if not sample:
        return False, "utf-8"
    if sample.startswith(codecs.BOM_UTF8):
        return False, "utf-8-sig"
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return False, "utf-16"

    suffix = path.suffix.lower()
    likely_text = mime_type.startswith("text/") or suffix in _TEXT_SUFFIXES
    if b"\x00" in sample and not likely_text:
        return True, None

    control_count = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    if not likely_text and control_count / max(1, len(sample)) > 0.02:
        return True, None

    for encoding in ("utf-8", "gb18030"):
        try:
            sample.decode(encoding)
            return False, encoding
        except UnicodeDecodeError:
            continue
    return (False, "utf-8") if likely_text else (True, None)


def _mime_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".md" or suffix == ".mdx":
        return "text/markdown"
    if suffix == ".glb":
        return "model/gltf-binary"
    if suffix == ".gltf":
        return "model/gltf+json"
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"


class WorkbenchFileService:
    def resolve(self, *, session_id: str, requested_path: str) -> ResolvedWorkbenchFile:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("sessionId is required")
        raw = _normalize_requested_path(requested_path)
        authority = workspace_authority_service.resolve(runtime_kind="chat", session_id=normalized_session_id)
        try:
            root = Path(authority.workspace_root).expanduser().resolve(strict=True)
        except OSError as exc:
            raise FileNotFoundError("Active workspace is unavailable") from exc
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise FileNotFoundError("Workspace path was not found") from exc
        if not _is_within(resolved, root):
            raise PermissionError("Path is outside the active session workspace")
        if not resolved.is_file():
            raise FileNotFoundError("Workspace path is not a file")

        stat = resolved.stat()
        mime_type = _mime_type(resolved)
        binary, encoding = _detect_text_encoding(resolved, mime_type)
        workspace_path = resolved.relative_to(root).as_posix()
        etag_seed = f"{stat.st_size}:{stat.st_mtime_ns}:{getattr(stat, 'st_ino', 0)}"
        etag = f'"{hashlib.sha256(etag_seed.encode("utf-8")).hexdigest()}"'
        return ResolvedWorkbenchFile(
            session_id=normalized_session_id,
            absolute_path=resolved,
            workspace_relative_path=workspace_path,
            workspace_id=str(authority.workspace_id or ""),
            project_id=str(authority.project_id or ""),
            mime_type=mime_type,
            language=_LANGUAGE_BY_SUFFIX.get(resolved.suffix.lower(), "text" if mime_type.startswith("text/") else ""),
            encoding=encoding,
            binary=binary,
            size=stat.st_size,
            mtime=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            etag=etag,
        )

    @staticmethod
    def _iter_lines(path: Path, encoding: str) -> Iterator[str]:
        with path.open("r", encoding=encoding, errors="replace", newline=None) as handle:
            yield from handle

    def read(
        self,
        *,
        session_id: str,
        requested_path: str,
        start_line: int = 1,
        line_count: int = DEFAULT_LINE_COUNT,
    ) -> dict[str, Any]:
        resolved = self.resolve(session_id=session_id, requested_path=requested_path)
        metadata = resolved.metadata()
        if resolved.binary:
            return {**metadata, "content": None, "lines": [], "totalLines": None}

        normalized_start = max(1, int(start_line or 1))
        normalized_count = min(MAX_LINE_COUNT, max(1, int(line_count or DEFAULT_LINE_COUNT)))
        selected: list[dict[str, Any]] = []
        selected_bytes = 0
        total_lines = 0
        truncated_by_bytes = False
        encoding = resolved.encoding or "utf-8"
        for line_number, text in enumerate(self._iter_lines(resolved.absolute_path, encoding), start=1):
            total_lines = line_number
            if line_number < normalized_start or len(selected) >= normalized_count or truncated_by_bytes:
                continue
            clean_text = text.rstrip("\r\n")
            encoded_size = len(clean_text.encode("utf-8", errors="replace"))
            if selected_bytes + encoded_size > MAX_FRAGMENT_BYTES:
                truncated_by_bytes = True
                continue
            selected.append({"number": line_number, "text": clean_text})
            selected_bytes += encoded_size

        end_line = selected[-1]["number"] if selected else min(total_lines, normalized_start - 1)
        return {
            **metadata,
            "startLine": normalized_start,
            "endLine": end_line,
            "lineCount": len(selected),
            "requestedLineCount": normalized_count,
            "totalLines": total_lines,
            "hasMore": end_line < total_lines,
            "truncatedByBytes": truncated_by_bytes,
            "content": "\n".join(item["text"] for item in selected),
            "lines": selected,
        }


workbench_file_service = WorkbenchFileService()
