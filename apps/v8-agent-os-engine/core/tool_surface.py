from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.types import Command


DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS = 60000
MAX_TOOL_OUTPUT_LENGTH = DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS
DEFAULT_CONTEXT_WINDOW_TOKENS = 32000
DEFAULT_OUTPUT_RESERVE_TOKENS = 2048
CONTEXT_SAFETY_BUFFER_RATIO = 0.2
CHARS_PER_TOKEN_ESTIMATE = 4
MIN_TOOL_OUTPUT_BUDGET_CHARS = 1200


@dataclass(slots=True)
class ToolSurfaceEnvelope:
    """Agent-visible summary contract for tool output surfaces."""

    runId: str | None = None
    tool: str = ""
    toolCallId: str | None = None
    runtimeKind: str = "native"
    summary: str = ""
    refs: dict[str, Any] = field(default_factory=dict)
    budget: dict[str, Any] = field(default_factory=dict)
    omitted: dict[str, Any] = field(default_factory=dict)
    nextAction: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value not in (None, "", {}, [])}

TOOL_OUTPUT_TARGET_CHARS = {
    "default": 6000,
    "catalog": 4000,
    "diagnostic": 10000,
    "operation": 2500,
    "research": 24000,
    "web": 16000,
    "skill_instructions": 24000,
}

JSON_PRIORITY_KEYS = (
    "ok",
    "kind",
    "status",
    "summary",
    "runId",
    "traceId",
    "toolCallId",
    "rawRef",
    "summaryRef",
    "researchAnswerPack",
    "recommendedNextAction",
    "selectedPlaybook",
    "selectedPlaybookExecutor",
    "factResolution",
    "laneDecision",
    "candidateAttempts",
    "shortSequenceVerification",
    "verification",
    "artifactIds",
    "artifacts",
    "jobId",
    "providerTaskId",
    "operationKind",
    "modality",
    "providerId",
    "model",
    "modelId",
    "modelRef",
    "qualityStatus",
    "qualityJobId",
    "qualityJobIds",
    "retryReason",
    "fallbackAttempts",
    "policyRejectReason",
    "rawProviderResponseRef",
    "error",
    "exitCode",
    "returnCode",
    "stderr",
    "stderrTail",
    "refs",
    "count",
    "limit",
    "hasMore",
    "cursor",
    "detailTool",
)

COMMAND_TOOL_NAMES = {
    "run_system_command",
    "execute_system_command",
    "command_session_broker",
    "read_background_output",
    "send_background_input",
    "terminate_background_command",
}

WORKER_RESULT_RE = re.compile(
    r"<V8_WORKER_RESULT\b[^>]*>.*?</V8_WORKER_RESULT>",
    re.IGNORECASE | re.DOTALL,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_kind_for_tool(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if name.startswith("creative_media_"):
        return "creative_media"
    if name.startswith("computer_use_"):
        return "computer_use"
    if name.startswith("rpa_"):
        return "rpa"
    if name.startswith("memory_") or name.startswith("mem_"):
        return "memory"
    if name == "web_broker" or name.startswith("web_"):
        return "web"
    if name in {"manage_cron", "manage_hook", "list_processes", "read_audit_log"}:
        return "automation"
    if name == "runtime_broker":
        return "runtime_broker"
    if name == "delegation_broker" or name.startswith("delegation_") or name.startswith("subagent_"):
        return "subagent_swarm"
    if name == "fetch_skill_instructions":
        return "extensions"
    return "native"


def _text_for_token_estimate(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    content = getattr(value, "content", None)
    if content is not None:
        return str(content)
    if isinstance(value, dict):
        return str(value.get("content") or value)
    return str(value)


def _estimate_tokens_from_chars(text: str) -> int:
    return max(0, int(len(text or "") / CHARS_PER_TOKEN_ESTIMATE))


def _request_messages(request: Any) -> list[Any]:
    state = getattr(request, "state", None)
    if isinstance(state, dict):
        messages = state.get("messages")
        if isinstance(messages, list):
            return messages
    input_payload = getattr(request, "input", None)
    if isinstance(input_payload, dict):
        messages = input_payload.get("messages")
        if isinstance(messages, list):
            return messages
    return []


def _nested_config_value(config: Any, *names: str) -> Any:
    if not isinstance(config, dict):
        return None
    for name in names:
        if config.get(name) not in (None, ""):
            return config.get(name)
    configurable = config.get("configurable")
    if isinstance(configurable, dict):
        for name in names:
            if configurable.get(name) not in (None, ""):
                return configurable.get(name)
    metadata = config.get("metadata")
    if isinstance(metadata, dict):
        for name in names:
            if metadata.get(name) not in (None, ""):
                return metadata.get(name)
    return None


def _request_config(request: Any) -> dict[str, Any]:
    config = getattr(request, "config", None)
    return config if isinstance(config, dict) else {}


def _tool_output_kind(tool_name: str) -> str:
    normalized = (tool_name or "").lower()
    if normalized == "fetch_skill_instructions":
        return "skill_instructions"
    if normalized == "web_broker" or normalized.startswith("web_"):
        return "web"
    if normalized == "research_broker" or normalized.startswith("research_"):
        return "research"
    if "catalog" in normalized or "list_" in normalized or normalized.endswith("_list"):
        return "catalog"
    if "diagnostic" in normalized or "capabilities" in normalized or "observe" in normalized:
        return "diagnostic"
    if any(part in normalized for part in ("delete", "update", "write", "run_", "execute", "manage", "broker")):
        return "operation"
    return "default"


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _scaled_tool_target_chars(kind: str, base_target: int, context_window_tokens: int) -> int:
    """Let long-context models see more cleaned evidence without expanding mutating tool noise."""
    if kind == "operation":
        return base_target
    if context_window_tokens >= 1_000_000:
        caps = {
            "skill_instructions": 80_000,
            "research": 80_000,
            "web": 60_000,
            "diagnostic": 24_000,
            "default": 16_000,
            "catalog": 8_000,
        }
    elif context_window_tokens >= 200_000:
        caps = {
            "skill_instructions": 48_000,
            "research": 48_000,
            "web": 32_000,
            "diagnostic": 18_000,
            "default": 12_000,
            "catalog": 6_000,
        }
    else:
        caps = {}
    return max(base_target, int(caps.get(kind, base_target)))


def tool_output_budget_for_request(request: Any, tool_name: str) -> dict[str, Any]:
    config = _request_config(request)
    run_id = _nested_config_value(config, "runId", "run_id", "activeRunId", "active_run_id")
    context_window_tokens = _safe_int(
        _nested_config_value(
            config,
            "contextWindowTokens",
            "modelContextWindowTokens",
            "model_context_window_tokens",
            "context_window_tokens",
        ),
        DEFAULT_CONTEXT_WINDOW_TOKENS,
    )
    output_reserve_tokens = _safe_int(
        _nested_config_value(
            config,
            "reservedOutputTokens",
            "maxOutputTokens",
            "max_tokens",
            "output_reserve_tokens",
        ),
        DEFAULT_OUTPUT_RESERVE_TOKENS,
    )
    hard_max_chars = _safe_int(
        _nested_config_value(
            config,
            "toolOutputHardMaxChars",
            "maxToolOutputChars",
            "tool_output_hard_max_chars",
        ),
        DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS,
    )
    messages = _request_messages(request)
    used_tokens = sum(_estimate_tokens_from_chars(_text_for_token_estimate(item)) for item in messages)
    safety_buffer_tokens = int(context_window_tokens * CONTEXT_SAFETY_BUFFER_RATIO)
    remaining_tokens = max(0, context_window_tokens - used_tokens - output_reserve_tokens - safety_buffer_tokens)
    dynamic_budget_chars = max(MIN_TOOL_OUTPUT_BUDGET_CHARS, remaining_tokens * CHARS_PER_TOKEN_ESTIMATE)
    kind = _tool_output_kind(tool_name)
    base_target_chars = TOOL_OUTPUT_TARGET_CHARS.get(kind, TOOL_OUTPUT_TARGET_CHARS["default"])
    target_chars = _scaled_tool_target_chars(kind, base_target_chars, context_window_tokens)
    agent_visible_budget = max(MIN_TOOL_OUTPUT_BUDGET_CHARS, min(dynamic_budget_chars, target_chars, hard_max_chars))
    return {
        "budgetSource": "dynamic_context_budget",
        "runId": run_id,
        "agentVisibleBudget": int(agent_visible_budget),
        "dynamicBudgetChars": int(dynamic_budget_chars),
        "hardMaxChars": int(hard_max_chars),
        "targetChars": int(target_chars),
        "toolOutputKind": kind,
        "contextWindowTokens": int(context_window_tokens),
        "estimatedPromptTokens": int(used_tokens),
        "reservedOutputTokens": int(output_reserve_tokens),
        "safetyBufferTokens": int(safety_buffer_tokens),
        "baseTargetChars": int(base_target_chars),
    }


def _line_safe_slice(text: str, limit: int, *, tail: bool = False) -> str:
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text
    if tail:
        chunk = text[-limit:]
        newline = chunk.find("\n")
        return chunk[newline + 1 :] if newline >= 0 else chunk
    chunk = text[:limit]
    newline = chunk.rfind("\n")
    if newline > max(80, limit // 2):
        return chunk[:newline]
    sentence = max(chunk.rfind("。"), chunk.rfind("."), chunk.rfind("!"), chunk.rfind("?"))
    if sentence > max(80, limit // 2):
        return chunk[: sentence + 1]
    return chunk.rstrip()


def _head_tail_truncate_text(text: str, budget: int, notice: str) -> str:
    if len(text) <= budget:
        return text
    ref_match = re.search(r"rawRef=(toolobs://[A-Za-z0-9_.:/?=&%+-]+)", notice or "")
    preserved_ref = ref_match.group(1).rstrip(".,);]") if ref_match else ""
    preserved_block = (
        f"\nDetail: tool_observation_detail(raw_ref='{preserved_ref}')\nRaw: {preserved_ref}"
        if preserved_ref
        else ""
    )
    marker = f"\n\n...[{notice}]...\n\n"
    available = max(0, budget - len(marker) - len(preserved_block))
    if available <= 0:
        return (marker + preserved_block)[-budget:]
    head_limit = max(1, int(available * 0.3))
    tail_limit = max(1, available - head_limit)
    return f"{_line_safe_slice(text, head_limit)}{marker}{_line_safe_slice(text, tail_limit, tail=True)}{preserved_block}"


def _truncate_worker_result_preserving_marker(text: str, budget: int, notice: str) -> str | None:
    match = WORKER_RESULT_RE.search(text or "")
    if not match:
        return None
    marker = match.group(0)
    if len(text) <= budget:
        return text
    marker_notice = f"\n\n...[{notice}; V8_WORKER_RESULT preserved]...\n\n"
    context_budget = max(0, budget - len(marker) - len(marker_notice))
    if context_budget <= 0:
        return marker
    before = _line_safe_slice(text[: match.start()], int(context_budget * 0.3))
    after = _line_safe_slice(text[match.end() :], context_budget - len(before), tail=True)
    return f"{before}{marker_notice}{marker}{marker_notice}{after}".strip()


def _compact_json_value(value: Any, *, depth: int = 0, text_limit: int = 700) -> Any:
    if depth > 3:
        return _text_for_token_estimate(value)[:text_limit]
    if isinstance(value, str):
        if len(value) <= text_limit:
            return value
        return _head_tail_truncate_text(value, text_limit, f"field truncated; original length {len(value)} chars")
    if isinstance(value, list):
        limit = 5 if depth else 8
        items = [_compact_json_value(item, depth=depth + 1, text_limit=max(220, text_limit // 2)) for item in value[:limit]]
        if len(value) > limit:
            items.append({"omittedItems": len(value) - limit})
        return items
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        keys = [key for key in JSON_PRIORITY_KEYS if key in value]
        keys.extend([key for key in value.keys() if key not in keys][: max(0, 8 - len(keys))])
        for key in keys:
            compact[key] = _compact_json_value(value.get(key), depth=depth + 1, text_limit=max(220, text_limit // 2))
        omitted = max(0, len(value) - len(keys))
        if omitted:
            compact["omittedFields"] = omitted
        return compact
    return value


def _tool_surface_payload(
    *,
    tool_name: str,
    tool_call_id: str | None,
    runtime_kind: str,
    raw_ref: str | None,
    budget_meta: dict[str, Any],
    was_truncated: bool,
    strategy: str,
    omitted_chars: int = 0,
    summary: str | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    if raw_ref:
        compact["rawRef"] = raw_ref
        compact["detailTool"] = f"tool_observation_detail(raw_ref='{raw_ref}')"
    if was_truncated:
        compact["truncated"] = True
    if omitted_chars:
        compact["omittedChars"] = max(0, int(omitted_chars or 0))
    if next_action:
        compact["nextAction"] = next_action
    elif raw_ref and was_truncated:
        compact["nextAction"] = "Use detailTool only if the compact output is insufficient."
    return compact


def _command_json_payload(text: str) -> dict[str, Any] | None:
    stripped = str(text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _tool_json_payload(text: str) -> dict[str, Any] | None:
    stripped = str(text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _tool_json_any_payload(text: str) -> Any | None:
    stripped = str(text or "").strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        payload = json.loads(stripped)
    except Exception:
        return None
    return payload if isinstance(payload, (dict, list)) else None


def _short_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\s+", " ", text)
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def _content_excerpt(value: Any, limit: int = 1600) -> str:
    """Preserve readable source/content shape while bounding long text."""
    text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    # Trim excessive blank lines, but keep paragraphs/tables/lists/code-ish shape.
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(text) <= limit:
        return text
    return _head_tail_truncate_text(text, limit, f"content excerpt truncated; original length {len(text)} chars")


def _short_id(value: Any, *, prefix: int = 12) -> str:
    text = _short_text(value, 80)
    if len(text) <= prefix + 4:
        return text
    return text[:prefix] + "…"


def _yes_no(value: Any) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value in (None, ""):
        return "unknown"
    return _short_text(value, 40)


def _status_counts_line(counts: Any) -> str:
    if not isinstance(counts, dict) or not counts:
        return ""
    parts = [f"{_short_text(key, 32)}={value}" for key, value in counts.items() if value not in (None, "", [], {})]
    return ", ".join(parts[:8])


def _surface_ref_lines(raw_ref: str, detail_tool: Any = None, *, include_raw: bool = True) -> list[str]:
    lines: list[str] = []
    detail = _short_text(detail_tool, 220)
    if detail:
        lines.append(f"Detail: {detail}")
    elif raw_ref and include_raw:
        lines.append(f"Detail: tool_observation_detail(raw_ref='{raw_ref}')")
    if raw_ref and include_raw:
        lines.append(f"Raw: {raw_ref}")
    return lines


def _first_text(payload: dict[str, Any], *keys: str, limit: int = 800) -> str:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            if isinstance(value, (dict, list)):
                continue
            return _short_text(value, limit)
    return ""


def _source_line(item: dict[str, Any], *, title_limit: int = 120, url_limit: int = 190) -> str:
    title = item.get("title") or item.get("sourceTitle") or item.get("name") or item.get("host") or item.get("url")
    url = item.get("url") or item.get("href") or item.get("finalUrl") or item.get("sourceUrl")
    snippet = (
        item.get("snippet")
        or item.get("summary")
        or item.get("text")
        or item.get("textPreview")
        or item.get("preview")
        or item.get("output")
    )
    parts = []
    if title:
        parts.append(_short_text(title, title_limit))
    if snippet:
        parts.append(_short_text(snippet, 220))
    if parts:
        line = " - ".join(parts)
    else:
        primitive = next((value for value in item.values() if isinstance(value, (str, int, float, bool))), "")
        line = _short_text(primitive, title_limit) if primitive not in (None, "") else "item"
    if url:
        line = f"{line}: {_short_text(url, url_limit)}"
    return line


def _render_runtime_broker_surface(payload: dict[str, Any], raw_ref: str) -> str:
    mode = _short_text(payload.get("mode") or "status", 40)
    lines = [f"Runtime route menu ({mode})"]
    active = payload.get("activeGrants") or payload.get("grants") or payload.get("runtimeToolGrants") or []
    if isinstance(active, list) and active:
        names = []
        for item in active[:8]:
            if isinstance(item, dict):
                names.append(str(item.get("group") or item.get("tool_group") or item.get("name") or "").strip())
            else:
                names.append(str(item).strip())
        lines.append("Active grants: " + ", ".join(name for name in names if name)[:240])
    else:
        lines.append("Active grants: none")
    groups = payload.get("availableGroups") or payload.get("groups") or []
    if isinstance(groups, list) and groups:
        lines.append("Candidate routes:")
        for group in groups[:10]:
            if isinstance(group, dict):
                name = group.get("group") or group.get("name")
                kind = group.get("kind") or group.get("runtimeKind")
                label = group.get("label") or group.get("summary")
                suffix = f" ({kind})" if kind else ""
                desc = f" - {_short_text(label, 80)}" if label else ""
                lines.append(f"- {_short_text(name, 80)}{suffix}{desc}")
            else:
                lines.append(f"- {_short_text(group, 100)}")
        if len(groups) > 10:
            lines.append(f"- … {len(groups) - 10} more; use catalog detail")
    omitted = payload.get("omitted")
    if isinstance(omitted, dict) and omitted.get("availableGroups"):
        lines.append(f"Catalog compacted: {omitted.get('availableGroups')} group(s) hidden; use catalog/detail only when needed.")
    changed = payload.get("changed") or payload.get("grant") or payload.get("revoked")
    if changed:
        lines.append(f"Change: {_short_text(changed, 160)}")
    next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
    if next_action:
        lines.append(f"Next: {_short_text(next_action, 180)}")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()


def _render_workspace_broker_surface(payload: dict[str, Any], raw_ref: str) -> str:
    lines = ["Workspace inventory"]
    root = payload.get("workspaceRoot") or payload.get("activeWorkspaceRoot")
    inspected = payload.get("inspectedPath") or payload.get("path")
    if root:
        lines.append(f"Root: {_short_text(root, 180)}")
    if inspected and inspected != root:
        lines.append(f"Inspected: {_short_text(inspected, 180)}")
    token = payload.get("token") or payload.get("inventoryToken")
    facts = []
    if "nonEmpty" in payload:
        facts.append(f"nonEmpty={_yes_no(payload.get('nonEmpty'))}")
    if payload.get("itemCount") not in (None, ""):
        facts.append(f"items={payload.get('itemCount')}")
    if payload.get("projectMarkerCount") not in (None, ""):
        facts.append(f"projectMarkers={payload.get('projectMarkerCount')}")
    if token:
        facts.append(f"token={_short_id(token, prefix=16)}")
    if facts:
        lines.append("State: " + " | ".join(facts))
    top_dirs = payload.get("topDirs")
    if isinstance(top_dirs, list) and top_dirs:
        lines.append("Top dirs: " + ", ".join(_short_text(item, 40) for item in top_dirs[:6]))
    markers = payload.get("projectMarkers")
    if isinstance(markers, list) and markers:
        lines.append("Project markers:")
        for marker in markers[:3]:
            if isinstance(marker, dict):
                lines.append(f"- {_short_text(marker.get('path'), 120)} ({_short_text(marker.get('kind'), 40)})")
            else:
                lines.append(f"- {_short_text(marker, 140)}")
        if len(markers) > 3:
            lines.append(f"- … {len(markers) - 3} more")
    conflicts = payload.get("conflicts") or payload.get("potentialConflicts")
    if isinstance(conflicts, list) and conflicts:
        lines.append("Conflicts:")
        for item in conflicts[:3]:
            lines.append(f"- {_short_text(item, 160)}")
    next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
    if next_action:
        lines.append(f"Next: {_short_text(next_action, 220)}")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()


def _render_research_broker_surface(payload: dict[str, Any], raw_ref: str, *, budget: int) -> str | None:
    mode = str(payload.get("mode") or "").strip()
    kind = str(payload.get("kind") or "").strip()
    if kind == "research_evidence_bundle":
        answer_pack = payload.get("researchAnswerPack") if isinstance(payload.get("researchAnswerPack"), dict) else {}
        pack = payload.get("finalExperiencePack") or payload.get("researchResult") or {}
        if not isinstance(pack, dict):
            pack = {}
        lines = ["Research result pack"]
        lines.append("Agent: Web Research Architect")
        question = pack.get("question") or payload.get("question")
        if question:
            lines.append(f"Question: {_short_text(question, 220)}")
        answer = answer_pack.get("answer") or pack.get("researchResult") or pack.get("answer")
        if answer:
            lines.append("Result:")
            lines.append(_content_excerpt(answer, max(1200, min(4800, budget // 3))))
        else:
            lines.append("Result: refresh required; no reliable source-backed answer was synthesized.")
        score = answer_pack.get("score") if isinstance(answer_pack.get("score"), dict) else {}
        if score:
            lines.append(f"Score: {_short_text(score.get('label') or score, 220)}")
        limitations = answer_pack.get("limitations") or pack.get("limitations") or payload.get("missingEvidence")
        if isinstance(limitations, list) and limitations:
            lines.append("Limitations / refresh notes:")
            for item in limitations[:4]:
                lines.append(f"- {_short_text(item, 220)}")
        rejected = answer_pack.get("rejectedEvidence") or payload.get("rejectedSources")
        if isinstance(rejected, list) and rejected:
            lines.append("Rejected evidence:")
            for item in rejected[:3]:
                if isinstance(item, dict):
                    title = item.get("title") or item.get("url") or item.get("sourceId")
                    reason = item.get("reason") or item.get("rejectedReason")
                    lines.append(f"- {_short_text(title, 110)}: {_short_text(reason, 140)}")
                else:
                    lines.append(f"- {_short_text(item, 220)}")
        findings = pack.get("keyFindings")
        if isinstance(findings, list) and findings:
            lines.append("Key findings:")
            for item in findings[:5]:
                if isinstance(item, dict):
                    claim = item.get("claim") or item.get("summary")
                    source_title = item.get("sourceTitle") or item.get("title")
                    suffix = f" ({_short_text(source_title, 90)})" if source_title else ""
                    lines.append(f"- {_content_excerpt(claim, 360)}{suffix}")
                else:
                    lines.append(f"- {_content_excerpt(item, 360)}")
        sources = answer_pack.get("sources") or pack.get("sourceUrls") or payload.get("sourceMatrix")
        if isinstance(sources, list) and sources:
            lines.append("Sources:")
            for item in sources[:8]:
                if not isinstance(item, dict):
                    continue
                url = item.get("url")
                if not url:
                    continue
                title = item.get("title") or item.get("sourceTitle") or item.get("host") or url
                lines.append(f"- {_short_text(title, 110)}: {_short_text(url, 180)}")
        next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 160)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if mode != "plan" and kind != "research_plan":
        return None
    lines = ["Research plan"]
    if payload.get("question"):
        lines.append(f"Question: {_short_text(payload.get('question'), 220)}")
    if payload.get("researchIntent"):
        lines.append(f"Intent: {_short_text(payload.get('researchIntent'), 160)}")
    policy = payload.get("experienceFirstPolicy")
    if isinstance(policy, dict):
        lines.append(f"Experience first: {_short_text(policy.get('summary') or policy.get('searchTool'), 180)}")
    limits = payload.get("limits")
    if isinstance(limits, dict):
        requested = limits.get("requestedMaxShards")
        effective = limits.get("effectiveMaxShards")
        rounds = limits.get("effectiveMaxRounds")
        lines.append(f"Shards: {effective or requested or '?'} effective; rounds={rounds or '?'}")
    shards = payload.get("shards")
    if isinstance(shards, list) and shards:
        lines.append("Shard briefs:")
        for shard in shards[:8]:
            if not isinstance(shard, dict):
                continue
            shard_id = shard.get("shardId") or shard.get("id")
            kind_text = shard.get("kind")
            query = shard.get("query")
            reason = shard.get("reason")
            lines.append(f"- {_short_id(shard_id, prefix=14)} [{_short_text(kind_text, 32)}]: {_short_text(query, 150)} ({_short_text(reason, 50)})")
        if len(shards) > 8:
            lines.append(f"- … {len(shards) - 8} more")
    next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
    if next_action:
        lines.append(f"Next: {_short_text(next_action, 160)}")
    lines.append("Omitted: shardDefaults, limits, source catalog, raw search config.")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()


def _render_computer_use_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    if tool_name == "computer_use_resolve_execution_route":
        lines = ["Computer Use route"]
        route = payload.get("recommendedMode") or payload.get("executionReadyMode")
        tool = payload.get("recommendedTool")
        action = payload.get("recommendedAction")
        if route or tool:
            lines.append(f"Recommended: {_short_text(route, 60)} -> {_short_text(tool, 80)}")
        if action:
            lines.append(f"Action: {_short_text(action, 100)}")
        match = payload.get("recommendedMatch")
        if isinstance(match, dict):
            lines.append(
                "Best match: "
                + " | ".join(
                    part
                    for part in (
                        _short_text(match.get("id"), 90),
                        _short_text(match.get("name"), 90),
                        f"score={match.get('score')}" if match.get("score") not in (None, "") else "",
                        f"confidence={match.get('confidence')}" if match.get("confidence") not in (None, "") else "",
                    )
                    if part
                )
            )
        missing = payload.get("missingVariables") or payload.get("missingRequiredVariables")
        if isinstance(missing, list) and missing:
            lines.append("Missing variables: " + ", ".join(_short_text(item, 50) for item in missing[:8]))
        else:
            lines.append("Missing variables: none")
        summary = payload.get("summary")
        if isinstance(summary, dict):
            bits = []
            for key in ("templateCount", "draftCount", "bestScore", "bestConfidence", "requiresLearning"):
                if summary.get(key) not in (None, ""):
                    bits.append(f"{key}={summary.get(key)}")
            if bits:
                lines.append("Signals: " + " | ".join(bits))
        next_action = payload.get("recommendedToolSummary") or payload.get("recommendedNextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name == "computer_use_list_apps":
        apps = payload.get("apps")
        if not isinstance(apps, list):
            return None
        lines = [f"Computer Use apps (showing {min(len(apps), 6)} of {payload.get('count') or len(apps)})"]
        for app in apps[:6]:
            if not isinstance(app, dict):
                continue
            aliases = app.get("aliases") if isinstance(app.get("aliases"), list) else []
            alias_text = ", ".join(_short_text(alias, 28) for alias in aliases[:2])
            state = []
            if app.get("isRunning"):
                state.append("running")
            if app.get("launchable"):
                state.append("launchable")
            title = app.get("topWindowTitle") or app.get("displayName")
            suffix = f" | aliases: {alias_text}" if alias_text else ""
            lines.append(f"- {_short_text(app.get('appId'), 48)}: {_short_text(title, 100)} ({', '.join(state) or 'unknown'}){suffix}")
        if len(apps) > 6:
            lines.append(f"- … {len(apps) - 6} more; use detail for full windows/aliases")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name in {"computer_use_list_muscle_memories", "computer_use_lookup_muscle_memory"}:
        memories = payload.get("memories") or payload.get("matches") or payload.get("items") or []
        if not isinstance(memories, list):
            memories = []
        lines = ["Computer Use muscle memory"]
        summary = payload.get("summary")
        if isinstance(summary, dict):
            bits = [f"{key}={summary.get(key)}" for key in ("count", "bestScore", "bestConfidence") if summary.get(key) not in (None, "")]
            if bits:
                lines.append("Signals: " + " | ".join(bits))
        if memories:
            lines.append("Matches:")
            for item in memories[:5]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    "- "
                    + " | ".join(
                        part
                        for part in (
                            _short_text(item.get("id") or item.get("memoryId"), 80),
                            _short_text(item.get("name") or item.get("goal"), 100),
                            _short_text(item.get("routeAction") or item.get("action"), 60),
                            f"confidence={item.get('confidence')}" if item.get("confidence") not in (None, "") else "",
                        )
                        if part
                    )
                )
        else:
            lines.append("Matches: none")
        next_action = payload.get("recommendedNextAction") or payload.get("recommendedAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name == "computer_use_desktop_capabilities":
        lines = ["Desktop capability snapshot"]
        host = payload.get("currentHost")
        if isinstance(host, dict):
            counts = _status_counts_line(host.get("statusCounts"))
            lines.append(f"Host: {_short_text(host.get('platform'), 50)}" + (f" | {counts}" if counts else ""))
        driver = payload.get("driverHealth")
        if isinstance(driver, dict):
            available = [key for key, value in driver.items() if isinstance(value, dict) and value.get("available")]
            missing = [key for key, value in driver.items() if isinstance(value, dict) and value.get("available") is False]
            if available:
                lines.append("Available: " + ", ".join(_short_text(item, 28) for item in available[:10]))
            if missing:
                lines.append("Blocking gaps: " + ", ".join(_short_text(item, 28) for item in missing[:8]))
        browser = payload.get("browser")
        if isinstance(browser, dict):
            lines.append(f"Browser: enabled={_yes_no(browser.get('enabled'))}; provider={_short_text(browser.get('provider'), 60)}")
        gaps = payload.get("knownGaps")
        if isinstance(gaps, list) and gaps:
            lines.append("Known gaps:")
            for gap in gaps[:3]:
                if isinstance(gap, dict):
                    lines.append(f"- {_short_text(gap.get('code'), 60)}: {_short_text(gap.get('summary'), 130)}")
        next_action = payload.get("recommendedNextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name == "computer_use_list_primitives":
        lines = ["Computer Use primitives"]
        summary = payload.get("summary")
        if isinstance(summary, dict):
            bits = []
            for key in ("primitiveCount", "categoryCount", "promotionEligibleCount"):
                if summary.get(key) not in (None, ""):
                    bits.append(f"{key}={summary.get(key)}")
            if bits:
                lines.append("Summary: " + " | ".join(bits))
            categories = summary.get("categories")
            if isinstance(categories, list) and categories:
                lines.append("Categories: " + ", ".join(_short_text(item, 24) for item in categories[:8] if not isinstance(item, dict)))
        primitives = payload.get("primitives") or payload.get("items") or []
        if isinstance(primitives, list) and primitives:
            lines.append("Actions:")
            for item in primitives[:8]:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("primitive") or item.get("action")
                    required = item.get("required") or item.get("requiredArgs") or item.get("parameters")
                    req = ""
                    if isinstance(required, list) and required:
                        req = " required=" + ",".join(_short_text(part, 20) for part in required[:4])
                    lines.append(f"- {_short_text(name, 80)}{req}")
        next_action = payload.get("recommendedNextAction") or payload.get("detailTool")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name in {"computer_use_observe", "computer_use_observe_scene", "computer_use_list_windows", "computer_use_find_element"}:
        lines = [f"Computer Use observation: {tool_name.replace('computer_use_', '')}"]
        for key in ("summary", "status", "state", "error"):
            if payload.get(key):
                lines.append(f"{key}: {_short_text(payload.get(key), 180)}")
        candidates = payload.get("candidates") or payload.get("elements") or payload.get("windows") or []
        if isinstance(candidates, list) and candidates:
            candidate_lines: list[str] = []
            for item in candidates[:5]:
                if isinstance(item, dict):
                    role_label = item.get("role")
                    if isinstance(role_label, str) and role_label.strip().lower() in {
                        "pane",
                        "group",
                        "window",
                        "document",
                        "section",
                        "generic",
                    }:
                        role_label = None
                    label = (
                        item.get("name")
                        or item.get("title")
                        or item.get("text")
                        or item.get("label")
                        or role_label
                        or item.get("className")
                        or item.get("id")
                        or item.get("elementId")
                    )
                    if label in (None, "", [], {}):
                        continue
                    confidence = item.get("confidence") or item.get("score")
                    suffix = f" confidence={confidence}" if confidence not in (None, "") else ""
                    candidate_lines.append(f"- {_short_text(label, 120)}{suffix}")
                else:
                    rendered = _short_text(item, 140)
                    if rendered:
                        candidate_lines.append(f"- {rendered}")
            if candidate_lines:
                lines.append("Top candidates:")
                lines.extend(candidate_lines)
            else:
                lines.append("Top candidates: none with actionable labels; use detail/rawRef if visual context is required.")
        next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 180)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()
    return None


def _render_creative_media_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    if tool_name == "creative_media_catalog":
        lines = ["Creative Media catalog"]
        if payload.get("summary"):
            lines.append(f"Summary: {_short_text(payload.get('summary'), 180)}")
        counts = []
        for key in ("modalityCount", "executableCandidateCount"):
            if payload.get(key) not in (None, ""):
                counts.append(f"{key}={payload.get(key)}")
        if counts:
            lines.append("Counts: " + " | ".join(counts))
        modalities = payload.get("modalities")
        if isinstance(modalities, list) and modalities:
            lines.append("Modalities:")
            for item in modalities[:8]:
                if not isinstance(item, dict):
                    continue
                lines.append(
                    f"- {_short_text(item.get('modality'), 40)}: providers={item.get('providerCount', '?')}, executable={item.get('executableCount', '?')}"
                )
        reminder = payload.get("catalogOnlyReminder")
        if reminder:
            lines.append(f"Reminder: {_short_text(reminder, 160)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name == "creative_media_resolutions":
        lines = ["Creative Media resolutions"]
        ratios = payload.get("ratios")
        if isinstance(ratios, list) and ratios:
            lines.append("Ratios: " + ", ".join(_short_text(item, 16) for item in ratios[:12]))
        image_presets = payload.get("imagePresets")
        if isinstance(image_presets, dict) and image_presets:
            lines.append("Image presets: " + ", ".join(_short_text(key, 24) for key in list(image_presets.keys())[:8]))
        video_presets = payload.get("videoPresets")
        if isinstance(video_presets, dict) and video_presets:
            lines.append("Video presets: " + ", ".join(_short_text(key, 24) for key in list(video_presets.keys())[:8]))
        if payload.get("summary"):
            lines.append(f"Summary: {_short_text(payload.get('summary'), 160)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name.startswith("creative_media_get_") or tool_name == "creative_media_job_artifacts":
        record_key = next(
            (
                key
                for key in (
                    "job",
                    "recipe",
                    "render",
                    "editPlan",
                    "qualityJob",
                    "characterBible",
                    "keyframe",
                )
                if isinstance(payload.get(key), dict)
            ),
            "",
        )
        record = payload.get(record_key) if record_key else payload
        if not isinstance(record, dict):
            return None
        title = record_key or tool_name.replace("creative_media_", "")
        lines = [f"Creative Media {title}"]
        ident = (
            record.get("jobId")
            or record.get("recipeId")
            or record.get("renderJobId")
            or record.get("planId")
            or record.get("qualityJobId")
            or record.get("assetId")
            or payload.get("jobId")
        )
        if ident:
            lines.append(f"Id: {_short_text(ident, 120)}")
        fields = []
        for key in ("status", "modality", "operationKind", "providerId", "adapter", "model", "workspaceId", "projectId"):
            if record.get(key) not in (None, "", [], {}):
                fields.append(f"{key}={_short_text(record.get(key), 70)}")
        if fields:
            lines.append("State: " + " | ".join(fields[:8]))
        error = record.get("error") or payload.get("error")
        if error:
            lines.append(f"Error: {_short_text(error, 180)}")
        artifacts = record.get("artifacts") or payload.get("artifacts") or []
        artifact_ids = record.get("artifactIds") or payload.get("artifactIds") or []
        artifact_count = (
            record.get("artifactCount")
            if record.get("artifactCount") not in (None, "")
            else len(artifacts)
            if isinstance(artifacts, list) and artifacts
            else len(artifact_ids)
            if isinstance(artifact_ids, list)
            else None
        )
        if artifact_count not in (None, ""):
            lines.append(f"Artifacts: {artifact_count}")
        if isinstance(artifacts, list) and artifacts:
            for artifact in artifacts[:3]:
                if isinstance(artifact, dict):
                    lines.append(
                        f"- {_short_id(artifact.get('artifactId'), prefix=14)} | {_short_text(artifact.get('kind'), 30)} | {_short_text(artifact.get('title'), 90)}"
                    )
        omitted = []
        for key in ("sourcePath", "contentUrl", "previewUrl", "providerResponse", "stderrTail", "fullRequest", "timeline"):
            if key in record or key in payload:
                omitted.append(key)
        if omitted:
            lines.append("Omitted: " + ", ".join(omitted[:8]))
        detail = record.get("detailTool") or payload.get("detailTool")
        lines.extend(_surface_ref_lines(raw_ref, detail, include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    list_keys = (
        ("creative_media_list_jobs", "jobs", "jobId", "creative_media_get_job(job_id=...)"),
        ("creative_media_list_assets", "assets", "assetId", "Use related creative_media_get_* tools for full asset details"),
        ("creative_media_list_renders", "renders", "renderJobId", "creative_media_get_render(render_job_id=...)"),
        ("creative_media_list_edit_plans", "editPlans", "planId", "creative_media_get_edit_plan(plan_id=...)"),
        ("creative_media_list_recipes", "recipes", "recipeId", "creative_media_get_recipe(recipe_id=...)"),
        ("creative_media_list_character_bibles", "characterBibles", "characterBibleId", "creative_media_get_character_bible(character_bible_id=...)"),
        ("creative_media_list_keyframes", "keyframes", "keyframeId", "creative_media_get_keyframe(keyframe_id=...)"),
        ("creative_media_list_quality_jobs", "qualityJobs", "qualityJobId", "creative_media_get_quality_job(quality_job_id=...)"),
        ("creative_media_safety_events", "events", "eventId", "Use rawRef for safety event detail"),
        ("creative_media_cost_ledger", "entries", "id", "Use rawRef for ledger detail"),
    )
    match = next((item for item in list_keys if item[0] == tool_name), None)
    if not match:
        return None
    _, key, id_key, fallback_detail = match
    items = payload.get(key)
    if not isinstance(items, list):
        items = []
    lines = [f"Creative Media {key} (showing {min(len(items), 3)} of {payload.get('count') or len(items)})"]
    counts = _status_counts_line(payload.get("statusCounts"))
    if counts:
        lines.append(f"Status: {counts}")
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        ident = item.get(id_key) or item.get("jobId") or item.get("assetId") or item.get("renderJobId") or item.get("planId")
        label = item.get("title") or item.get("operationKind") or item.get("role") or item.get("modality") or item.get("status")
        status = item.get("status") or item.get("qualityStatus")
        provider = item.get("providerId") or item.get("adapter")
        model = item.get("model") or item.get("modelId")
        error = item.get("error")
        fields = [
            _short_id(ident, prefix=14),
            _short_text(label, 70),
            _short_text(status, 40),
            _short_text(provider, 60),
            _short_text(model, 60),
        ]
        line = "- " + " | ".join(part for part in fields if part)
        if error:
            line += f" | error={_short_text(error, 90)}"
        lines.append(line)
    if len(items) > 3:
        lines.append(f"- … {len(items) - 3} more; use detail for a single item")
    if payload.get("hasMore"):
        lines.append("Has more: yes")
    detail = payload.get("detailTool") or fallback_detail
    lines.extend(_surface_ref_lines(raw_ref, detail, include_raw=True))
    return "\n".join(line for line in lines if line).strip()


def _render_rpa_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    if tool_name != "rpa_list_robot_scripts":
        return None
    scripts = payload.get("scripts")
    if not isinstance(scripts, list):
        scripts = []
    lines = [f"RPA robot scripts (showing {min(len(scripts), 5)} of {payload.get('count') or len(scripts)})"]
    for script in scripts[:5]:
        if not isinstance(script, dict):
            continue
        bits = [
            _short_text(script.get("name"), 90),
            f"size={script.get('size')}" if script.get("size") not in (None, "") else "",
            f"updated={_short_text(script.get('updatedAt'), 40)}" if script.get("updatedAt") else "",
        ]
        lines.append("- " + " | ".join(bit for bit in bits if bit))
    if not scripts:
        lines.append("No robot scripts found.")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()


def _render_native_json_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    if tool_name not in {"read_native_file", "grep_search"}:
        return None
    if payload.get("ok") is not False and not payload.get("error"):
        return None
    label = "Read native file" if tool_name == "read_native_file" else "Grep search"
    lines = [label]
    if payload.get("summary"):
        lines.append(f"Summary: {_short_text(payload.get('summary'), 220)}")
    if payload.get("error"):
        lines.append(f"Error: {_short_text(payload.get('error'), 100)}")
    for key in ("inputPath", "resolvedPath", "path"):
        if payload.get(key):
            lines.append(f"{key}: {_short_text(payload.get(key), 180)}")
    next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
    if next_action:
        lines.append(f"Next: {_short_text(next_action, 220)}")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()


def _render_memory_surface(tool_name: str, payload: dict[str, Any], raw_ref: str) -> str | None:
    if tool_name == "memory_broker":
        mode = str(payload.get("mode") or "").strip() or "recall"
        lines = [f"Memory broker: {mode}"]
        if payload.get("summary"):
            lines.append(f"Summary: {_short_text(payload.get('summary'), 220)}")
        if payload.get("query"):
            lines.append(f"Query: {_short_text(payload.get('query'), 160)}")
        if payload.get("scope"):
            lines.append(f"Scope: {_short_text(payload.get('scope'), 80)}")

        packs = payload.get("evidencePacks")
        if isinstance(packs, list) and packs:
            lines.append("Evidence packs:")
            for pack in packs[:3]:
                if not isinstance(pack, dict):
                    continue
                domain = pack.get("sourceDomain") or pack.get("domain")
                confidence = pack.get("confidence")
                header_bits = [_short_text(domain, 80)] if domain else ["unknown"]
                if confidence not in (None, "", [], {}):
                    header_bits.append(f"confidence={_short_text(confidence, 60)}")
                lines.append(f"- {' | '.join(bit for bit in header_bits if bit)}")
                why = pack.get("whySelected")
                if why:
                    lines.append(f"  Why: {_short_text(why, 240)}")
                selected = pack.get("selectedEvidence")
                if isinstance(selected, list) and selected:
                    lines.append("  Selected evidence:")
                    for item in selected[:3]:
                        if not isinstance(item, dict):
                            lines.append(f"  - {_content_excerpt(item, 300)}")
                            continue
                        title = item.get("title") or item.get("id") or item.get("memoryRef") or item.get("sourceRef")
                        answer = (
                            item.get("answer")
                            or item.get("researchResult")
                            or item.get("text")
                            or item.get("summary")
                            or item.get("fact")
                            or item.get("claim")
                        )
                        prefix = f"{_short_text(title, 90)}: " if title and title != answer else ""
                        line = _content_excerpt(answer, 420) if answer else _source_line(item)
                        lines.append(f"  - {prefix}{line}")
                        claims = item.get("claimDigest") or item.get("claims")
                        if isinstance(claims, list) and claims:
                            for claim in claims[:2]:
                                claim_text = claim.get("claim") if isinstance(claim, dict) else claim
                                if claim_text:
                                    lines.append(f"    claim: {_content_excerpt(claim_text, 240)}")
                        sources = item.get("sources") or item.get("sourceRefs") or item.get("sourceUrls")
                        if isinstance(sources, list) and sources:
                            rendered_sources = []
                            for source in sources[:3]:
                                if isinstance(source, dict):
                                    url = source.get("url") or source.get("sourceUrl")
                                    title_text = source.get("title") or source.get("host") or url
                                    if url:
                                        rendered_sources.append(f"{_short_text(title_text, 80)}: {_short_text(url, 140)}")
                                elif str(source or "").strip():
                                    rendered_sources.append(_short_text(source, 140))
                            if rendered_sources:
                                lines.append("    sources: " + "; ".join(rendered_sources))
                rejected = pack.get("rejectedEvidence")
                if isinstance(rejected, list) and rejected:
                    lines.append("  Rejected evidence:")
                    for item in rejected[:2]:
                        if isinstance(item, dict):
                            label = item.get("title") or item.get("id") or item.get("memoryRef") or item.get("sourceRef")
                            reason = item.get("reason") or item.get("rejectedReason") or item.get("whyRejected")
                            lines.append(f"  - {_short_text(label, 110)}: {_short_text(reason, 160)}")
                        else:
                            lines.append(f"  - {_short_text(item, 220)}")
                stale = pack.get("missingOrStaleReasons")
                if isinstance(stale, list) and stale:
                    lines.append("  Missing/stale: " + "; ".join(_short_text(item, 120) for item in stale[:3]))
                pack_next = pack.get("recommendedNextAction")
                if pack_next:
                    lines.append(f"  Next: {_short_text(pack_next, 220)}")
            if len(packs) > 3:
                lines.append(f"- … {len(packs) - 3} more packs")

        items = payload.get("items")
        if isinstance(items, list) and items:
            lines.append("Items:")
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                label = item.get("text") or item.get("name") or item.get("memoryRef") or item.get("id")
                prefix = item.get("id") or item.get("memoryRef") or item.get("name")
                extras = []
                if item.get("scope"):
                    extras.append(str(item.get("scope")))
                if item.get("category") or item.get("type"):
                    extras.append(str(item.get("category") or item.get("type")))
                if item.get("confidence") not in (None, ""):
                    extras.append(f"confidence={item.get('confidence')}")
                suffix = f" | {'; '.join(extras)}" if extras else ""
                if prefix and prefix != label:
                    lines.append(f"- {_short_text(prefix, 80)}: {_short_text(label, 240)}{suffix}")
                else:
                    lines.append(f"- {_short_text(label, 240)}{suffix}")
            if len(items) > 5:
                lines.append(f"- … {len(items) - 5} more")

        relations = payload.get("relations")
        if isinstance(relations, list) and relations:
            lines.append("Relations:")
            for item in relations[:6]:
                if not isinstance(item, dict):
                    continue
                triple = " ".join(
                    _short_text(item.get(key), 80)
                    for key in ("subject", "predicate", "object")
                    if item.get(key)
                )
                if triple:
                    hop = f"hop={item.get('hop')} | " if item.get("hop") else ""
                    lines.append(f"- {hop}{triple}")
            if len(relations) > 6:
                lines.append(f"- … {len(relations) - 6} more")

        if payload.get("preview"):
            lines.append("Preview:")
            lines.append(_short_text(payload.get("preview"), 1200))
        if payload.get("omittedChars"):
            lines.append(f"Omitted chars: {payload.get('omittedChars')}")
        next_action = payload.get("nextAction") or payload.get("recommendedNextAction")
        if next_action:
            lines.append(f"Next: {_short_text(next_action, 220)}")
        if payload.get("ok") is False and payload.get("failureClass"):
            lines.append(f"Failure: {_short_text(payload.get('failureClass'), 80)}")
        lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
        return "\n".join(line for line in lines if line).strip()

    if tool_name != "memory_map":
        return None
    lines = ["Memory map"]
    if payload.get("anchorDate"):
        lines.append(f"Anchor: {_short_text(payload.get('anchorDate'), 40)}")
    refs = payload.get("currentRefs")
    if isinstance(refs, dict) and refs:
        lines.append("Current refs: " + ", ".join(f"{key}={_short_text(value, 60)}" for key, value in list(refs.items())[:4]))
    items = payload.get("items")
    if isinstance(items, list) and items:
        lines.append("Items:")
        for item in items[:5]:
            if not isinstance(item, dict):
                continue
            summary_state = item.get("summaryState")
            day_count = item.get("dayCount")
            latest = item.get("latestDay")
            line = f"- {_short_text(item.get('memoryRef'), 80)} | {_short_text(item.get('kind'), 24)} | {_short_text(item.get('label'), 40)}"
            extras = []
            if summary_state:
                extras.append(f"summary={summary_state}")
            if day_count not in (None, ""):
                extras.append(f"days={day_count}")
            if latest:
                extras.append(f"latest={latest}")
            if extras:
                line += " | " + " ".join(extras)
            lines.append(line)
        if len(items) > 5:
            lines.append(f"- … {len(items) - 5} more")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()


def _render_web_broker_surface(payload: dict[str, Any], raw_ref: str, *, budget: int) -> str:
    mode = _short_text(payload.get("mode") or payload.get("kind") or payload.get("operation") or "result", 40)
    lines = [f"Web broker ({mode})"]
    summary = _first_text(payload, "summary", "answer", "result", "message", limit=700)
    if summary:
        lines.append(f"Summary: {summary}")
    if payload.get("query"):
        lines.append(f"Query: {_short_text(payload.get('query'), 220)}")

    if payload.get("ok") is False:
        failure = _first_text(payload, "error", "failureClass", "reason", "warning", limit=300)
        if failure:
            lines.append(f"Failure: {failure}")

    title = payload.get("title")
    final_url = payload.get("finalUrl") or payload.get("url")
    if title or final_url:
        lines.append(f"Page: {_short_text(title or final_url, 180)}")
        if final_url:
            lines.append(f"URL: {_short_text(final_url, 220)}")

    page_value = None
    for key in ("text", "textPreview", "content", "contentPreview", "rawHtmlPreview", "extract"):
        if payload.get(key) not in (None, "", [], {}):
            page_value = payload.get(key)
            break
    page_text = _content_excerpt(page_value, max(1200, min(9000, budget // 2))) if page_value is not None else ""
    if page_text:
        content_label = "Raw HTML preview:" if payload.get("rawHtmlPreview") else "Content:"
        lines.append(content_label)
        lines.append(page_text)
    ui_snapshot = payload.get("uiSnapshot")
    if isinstance(ui_snapshot, list) and ui_snapshot:
        lines.append("UI snapshot:")
        for item in ui_snapshot[:8]:
            if isinstance(item, dict):
                label = _source_line(item) or _short_text(item, 180)
                lines.append(f"- {label}")
        if len(ui_snapshot) > 8:
            lines.append(f"- … {len(ui_snapshot) - 8} more")

    source_summary = payload.get("sourceQualitySummary")
    if isinstance(source_summary, dict):
        quality_bits = []
        for key in ("quality", "resultCount", "officialCount"):
            if source_summary.get(key) not in (None, "", [], {}):
                quality_bits.append(f"{key}={_short_text(source_summary.get(key), 80)}")
        if quality_bits:
            lines.append("Source quality: " + " | ".join(quality_bits[:4]))
    elif payload.get("quality"):
        lines.append(f"Source quality: {_short_text(payload.get('quality'), 100)}")
    extraction_bits = []
    for key in ("extractionQuality", "contentFormat", "contentChars", "htmlChars", "missingContentReason", "usedBrowserProfile"):
        if payload.get(key) not in (None, "", [], {}):
            extraction_bits.append(f"{key}={_short_text(payload.get(key), 80)}")
    if extraction_bits:
        lines.append("Extraction: " + " | ".join(extraction_bits[:6]))

    results = payload.get("results") or payload.get("sources") or payload.get("items")
    if isinstance(results, list) and results:
        lines.append("Sources:")
        shown = 0
        for item in results:
            if not isinstance(item, dict):
                continue
            line = _source_line(item)
            if line:
                lines.append(f"- {line}")
                shown += 1
            if shown >= 6:
                break
        if len(results) > shown:
            lines.append(f"- … {len(results) - shown} more")

    links = payload.get("links")
    if isinstance(links, list) and links and not isinstance(results, list):
        lines.append("Links:")
        for item in links[:5]:
            if isinstance(item, dict):
                lines.append(f"- {_source_line(item)}")
            else:
                lines.append(f"- {_short_text(item, 180)}")
        if len(links) > 5:
            lines.append(f"- … {len(links) - 5} more")

    warnings = payload.get("warnings") or payload.get("warning")
    if isinstance(warnings, list) and warnings:
        lines.append("Warnings: " + "; ".join(_short_text(item, 120) for item in warnings[:3]))
    elif warnings:
        lines.append(f"Warning: {_short_text(warnings, 220)}")
    next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
    if next_action:
        lines.append(f"Next: {_short_text(next_action, 220)}")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
    return "\n".join(line for line in lines if line).strip()


def _render_delegation_broker_surface(payload: dict[str, Any], raw_ref: str) -> str:
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
            target = item.get("target") or item.get("worker") or item.get("subagent") or item.get("member") or item.get("family")
            status = item.get("status") or item.get("state") or item.get("dispatchStatus")
            parts = [
                _short_text(goal, 220),
                f"target={_short_text(target, 80)}" if target else "",
                f"status={_short_text(status, 50)}" if status else "",
            ]
            lines.append("- " + " | ".join(part for part in parts if part))
        if len(tasks) > 8:
            lines.append(f"- … {len(tasks) - 8} more")

    next_action = payload.get("recommendedNextAction") or payload.get("nextAction") or payload.get("recommendedAction")
    if next_action:
        lines.append(f"Next: {_short_text(next_action, 220)}")
    lines.extend(_surface_ref_lines(raw_ref, payload.get("detailTool"), include_raw=True))
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


def _render_generic_json_surface(tool_name: str, payload: Any, raw_ref: str, *, budget: int) -> str | None:
    if not isinstance(payload, (dict, list)):
        return None
    label = _short_text(str(tool_name or "tool_result").replace("_", " "), 80)
    lines = [f"{label} result"]
    if isinstance(payload, dict):
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
    lines.append("- Reading scripts/ assets does not grant execution permission; run scripts only through governed command/runtime tools when the task explicitly requires it.")
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
    payload = _tool_json_payload(text)
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
    if tool_name == "runtime_broker":
        renderer_result = _render_runtime_broker_surface(payload, raw_ref)
    elif tool_name == "workspace_broker":
        renderer_result = _render_workspace_broker_surface(payload, raw_ref)
    elif tool_name == "research_broker":
        renderer_result = _render_research_broker_surface(payload, raw_ref, budget=budget)
    elif tool_name == "web_broker" or tool_name.startswith("web_"):
        renderer_result = _render_web_broker_surface(payload, raw_ref, budget=budget)
    elif tool_name == "delegation_broker" or tool_name.startswith("delegation_") or tool_name.startswith("subagent_"):
        renderer_result = _render_delegation_broker_surface(payload, raw_ref)
    elif tool_name.startswith("computer_use_"):
        renderer_result = _render_computer_use_surface(tool_name, payload, raw_ref)
    elif tool_name.startswith("creative_media_"):
        renderer_result = _render_creative_media_surface(tool_name, payload, raw_ref)
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
    if len(renderer_result) > budget:
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
    payload = _command_json_payload(text)
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
        stdout_candidates = (
            (
                payload.get("finalPreview"),
                payload.get("keyOutput"),
                payload.get("deltaText"),
                payload.get("outputPreview"),
            )
            if state in {"completed", "failed"}
            else (
                payload.get("deltaText"),
                payload.get("keyOutput"),
                payload.get("outputPreview"),
                payload.get("finalPreview"),
            )
        )
        stdout = next((item for item in stdout_candidates if item not in (None, "")), "")
        control: list[str] = []
        if session_id and state not in {"completed", "failed"}:
            control.append(f"[session: {session_id}]")
        if state == "recoverable_stalled":
            control.append("[command appears stalled; observe later or terminate]")
        elif state == "render_stalled":
            control.append("[terminal screen is still settling]")
        if payload.get("terminated"):
            control.append("[terminated]")
        return _render_terminal_command_surface(
            command=command,
            stdout=stdout,
            stderr=payload.get("error") or "",
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
            ),
            control_lines=control,
        )
    if kind in {"command_session_required", "command_session_redirect"} or str(tool_name or "") in COMMAND_TOOL_NAMES:
        control = [f"[{kind or 'command notice'}]"]
        for key in ("reason", "summary", "error"):
            value = str(payload.get(key) or "").strip()
            if value:
                control.append(f"[{value}]")
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


def _prune_agent_visible_json(value: Any) -> Any:
    if isinstance(value, dict):
        pruned: dict[str, Any] = {}
        for key, item in value.items():
            nested = _prune_agent_visible_json(item)
            if nested in (None, "", [], {}):
                continue
            pruned[key] = nested
        return pruned
    if isinstance(value, list):
        return [
            nested
            for nested in (_prune_agent_visible_json(item) for item in value)
            if nested not in (None, "", [], {})
        ]
    return value


def _inject_surface_metadata(text: str, surface: dict[str, Any], *, budget: int) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("{"):
        return text
    try:
        payload = json.loads(stripped)
    except Exception:
        return text
    if not isinstance(payload, dict):
        return text
    payload.setdefault("_v8ToolSurface", surface)
    payload = _prune_agent_visible_json(payload)
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    return rendered if len(rendered) <= budget else text


def _truncate_json_semantic(
    text: str,
    budget_meta: dict[str, Any],
    *,
    tool_name: str,
    tool_call_id: str | None,
    runtime_kind: str,
    raw_ref: str | None,
) -> str | None:
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, (dict, list)):
        return None

    original_len = len(text)
    budget = int(budget_meta["agentVisibleBudget"])
    surface = _tool_surface_payload(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        runtime_kind=runtime_kind,
        raw_ref=raw_ref,
        budget_meta=budget_meta,
        was_truncated=True,
        strategy="json_priority_fields",
        omitted_chars=max(0, original_len - budget),
    )
    compact = _compact_json_value(payload, text_limit=max(400, budget // 8))
    if isinstance(compact, dict):
        compact["_v8ToolSurface"] = surface
    else:
        compact = {"items": compact, "_v8ToolSurface": surface}
    compact = _prune_agent_visible_json(compact)
    rendered = json.dumps(compact, ensure_ascii=False, indent=2)
    if len(rendered) <= budget:
        return rendered

    minimal: dict[str, Any] = {"_v8ToolSurface": surface}
    if isinstance(payload, dict):
        for key in JSON_PRIORITY_KEYS:
            if key in payload:
                minimal[key] = _compact_json_value(payload.get(key), depth=1, text_limit=240)
    minimal = _prune_agent_visible_json(minimal)
    rendered = json.dumps(minimal, ensure_ascii=False, indent=2)
    if len(rendered) <= budget:
        return rendered
    return _head_tail_truncate_text(rendered, budget, f"semantic JSON output truncated; original length {original_len} chars")


def _hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", errors="ignore")).hexdigest()


def record_raw_observation(
    *,
    tool_name: str,
    tool_call_id: str | None,
    runtime_kind: str,
    surface: str,
    raw_content: str,
    visible_content: str | None = None,
    budget_meta: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    observation_id = f"toolobs_{uuid.uuid4().hex}"
    raw_ref = f"toolobs://{observation_id}"
    metadata_payload = dict(metadata or {})
    run_id = str((budget_meta or {}).get("runId") or (budget_meta or {}).get("run_id") or "").strip()
    if run_id and not metadata_payload.get("runId"):
        metadata_payload["runId"] = run_id
    try:
        from core.observability_db import observability_db

        observability_db.add_tool_observation_record(
            {
                "id": observation_id,
                "raw_ref": raw_ref,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "runtime_kind": runtime_kind,
                "surface": surface,
                "raw_chars": len(raw_content or ""),
                "visible_chars": len(visible_content if visible_content is not None else raw_content or ""),
                "raw_sha256": _hash_text(raw_content or ""),
                "raw_body": raw_content,
                "budget": dict(budget_meta or {}),
                "metadata": metadata_payload,
                "created_at": utc_now_iso(),
            }
        )
    except Exception:
        # Raw refs should never break the agent-visible tool result.
        pass
    return raw_ref


def _copy_tool_message_with_budget(message: ToolMessage, content: str, budget_meta: dict[str, Any]) -> ToolMessage:
    additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
    response_metadata = dict(getattr(message, "response_metadata", {}) or {})
    additional_kwargs["v8_tool_output_budget"] = budget_meta
    response_metadata["v8_tool_output_budget"] = budget_meta
    return message.model_copy(
        update={
            "content": content,
            "additional_kwargs": additional_kwargs,
            "response_metadata": response_metadata,
        }
    )


def apply_tool_surface_budget(
    message: ToolMessage,
    budget_meta: dict[str, Any] | None = None,
    *,
    tool_name: str | None = None,
    runtime_kind: str | None = None,
    surface: str = "tool_node",
) -> ToolMessage:
    content = message.content
    if not content:
        return message

    tool_name = str(tool_name or getattr(message, "name", "") or "").strip() or "unknown"
    tool_call_id = getattr(message, "tool_call_id", None)
    runtime_kind = str(runtime_kind or runtime_kind_for_tool(tool_name)).strip() or "native"
    original_content_str = content if isinstance(content, str) else str(content)
    content_str = original_content_str
    budget_meta = dict(budget_meta or {})
    budget = int(budget_meta.get("agentVisibleBudget") or DEFAULT_TOOL_OUTPUT_HARD_MAX_CHARS)
    raw_ref = record_raw_observation(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        runtime_kind=runtime_kind,
        surface=surface,
        raw_content=original_content_str,
        budget_meta=budget_meta,
    )

    budget_meta.update(
        {
            "toolCallId": tool_call_id,
            "runtimeKind": runtime_kind,
            "rawRef": raw_ref,
        }
    )

    if tool_name in COMMAND_TOOL_NAMES and WORKER_RESULT_RE.search(content_str or ""):
        notice = (
            "OUTPUT TRUNCATED BY DYNAMIC TOOL OUTPUT BUDGET. "
            f"Original length: {len(content_str)} chars; budget: {budget} chars"
        )
        marker_preserved = _truncate_worker_result_preserving_marker(content_str, budget, notice)
        if marker_preserved is not None:
            budget_meta.update(
                {
                    "wasBudgetTruncated": len(content_str) > len(marker_preserved),
                    "semanticTruncationStrategy": "worker_result_marker_preserving",
                    "originalChars": len(original_content_str),
                    "visibleChars": len(marker_preserved),
                }
            )
            return _copy_tool_message_with_budget(message, marker_preserved, budget_meta)

    if tool_name in COMMAND_TOOL_NAMES:
        command_surface = _command_agent_visible_surface(
            tool_name=tool_name,
            content=content_str,
            raw_ref=raw_ref,
            budget=budget,
        )
        if command_surface is not None:
            was_truncated = len(command_surface) > budget
            if was_truncated:
                command_surface = _head_tail_truncate_text(
                    command_surface,
                    budget,
                    f"command output truncated; rawRef={raw_ref}",
                )
            budget_meta.update(
                {
                    "wasBudgetTruncated": was_truncated,
                    "semanticTruncationStrategy": "command_terminal_surface",
                    "originalChars": len(original_content_str),
                    "visibleChars": len(command_surface),
                }
            )
            return _copy_tool_message_with_budget(message, command_surface, budget_meta)

    if tool_name == "fetch_skill_instructions":
        skill_surface = _render_skill_instructions_surface(content_str, raw_ref, budget=budget)
        budget_meta.update(
            {
                "wasBudgetTruncated": len(skill_surface) < len(content_str),
                "semanticTruncationStrategy": "skill_instructions_surface",
                "originalChars": len(original_content_str),
                "visibleChars": len(skill_surface),
            }
        )
        return _copy_tool_message_with_budget(message, skill_surface, budget_meta)

    if tool_name == "read_audit_log":
        audit_surface = _render_audit_log_surface(content_str, raw_ref, budget=budget)
        budget_meta.update(
            {
                "wasBudgetTruncated": len(audit_surface) < len(content_str),
                "semanticTruncationStrategy": "audit_log_surface",
                "originalChars": len(original_content_str),
                "visibleChars": len(audit_surface),
            }
        )
        return _copy_tool_message_with_budget(message, audit_surface, budget_meta)

    decision_surface = _decision_agent_visible_surface(
        tool_name=tool_name,
        content=content_str,
        raw_ref=raw_ref,
        budget=budget,
    )
    if decision_surface is not None:
        was_truncated = len(decision_surface) > budget
        if was_truncated:
            decision_surface = _head_tail_truncate_text(
                decision_surface,
                budget,
                f"decision surface truncated; rawRef={raw_ref}",
            )
        budget_meta.update(
            {
                "wasBudgetTruncated": was_truncated,
                "semanticTruncationStrategy": "decision_summary_surface",
                "originalChars": len(original_content_str),
                "visibleChars": len(decision_surface),
            }
        )
        return _copy_tool_message_with_budget(message, decision_surface, budget_meta)

    strategy = "none"
    if len(content_str) > budget:
        notice = (
            "OUTPUT TRUNCATED BY DYNAMIC TOOL OUTPUT BUDGET. "
            f"Original length: {len(content_str)} chars; budget: {budget} chars"
        )
        marker_preserved = _truncate_worker_result_preserving_marker(content_str, budget, notice)
        if marker_preserved is not None:
            content_str = marker_preserved
            strategy = "worker_result_marker_preserving"
        else:
            json_truncated = _truncate_json_semantic(
                content_str,
                budget_meta,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                runtime_kind=runtime_kind,
                raw_ref=raw_ref,
            )
            if json_truncated is not None:
                content_str = json_truncated
                strategy = "json_priority_fields"
            else:
                surface_payload = _tool_surface_payload(
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    runtime_kind=runtime_kind,
                    raw_ref=raw_ref,
                    budget_meta=budget_meta,
                    was_truncated=True,
                    strategy="head_tail_semantic_text",
                    omitted_chars=max(0, len(original_content_str) - budget),
                )
                text_budget = max(0, budget - len(json.dumps({"_v8ToolSurface": surface_payload}, ensure_ascii=False)) - 32)
                compact_text = _head_tail_truncate_text(content_str, max(1, text_budget), notice)
                content_str = json.dumps(
                    {
                        "summary": compact_text,
                        "_v8ToolSurface": surface_payload,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                strategy = "head_tail_semantic_text"
        budget_meta.update(
            {
                "wasBudgetTruncated": True,
                "semanticTruncationStrategy": strategy,
                "originalChars": len(original_content_str),
                "visibleChars": len(content_str),
            }
        )
    else:
        surface_payload = _tool_surface_payload(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            runtime_kind=runtime_kind,
            raw_ref=raw_ref,
            budget_meta=budget_meta,
            was_truncated=False,
            strategy="none",
        )
        content_str = _inject_surface_metadata(content_str, surface_payload, budget=budget)
        budget_meta.update(
            {
                "wasBudgetTruncated": False,
                "semanticTruncationStrategy": "none",
                "originalChars": len(original_content_str),
                "visibleChars": len(content_str),
            }
        )
    return _copy_tool_message_with_budget(message, content_str, budget_meta)


def apply_command_tool_surface_budget(command: Command, budget_meta: dict[str, Any] | None = None) -> Command:
    update = getattr(command, "update", None)
    if not isinstance(update, dict):
        return command
    messages = update.get("messages")
    if not isinstance(messages, list):
        return command

    changed = False
    next_messages = []
    for message in messages:
        if isinstance(message, ToolMessage):
            truncated = apply_tool_surface_budget(message, dict(budget_meta or {}))
            changed = changed or truncated is not message
            next_messages.append(truncated)
        else:
            next_messages.append(message)
    if not changed:
        return command
    next_update = dict(update)
    next_update["messages"] = next_messages
    return Command(
        graph=getattr(command, "graph", None),
        update=next_update,
        resume=getattr(command, "resume", None),
        goto=getattr(command, "goto", ()),
    )


def apply_agent_visible_budget(result: Any, budget_meta: dict[str, Any] | None = None):
    if isinstance(result, ToolMessage):
        return apply_tool_surface_budget(result, budget_meta)
    if isinstance(result, Command):
        return apply_command_tool_surface_budget(result, budget_meta)
    return result
