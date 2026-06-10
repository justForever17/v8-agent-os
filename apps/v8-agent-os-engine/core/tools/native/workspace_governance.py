from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.workspace_capability import build_workspace_binding


_SCAFFOLD_INSTALL_PATTERN = re.compile(
    r"(?i)(?:^|[;&|]\s*)(?:npx\s+(?:--yes\s+|-y\s+)?create-[\w@./-]+|create-[\w@./-]+|npm\s+create\b|pnpm\s+create\b|yarn\s+create\b|bun\s+create\b|npm\s+(?:install|i)\b|pnpm\s+(?:install|i)\b|yarn\s+(?:install|add)\b|bun\s+(?:install|add)\b)"
)
_BULK_WRITE_PATH_MARKERS = {
    "package.json",
    "pnpm-lock.yaml",
    "package-lock.json",
    "yarn.lock",
    "tsconfig.json",
    "next.config.js",
    "next.config.mjs",
    "vite.config.ts",
    "src/",
    "app/",
}
_workspace_inventory_tokens: dict[str, dict[str, Any]] = {}


def _current_run_inventory_key(runtime_context: dict[str, Any] | None, workspace_root: str) -> str:
    context = dict(runtime_context or {})
    run_id = str(context.get("run_id") or context.get("runId") or "").strip() or "no_run"
    session_id = str(context.get("session_id") or context.get("sessionId") or "").strip() or "no_session"
    return f"{session_id}:{run_id}:{str(workspace_root or '').strip().lower()}"


def _workspace_has_existing_items(workspace_root: Path) -> bool:
    try:
        return any(item.name not in {".git"} for item in workspace_root.iterdir())
    except Exception:
        return False


def _workspace_inventory_gate_required(command_or_path: str, *, workspace_root: str | None = None) -> bool:
    text = str(command_or_path or "").strip().replace("\\", "/")
    lowered = text.lower()
    if workspace_root and not _workspace_has_existing_items(Path(workspace_root)):
        return False
    if _SCAFFOLD_INSTALL_PATTERN.search(lowered):
        return True
    if any(marker in lowered for marker in _BULK_WRITE_PATH_MARKERS):
        return True
    return False


def _workspace_inventory_status(runtime_context: dict[str, Any] | None = None) -> dict[str, Any]:
    binding = build_workspace_binding(runtime_context)
    workspace_root = Path(binding.active_workspace_root)
    token_key = _current_run_inventory_key(runtime_context, str(workspace_root))
    token = _workspace_inventory_tokens.get(token_key)
    return {
        "binding": binding.as_dict(),
        "workspaceRoot": str(workspace_root),
        "hasInventoryToken": bool(token),
        "inventoryToken": token,
        "tokenKey": token_key,
        "nonEmpty": _workspace_has_existing_items(workspace_root),
    }


def _workspace_inventory_block_payload(runtime_context: dict[str, Any] | None, *, operation: str, subject: str) -> dict[str, Any]:
    status = _workspace_inventory_status(runtime_context)
    return {
        "ok": False,
        "kind": "workspace_inventory_required",
        "summary": "当前 Active Workspace Root 非空或操作会创建/安装项目，必须先盘点工作区再继续。",
        "operation": operation,
        "subject": subject,
        "workspaceBinding": status.get("binding"),
        "workspaceRoot": status.get("workspaceRoot"),
        "recommendedNextAction": "先调用 workspace_broker(mode=\"inspect\")，再根据已有项目选择继续现有目录、新建明确子目录或询问用户。",
        "detailTool": "workspace_broker",
    }


def _line_count_for_guard(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def _dominant_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _apply_scoped_text_patch(
    *,
    original: str,
    replacement: str,
    line_start: int | None = None,
    line_end: int | None = None,
    expected_old_text: str | None = None,
) -> dict[str, Any]:
    replacement = str(replacement or "")
    expected_old_text = str(expected_old_text or "")
    if line_start is not None or line_end is not None:
        if line_start is None or line_end is None:
            return {"ok": False, "error": "patch_range_incomplete", "summary": "行号替换需要同时提供 line_start 和 line_end。"}
        try:
            start = int(line_start)
            end = int(line_end)
        except (TypeError, ValueError):
            return {"ok": False, "error": "patch_range_invalid", "summary": "line_start / line_end 必须是整数。"}
        original_lines = original.splitlines(keepends=True)
        line_count = len(original_lines)
        if start < 1 or end < start or end > max(line_count, 1):
            return {
                "ok": False,
                "error": "patch_anchor_missing",
                "summary": "行号范围不在当前文件内，已阻止写入以避免误覆盖。",
                "lineStart": start,
                "lineEnd": end,
                "lineCount": line_count,
            }
        newline = _dominant_newline(original)
        if replacement and not replacement.endswith(("\n", "\r")):
            old_slice = original_lines[start - 1 : end]
            if (old_slice and old_slice[-1].endswith(("\n", "\r"))) or end < line_count:
                replacement = replacement + newline
        new_text = "".join(original_lines[: start - 1]) + replacement + "".join(original_lines[end:])
        replacement_line_count = _line_count_for_guard(replacement)
        return {
            "ok": True,
            "newText": new_text,
            "proof": {
                "mode": "line_range",
                "lineStart": start,
                "lineEnd": end,
                "originalLineCount": line_count,
                "replacementLineCount": replacement_line_count,
                "touchedLineCount": max(end - start + 1, replacement_line_count),
            },
        }
    if expected_old_text:
        offset = original.find(expected_old_text)
        if offset < 0:
            return {"ok": False, "error": "patch_anchor_missing", "summary": "未找到 expected_old_text 锚点，已阻止写入以避免误覆盖。"}
        before = original[:offset]
        start_line = before.count("\n") + 1
        old_line_count = _line_count_for_guard(expected_old_text)
        new_text = original[:offset] + replacement + original[offset + len(expected_old_text) :]
        return {
            "ok": True,
            "newText": new_text,
            "proof": {
                "mode": "text_anchor",
                "lineStart": start_line,
                "lineEnd": start_line + max(old_line_count - 1, 0),
                "originalTextLength": len(expected_old_text),
                "replacementTextLength": len(replacement),
                "replacementLineCount": _line_count_for_guard(replacement),
                "touchedLineCount": max(old_line_count, _line_count_for_guard(replacement)),
            },
        }
    return {"ok": False, "error": "patch_anchor_missing", "summary": "局部替换需要提供行号范围或 expected_old_text 锚点。"}
