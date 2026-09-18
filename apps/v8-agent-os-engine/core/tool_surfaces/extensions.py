from __future__ import annotations

import json
import re
from typing import Any

from .formatting import (
    _first_text,
    _head_tail_truncate_text,
    _parse_json_object,
    _short_text,
    _source_line,
    _surface_ref_lines,
)
from .runtime import (
    _render_runtime_broker_surface,
)

def _render_delegation_broker_surface(payload: dict[str, Any], raw_ref: str, *, budget: int = 2500) -> str:
    if payload.get("mode") == "inspect" and payload.get("episodeId"):
        return _render_runtime_broker_surface(payload, raw_ref)
    items = payload.get("items")
    if (payload.get("ok") is True and payload.get("mode") == "dispatch" and isinstance(items, list)
            and any(isinstance(item, dict) and item.get("delegationId") for item in items)):
        # The exact returned handle is needed on the very next invocation.
        # Task goals already remain in model history; don't repeat them at the
        # cost of losing the only identity accepted by the control owner.
        receipt = {"items": [{key: item[key] for key in (
            "taskBriefId", "delegationId", "lane", "targetLabel", "status", "effectiveExecution", "toolPolicy",
            "supervisorAcceptance") if key in item} for item in items if isinstance(item, dict)],
            "control": {"tool": "delegation_broker", "idArgument": "delegation_id",
                        "guidance": "For local episodes, copy the exact delegationId into inspect/await/steer/cancel. taskBriefId is a label, not the control ID."},
            "nextAction": "Continue independent work or await a required episode. Dispatch is not execution proof or acceptance; inspect partial proof and use runtime_broker accept_partial before dependent use.",
            "rawRef": raw_ref}
        return "Delegation dispatch receipt\n" + json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
    handoff = payload.get("handoff")
    if payload.get("ok") is True and isinstance(handoff, dict) and handoff.get("status") == "partial":
        receipt = {key: handoff[key] for key in ("handoffRefId", "outputKey", "version", "sourceVersion", "usableFor", "status") if key in handoff}
        receipt.update({"executionTerminal": payload.get("executionTerminal"), "rawRef": raw_ref,
                        "nextAction": "Continue the current task. Publication is not final completion or downstream acceptance."})
        return "Partial handoff published\n" + json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
    mode = _short_text(payload.get("mode") or payload.get("kind") or "dispatch", 40)
    lines = [f"Delegation broker ({mode})"]
    summary = _first_text(payload, "summary", "message", "result", "error", limit=500)
    if summary:
        lines.append(f"Summary: {summary}")
    if payload.get("ok") is False and not summary:
        lines.append("Summary: delegation request did not complete.")

    dispatch_status = payload.get("dispatchStatus") or payload.get("status")
    if dispatch_status:
        lines.append(f"Status: {_short_text(dispatch_status, 100)}")
    missing = payload.get("missingTasks") or payload.get("missing_tasks")
    if missing:
        lines.append(f"Missing tasks: {_short_text(missing, 260)}")
    context_repair = payload.get("error") == "task_context_execution_fields" and isinstance(payload.get("repairPatch"), dict)
    if context_repair:
        from core.tool_observation_detail import _redact_tool_observation_preview
        payload = json.loads(_redact_tool_observation_preview(json.dumps(payload, ensure_ascii=False)))
        lines.append("Nothing has been dispatched. Apply these top-level fields to the same original tasks; retain their complete goal/context and existing policies. Do not submit this patch as a replacement task:")
        lines.append(json.dumps(payload["repairPatch"], ensure_ascii=False, separators=(",", ":")))
        if payload.get("unresolvedFields"):
            lines.append("Still invalid; choose explicit JSON booleans from the authorized task: " + ", ".join(payload["unresolvedFields"]))
        lines.append("Original task hashes: " + json.dumps(payload.get("preservedTaskHashes") or [], ensure_ascii=False, separators=(",", ":")))
        lines.append("Full exampleTasks and original goal/context are in the detail below. Review the repaired fields and explicitly resubmit; context has not been promoted to permission.")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        complete = "\n".join(lines)
        if len(complete) <= budget:
            return complete
        return "\n".join([
            "Delegation repair: task_context_execution_fields. Nothing dispatched.",
            f"Omitted complete patch: {len(payload['repairPatch'])} fields across {len(payload.get('preservedTaskHashes') or [])} tasks; JSON has not been clipped.",
            "Read all pages, apply typed fields to the original tasks, then explicitly retry.",
            f"tool_observation_detail(raw_ref='{raw_ref}', max_chars=1400, start_char=0)",
        ])
    elif payload.get("ok") is False and isinstance(payload.get("exampleTasks"), list):
        lines.append("Repair example (replace placeholders with the authorized task; nothing has been dispatched):")
        lines.append(json.dumps({"mode": "dispatch", "tasks": payload["exampleTasks"]}, ensure_ascii=False))
    if payload.get("ok") is False and payload.get("repairFields"):
        lines.append("Repair fields: " + ", ".join(str(field) for field in payload["repairFields"]))
    if payload.get("ok") is False and payload.get("repairInstruction") and not context_repair:
        lines.append("Repair: " + str(payload["repairInstruction"]))
    if mode == "review_result" and isinstance(payload.get("receipt"), dict):
        lines.append("Result decision: " + json.dumps(payload["receipt"], ensure_ascii=False, separators=(",", ":")))

    tasks = (
        payload.get("tasks")
        or payload.get("taskBriefs")
        or payload.get("workers")
        or payload.get("items")
        or payload.get("results")
        or []
    )
    if isinstance(tasks, list) and tasks:
        lines.append("Tasks:")
        for item in tasks[:8]:
            if not isinstance(item, dict):
                lines.append(f"- {_short_text(item, 220)}")
                continue
            goal = item.get("taskGoal") or item.get("goal") or item.get("brief") or item.get("summary") or item.get("name")
            target = (
                item.get("targetLabel")
                or item.get("agentName")
                or item.get("target")
                or item.get("worker")
                or item.get("subagent")
                or item.get("member")
                or item.get("family")
                or item.get("targetId")
                or item.get("agentId")
            )
            status = item.get("status") or item.get("state") or item.get("dispatchStatus")
            parts = [
                _short_text(goal, 220),
                f"target={_short_text(target, 80)}" if target else "",
                f"status={_short_text(status, 50)}" if status else "",
            ]
            lines.append("- " + " | ".join(part for part in parts if part))
            if isinstance(item.get("effectiveExecution"), dict):
                from core.engineering_capsule import render_effective_execution_surface
                lines.append(render_effective_execution_surface(item["effectiveExecution"]))
            tool_policy = item.get("toolPolicy") if isinstance(item.get("toolPolicy"), dict) else {}
            policy_mode = str(tool_policy.get("mode") or "").strip().lower()
            if policy_mode == "none":
                lines.append("  Tool authority: none")
            elif policy_mode == "allowlist":
                allowed = ", ".join(_short_text(name, 80) for name in list(tool_policy.get("allowedTools") or []))
                lines.append(f"  Tool authority: {allowed or 'empty allowlist'}")
            result_text = item.get("resultText")
            if result_text:
                lines.append(f"  Exact result: {_short_text(result_text, 1600)}")
            result_summary = item.get("summary") or item.get("compactTranscript")
            if result_summary and str(result_summary) not in {str(goal or ""), str(result_text or "")}:
                lines.append(f"  Result: {_short_text(result_summary, 520)}")
            local_self_check = item.get("localSelfCheck")
            if local_self_check:
                lines.append(f"  Self-check: {_short_text(local_self_check, 360)}")
            artifact_refs = item.get("artifactRefs")
            if isinstance(artifact_refs, list) and artifact_refs:
                rendered_refs = ", ".join(_short_text(ref, 140) for ref in artifact_refs[:6])
                lines.append(f"  Evidence: {rendered_refs}")
            acceptance_hint = item.get("acceptanceHint")
            if acceptance_hint:
                lines.append(f"  Acceptance: {_short_text(acceptance_hint, 300)}")
        if len(tasks) > 8:
            lines.append(f"- … {len(tasks) - 8} more")

    next_action = payload.get("recommendedNextAction") or payload.get("nextAction") or payload.get("recommendedAction")
    if next_action:
        lines.append(f"Next: {_short_text(next_action, 220)}")
    return "\n".join(line for line in lines if line).strip()
def _generic_json_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("results", "items", "sources", "artifacts", "tasks", "events", "messages", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []
def _render_agent_registry_surface(payload: dict[str, Any], raw_ref: str) -> str | None:
    if payload.get("mode") not in {"list", "inspect", "validate"}:
        return None
    lines = ["Agent registry", f"Mode: {payload['mode']}"]
    if payload.get("status") or payload.get("ok") is False:
        lines.append(f"Status: {_short_text(payload.get('status') or 'failed', 100)}")
    if payload.get("summary"):
        lines.append(_short_text(payload["summary"], 500))
    if payload.get("error"):
        lines.append(f"Error: {_short_text(payload['error'], 150)}")
    items = payload.get("items") if isinstance(payload.get("items"), list) else [payload.get("item")]
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("agentId") or "").strip()
        lines.append(f"Agent: {_short_text(name, 160)}")
        for key in ("agentId", "description", "family", "toolMode"):
            if item.get(key):
                lines.append(f"  {key}: {_short_text(item[key], 500)}")
        if isinstance(item.get("enabled"), bool):
            lines.append(f"  enabled: {str(item['enabled']).lower()}")
        model_id = item.get("effectiveModelId") or item.get("modelId")
        lines.append(f"  Model binding: {_short_text(model_id, 200) if model_id else 'inherits default; use validate to resolve'}")
        if payload["mode"] == "validate":
            lines.append(f"  Model readiness: {_short_text(payload.get('status') or 'unknown', 100)}")
        else:
            lines.append("  Model readiness: not validated")
        for key in ("domainTags", "operationCapabilities", "artifactCapabilities"):
            values = item.get(key)
            if isinstance(values, list):
                labels = [_short_text(value, 140) for value in values if isinstance(value, str)]
                if labels:
                    lines.append(f"  {key}: {', '.join(labels)}")
        for binding in item.get("runtimeBindings") or []:
            if not isinstance(binding, dict):
                continue
            groups = [value for value in binding.get("grantGroups") or [] if isinstance(value, str)]
            lines.append(f"  runtimeBindings: {_short_text(binding.get('runtimeKind') or '', 100)}"
                         + (f"; grantGroups={', '.join(_short_text(value, 140) for value in groups)}" if groups else ""))
    if payload.get("mode") == "list":
        lines.append("Next: choose the matching exact name; use agent_broker(mode='inspect', agentName=...) for one entry. Narrow long lists with family.")
    elif payload.get("mode") == "inspect":
        lines.append("Next: use this exact name in task.targetAgentName; agent_broker(mode='validate', agentName=...) resolves model readiness when needed.")
    elif payload.get("nextAction"):
        lines.append(f"Next: {_short_text(payload['nextAction'], 300)}")
    lines.append("Registry bindings describe capabilities; dispatch still applies the task's permissions and tool policy.")
    if raw_ref:
        # Selection evidence is inline. Do not direct a required-delegation
        # turn to an observation reader that is absent from its tool surface.
        lines.append(f"Raw: {raw_ref}")
    return "\n".join(lines)
def _render_generic_json_surface(tool_name: str, payload: Any, raw_ref: str, *, budget: int) -> str | None:
    if not isinstance(payload, (dict, list)):
        return None
    label = _short_text(str(tool_name or "tool_result").replace("_", " "), 80)
    lines = [f"{label} result"]
    if isinstance(payload, dict):
        if tool_name == "write_native_file" and payload.get("ok") is True:
            version = str(payload.get("contentVersion") or "")
            if re.fullmatch(r"sha256:[a-f0-9]{64}", version):
                lines.append(f"Content version: {version}; reuse as expected_version for the next same-actor edit.")
        if payload.get("ok") is False:
            lines.append("Status: failed")
        elif payload.get("status"):
            lines.append(f"Status: {_short_text(payload.get('status'), 90)}")
        elif payload.get("kind"):
            lines.append(f"Kind: {_short_text(payload.get('kind'), 90)}")
        summary = _first_text(
            payload,
            "summary",
            "answer",
            "result",
            "resultPreview",
            "message",
            "text",
            "textPreview",
            "content",
            "output",
            "error",
            limit=1200,
        )
        if summary:
            lines.append("Summary:")
            lines.append(summary)
        verification = payload.get("verification")
        if isinstance(verification, dict):
            verification_bits = []
            for key in ("status", "reason", "summary", "result"):
                if verification.get(key) not in (None, "", [], {}):
                    verification_bits.append(_short_text(verification.get(key), 120))
            if verification_bits:
                lines.append("Verification: " + " | ".join(verification_bits[:3]))
        elif verification not in (None, "", [], {}):
            lines.append(f"Verification: {_short_text(verification, 240)}")
        for key in ("url", "finalUrl", "sourceUrl"):
            if payload.get(key):
                lines.append(f"Source: {_short_text(payload.get(key), 220)}")
                break
    items = _generic_json_items(payload)
    if items:
        lines.append("Items:")
        for item in items[:6]:
            if isinstance(item, dict):
                lines.append(f"- {_source_line(item)}")
            else:
                lines.append(f"- {_short_text(item, 260)}")
        if len(items) > 6:
            lines.append(f"- … {len(items) - 6} more")
    if isinstance(payload, dict):
        next_action = payload.get("recommendedNextAction") or payload.get("nextAction") or payload.get("recommendedAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 220)}")
        detail_tool = payload.get("detailTool")
    else:
        detail_tool = None
    lines.extend(_surface_ref_lines(raw_ref, detail_tool, include_raw=True))
    rendered = "\n".join(line for line in lines if line).strip()
    if not rendered:
        return None
    if len(rendered) > budget:
        return _head_tail_truncate_text(rendered, budget, f"generic JSON surface truncated; rawRef={raw_ref}")
    return rendered
def _skill_header_value(text: str, key: str) -> str:
    pattern = re.compile(rf"(?im)^\s*{re.escape(key)}\s*:\s*(.*)$")
    match = pattern.search(text)
    return str(match.group(1) if match else "").strip()
def _render_skill_relative_file_surface(text: str, raw_ref: str, *, budget: int) -> str:
    header, separator, body = text.partition("\n\n")
    if not separator:
        header = text
        body = ""
    content_marker = "=== FILE CONTENT ==="
    if body.lstrip().startswith(content_marker):
        body = body.lstrip()[len(content_marker):].lstrip("\r\n")
    skill_name = _skill_header_value(header, "Skill Name")
    relative_path = _skill_header_value(header, "Relative Path")
    continuation_api = _skill_header_value(header, "Continuation API")
    read_offset_raw = _skill_header_value(header, "Read Offset")
    total_chars = _skill_header_value(header, "Total Chars")
    execution_boundary = ""
    for line in header.splitlines():
        if line.strip().startswith("Execution Boundary:"):
            execution_boundary = line.strip()
            break
    try:
        read_offset = int(read_offset_raw or "0")
    except ValueError:
        read_offset = 0

    title = "Skill file" + (f": {relative_path}" if relative_path else "")
    prefix_lines = [
        title,
        "Status: loaded via fetch_skill_instructions relative_path.",
        "Contract: preserve this file's original order; it is a continuation of the skill method, not a summary.",
    ]
    if execution_boundary:
        prefix_lines.append(execution_boundary)
    prefix_lines.append("Content:")
    suffix_lines = [
        "Continuation:",
        f"- {continuation_api}" if continuation_api else "- Continue with fetch_skill_instructions(skill_name=..., relative_path=..., offset=<next offset>) when needed.",
        *_surface_ref_lines(raw_ref, include_raw=True),
    ]
    prefix = "\n".join(line for line in prefix_lines if line).strip()
    suffix = "\n".join(line for line in suffix_lines if line).strip()
    full_rendered = "\n".join(part for part in (prefix, body.strip(), suffix) if part).strip()
    if len(full_rendered) <= budget:
        return full_rendered

    reserved = len(prefix) + len(suffix) + 180
    body_budget = max(400, budget - reserved)
    visible_body = body[:body_budget].rstrip()
    next_offset = read_offset + len(visible_body)
    continuation = (
        f"fetch_skill_instructions(skill_name={skill_name!r}, relative_path={relative_path!r}, offset={next_offset})"
        if skill_name and relative_path
        else f"fetch_skill_instructions(skill_name=..., relative_path=..., offset={next_offset})"
    )
    truncated_lines = [
        prefix,
        visible_body,
        (
            f"...[skill relative file truncated at offset {next_offset}"
            + (f" of {total_chars}" if total_chars else "")
            + "; continue the same document with the command below]"
        ),
        "Continuation:",
        f"- {continuation}",
        *_surface_ref_lines(raw_ref, include_raw=True),
    ]
    return "\n".join(line for line in truncated_lines if line).strip()
def _render_skill_instructions_surface(content: str, raw_ref: str, *, budget: int) -> str:
    text = str(content or "").strip()
    if not text:
        return "\n".join(["Skill instructions", *_surface_ref_lines(raw_ref, include_raw=True)])
    if text.startswith("=== SKILL BLOCKED") or text.startswith("=== SKILL APPROVAL REQUIRED"):
        visible = _head_tail_truncate_text(text, budget, f"skill notice truncated; rawRef={raw_ref}") if len(text) > budget else text
        return visible
    if text.startswith("=== SKILL FILE ==="):
        return _render_skill_relative_file_surface(text, raw_ref, budget=budget)
    if text.startswith("=== SKILL SCRIPT RESULT ==="):
        rendered = "\n".join(
            [
                text,
                *_surface_ref_lines(raw_ref, include_raw=True),
            ]
        ).strip()
        if len(rendered) > budget:
            return _head_tail_truncate_text(rendered, budget, f"skill script output truncated; rawRef={raw_ref}")
        return rendered

    skill_name = ""
    for pattern in (
        r"(?im)^\s*Skill Name\s*:\s*(.+)$",
        r"(?im)^\s*name\s*:\s*(.+)$",
        r"fetch_skill_instructions\(skill_name=(['\"])(.+?)\1",
        r"(?im)^\s*Skill ID\s*:\s*(.+)$",
        r"(?im)^\s*Skill\s*:\s*(.+)$",
    ):
        match = re.search(pattern, text)
        if match:
            skill_name = _short_text(match.group(2) if len(match.groups()) >= 2 and match.group(2) else match.group(1), 120)
            break

    def _section(*headings: str) -> str:
        for heading in headings:
            pattern = re.compile(rf"(?im)^===\s*{re.escape(heading)}\s*===$")
            match = pattern.search(text)
            if not match:
                continue
            start = match.end()
            end_match = re.search(r"\n=== [A-Z0-9 _()/-]+ ===", text[start:])
            end = start + end_match.start() if end_match else len(text)
            value = text[start:end].strip()
            if value:
                return value
        return ""

    instructions = _section("INSTRUCTIONS (FULL)", "INSTRUCTIONS FULL", "INSTRUCTIONS SECTION", "INSTRUCTIONS SUMMARY")
    if not instructions:
        instructions = text
    continuation_manifest = _section("CONTINUATION MANIFEST")
    read_offset_raw = _skill_header_value(instructions, "Read Offset") or _skill_header_value(text, "Read Offset")
    total_chars = _skill_header_value(instructions, "Total Chars") or _skill_header_value(text, "Total Chars")
    try:
        read_offset = int(read_offset_raw or "0")
    except ValueError:
        read_offset = 0
    cleaned_lines: list[str] = []
    skip_prefixes = (
        "Skill Root:",
        "Skill Path:",
        "Absolute Path:",
        "Workspace Root:",
        "Project Root:",
        "Directory Structure:",
        "Source Root:",
    )
    for line in instructions.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(prefix) for prefix in skip_prefixes):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()

    lines = ["Skill instructions"]
    lines.append("Use the main SKILL.md instructions below as the method contract; relative paths remain resolved through fetch_skill_instructions.")
    if cleaned:
        lines.append("Instructions:")
        lines.append(cleaned)
    lines.append("Relative path continuation:")
    lines.append("- When the instructions mention a relative Markdown/template/script path, read it with fetch_skill_instructions(skill_name=..., relative_path='...').")
    lines.append('- When SKILL.md instructs you to run a bundled script, use fetch_skill_instructions(mode="run_script", relative_path="scripts/...", script_args=[...]); reading the full script source is optional.')
    base_lines = [*lines, *_surface_ref_lines(raw_ref, include_raw=True)]
    base_rendered = "\n".join(line for line in base_lines if line).strip()
    if len(base_rendered) > budget:
        prefix = "\n".join(line for line in lines if line).strip()
        suffix_probe = "\n".join(
            [
                "...[main SKILL.md truncated; continue this same SKILL.md with the command below before executing the skill]",
                "Continuation:",
                "- fetch_skill_instructions(skill_name=..., detail_level='full', offset=<next offset>)",
                "Execution guard: do not start implementing from a partial SKILL.md; continue reading until the document is complete.",
                *_surface_ref_lines(raw_ref, include_raw=True),
            ]
        ).strip()
        reserved = len(prefix) + len(suffix_probe) + 180
        body_budget = max(420, budget - reserved)
        visible_body = cleaned[:body_budget].rstrip()
        next_offset = read_offset + len(visible_body)
        continuation = (
            f"fetch_skill_instructions(skill_name={skill_name!r}, detail_level='full', offset={next_offset})"
            if skill_name
            else f"fetch_skill_instructions(skill_name=..., detail_level='full', offset={next_offset})"
        )
        truncated_lines = [
            prefix,
            visible_body,
            (
                f"...[main SKILL.md truncated at offset {next_offset}"
                + (f" of {total_chars}" if total_chars else "")
                + "; continue this same SKILL.md before executing the skill]"
            ),
            "Continuation:",
            f"- {continuation}",
            "Execution guard: do not start implementing from a partial SKILL.md; continue reading until the document is complete.",
            *_surface_ref_lines(raw_ref, include_raw=True),
        ]
        return "\n".join(line for line in truncated_lines if line).strip()

    if continuation_manifest:
        manifest_lines = [
            *lines,
            "Continuation manifest (auxiliary, not a replacement for the instructions above):",
            continuation_manifest,
            *_surface_ref_lines(raw_ref, include_raw=True),
        ]
        manifest_rendered = "\n".join(line for line in manifest_lines if line).strip()
        if len(manifest_rendered) <= budget:
            return manifest_rendered
    return base_rendered
def _audit_body_summary(body: str) -> str:
    text = str(body or "").strip()
    if not text:
        return ""
    payload = _parse_json_object(text)
    if not isinstance(payload, dict):
        return _short_text(text, 520)

    parts: list[str] = []
    for key in (
        "summary",
        "message",
        "reason",
        "error",
        "skillName",
        "verdict",
        "effectiveVerdict",
        "posture",
        "confidence",
        "runId",
        "sessionId",
        "ledgerId",
        "ledgerStatus",
        "targetCount",
        "failedTargetCount",
        "workflowCandidateCount",
        "workflowCandidateUpdatedCount",
        "scannedFiles",
        "candidateFiles",
        "skillTrustScore",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            parts.append(f"{key}={_short_text(value, 120)}")

    reasons = payload.get("reasons") or payload.get("failedReasons") or payload.get("warnings")
    if isinstance(reasons, list) and reasons:
        parts.append("reasons=" + "; ".join(_short_text(item, 120) for item in reasons[:3]))

    categories = payload.get("findingCategories")
    if isinstance(categories, list) and categories:
        parts.append("categories=" + ", ".join(_short_text(item, 50) for item in categories[:5]))

    flagged = payload.get("flaggedFiles") or payload.get("failedTargets") or payload.get("touchedRefs")
    if isinstance(flagged, list):
        parts.append(f"items={len(flagged)}")
        examples = []
        for item in flagged[:3]:
            if isinstance(item, dict):
                examples.append(_short_text(item.get("path") or item.get("memoryRef") or item.get("id") or item, 90))
            else:
                examples.append(_short_text(item, 90))
        if examples:
            parts.append("examples=" + "; ".join(examples))

    if not parts:
        for key, value in payload.items():
            if value in (None, "", [], {}) or isinstance(value, (dict, list)):
                continue
            parts.append(f"{_short_text(key, 40)}={_short_text(value, 90)}")
            if len(parts) >= 6:
                break
    return _short_text(" | ".join(parts), 900)
def _render_audit_log_surface(content: str, raw_ref: str, *, budget: int) -> str:
    raw_lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    lines = ["Audit log"]
    if not raw_lines:
        lines.append("No audit logs found matching the criteria.")
        lines.extend(_surface_ref_lines(raw_ref, include_raw=True))
        return "\n".join(lines)

    for raw_line in raw_lines[:8]:
        match = re.match(
            r"^\[(?P<ts>[^\]]+)\]\s+\[(?P<source>[^\]]+)\]\s+(?P<action>.*?)\s+-\s+(?P<status>[^:]+):\s*(?P<body>.*)$",
            raw_line,
        )
        if match:
            summary = _audit_body_summary(match.group("body"))
            prefix = (
                f"- {match.group('ts')} | {match.group('source')} | "
                f"{_short_text(match.group('action'), 80)} | {match.group('status')}"
            )
            lines.append(prefix + (f": {summary}" if summary else ""))
        else:
            lines.append(f"- {_short_text(raw_line, 520)}")
    if len(raw_lines) > 8:
        lines.append(f"- … {len(raw_lines) - 8} more log line(s)")
    lines.extend(_surface_ref_lines(raw_ref, include_raw=True))
    rendered = "\n".join(line for line in lines if line).strip()
    if len(rendered) > budget:
        return _head_tail_truncate_text(rendered, budget, f"audit log surface truncated; rawRef={raw_ref}")
    return rendered
