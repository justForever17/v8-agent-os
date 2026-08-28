from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Optional

from langchain_core.tools import InjectedToolCallId, tool

from core.artifact_store import artifact_store
from core.tools.native.tool_governance import (
    _enforce_safety_decision,
    _raise_runtime_governance_exception_if_needed,
)
from core.tools.native.workspace_governance import (
    _apply_scoped_text_patch,
    _line_count_for_guard,
)
from core.workspace_capability import (
    ensure_workspace_side_effect_allowed,
    is_global_skill_path,
    resolve_workspace_tool_path,
)
from core.workspace_state_digest import mark_workspace_state_stale
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian

__all__ = [
    "TEXT_EXTENSIONS",
    "DOCUMENT_EXTENSIONS",
    "is_readable_native_file",
    "is_binary",
    "read_native_file",
    "share_workspace_file",
    "write_native_file",
    "grep_search",
]


def _global_skill_write_block_payload(target_path: Path | str) -> dict[str, Any]:
    return {
        "ok": False,
        "kind": "global_skill_read_execute_only",
        "summary": "全局 Skill 目录可读、可执行，但 Agent 不可修改、覆盖、移动或删除其中内容。",
        "error": "global_skill_write_blocked",
        "path": str(target_path),
        "recommendedNextAction": (
            "读取请使用 fetch_skill_instructions 或 read_native_file；运行 Skill 内脚本请通过 Skill 工具的 run_script。"
            " 如需安装或更新全局 Skill，请由用户通过 Admin/CLI 的 Skill 管理入口完成。"
        ),
    }


def _task_write_scope_values(runtime_context: dict[str, Any]) -> list[str]:
    raw_values = runtime_context.get("allowed_write_paths") or runtime_context.get("allowedWritePaths") or []
    values = [raw_values] if isinstance(raw_values, str) else list(raw_values or [])
    return [str(value or "").strip() for value in values if str(value or "").strip()]


def _task_write_scope_allows(runtime_context: dict[str, Any], target_path: Path) -> bool:
    runtime_kind = str(runtime_context.get("runtime_kind") or runtime_context.get("runtimeKind") or "").strip().lower()
    capsule_mode = str(
        runtime_context.get("engineering_capsule_mode")
        or runtime_context.get("engineeringCapsuleMode")
        or ""
    ).strip().lower()
    allowed_values = _task_write_scope_values(runtime_context)
    if runtime_kind == "subagent" and capsule_mode != "write":
        return False
    if not allowed_values:
        return runtime_kind != "subagent"
    workspace_root = Path(
        str(runtime_context.get("workspace_path") or runtime_context.get("workspacePath") or "")
    ).expanduser()
    try:
        workspace_root = workspace_root.resolve()
        target_candidate = target_path.expanduser()
        target = (target_candidate if target_candidate.is_absolute() else workspace_root / target_candidate).resolve()
    except Exception:
        return False
    for allowed_value in allowed_values:
        candidate_path = Path(allowed_value).expanduser()
        candidate = candidate_path if candidate_path.is_absolute() else workspace_root / candidate_path
        try:
            candidate = candidate.resolve()
        except Exception:
            continue
        if target == candidate:
            return True
        directory_scope = (
            allowed_value.endswith(("/", "\\"))
            or candidate == workspace_root
            or (candidate.exists() and candidate.is_dir())
        )
        if directory_scope:
            try:
                target.relative_to(candidate)
                return True
            except ValueError:
                continue
    return False


def _task_write_scope_block_payload(runtime_context: dict[str, Any], target_path: Path) -> dict[str, Any]:
    return {
        "ok": False,
        "kind": "write_set_scope_block",
        "summary": "目标文件不在当前委派任务的允许写集内，已阻止写入。",
        "error": "path_outside_allowed_write_set",
        "path": str(target_path),
        "engineeringCapsuleMode": str(
            runtime_context.get("engineering_capsule_mode")
            or runtime_context.get("engineeringCapsuleMode")
            or "none"
        ),
        "allowedWritePaths": _task_write_scope_values(runtime_context),
        "recommendedNextAction": "仅写入任务合同列出的产物；如确需新增文件，由 Supervisor 重新划定任务写集。",
    }


TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".mdx", ".rst",
    ".json", ".jsonl", ".ipynb", ".yaml", ".yml", ".xml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".csv", ".tsv", ".log", ".sql", ".graphql", ".gql", ".proto", ".properties", ".lock", ".diff", ".patch",
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx",
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    ".java", ".kt", ".kts", ".go", ".rs", ".c", ".cc", ".cpp", ".cxx",
    ".h", ".hh", ".hpp", ".hxx", ".cs", ".fs", ".fsx", ".vb", ".php", ".rb", ".swift", ".scala",
    ".r", ".rmd", ".lua", ".dart", ".groovy", ".gradle", ".clj", ".cljs", ".ex", ".exs", ".erl", ".hrl",
    ".sol", ".asm", ".s", ".tex", ".tf", ".tfvars",
    ".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1", ".psm1",
}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xlsx", ".xls"}
_TEXT_FILENAMES = {
    "dockerfile", "makefile", "rakefile", "gemfile", "procfile",
    ".gitignore", ".gitattributes", ".editorconfig",
}
_READ_MAX_LINES = 2000
_READ_MAX_CHARS = 240_000
_READ_BEFORE_WRITE_TTL_SECONDS = 30 * 60
_READ_BEFORE_WRITE_MAX_RECEIPTS = 4096
_READ_BEFORE_WRITE_LOCK = threading.RLock()
_READ_BEFORE_WRITE_RECEIPTS: dict[str, dict[str, Any]] = {}


def is_readable_native_file(path_or_name: str, mime_type: str = "") -> bool:
    candidate = str(path_or_name or "").split("?", 1)[0].split("#", 1)[0]
    path = Path(candidate)
    name = path.name.lower()
    suffix = path.suffix.lower()
    normalized_mime = str(mime_type or "").strip().lower().split(";", 1)[0]
    if normalized_mime.startswith("text/") or normalized_mime in {
        "application/json",
        "application/ld+json",
        "application/x-ipynb+json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }:
        return True
    return (
        suffix in TEXT_EXTENSIONS
        or suffix in DOCUMENT_EXTENSIONS
        or name in _TEXT_FILENAMES
        or name.startswith(".env.")
    )


def is_binary(file_path: str) -> bool:
    """Check if a file is highly likely to be binary."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type:
        if mime_type.startswith('text/') or mime_type == 'application/json':
            return False

    if is_readable_native_file(file_path):
        return False

    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(1024)
            if b'\0' in chunk:
                return True
    except Exception:
        pass

    return True


def _record_agent_written_file_artifact(
    runtime_context: dict[str, Any],
    target_path: Path,
    *,
    operation: str,
) -> dict[str, Any] | None:
    session_id = str(runtime_context.get("session_id") or "").strip()
    run_id = str(runtime_context.get("run_id") or "").strip()
    if not session_id or not run_id or not target_path.is_file():
        return None
    workspace_root = str(runtime_context.get("workspace_path") or "").strip()
    original_workspace_root = str(
        runtime_context.get("original_workspace_path")
        or runtime_context.get("originalWorkspacePath")
        or ""
    ).strip()
    managed_execution = runtime_context.get("managed_engineering_execution") is True
    workspace_relative_path = ""
    if workspace_root:
        try:
            workspace_relative_path = target_path.resolve().relative_to(Path(workspace_root).resolve()).as_posix()
        except (OSError, ValueError):
            workspace_relative_path = ""
    try:
        return artifact_store.record_local_file(
            file_path=target_path,
            session_id=session_id,
            run_id=run_id,
            workspace_path=workspace_relative_path or None,
            metadata={
                "origin": "agent_file_write",
                "source": "write_native_file",
                "writeOperation": operation,
                "surfaceVisible": True,
                "storageClass": "workspace",
                "pathPlane": "workspace_artifact",
                "workspaceRoot": (
                    original_workspace_root if managed_execution and original_workspace_root else workspace_root
                ) or None,
                "workspaceRelativePath": workspace_relative_path or None,
                "projectId": str(runtime_context.get("project_id") or "").strip() or None,
                "workspaceId": str(runtime_context.get("workspace_id") or "").strip() or None,
                "managedExecution": managed_execution,
                "deliveryState": "candidate" if managed_execution else "authoritative",
                "worktreeId": str(
                    runtime_context.get("worktree_id") or runtime_context.get("worktreeId") or ""
                ).strip() or None,
            },
            source_component="write_native_file",
            node="write_native_file",
        )
    except Exception:
        # The file write remains authoritative. Artifact indexing is a
        # recoverable projection and must never turn a successful write into a
        # false failure.
        return None


def _agent_preview_text(value: Any, limit: int = 700) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: max(0, limit - 3)].rstrip() + "..."
    return text


def _agent_compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value not in (None, "", [], {})
    }


def _agent_limited_list(value: Any, *, limit: int = 20) -> list[Any]:
    return list(value or [])[:limit] if isinstance(value, list) else []


def _read_before_write_key(runtime_context: dict[str, Any], target_path: Path) -> str:
    scope_parts = (
        str(runtime_context.get("session_id") or "").strip(),
        str(runtime_context.get("run_id") or runtime_context.get("root_run_id") or "").strip(),
        str(runtime_context.get("workspace_id") or runtime_context.get("project_id") or "").strip(),
    )
    normalized_path = os.path.normcase(str(target_path.resolve(strict=False)))
    return "\x1f".join((*scope_parts, normalized_path))


def _file_state_fingerprint(target_path: Path) -> tuple[int, int]:
    stat = target_path.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


def _prune_read_before_write_receipts(now: float) -> None:
    expired = [
        key
        for key, receipt in _READ_BEFORE_WRITE_RECEIPTS.items()
        if now - float(receipt.get("recordedAtMonotonic") or 0.0) > _READ_BEFORE_WRITE_TTL_SECONDS
    ]
    for key in expired:
        _READ_BEFORE_WRITE_RECEIPTS.pop(key, None)
    if len(_READ_BEFORE_WRITE_RECEIPTS) <= _READ_BEFORE_WRITE_MAX_RECEIPTS:
        return
    oldest = sorted(
        _READ_BEFORE_WRITE_RECEIPTS.items(),
        key=lambda item: float(item[1].get("recordedAtMonotonic") or 0.0),
    )
    for key, _ in oldest[: len(_READ_BEFORE_WRITE_RECEIPTS) - _READ_BEFORE_WRITE_MAX_RECEIPTS]:
        _READ_BEFORE_WRITE_RECEIPTS.pop(key, None)


def _record_file_read(runtime_context: dict[str, Any], target_path: Path) -> None:
    now = time.monotonic()
    receipt = {
        "fingerprint": _file_state_fingerprint(target_path),
        "recordedAtMonotonic": now,
    }
    with _READ_BEFORE_WRITE_LOCK:
        _prune_read_before_write_receipts(now)
        _READ_BEFORE_WRITE_RECEIPTS[_read_before_write_key(runtime_context, target_path)] = receipt


def _consume_file_read_receipt(runtime_context: dict[str, Any], target_path: Path) -> tuple[bool, str]:
    if not target_path.exists() or not target_path.is_file():
        return True, ""
    now = time.monotonic()
    key = _read_before_write_key(runtime_context, target_path)
    with _READ_BEFORE_WRITE_LOCK:
        _prune_read_before_write_receipts(now)
        receipt = _READ_BEFORE_WRITE_RECEIPTS.get(key)
        if not receipt:
            return False, "missing"
        if tuple(receipt.get("fingerprint") or ()) != _file_state_fingerprint(target_path):
            _READ_BEFORE_WRITE_RECEIPTS.pop(key, None)
            return False, "stale"
    return True, ""


def _invalidate_file_read_receipt(runtime_context: dict[str, Any], target_path: Path) -> None:
    with _READ_BEFORE_WRITE_LOCK:
        _READ_BEFORE_WRITE_RECEIPTS.pop(_read_before_write_key(runtime_context, target_path), None)


def _read_before_write_block_payload(target_path: Path, reason: str) -> dict[str, Any]:
    stale = reason == "stale"
    return {
        "ok": False,
        "kind": "read_before_write_required",
        "summary": (
            "文件在读取后发生了变化，已阻止基于旧内容继续修改。"
            if stale
            else "修改已有文件前必须先读取当前内容。"
        ),
        "error": "file_changed_after_read" if stale else "existing_file_not_read",
        "path": str(target_path),
        "recommendedNextAction": (
            "重新调用 read_native_file 读取当前文件，再重试 write_native_file。局部修改请同时提供行范围或 expected_old_text。"
        ),
    }


def _atomic_write_text(target_path: Path, content: str, *, append: bool = False) -> None:
    """Write text without exposing a partially truncated target file."""
    existing_content = ""
    if append and target_path.exists():
        with target_path.open("r", encoding="utf-8", errors="ignore", newline="") as handle:
            existing_content = handle.read()

    target_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".v8os-tmp",
        dir=str(target_path.parent),
        text=False,
    )
    temporary_path = Path(temporary_name)
    descriptor_open = True
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor_open = False
            handle.write(existing_content + str(content or ""))
            handle.flush()
            os.fsync(handle.fileno())
        if target_path.exists():
            try:
                os.chmod(temporary_path, target_path.stat().st_mode & 0o777)
            except OSError:
                pass
        os.replace(temporary_path, target_path)
    except BaseException:
        if descriptor_open:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _html_integrity_issues(content: str) -> list[str]:
    """Return only high-confidence signs that a full HTML write was truncated."""

    text = str(content or "")
    lowered = text.lower()
    issues: list[str] = []
    document_like = any(token in lowered for token in ("<!doctype html", "<html", "<head", "<body"))
    if document_like:
        for opening, closing, label in (
            ("<html", "</html>", "html_close_missing"),
            ("<head", "</head>", "head_close_missing"),
            ("<body", "</body>", "body_close_missing"),
        ):
            if opening in lowered and closing not in lowered:
                issues.append(label)
    for opening, closing, label in (
        ("<style", "</style>", "style_close_missing"),
        ("<script", "</script>", "script_close_missing"),
    ):
        if lowered.count(opening) > lowered.count(closing):
            issues.append(label)

    # A model/tool payload cut in the middle of a CSS declaration commonly
    # ends with `property:` or an unterminated block.  Restrict this check to a
    # trailing style section so valid text such as `Note:` is not rejected.
    style_start = lowered.rfind("<style")
    style_end = lowered.rfind("</style>")
    if style_start > style_end:
        style_tail = text[style_start:].rstrip()
        if re.search(r"[-\w]+\s*:\s*$", style_tail):
            issues.append("css_declaration_truncated")
        if style_tail.count("{") > style_tail.count("}"):
            issues.append("css_block_unclosed")
    return list(dict.fromkeys(issues))


def _html_integrity_block_payload(target_path: Path, issues: list[str]) -> dict[str, Any]:
    return {
        "ok": False,
        "kind": "incomplete_html_document",
        "error": "html_integrity_check_failed",
        "summary": "HTML 内容疑似在标签或 CSS 声明中途截断，已阻止写入和产物发布。",
        "path": str(target_path),
        "issues": issues,
        "recommendedNextAction": (
            "重新生成完整 HTML，确认文档级 html/head/body、style/script 均闭合后再次调用 "
            "write_native_file；工具返回 ok=false 时不得把该文件宣称为完成产物。"
        ),
    }


def _native_file_read_error(
    *,
    kind: str,
    summary: str,
    path: str | Path,
    error: str | None = None,
    recommended_next_action: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "ok": False,
        "kind": kind,
        "summary": summary,
        "error": error or kind,
        "path": str(path),
    }
    if recommended_next_action:
        payload["recommendedNextAction"] = recommended_next_action
    return json.dumps(payload, ensure_ascii=False)


@tool
def read_native_file(path: str, start_line: Optional[int] = None, end_line: Optional[int] = None) -> str:
    """Read a code, text, Markdown, PDF, DOCX, PPTX, or spreadsheet file.

    Use this for known text, JSON, Markdown, source, task brief, Spec, or config paths in the active workspace.
    Do not use shell commands, Python one-liners, `type`, `Get-Content`, or `cat` just to read a known file.
    Reading a file also creates the same-run receipt required before modifying an existing file with `write_native_file`.

    Supported office documents are converted to bounded Markdown/text with the
    same document-ingestion parser used by Memory. Other binary files are
    refused to protect context.
    If the file is very large (> 2000 lines), specify start_line and end_line and continue reading the same file as needed.

    Arguments:
        path (str): Absolute path to the file.
        start_line (int, optional): The 1-indexed line number to start reading from.
        end_line (int, optional): The 1-indexed line number to stop reading at.
    """
    try:
        runtime_context = get_runtime_context()
        path_preflight = resolve_workspace_tool_path(path, runtime_context=runtime_context, allow_global_skill_read=True)
        if not path_preflight.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "kind": "workspace_boundary_block",
                    "summary": path_preflight.get("summary"),
                    "error": path_preflight.get("error"),
                    "inputPath": path,
                    "resolvedPath": path_preflight.get("resolvedPath"),
                    "workspaceBinding": path_preflight.get("binding"),
                    "recommendedNextAction": "使用当前 Active Workspace Root 内的相对路径，或由用户显式授予额外 root 后再读。",
                },
                ensure_ascii=False,
            )
        target_path = Path(str(path_preflight.get("resolvedPath") or path))
        if not target_path.exists() or not target_path.is_file():
            return _native_file_read_error(
                kind="file_not_found",
                summary=f"File '{target_path}' does not exist or is not a file.",
                path=target_path,
                recommended_next_action="确认 Active Workspace Root 和文件相对路径后重试。",
            )

        suffix = target_path.suffix.lower()
        if suffix in DOCUMENT_EXTENSIONS:
            from core.document_parser import (
                DocumentIngestionDependencyError,
                DocumentParser,
                UnsupportedLegacyDocumentError,
            )

            try:
                document_content = DocumentParser.parse_file(target_path)
            except (DocumentIngestionDependencyError, UnsupportedLegacyDocumentError) as exc:
                return json.dumps(
                    {
                        "ok": False,
                        "kind": "document_ingestion_unavailable",
                        "summary": str(exc),
                        **exc.to_payload(),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            if document_content.startswith("Error parsing document "):
                return _native_file_read_error(
                    kind="document_parse_failed",
                    summary=document_content,
                    path=target_path,
                    recommended_next_action="检查文档是否损坏，并查看 document_ingestion 能力包诊断。",
                )
            lines = document_content.splitlines(keepends=True)
        elif is_binary(str(target_path)):
            return _native_file_read_error(
                kind="binary_file_unsupported",
                summary=f"'{path}' appears to be a binary file.",
                path=target_path,
                recommended_next_action=(
                    "图片请使用 vision_media_analyzer；分享页或视频请使用 download_media_for_vision。"
                ),
            )

        else:
            with open(target_path, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()

        total_lines = len(lines)
        if start_line is not None and start_line < 1:
            return _native_file_read_error(
                kind="invalid_line_range",
                summary="start_line must be a positive 1-based line number.",
                path=target_path,
                error="start_line_out_of_range",
                recommended_next_action="Use start_line=1 or another line within the file.",
            )
        if end_line is not None and end_line < 1:
            return _native_file_read_error(
                kind="invalid_line_range",
                summary="end_line must be a positive 1-based line number.",
                path=target_path,
                error="end_line_out_of_range",
                recommended_next_action="Use an end_line within the file.",
            )
        if start_line is not None and end_line is not None and end_line < start_line:
            return _native_file_read_error(
                kind="invalid_line_range",
                summary=f"end_line ({end_line}) cannot be before start_line ({start_line}).",
                path=target_path,
                error="reversed_line_range",
                recommended_next_action=f"Use a range between line 1 and line {total_lines}.",
            )
        if start_line is not None and start_line > max(total_lines, 1):
            return _native_file_read_error(
                kind="line_range_out_of_bounds",
                summary=f"start_line {start_line} is beyond the file's {total_lines} lines.",
                path=target_path,
                error="start_line_beyond_end_of_file",
                recommended_next_action=f"Use a start_line between 1 and {max(total_lines, 1)}.",
            )
        start_idx = max(0, start_line - 1) if start_line else 0
        end_idx = min(total_lines, end_line) if end_line else total_lines

        if end_idx - start_idx > _READ_MAX_LINES:
            end_idx = start_idx + _READ_MAX_LINES
            truncated = True
        else:
            truncated = False

        content = "".join(lines[start_idx:end_idx])
        if len(content) > _READ_MAX_CHARS:
            content = content[:_READ_MAX_CHARS]
            truncated = True
        header = f"--- File: {target_path} (Lines {start_idx + 1} to {end_idx} of {total_lines}) ---\n"
        footer = (
            "\n--- [TRUNCATED] Read exceeded the 2000-line or 240000-character context limit. "
            "Use start_line/end_line to read more. ---"
            if truncated
            else ""
        )
        _record_file_read(runtime_context, target_path)

        return header + content + footer

    except Exception as e:
        return _native_file_read_error(
            kind="file_read_failed",
            summary=f"Error reading file '{path}': {str(e)}",
            path=path,
        )


@tool
def share_workspace_file(path: str, mode: str = "auto") -> dict[str, Any]:
    """Share a file from the current main/project workspace as a remote session resource for preview or download.

    Compatibility wrapper: the file is adopted into the runtime artifact store with origin=workspace_adopted.

    Arguments:
        path: Absolute or relative path inside the current workspace/project workspace.
        mode: auto, preview, or download. Defaults to auto.
    """
    try:
        runtime_context = get_runtime_context()
        artifact = artifact_store.adopt_workspace_file(
            path=path,
            mode=mode,
            session_id=str(runtime_context.get("session_id") or "").strip() or None,
            run_id=str(runtime_context.get("run_id") or "").strip() or None,
            message_id=str(runtime_context.get("message_id") or "").strip() or None,
            source_component="share_workspace_file_compat",
            node="share_workspace_file",
        )
        adopted_from = artifact.get("adoptedFrom") if isinstance(artifact.get("adoptedFrom"), dict) else {}
        return {
            "ok": True,
            "artifact": artifact,
            "artifactId": artifact.get("artifactId") or artifact.get("id"),
            "origin": "workspace_adopted",
            "filename": adopted_from.get("filename") or artifact.get("title"),
            "mimeType": artifact.get("mimeType"),
            "mode": adopted_from.get("mode") or str(mode or "auto").strip() or "auto",
            "url": artifact.get("contentUrl") or artifact.get("previewUrl"),
            "previewable": bool(adopted_from.get("previewable", artifact.get("hasPreview"))),
            "downloadable": bool(adopted_from.get("downloadable", True)),
            "viewerKind": adopted_from.get("viewerKind") or (artifact.get("metadata") or {}).get("viewerKind"),
            "workspaceRelativePath": artifact.get("workspaceRelativePath") or artifact.get("workspacePath"),
            "workspaceId": artifact.get("workspaceId"),
            "projectId": artifact.get("projectId"),
            "message": "File adopted into the runtime artifact system.",
        }
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return {
            "ok": False,
            "error": str(e),
            "filename": Path(str(path or "")).name if str(path or "").strip() else None,
            "mode": str(mode or "auto").strip() or "auto",
            "previewable": False,
            "downloadable": False,
            "viewerKind": "download",
        }


@tool
def write_native_file(
    path: str,
    content: str,
    append: bool = False,
    line_start: int | None = None,
    line_end: int | None = None,
    expected_old_text: str = "",
    allow_full_replace: bool = False,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Author governed text/JSON/Markdown/source artifacts in the active workspace.

    Use this instead of shell commands or redirection for content-bearing project files. New files may be created
    directly. Before changing or appending to an existing file,
    call `read_native_file` in the same run. A successful write consumes that read receipt,
    so read the file again before another modification. For an existing file, choose exactly
    one explicit mutation intent: append=True, a line/expected-text scoped patch, or
    allow_full_replace=True for a deliberate whole-file replacement. The default must never
    silently truncate an existing file.

    Arguments:
        path (str): Absolute path to the file.
        content (str): The string content to write.
        append (bool): If True, appends to the end of the file. If False, another explicit
            mutation intent is still required when the target already exists.
        line_start (int | None): Optional 1-based start line for scoped replacement.
        line_end (int | None): Optional 1-based end line for scoped replacement.
        expected_old_text (str): Optional exact text anchor for scoped replacement.
        allow_full_replace (bool): Explicitly allow full overwrite of an existing file.
    """
    try:
        runtime_context = get_runtime_context()
        if not _task_write_scope_allows(runtime_context, Path(str(path or ""))):
            return json.dumps(
                _task_write_scope_block_payload(runtime_context, Path(str(path or ""))),
                ensure_ascii=False,
                indent=2,
            )
        side_effect_preflight = ensure_workspace_side_effect_allowed(
            runtime_context,
            operation="file_write",
            subject=path,
        )
        if not side_effect_preflight.get("ok"):
            return json.dumps(side_effect_preflight, ensure_ascii=False, indent=2)
        if str(path or "").strip() and is_global_skill_path(path):
            return json.dumps(_global_skill_write_block_payload(path), ensure_ascii=False, indent=2)
        path_preflight = resolve_workspace_tool_path(path, runtime_context=runtime_context)
        if not path_preflight.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "kind": "workspace_boundary_block",
                    "summary": path_preflight.get("summary"),
                    "error": path_preflight.get("error"),
                    "inputPath": path,
                    "resolvedPath": path_preflight.get("resolvedPath"),
                    "workspaceBinding": path_preflight.get("binding"),
                    "recommendedNextAction": "将写入路径改到当前 Active Workspace Root 内，或由用户显式授予额外 root。",
                },
                ensure_ascii=False,
            )
        target_path = Path(str(path_preflight.get("resolvedPath") or path))
        if is_global_skill_path(target_path):
            return json.dumps(_global_skill_write_block_payload(target_path), ensure_ascii=False, indent=2)
        if not _task_write_scope_allows(runtime_context, target_path):
            return json.dumps(
                _task_write_scope_block_payload(runtime_context, target_path),
                ensure_ascii=False,
                indent=2,
            )
        read_allowed, read_block_reason = _consume_file_read_receipt(runtime_context, target_path)
        if not read_allowed:
            return json.dumps(
                _read_before_write_block_payload(target_path, read_block_reason),
                ensure_ascii=False,
                indent=2,
            )
        allowed, error_message = _enforce_safety_decision(
            safety_guardian.assess_file_write(
                str(target_path),
                append=append,
                runtime_context=runtime_context,
                content_preview=str(content or "")[:12000],
            ),
            tool_call_id=tool_call_id,
            question=f"Safety Guardian 检测到写文件动作需要确认，是否继续？\n\n路径：{path}",
        )
        if not allowed:
            return error_message or "Safety Guardian 已阻止文件写入。"

        incoming_content = str(content or "")

        target_path.parent.mkdir(parents=True, exist_ok=True)

        scoped_patch_requested = (
            not append
            and target_path.exists()
            and (line_start is not None or line_end is not None or bool(str(expected_old_text or "")))
        )
        patch_proof: dict[str, Any] | None = None
        write_content = incoming_content
        write_reason = "file_write"
        if scoped_patch_requested:
            original_text = target_path.read_text(encoding="utf-8", errors="ignore")
            patch_result = _apply_scoped_text_patch(
                original=original_text,
                replacement=write_content,
                line_start=line_start,
                line_end=line_end,
                expected_old_text=expected_old_text,
            )
            if not patch_result.get("ok"):
                return json.dumps(
                    {
                        "ok": False,
                        "kind": "scoped_patch_block",
                        "summary": patch_result.get("summary") or "局部替换锚点缺失，已阻止写入。",
                        "error": patch_result.get("error") or "patch_anchor_missing",
                        "path": str(target_path),
                        "lineStart": line_start,
                        "lineEnd": line_end,
                        "recommendedNextAction": "先读取目标行号或提供 expected_old_text 锚点，再执行局部替换；不要全量覆盖长文件。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            write_content = str(patch_result.get("newText") or "")
            patch_proof = dict(patch_result.get("proof") or {})
            write_reason = "file_scoped_patch"
        elif not append and target_path.exists() and not allow_full_replace:
            existing_line_count = _line_count_for_guard(
                target_path.read_text(encoding="utf-8", errors="ignore")
            )
            return json.dumps(
                {
                    "ok": False,
                    "kind": "existing_file_full_overwrite_block",
                    "summary": "目标文件已存在，但未声明追加、局部替换或显式全量覆盖意图，已阻止写入。",
                    "error": "existing_file_requires_explicit_write_intent",
                    "path": str(target_path),
                    "existingLineCount": existing_line_count,
                    "recommendedNextAction": "追加请设置 append=true；局部修改请提供 line_start/line_end 或 expected_old_text；确需整份重写时设置 allow_full_replace=true。",
                },
                ensure_ascii=False,
                indent=2,
            )

        final_content = write_content
        if append and target_path.exists():
            final_content = target_path.read_text(encoding="utf-8", errors="ignore") + write_content
        if target_path.suffix.lower() in {".html", ".htm"}:
            integrity_issues = _html_integrity_issues(final_content)
            if integrity_issues:
                return json.dumps(
                    _html_integrity_block_payload(target_path, integrity_issues),
                    ensure_ascii=False,
                    indent=2,
                )

        _atomic_write_text(target_path, write_content, append=append)
        _invalidate_file_read_receipt(runtime_context, target_path)

        safety_guardian.observe_post_action(
            action_family="file_write",
            summary=f"已写入文件：{target_path}",
            details={
                "path": str(target_path),
                "inputPath": path,
                "workspaceBinding": path_preflight.get("binding"),
                "append": append,
                "content_length": len(write_content),
                **({"scopedPatchProof": patch_proof} if patch_proof else {}),
            },
            runtime_context=runtime_context,
        )
        mark_workspace_state_stale(
            runtime_context,
            reason="file_append" if append else write_reason,
            subject=str(target_path),
        )
        _record_agent_written_file_artifact(
            runtime_context,
            target_path,
            operation="append" if append else write_reason,
        )
        if patch_proof:
            return json.dumps(
                {
                    "ok": True,
                    "kind": "scoped_file_patch",
                    "summary": f"已按局部锚点替换文件：{target_path}",
                    "path": str(target_path),
                    "charsWritten": len(write_content),
                    "proof": patch_proof,
                },
                ensure_ascii=False,
                indent=2,
            )
        action = "Appended" if append else "Created/Overwritten"
        return f"Successfully {action} file: {target_path} ({len(write_content)} chars written)"
    except Exception as e:
        _raise_runtime_governance_exception_if_needed(e)
        return f"Error writing file '{path}': {str(e)}"


@tool
def grep_search(query: str, path: str, regex: bool = False, ignore_case: bool = True) -> str:
    """Search file contents for a text or regex pattern under a known file/directory path. Not for finding file names or paths.

    This operates completely natively in Python without requiring the GNU `grep` utility,
    making it fully compatible with Windows.

    Arguments:
        query (str): The content string or regex pattern to search for inside files.
        path (str): The known absolute file or directory path whose contents should be searched.
        regex (bool): Whether the query should be treated as a Regular Expression.
        ignore_case (bool): Whether the search is case-insensitive.
    """
    try:
        runtime_context = get_runtime_context()
        path_preflight = resolve_workspace_tool_path(path, runtime_context=runtime_context, allow_global_skill_read=True)
        if not path_preflight.get("ok"):
            return json.dumps(
                {
                    "ok": False,
                    "kind": "workspace_boundary_block",
                    "summary": path_preflight.get("summary"),
                    "error": path_preflight.get("error"),
                    "inputPath": path,
                    "resolvedPath": path_preflight.get("resolvedPath"),
                    "workspaceBinding": path_preflight.get("binding"),
                    "recommendedNextAction": "使用当前 Active Workspace Root 内的相对路径，或由用户显式授予额外 root 后再搜索。",
                },
                ensure_ascii=False,
            )
        target_path = Path(str(path_preflight.get("resolvedPath") or path))
        if not target_path.exists():
            return f"Error: Path '{path}' does not exist."

        flags = re.IGNORECASE if ignore_case else 0
        if not regex:
            query = re.escape(query)

        try:
            pattern = re.compile(query, flags)
        except re.error as e:
            return f"Error compiling regex pattern: {str(e)}"

        results = []
        max_results = 200

        def search_file(filepath: Path):
            if is_binary(str(filepath)):
                return
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    for i, line in enumerate(f):
                        if pattern.search(line):
                            results.append(f"{filepath}:{i+1}:{line.strip()}")
                            if len(results) >= max_results:
                                return
            except Exception:
                pass

        if target_path.is_file():
            search_file(target_path)

        elif target_path.is_dir():
            files_scanned = 0
            for root, _, files in os.walk(target_path):
                for file in files:
                    files_scanned += 1
                    if files_scanned > 1000:
                        break

                    filepath = Path(root) / file
                    search_file(filepath)
                    if len(results) >= max_results:
                        break
                if len(results) >= max_results or files_scanned > 1000:
                    break

        if not results:
            return f"No matches found for '{query}' in {path}."

        output = "\n".join(results)
        if len(results) >= max_results:
            output += f"\n\n[WARNING: Results truncated at {max_results} matches.]"

        return output
    except Exception as e:
        return f"Error performing search: {str(e)}"

