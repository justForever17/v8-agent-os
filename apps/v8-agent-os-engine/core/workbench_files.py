from __future__ import annotations

import codecs
import hashlib
import mimetypes
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator
from urllib.parse import quote

from core.workspace_authority import workspace_authority_service


DEFAULT_LINE_COUNT = 500
MAX_LINE_COUNT = 2000
MAX_FRAGMENT_BYTES = 512 * 1024
DEFAULT_CATALOG_LIMIT = 80
MAX_CATALOG_LIMIT = 200
MAX_CATALOG_SCAN = 10_000
MAX_CATALOG_DEPTH = 12

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

_PREVIEWABLE_BINARY_SUFFIXES = {
    ".aac",
    ".avif",
    ".bmp",
    ".flac",
    ".gif",
    ".glb",
    ".ico",
    ".jpeg",
    ".jpg",
    ".m4a",
    ".m4v",
    ".mp3",
    ".mp4",
    ".ogg",
    ".pdf",
    ".png",
    ".svg",
    ".wav",
    ".webm",
    ".webp",
}

_CATALOG_SUFFIXES = _TEXT_SUFFIXES | _PREVIEWABLE_BINARY_SUFFIXES | {".gltf"}

_CATALOG_IGNORED_DIRECTORIES = {
    ".cache",
    ".expo",
    ".git",
    ".gradle",
    ".idea",
    ".next",
    ".nuxt",
    ".parcel-cache",
    ".pytest_cache",
    ".python",
    ".turbo",
    ".venv",
    ".yarn",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "venv",
}

_CATALOG_SENSITIVE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
    "service-account.json",
}

_CATALOG_SENSITIVE_SUFFIXES = {".key", ".p12", ".pem", ".pfx", ".sqlite", ".sqlite3", ".db", ".log"}


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
            "previewable": (not self.binary) or self.absolute_path.suffix.lower() in _PREVIEWABLE_BINARY_SUFFIXES,
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


def _is_catalog_sensitive(path: Path) -> bool:
    name = path.name.lower()
    if name in _CATALOG_SENSITIVE_NAMES or name.startswith(".env."):
        return True
    return path.suffix.lower() in _CATALOG_SENSITIVE_SUFFIXES


def _is_catalog_ignored_directory(name: str) -> bool:
    normalized = name.lower()
    return (
        normalized in _CATALOG_IGNORED_DIRECTORIES
        or normalized.startswith((".next-", ".venv-", "node_modules-"))
    )


def _catalog_kind(path: Path, mime_type: str, language: str) -> str:
    if language == "markdown":
        return "markdown"
    if language == "html":
        return "html"
    if language and language != "text":
        return "code"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type == "application/pdf":
        return "pdf"
    if "gltf" in mime_type:
        return "model_3d"
    return "text"


class WorkbenchFileService:
    @staticmethod
    def _workspace_root(*, session_id: str) -> tuple[Path, Any]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("sessionId is required")
        authority = workspace_authority_service.resolve(runtime_kind="chat", session_id=normalized_session_id)
        try:
            root = Path(authority.workspace_root).expanduser().resolve(strict=True)
        except OSError as exc:
            raise FileNotFoundError("Active workspace is unavailable") from exc
        return root, authority

    def resolve(self, *, session_id: str, requested_path: str) -> ResolvedWorkbenchFile:
        normalized_session_id = str(session_id or "").strip()
        raw = _normalize_requested_path(requested_path)
        root, authority = self._workspace_root(session_id=normalized_session_id)
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

    def list_files(
        self,
        *,
        session_id: str,
        query: str = "",
        cursor: int = 0,
        limit: int = DEFAULT_CATALOG_LIMIT,
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        root, authority = self._workspace_root(session_id=normalized_session_id)
        normalized_query = str(query or "").strip().casefold()
        normalized_cursor = max(0, int(cursor or 0))
        normalized_limit = min(MAX_CATALOG_LIMIT, max(1, int(limit or DEFAULT_CATALOG_LIMIT)))

        candidates: list[dict[str, Any]] = []
        scanned = 0
        for current_root, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current_root)
            try:
                relative_root = current_path.relative_to(root)
            except ValueError:
                directory_names[:] = []
                continue
            if len(relative_root.parts) >= MAX_CATALOG_DEPTH:
                directory_names[:] = []
            else:
                directory_names[:] = sorted(
                    name
                    for name in directory_names
                    if not _is_catalog_ignored_directory(name)
                    and not (current_path / name).is_symlink()
                )

            for file_name in sorted(file_names):
                if scanned >= MAX_CATALOG_SCAN:
                    break
                scanned += 1
                candidate = current_path / file_name
                suffix = candidate.suffix.lower()
                if suffix not in _CATALOG_SUFFIXES or _is_catalog_sensitive(candidate) or candidate.is_symlink():
                    continue
                try:
                    resolved = candidate.resolve(strict=True)
                    if not _is_within(resolved, root) or not resolved.is_file():
                        continue
                    workspace_path = resolved.relative_to(root).as_posix()
                    if normalized_query and normalized_query not in workspace_path.casefold():
                        continue
                    stat = resolved.stat()
                except OSError:
                    continue
                mime_type = _mime_type(resolved)
                language = _LANGUAGE_BY_SUFFIX.get(suffix, "text" if mime_type.startswith("text/") else "")
                candidates.append(
                    {
                        "sessionId": normalized_session_id,
                        "workspacePath": workspace_path,
                        "name": resolved.name,
                        "mimeType": mime_type,
                        "language": language or None,
                        "kind": _catalog_kind(resolved, mime_type, language),
                        "previewable": True,
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    }
                )
            if scanned >= MAX_CATALOG_SCAN:
                break

        candidates.sort(key=lambda item: (str(item["workspacePath"]).casefold(), str(item["workspacePath"])))
        page = candidates[normalized_cursor : normalized_cursor + normalized_limit]
        next_cursor = normalized_cursor + len(page)
        has_more = next_cursor < len(candidates)
        return {
            "sessionId": normalized_session_id,
            "workspaceId": str(authority.workspace_id or "") or None,
            "projectId": str(authority.project_id or "") or None,
            "items": page,
            "nextCursor": str(next_cursor) if has_more else None,
            "hasMore": has_more,
            "scanned": scanned,
            "truncated": scanned >= MAX_CATALOG_SCAN,
        }

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
