from __future__ import annotations

import json
import re
from typing import Any

from .budget import (
    COMMAND_TOOL_NAMES,
)
from .device_media import (
    _focused_creative_media_contract,
    _render_computer_use_surface,
    _render_creative_media_surface,
    _render_memory_surface,
    _render_native_json_surface,
    _render_rpa_surface,
)
from .extensions import (
    _render_agent_registry_surface,
    _render_delegation_broker_surface,
    _render_generic_json_surface,
)
from .formatting import (
    _first_text,
    _head_tail_truncate_text,
    _parse_json_object,
    _short_text,
    _surface_ref_lines,
    _tool_json_any_payload,
)
from .research_web import (
    _render_research_broker_surface,
    _render_web_broker_surface,
)
from .runtime import (
    _render_config_broker_surface,
    _render_plugin_broker_surface,
    _render_runtime_broker_surface,
    _render_session_context_surface,
    _render_session_coordination_surface,
)

def _decision_agent_visible_surface(
    *,
    tool_name: str,
    content: str,
    raw_ref: str,
    budget: int,
) -> str | None:
    payload_any = _tool_json_any_payload(content)
    if not isinstance(payload_any, (dict, list)):
        return None
    payload = payload_any if isinstance(payload_any, dict) else {}
    renderer_result: str | None = None
    if payload.get("kind") in {"tool_parameter_repair", "tool_execution_error"}:
        # Callback errors precede broker-specific results. Keep their input
        # rejection / uncertain side-effect distinction across all projections.
        lines = [_first_text(payload, "summary", "message", limit=500)]
        outcome = _short_text(payload.get("executionOutcome") or "unverified", 80)
        lines.append(f"Execution outcome: {outcome}")
        fields = payload.get("invalidFields")
        if isinstance(fields, list) and fields:
            lines.append("Invalid field paths: " + ", ".join(_short_text(field, 180) for field in fields[:8]))
        lines.extend(_surface_ref_lines(raw_ref))
        renderer_result = "\n".join(line for line in lines if line)
    elif tool_name == "runtime_broker":
        renderer_result = _render_runtime_broker_surface(payload, raw_ref)
    elif tool_name == "agent_broker":
        renderer_result = _render_agent_registry_surface(payload, raw_ref)
    elif tool_name == "config_broker":
        renderer_result = _render_config_broker_surface(payload, raw_ref)
    elif tool_name == "plugin_broker":
        renderer_result = _render_plugin_broker_surface(payload, raw_ref)
    elif tool_name == "session_context_broker":
        renderer_result = _render_session_context_surface(payload, raw_ref, budget=budget)
    elif tool_name == "session_command_broker":
        def compact_assignment(item):
            return {
                key: item.get(key) for key in
                ("assignmentId", "rootSessionId", "childSessionId", "revision", "status",
                 "authorizationRef", "scopeRevision", "requirementRevision")
            }
        compact = {key: payload[key] for key in ("ok", "error", "idempotent", "afterCursor", "nextCursor", "hasMore", "message", "deliveryAcknowledged", "waiting", "generation", "summary", "controlStatus", "targetRunId", "requestId", "cancellationRequested", "stopConfirmed", "observedRunStatus") if key in payload}
        if isinstance(payload.get("assignment"), dict):
            compact["assignment"] = compact_assignment(payload["assignment"])
        if isinstance(payload.get("assignments"), list):
            compact["assignments"] = [compact_assignment(item) for item in payload["assignments"] if isinstance(item, dict)]
        if isinstance(payload.get("results"), list):
            compact["results"] = [{key: item.get(key) for key in
                                  ("messageId", "assignmentId", "resultVersion", "resultCursor",
                                   "resultFinal", "superseded", "replyStatus", "detailRef")}
                                 for item in payload["results"] if isinstance(item, dict)]
        compact["rawRef"] = raw_ref
        renderer_result = json.dumps(compact, ensure_ascii=False, separators=(",", ":"))
    elif tool_name == "session_message_broker":
        renderer_result = _render_session_coordination_surface(payload, raw_ref, budget=budget)
    elif tool_name == "system_operations":
        from core.system_operations.surface import render_system_operation
        renderer_result = render_system_operation(payload, raw_ref)
    elif tool_name == "device_broker":
        from core.device_tool_surface import render_device_surface
        renderer_result = render_device_surface(payload, raw_ref, budget=budget)
    elif tool_name == "research_broker":
        renderer_result = _render_research_broker_surface(payload, raw_ref, budget=budget)
    elif tool_name == "web_broker" or tool_name.startswith("web_"):
        renderer_result = _render_web_broker_surface(payload, raw_ref, budget=budget)
    elif tool_name == "delegation_broker" or tool_name.startswith("delegation_") or tool_name.startswith("subagent_"):
        renderer_result = _render_delegation_broker_surface(payload, raw_ref, budget=budget)
    elif tool_name.startswith("computer_use_"):
        renderer_result = _render_computer_use_surface(tool_name, payload, raw_ref)
    elif tool_name.startswith("creative_media_"):
        renderer_result = _render_creative_media_surface(tool_name, payload, raw_ref, budget=budget)
    elif tool_name.startswith("rpa_"):
        renderer_result = _render_rpa_surface(tool_name, payload, raw_ref)
    elif tool_name in {"read_native_file", "grep_search"}:
        renderer_result = _render_native_json_surface(tool_name, payload, raw_ref)
    elif tool_name.startswith("memory_"):
        renderer_result = _render_memory_surface(tool_name, payload, raw_ref)
    if renderer_result is None:
        renderer_result = _render_generic_json_surface(tool_name, payload_any, raw_ref, budget=budget)
    if renderer_result is None:
        return None
    if renderer_result.startswith(("Runtime episode inspection\n", "Partial handoff published\n", "Delegation dispatch receipt\n")):
        from core.tool_observation_detail import _redact_tool_observation_preview
        title, structured = renderer_result.split("\n", 1)
        safe = _redact_tool_observation_preview(structured)
        if safe != structured:
            safe_payload = json.loads(safe)
            safe_payload["secretsRedacted"] = True
            renderer_result = title + "\n" + json.dumps(safe_payload, ensure_ascii=False, separators=(",", ":"))
    preserve_full_research = tool_name == "research_broker" and renderer_result.startswith("Research answer\n")
    preserve_focused_media_contract = tool_name == "creative_media_capabilities" and _focused_creative_media_contract(payload) is not None
    if len(renderer_result) > budget and not (preserve_full_research or preserve_focused_media_contract):
        if renderer_result.startswith(("Runtime episode inspection\n", "Partial handoff published\n", "Delegation dispatch receipt\n")):
            # Do not splice serialized proof/acceptance arguments. A bounded
            # recovery view points to the same retained observation and cannot
            # be mistaken for an inspected or accepted partial result.
            recovery = {key: payload[key] for key in ("episodeId", "state", "executionTerminal") if key in payload}
            recovery.update({"truncated": True, "handoffsOmitted": len(payload.get("handoffs") or ([payload["handoff"]] if payload.get("handoff") else [])),
                             "controlsOmitted": len(payload.get("controls") or []), "rawRef": raw_ref,
                             "detailTool": f"tool_observation_detail(raw_ref='{raw_ref}')",
                             "nextAction": "Read detailTool before deciding on the omitted proof or issuing acceptance/control actions."})
            if renderer_result.startswith("Delegation dispatch receipt\n"):
                recovery["dispatchItemsOmitted"] = len(payload.get("items") or [])
            return renderer_result.split("\n", 1)[0] + "\n" + json.dumps(recovery, ensure_ascii=False, separators=(",", ":"))
        return _head_tail_truncate_text(renderer_result, budget, f"decision surface truncated; rawRef={raw_ref}")
    return renderer_result
def _append_terminal_stream(
    lines: list[str],
    tag: str,
    value: Any,
    *,
    truncated: bool = False,
    raw_ref: str = "",
    limit: int = 2400,
) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    visible = _head_tail_truncate_text(text, limit, f"{tag} truncated; original length {len(text)} chars")
    lines.append(f"<{tag}>")
    lines.append(visible)
    lines.append(f"</{tag}>")
    if truncated or len(visible) < len(text):
        suffix = f"; rawRef={raw_ref}" if raw_ref else ""
        lines.append(f"[{tag} truncated{suffix}]")
    return True
def _strip_command_echo_from_stream(command: str, value: Any) -> str:
    text = str(value or "").strip()
    rendered_command = str(command or "").strip()
    if not text or not rendered_command:
        return text
    lines = text.splitlines()
    while lines:
        first = lines[0].strip()
        if first == rendered_command or first.endswith(f">{rendered_command}") or first.endswith(f"$ {rendered_command}"):
            lines.pop(0)
            continue
        break
    return "\n".join(lines).strip()
def _render_terminal_command_surface(
    *,
    command: str = "",
    stdout: Any = "",
    stderr: Any = "",
    exit_code: Any = None,
    session_id: str = "",
    waiting_input: bool = False,
    still_running: bool = False,
    raw_ref: str = "",
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
    control_lines: list[str] | None = None,
) -> str:
    lines: list[str] = []
    command_text = str(command or "").strip()
    if command_text:
        lines.append(f"$ {command_text}")
    elif session_id:
        lines.append(f"$ <command session {session_id}>")
    else:
        lines.append("$ <command>")
    cleaned_stdout = _strip_command_echo_from_stream(command_text, stdout)
    cleaned_stderr = _strip_command_echo_from_stream(command_text, stderr)
    has_stream = False
    has_stream = _append_terminal_stream(lines, "stdout", cleaned_stdout, truncated=stdout_truncated, raw_ref=raw_ref) or has_stream
    has_stream = _append_terminal_stream(lines, "stderr", cleaned_stderr, truncated=stderr_truncated, raw_ref=raw_ref) or has_stream
    for line in control_lines or []:
        normalized = str(line or "").strip()
        if normalized:
            lines.append(normalized)
    if waiting_input:
        lines.append("[waiting for input]")
    if still_running:
        lines.append("[still running]")
    if exit_code not in (None, "", [], 0, "0"):
        lines.append(f"[exit code: {exit_code}]")
    if not has_stream and not waiting_input and not still_running and exit_code in (None, 0, "0"):
        lines.append("[completed with no output]")
    return "\n".join(lines).strip()
def _command_agent_visible_surface(
    *,
    tool_name: str,
    content: str,
    raw_ref: str,
    budget: int,
) -> str | None:
    text = str(content or "").strip()
    if text.startswith("$ ") or "\n<stdout>" in text or "\n<stderr>" in text:
        return _head_tail_truncate_text(text, budget, f"command output truncated; rawRef={raw_ref}") if len(text) > budget else text
    payload = _parse_json_object(text)
    if not isinstance(payload, dict):
        if not text:
            return None
        tag = "stderr" if text.lower().startswith("error") else "stdout"
        return _render_terminal_command_surface(
            stderr=text if tag == "stderr" else "",
            stdout=text if tag == "stdout" else "",
            raw_ref=raw_ref,
            stderr_truncated=len(text) > budget,
            stdout_truncated=len(text) > budget,
        )

    kind = str(payload.get("kind") or "").strip()
    command = str(payload.get("command") or "").strip()
    session_id = str(payload.get("sessionId") or payload.get("commandId") or "").strip()
    if not command:
        redirect = payload.get("redirect")
        if isinstance(redirect, dict) and isinstance(redirect.get("args"), dict):
            command = str(redirect.get("args", {}).get("command") or "").strip()
    if kind == "command_result":
        return _render_terminal_command_surface(
            command=command,
            stdout=payload.get("keyOutput") or payload.get("stdoutPreview") or "",
            stderr=payload.get("keyErrors") or payload.get("stderrPreview") or "",
            exit_code=payload.get("returnCode"),
            raw_ref=raw_ref,
            stdout_truncated=bool(payload.get("keyOutputTruncated") or payload.get("stdoutTruncated")),
            stderr_truncated=bool(payload.get("keyErrorsTruncated") or payload.get("stderrTruncated")),
        )
    if kind == "command_session":
        state = str(payload.get("state") or "").strip().lower()
        terminal_states = {"completed", "failed", "timed_out", "terminated"}
        stdout_candidates = (
            (
                payload.get("finalPreview"),
                payload.get("keyOutput"),
                payload.get("deltaText"),
                payload.get("outputPreview"),
                payload.get("initialPreview"),
            )
            if state in terminal_states
            else (
                payload.get("deltaText"),
                payload.get("keyOutput"),
                payload.get("outputPreview"),
                payload.get("finalPreview"),
                payload.get("initialPreview"),
            )
        )
        stdout = next((item for item in stdout_candidates if item not in (None, "")), "")
        control: list[str] = []
        debug = payload.get("debug") if isinstance(payload.get("debug"), dict) else {}
        if not stdout and isinstance(debug.get("screenPreview"), str) and debug["screenPreview"].strip():
            _append_terminal_stream(control, "terminal screen", debug["screenPreview"], raw_ref=raw_ref,
                                    truncated=bool(debug.get("screenPreviewTruncated")))
        if session_id:
            control.append(f"[session: {session_id}]")
        for key in ("cursor", "nextCursor"):
            if key in payload:
                control.append(f"[{key}: {json.dumps(payload[key], ensure_ascii=False)}]")
        if state in terminal_states and payload.get("returnCode") in (0, "0"):
            control.append("[exit code: 0]")
        if state == "recoverable_stalled":
            control.append("[command appears stalled; observe later or terminate]")
        elif state == "render_stalled":
            control.append("[terminal screen is still settling]")
        if state == "timed_out" or payload.get("timedOut"):
            control.append("[deadline exceeded; process tree terminated]")
        elif state == "terminated" or payload.get("terminated"):
            control.append("[terminated]")
        return _render_terminal_command_surface(
            command=command,
            stdout=stdout,
            stderr=payload.get("error") or payload.get("failureMessage") or "",
            exit_code=payload.get("returnCode"),
            session_id=session_id,
            waiting_input=bool(payload.get("awaitingInput")) or state == "awaiting_input",
            still_running=state in {"running", "render_stalled", "recoverable_stalled"} and not bool(payload.get("awaitingInput")),
            raw_ref=raw_ref,
            stdout_truncated=bool(
                payload.get("deltaTruncated")
                or payload.get("keyOutputTruncated")
                or payload.get("outputPreviewTruncated")
                or payload.get("finalPreviewTruncated")
                or payload.get("initialPreviewTruncated")
            ),
            control_lines=control,
        )
    if kind in {"command_session_required", "command_session_redirect"} or str(tool_name or "") in COMMAND_TOOL_NAMES:
        control = [f"[{kind or 'command notice'}]"]
        for key in ("reason", "summary", "error"):
            value = str(payload.get(key) or "").strip()
            if value:
                control.append(f"[{value}]")
        next_action = str(payload.get("recommendedNextAction") or "").strip()
        if next_action and next_action.lower() != "none":
            control.append(f"[Next: {next_action}]")
        redirect = payload.get("redirect")
        if isinstance(redirect, dict) and str(redirect.get("tool") or "").strip():
            control.append(f"[use {redirect.get('tool')} to continue]")
        return _render_terminal_command_surface(
            command=command,
            stderr=payload.get("error") or payload.get("summary") or "",
            raw_ref=raw_ref,
            control_lines=control,
        )
    return None
