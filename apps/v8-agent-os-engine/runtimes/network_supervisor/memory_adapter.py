from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from core.database import db
from core.time_truth import utc_now_iso
from erc.run_service import run_service
from runtimes.memory.runtime import memory_runtime
from runtimes.network_supervisor.openai_compat import (
    extract_external_tool_calls_from_events,
    extract_text_from_events,
    flatten_openai_message_content,
    openai_finish_reason_from_events,
)


logger = logging.getLogger("v8_agent_os.network_supervisor.memory_adapter")

ExternalApiMemoryProvenance = Literal["external_api_dialogue"]
AdapterStatus = Literal["skipped", "pending_tool", "extracted", "audit_only", "failed"]
ToolRoundTripState = Literal["pending", "completed", "none"]

_DURABLE_SIGNAL_RE = re.compile(
    r"(记住|以后|偏好|我的偏好|项目约定|长期|remember|preference|always|in future|from now on)",
    re.IGNORECASE,
)
_MAX_TEXT_PREVIEW = 500
_MAX_TOOL_PREVIEW = 360


@dataclass
class NetworkSupervisorMemoryDelta:
    source_runtime: str
    provenance_class: ExternalApiMemoryProvenance
    memory_policy: str
    session_id: str
    run_id: str
    external_thread_id: str | None
    external_user_id: str | None
    project_id: str | None
    workspace_id: str | None
    scope_hint: str | None
    resolved_scope: str | None
    latest_user_delta: str
    assistant_final_text: str
    tool_round_trip_state: ToolRoundTripState
    external_tool_summary: list[dict[str, Any]]


def _clip(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _raw_messages(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [dict(item) for item in list((payload or {}).get("messages") or []) if isinstance(item, dict)]


def _latest_user_delta(payload: dict[str, Any] | None) -> str:
    for item in reversed(_raw_messages(payload)):
        if str(item.get("role") or "").strip().lower() != "user":
            continue
        content = flatten_openai_message_content(item.get("content")).strip()
        if content:
            return _clip(content, _MAX_TEXT_PREVIEW)
    return ""


def _tail_tool_messages(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    messages = _raw_messages(payload)
    tool_messages: list[dict[str, Any]] = []
    for item in reversed(messages):
        role = str(item.get("role") or "").strip().lower()
        if role == "tool":
            tool_messages.append(item)
            continue
        if tool_messages:
            break
    return list(reversed(tool_messages))


def _summarize_tool_messages(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for item in _tail_tool_messages(payload)[-4:]:
        content = flatten_openai_message_content(item.get("content"))
        tool_name = str(item.get("name") or "").strip() or None
        summaries.append(
            {
                "toolName": tool_name,
                "toolCallId": str(item.get("tool_call_id") or item.get("toolCallId") or "").strip() or None,
                "contentLength": len(content),
                "contentPreview": _clip(content, _MAX_TOOL_PREVIEW),
                "success": not any(token in content.lower() for token in ("error", "exception", "failed", "失败", "错误")),
            }
        )
    return summaries


def _scope_token(value: str) -> str:
    token = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(value or "").strip())
    return token.strip("_")[:120]


def _resolved_memory_scope(
    *,
    project_id: str | None,
    workspace_id: str | None,
    scope_hint: str | None,
    external_thread_id: str | None,
) -> str | None:
    normalized_hint = str(scope_hint or "").strip()
    if normalized_hint.startswith(("project:", "channel:", "workspace:", "external_api_thread:")):
        return normalized_hint
    normalized_project = str(project_id or "").strip()
    if normalized_project:
        return f"project:{normalized_project}"
    normalized_workspace = str(workspace_id or "").strip()
    if normalized_workspace:
        return f"workspace:{normalized_workspace}"
    normalized_thread = _scope_token(str(external_thread_id or "").strip())
    if normalized_thread:
        return f"external_api_thread:{normalized_thread}"
    return None


def _tool_round_trip_state(*, finish_reason: str, payload: dict[str, Any] | None) -> ToolRoundTripState:
    if finish_reason == "tool_calls":
        return "pending"
    return "completed" if _tail_tool_messages(payload) else "none"


def _has_durable_signal(delta: NetworkSupervisorMemoryDelta) -> bool:
    if _DURABLE_SIGNAL_RE.search(delta.latest_user_delta or ""):
        return True
    if delta.tool_round_trip_state == "completed" and delta.assistant_final_text and delta.external_tool_summary:
        return True
    return False


def _build_compact_fact(delta: NetworkSupervisorMemoryDelta) -> str:
    lines = [
        "OpenAI compat external API interaction produced a compact memory delta.",
        f"sourceRuntime: {delta.source_runtime}",
        f"provenanceClass: {delta.provenance_class}",
        f"memoryPolicy: {delta.memory_policy}",
        f"toolRoundTripState: {delta.tool_round_trip_state}",
    ]
    if delta.external_thread_id:
        lines.append(f"externalThreadId: {delta.external_thread_id}")
    if delta.external_user_id:
        lines.append(f"externalUserId: {delta.external_user_id}")
    if delta.latest_user_delta:
        lines.append(f"latestUserDelta: {_clip(delta.latest_user_delta, _MAX_TEXT_PREVIEW)}")
    if delta.external_tool_summary:
        compact_tools = [
            {
                "toolName": item.get("toolName"),
                "success": item.get("success"),
                "contentLength": item.get("contentLength"),
                "contentPreview": item.get("contentPreview"),
            }
            for item in delta.external_tool_summary
        ]
        lines.append("externalToolRoundTrip: " + json.dumps(compact_tools, ensure_ascii=False, separators=(",", ":")))
    if delta.assistant_final_text:
        lines.append(f"assistantFinal: {_clip(delta.assistant_final_text, _MAX_TEXT_PREVIEW)}")
    return "\n".join(lines)


class NetworkSupervisorMemoryAdapter:
    def _record_runtime_event(self, *, session_id: str, run_id: str, result: dict[str, Any]) -> None:
        if not session_id:
            return
        try:
            db.add_runtime_event(
                {
                    "event_id": f"ns_mem_{uuid.uuid4().hex}",
                    "session_id": session_id,
                    "run_id": run_id,
                    "seq": db.get_next_runtime_seq(session_id),
                    "kind": "diagnostic",
                    "topic": "network_supervisor.openai.memory_adapter",
                    "ts": utc_now_iso(),
                    "source": {
                        "runtime": "network_supervisor",
                        "component": "openai_compat_memory_adapter",
                    },
                    "payload": result,
                }
            )
        except Exception as exc:
            logger.warning("Failed to record OpenAI compat memory adapter runtime event: %s", exc)

    def _update_run_metadata(self, *, run_id: str, result: dict[str, Any]) -> None:
        if not run_id:
            return
        try:
            run_service.update_metadata(run_id, {"openaiCompatMemoryAdapter": result})
        except Exception as exc:
            logger.warning("Failed to update OpenAI compat memory adapter run metadata: %s", exc)

    def _finalize(self, *, delta: NetworkSupervisorMemoryDelta, status: AdapterStatus, reason: str, fact_id: str | None = None) -> dict[str, Any]:
        result = {
            "adapterStatus": status,
            "reason": reason,
            "sourceRuntime": delta.source_runtime,
            "provenanceClass": delta.provenance_class,
            "memoryPolicy": delta.memory_policy,
            "externalThreadId": delta.external_thread_id,
            "externalUserId": delta.external_user_id,
            "projectId": delta.project_id,
            "workspaceId": delta.workspace_id,
            "scopeHint": delta.scope_hint,
            "resolvedScope": delta.resolved_scope,
            "toolRoundTripState": delta.tool_round_trip_state,
            "latestUserDeltaPreview": _clip(delta.latest_user_delta, 180),
            "assistantFinalPreview": _clip(delta.assistant_final_text, 180),
            "externalToolSummary": delta.external_tool_summary,
            "factId": fact_id,
            "updatedAt": utc_now_iso(),
        }
        self._update_run_metadata(run_id=delta.run_id, result=result)
        self._record_runtime_event(session_id=delta.session_id, run_id=delta.run_id, result=result)
        return result

    def record_openai_compat_delta(
        self,
        *,
        payload: dict[str, Any] | None,
        chat_request: Any,
        run_id: str,
        events: list[dict[str, Any]] | None,
        response_payload: dict[str, Any] | None = None,
        project_id: str | None = None,
        workspace_id: str | None = None,
        scope_hint: str | None = None,
        external_thread_id: str | None = None,
        external_user_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            external_tool_calls = extract_external_tool_calls_from_events(
                list(events or []),
                external_tools=getattr(getattr(chat_request, "config", None), "external_tools", None),
            )
            finish_reason = ""
            if isinstance(response_payload, dict):
                choices = list(response_payload.get("choices") or [])
                if choices and isinstance(choices[0], dict):
                    finish_reason = str(choices[0].get("finish_reason") or "").strip()
            finish_reason = finish_reason or openai_finish_reason_from_events(list(events or []), tool_calls=external_tool_calls)
            session_id = str(getattr(chat_request, "session_id", "") or "").strip()
            assistant_final_text = extract_text_from_events(list(events or []))
            delta = NetworkSupervisorMemoryDelta(
                source_runtime="network_supervisor",
                provenance_class="external_api_dialogue",
                memory_policy="compat_minimal",
                session_id=session_id,
                run_id=str(run_id or "").strip(),
                external_thread_id=str(external_thread_id or "").strip() or None,
                external_user_id=str(external_user_id or "").strip() or None,
                project_id=str(project_id or "").strip() or None,
                workspace_id=str(workspace_id or "").strip() or None,
                scope_hint=str(scope_hint or "").strip() or None,
                resolved_scope=_resolved_memory_scope(
                    project_id=project_id,
                    workspace_id=workspace_id,
                    scope_hint=scope_hint,
                    external_thread_id=external_thread_id,
                ),
                latest_user_delta=_latest_user_delta(payload),
                assistant_final_text=_clip(assistant_final_text, _MAX_TEXT_PREVIEW),
                tool_round_trip_state=_tool_round_trip_state(finish_reason=finish_reason, payload=payload),
                external_tool_summary=_summarize_tool_messages(payload),
            )

            if delta.tool_round_trip_state == "pending":
                return self._finalize(delta=delta, status="pending_tool", reason="external_tool_call_requested")
            if not delta.resolved_scope and not delta.external_thread_id:
                return self._finalize(delta=delta, status="audit_only", reason="no_stable_scope_or_external_thread")
            if finish_reason and finish_reason != "stop":
                return self._finalize(delta=delta, status="audit_only", reason=f"non_terminal_finish_reason:{finish_reason}")
            if not _has_durable_signal(delta):
                return self._finalize(delta=delta, status="audit_only", reason="no_durable_compat_delta")

            fact = _build_compact_fact(delta)
            fact_id = memory_runtime.add_knowledge(
                fact=fact,
                category="external_api_dialogue",
                scope=delta.resolved_scope,
                source_session=delta.session_id,
            )
            return self._finalize(delta=delta, status="extracted", reason="compact_delta_persisted", fact_id=fact_id)
        except Exception as exc:
            logger.exception("OpenAI compat memory adapter failed: %s", exc)
            session_id = str(getattr(chat_request, "session_id", "") or "").strip()
            result = {
                "adapterStatus": "failed",
                "reason": str(exc),
                "sourceRuntime": "network_supervisor",
                "provenanceClass": "external_api_dialogue",
                "memoryPolicy": "compat_minimal",
                "externalThreadId": str(external_thread_id or "").strip() or None,
                "externalUserId": str(external_user_id or "").strip() or None,
                "projectId": str(project_id or "").strip() or None,
                "workspaceId": str(workspace_id or "").strip() or None,
                "scopeHint": str(scope_hint or "").strip() or None,
                "toolRoundTripState": "none",
                "updatedAt": utc_now_iso(),
            }
            self._update_run_metadata(run_id=run_id, result=result)
            self._record_runtime_event(session_id=session_id, run_id=run_id, result=result)
            return result


network_supervisor_memory_adapter = NetworkSupervisorMemoryAdapter()
