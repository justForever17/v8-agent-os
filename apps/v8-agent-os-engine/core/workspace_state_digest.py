from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.process_launch import run_windowless
from core.workspace_capability import build_workspace_binding
from erc.runtime_context import get_runtime_context


_DIGEST_LOCK = threading.Lock()
_DIGEST_CACHE_LIMIT = 128
_IGNORED_DIRS = {".git", "node_modules", ".next", "dist", "build", ".venv", "__pycache__"}
_PROJECT_MARKERS = {
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "bun.lockb",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "vite.config.ts",
    "vite.config.js",
    "next.config.js",
    "next.config.mjs",
    "tsconfig.json",
}
_MUTATING_COMMAND_RE = re.compile(
    r"(?i)(?:^|[;&|]\s*)("
    r"npm\s+(?:install|i|ci|add|remove|uninstall)\b|"
    r"pnpm\s+(?:install|i|add|remove)\b|"
    r"yarn\s+(?:install|add|remove)\b|"
    r"bun\s+(?:install|add|remove)\b|"
    r"npx\s+(?:--yes\s+|-y\s+)?create-[\w@./-]+|"
    r"npm\s+(?:create|init)\b|pnpm\s+create\b|yarn\s+create\b|bun\s+create\b|"
    r"git\s+(?:checkout|switch|reset|clean|commit|merge|rebase|pull|apply|am)\b|"
    r"mkdir\b|new-item\b|rm\b|del\b|erase\b|rmdir\b|rd\b|copy\b|xcopy\b|robocopy\b|move\b|ren\b|"
    r"touch\b|tee\b"
    r")"
)


@dataclass
class WorkspaceDigestEntry:
    text: str
    diagnostics: dict[str, Any]
    snapshot_at: str
    stale: bool = False
    stale_reasons: list[dict[str, str]] = field(default_factory=list)


_DIGEST_CACHE: dict[str, WorkspaceDigestEntry] = {}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _context_from_state(state: dict[str, Any] | None, session_id: str | None = None) -> dict[str, Any]:
    raw = dict(state or {})
    return {
        "runtime_kind": "chat",
        "session_id": session_id or raw.get("session_id") or raw.get("sessionId"),
        "run_id": raw.get("run_id") or raw.get("runId"),
        "workspace_id": raw.get("workspace_id") or raw.get("workspaceId"),
        "workspace_path": raw.get("workspace_path") or raw.get("workspacePath"),
        "project_id": raw.get("project_id") or raw.get("projectId"),
    }


def _cache_key(runtime_context: dict[str, Any] | None) -> tuple[str, str]:
    context = dict(runtime_context or get_runtime_context() or {})
    binding = build_workspace_binding(context, runtime_kind=str(context.get("runtime_kind") or "chat"))
    run_id = str(context.get("run_id") or context.get("runId") or "").strip() or "no_run"
    session_id = str(context.get("session_id") or context.get("sessionId") or "").strip() or "no_session"
    root = str(binding.active_workspace_root.resolve(strict=False))
    key = hashlib.sha256(f"{session_id}|{run_id}|{root.lower()}".encode("utf-8", errors="ignore")).hexdigest()[:24]
    return key, root


def _run_git(workspace_root: Path, *args: str, timeout: float = 2.0) -> tuple[int, str]:
    try:
        result = run_windowless(
            ["git", "-C", str(workspace_root), *args],
            capture_output=True,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        output = (result.stdout or result.stderr or "").strip()
        return int(result.returncode or 0), output
    except Exception as exc:
        return 999, str(exc)


def _collect_git_summary(workspace_root: Path) -> dict[str, Any]:
    code, root_text = _run_git(workspace_root, "rev-parse", "--show-toplevel")
    if code != 0 or not root_text:
        return {"repoDetected": False}
    _, branch = _run_git(workspace_root, "branch", "--show-current")
    _, status = _run_git(workspace_root, "status", "--short")
    changed = [line.strip() for line in status.splitlines() if line.strip()]
    return {
        "repoDetected": True,
        "repoRoot": root_text,
        "branch": branch or "detached",
        "changedCount": len(changed),
        "topChangedFiles": changed[:8],
    }


def _collect_project_markers(workspace_root: Path) -> list[str]:
    markers: list[str] = []
    if not workspace_root.exists() or not workspace_root.is_dir():
        return markers
    candidates: list[Path] = [workspace_root]
    try:
        for child in workspace_root.iterdir():
            if child.is_dir() and child.name not in _IGNORED_DIRS:
                candidates.append(child)
    except Exception:
        return markers
    for base in candidates[:32]:
        try:
            for marker in _PROJECT_MARKERS:
                if (base / marker).exists():
                    try:
                        rel = (base / marker).relative_to(workspace_root).as_posix()
                    except Exception:
                        rel = str(base / marker)
                    markers.append(rel)
                    if len(markers) >= 12:
                        return markers
        except Exception:
            continue
    return markers


def _format_list(values: list[str], *, max_items: int = 6) -> str:
    cleaned = [str(item).strip() for item in values if str(item).strip()]
    if not cleaned:
        return "none"
    rendered = ", ".join(cleaned[:max_items])
    if len(cleaned) > max_items:
        rendered += f", +{len(cleaned) - max_items} more"
    return rendered


def _build_digest(runtime_context: dict[str, Any] | None, *, stale: bool = False, stale_reasons: list[dict[str, str]] | None = None) -> WorkspaceDigestEntry:
    context = dict(runtime_context or get_runtime_context() or {})
    binding = build_workspace_binding(context, runtime_kind=str(context.get("runtime_kind") or "chat"))
    workspace_root = binding.active_workspace_root.resolve(strict=False)
    snapshot_at = _utc_now_iso()
    git_summary = _collect_git_summary(workspace_root)
    markers = _collect_project_markers(workspace_root)
    stale_reasons = list(stale_reasons or [])
    git_line = "Git: repoDetected=false"
    if git_summary.get("repoDetected"):
        git_line = (
            "Git: repoDetected=true "
            f"branch={git_summary.get('branch') or 'unknown'} "
            f"changed={int(git_summary.get('changedCount') or 0)} "
            f"top={_format_list(list(git_summary.get('topChangedFiles') or []), max_items=5)}"
        )
    text = (
        "[WORKSPACE FACTS]\n"
        "This automatic run-scoped snapshot is the initial workspace truth for Supervisor and delegated agents. "
        "It is refreshed by the Engine after observed workspace mutations.\n"
        f"Snapshot: {snapshot_at}; stale={'true' if stale else 'false'}\n"
        f"Active Workspace Root: {workspace_root}\n"
        f"Physical Path Present: {'true' if workspace_root.exists() and workspace_root.is_dir() else 'false'}\n"
        f"Binding: source={binding.source or 'unknown'} workspaceId={binding.workspace_id or 'none'} projectId={binding.project_id or 'none'}\n"
        f"{git_line}\n"
        f"Project markers: {_format_list(markers, max_items=8)}\n"
    )
    if stale_reasons:
        latest = stale_reasons[-3:]
        reason_text = "; ".join(
            f"{item.get('reason') or 'changed'}:{item.get('subject') or ''}".strip(":")
            for item in latest
        )
        text += f"Stale reasons: {reason_text}\n"
    text += "[/WORKSPACE FACTS]\n"
    diagnostics = {
        "source": "workspace_state_digest",
        "estimatedTokens": max(1, len(text) // 4),
        "stale": stale,
        "snapshotAt": snapshot_at,
        "workspaceRoot": str(workspace_root),
        "physicalPathPresent": bool(workspace_root.exists() and workspace_root.is_dir()),
        "repoDetected": bool(git_summary.get("repoDetected")),
        "changedCount": int(git_summary.get("changedCount") or 0),
        "projectMarkerCount": len(markers),
    }
    return WorkspaceDigestEntry(
        text=text,
        diagnostics=diagnostics,
        snapshot_at=snapshot_at,
        stale=stale,
        stale_reasons=stale_reasons,
    )


def build_workspace_state_digest_context(*, state: dict[str, Any] | None = None, session_id: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    runtime_context = _context_from_state(state, session_id=session_id)
    key, root = _cache_key(runtime_context)
    with _DIGEST_LOCK:
        entry = _DIGEST_CACHE.get(key)
        physical_path_present = Path(root).exists() and Path(root).is_dir()
        if entry is None or bool(entry.diagnostics.get("physicalPathPresent")) != physical_path_present:
            entry = _build_digest(runtime_context)
            _DIGEST_CACHE[key] = entry
            while len(_DIGEST_CACHE) > _DIGEST_CACHE_LIMIT:
                _DIGEST_CACHE.pop(next(iter(_DIGEST_CACHE)), None)
        return entry.text, [dict(entry.diagnostics)]


def mark_workspace_state_stale(runtime_context: dict[str, Any] | None = None, *, reason: str = "", subject: str = "") -> None:
    context = dict(runtime_context or get_runtime_context() or {})
    key, _root = _cache_key(context)
    marker = {"reason": str(reason or "changed")[:80], "subject": str(subject or "")[:240], "at": _utc_now_iso()}
    with _DIGEST_LOCK:
        entry = _DIGEST_CACHE.get(key)
        if entry is None:
            _DIGEST_CACHE[key] = _build_digest(context, stale=True, stale_reasons=[marker])
            return
        entry.stale = True
        entry.stale_reasons.append(marker)
        refreshed = _build_digest(context, stale=True, stale_reasons=entry.stale_reasons[-8:])
        _DIGEST_CACHE[key] = refreshed


def command_may_change_workspace(command: str) -> bool:
    return bool(_MUTATING_COMMAND_RE.search(str(command or "")))
