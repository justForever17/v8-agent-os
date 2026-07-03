from __future__ import annotations

import json
from typing import Any

from .backend import V8PromptUpdate


PRODUCT_AGENT_NAME = "V8OS 编程助手"


def compact_text(value: Any, *, max_chars: int = 1200) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 36].rstrip()}\n\n...[已省略 {len(text) - max_chars + 36} 字]"


def markdown_update_from_v8(update: V8PromptUpdate) -> dict[str, Any]:
    body = compact_text(update.text)
    if not body:
        body = "V8OS 已更新当前任务状态。"
    payload: dict[str, Any] = {
        "role": update.role,
        "kind": update.kind,
        "status": update.status,
        "content": body,
        "_meta": {
            "v8os": {
                "runId": update.run_id,
                "toolCallId": update.tool_call_id,
                "episodeId": update.episode_id,
                "detailRef": update.detail_ref,
                "rawRef": update.raw_ref,
            }
        },
    }
    artifacts = normalize_artifacts(update.artifacts)
    file_changes = normalize_file_changes(update.file_changes)
    diagnostics = normalize_diagnostics(update.diagnostics)
    if artifacts:
        payload["artifacts"] = artifacts
    if file_changes:
        payload["fileChanges"] = file_changes
    if diagnostics:
        payload["diagnostics"] = diagnostics
    return payload


def _read_str(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def normalize_artifacts(items: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        artifact_id = _read_str(item, "artifactId", "artifact_id", "id")
        title = _read_str(item, "title", "name", "filename", "fileName") or artifact_id or "artifact"
        normalized.append({
            "id": artifact_id or None,
            "title": compact_text(title, max_chars=120),
            "mimeType": _read_str(item, "mimeType", "mime_type", "contentType") or None,
            "kind": _read_str(item, "kind", "type", "modality") or None,
            "previewUrl": _read_str(item, "previewUrl", "preview_url", "thumbnailUrl") or None,
            "downloadUrl": _read_str(item, "downloadUrl", "contentUrl", "url") or None,
            "detailRef": _read_str(item, "detailRef", "rawRef", "ref") or None,
        })
        if len(normalized) >= limit:
            break
    return normalized


def normalize_file_changes(items: Any, *, limit: int = 12) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            path = item.strip()
            if path:
                normalized.append({"path": path})
        elif isinstance(item, dict):
            path = _read_str(item, "path", "filePath", "file", "relativePath")
            if path:
                normalized.append({
                    "path": path,
                    "status": _read_str(item, "status", "changeType", "type") or None,
                    "additions": item.get("additions"),
                    "deletions": item.get("deletions"),
                    "detailRef": _read_str(item, "detailRef", "rawRef", "diffRef") or None,
                })
        if len(normalized) >= limit:
            break
    return normalized


def normalize_diagnostics(items: Any, *, limit: int = 8) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append({"message": compact_text(text, max_chars=240)})
        elif isinstance(item, dict):
            message = _read_str(item, "message", "summary", "text", "reason")
            if message:
                normalized.append({
                    "message": compact_text(message, max_chars=240),
                    "severity": _read_str(item, "severity", "level", "status") or None,
                    "detailRef": _read_str(item, "detailRef", "rawRef") or None,
                })
        if len(normalized) >= limit:
            break
    return normalized


def _first_list(payload: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _summary_from_payload(payload: dict[str, Any], *, topic: str) -> tuple[str, str, dict[str, Any]]:
    artifacts = normalize_artifacts(_first_list(payload, "artifacts", "artifactRefs", "artifact_refs"))
    file_changes = normalize_file_changes(_first_list(payload, "fileChanges", "file_changes", "changedFiles", "changed_files", "files"))
    diagnostics = normalize_diagnostics(_first_list(payload, "diagnostics", "diagnostic"))
    if artifacts:
        titles = "、".join(item.get("title") or item.get("id") or "artifact" for item in artifacts[:4])
        return "artifact", f"产物已更新：{titles}", {"artifacts": artifacts}
    if file_changes:
        paths = "、".join(item.get("path") or "file" for item in file_changes[:4])
        return "file_edit", f"文件变更已更新：{paths}", {"fileChanges": file_changes}
    if diagnostics:
        return "diagnostic", diagnostics[0].get("message") or "诊断信息已更新。", {"diagnostics": diagnostics}
    summary = (
        payload.get("agentVisible")
        or payload.get("summary")
        or payload.get("message")
        or payload.get("text")
        or topic
    )
    return "event", str(summary), {}


def compact_runtime_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    topic = str(event.get("topic") or payload.get("topic") or "runtime.update")
    kind, summary, extras = _summary_from_payload(payload, topic=topic)
    text = compact_text(summary, max_chars=1000)
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            text = compact_text(parsed.get("summary") or parsed.get("message") or topic, max_chars=1000)
        except Exception:
            text = topic
    result = {
        "role": "assistant",
        "kind": kind,
        "status": str(payload.get("status") or event.get("kind") or "running"),
        "content": text,
        "_meta": {
            "v8os": {
                "sessionId": event.get("session_id") or event.get("sessionId"),
                "runId": event.get("run_id") or event.get("runId"),
                "toolCallId": payload.get("toolCallId") or payload.get("tool_call_id"),
                "episodeId": payload.get("episodeId") or payload.get("episode_id"),
                "detailRef": payload.get("detailRef") or payload.get("rawRef"),
                "topic": topic,
            }
        },
    }
    result.update(extras)
    return result


def permission_event_kind(kind: str | None) -> str:
    normalized = str(kind or "").strip().lower().replace("-", "_")
    if normalized in {"ask_user", "askuser", "human_question", "user_question"}:
        return "ask_user"
    if normalized in {"spec_approval", "spec_stage_approval", "requirements_approval", "design_approval", "tasks_approval"}:
        return "spec_approval"
    if any(token in normalized for token in ("file", "command", "shell", "safety", "approval", "permission")):
        return "permission"
    return "unknown"
