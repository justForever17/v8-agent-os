from __future__ import annotations

import json
import re
from typing import Any


_TOOL_OBSERVATION_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\s,;\"']{8,})"),
    re.compile(r"(?i)(bearer)\s+([A-Za-z0-9._~+/=-]{12,})"),
]

_TOOL_OBSERVATION_INTERNAL_KEYS = {
    "_v8ToolSurface",
    "rawRef",
    "raw_ref",
    "registryVersion",
    "registryHash",
    "registry_version",
    "registry_hash",
    "macroTaskCount",
    "requestedTaskCount",
    "diagnosticKey",
    "dispatchGroup",
    "traceRef",
}


def _redact_tool_observation_preview(text: str) -> str:
    redacted = str(text or "")
    for pattern in _TOOL_OBSERVATION_SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)
    return redacted


def _parse_tool_observation_json(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(str(text or ""))
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _tool_observation_short_text(value: Any, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _tool_observation_content_excerpt(value: Any, limit: int = 1600) -> str:
    text = str(value or "").strip().replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    if len(text) <= limit:
        return text
    head_limit = max(400, int(limit * 0.65))
    tail_limit = max(120, limit - head_limit - 48)
    return f"{text[:head_limit].rstrip()}\n\n...[content truncated]...\n\n{text[-tail_limit:].lstrip()}"


def _tool_observation_display_value(value: Any, limit: int = 420) -> str:
    if isinstance(value, dict):
        parts = [
            f"{key}={_tool_observation_short_text(item, 120)}"
            for key, item in list(value.items())[:6]
            if not isinstance(item, (dict, list)) and item not in (None, "")
        ]
        return "; ".join(parts) if parts else f"{len(value)} field(s)"
    if isinstance(value, list):
        scalar_items = [
            _tool_observation_short_text(item, 120)
            for item in value[:6]
            if not isinstance(item, (dict, list))
        ]
        return "; ".join(scalar_items) if scalar_items else f"{len(value)} item(s)"
    return _tool_observation_short_text(value, limit)


def _render_generic_json_observation_detail(
    payload: dict[str, Any],
    *,
    record: dict[str, Any],
    raw_ref: str,
    max_chars: int,
) -> str:
    lines = [
        "Tool observation detail",
        f"tool: {record.get('tool_name') or 'unknown'}",
    ]
    if payload.get("ok") is False:
        lines.append("status: failed")
    elif payload.get("status") not in (None, "", [], {}):
        lines.append(f"status: {_tool_observation_display_value(payload.get('status'), 100)}")
    elif payload.get("kind") not in (None, "", [], {}):
        lines.append(f"kind: {_tool_observation_display_value(payload.get('kind'), 100)}")

    rendered_keys: set[str] = {"ok", "status", "kind", *_TOOL_OBSERVATION_INTERNAL_KEYS}
    for key, label in (
        ("summary", "Summary"),
        ("message", "Message"),
        ("answer", "Answer"),
        ("result", "Result"),
        ("error", "Error"),
        ("recommendedNextAction", "Next"),
        ("nextAction", "Next"),
    ):
        value = payload.get(key)
        if value in (None, "", [], {}):
            continue
        lines.append(f"{label}: {_tool_observation_display_value(value, 1000)}")
        rendered_keys.add(key)

    remaining = [
        (key, value)
        for key, value in payload.items()
        if key not in rendered_keys and value not in (None, "", [], {})
    ]
    if remaining:
        lines.append("")
        lines.append("Details:")
        for key, value in remaining[:12]:
            lines.append(f"- {key}: {_tool_observation_display_value(value, 360)}")
        if len(remaining) > 12:
            lines.append(f"- … {len(remaining) - 12} more field(s)")

    rendered = _redact_tool_observation_preview("\n".join(lines).strip())
    if len(rendered) > max_chars:
        rendered = rendered[: max_chars - 32].rstrip() + "\n[truncated]"
    return rendered


def _render_runtime_route_observation_detail(
    payload: dict[str, Any],
    *,
    max_chars: int,
) -> str | None:
    """Resolve a route receipt to current durable episode truth.

    A runtime_broker route result is an immutable queue receipt. Re-rendering
    that old JSON after a graph-owned wait must not project ``queued`` as the
    current execution state or encourage command-session polling.
    """

    episode_id = str(
        payload.get("queuedEpisodeId")
        or payload.get("episodeId")
        or payload.get("runtimeEpisodeId")
        or ""
    ).strip()
    if not episode_id:
        return None
    try:
        from core.database import db

        episode = db.get_runtime_episode(episode_id)
        handoff_rows = db.list_runtime_episode_handoffs(episode_id)
    except Exception:
        return None
    state = str((episode or {}).get("state") or payload.get("state") or "unknown").strip()
    lines = [
        "Runtime episode current state",
        f"Episode: {episode_id}",
        f"Status: {state}",
    ]
    terminal_handoffs: list[dict[str, Any]] = []
    for row in list(handoff_rows or []):
        handoff = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        merged = {**dict(row), **dict(handoff)}
        status = str(merged.get("status") or "").strip().lower()
        if status in {"ready", "degraded", "failed", "blocked", "cancelled", "canceled"}:
            terminal_handoffs.append(merged)
    if terminal_handoffs:
        lines.append("The earlier queued route receipt is superseded by the durable terminal handoff below.")
        for handoff in terminal_handoffs[-4:]:
            status = str(handoff.get("status") or "terminal").strip()
            kind = str(handoff.get("kind") or "runtime").strip()
            summary = _tool_observation_short_text(
                handoff.get("compactSummary")
                or handoff.get("compact_summary")
                or handoff.get("summary")
                or "Terminal handoff recorded.",
                700,
            )
            lines.append(f"- {kind} / {status}: {summary}")
            artifact_refs = handoff.get("artifactRefs") if isinstance(handoff.get("artifactRefs"), list) else []
            proof_refs = handoff.get("proofRefs") if isinstance(handoff.get("proofRefs"), list) else []
            if artifact_refs:
                lines.append("  Artifact refs: " + ", ".join(str(item) for item in artifact_refs[:8]))
            if proof_refs:
                lines.append("  Proof refs: " + ", ".join(str(item) for item in proof_refs[:8]))
        lines.append(
            "Consume this terminal handoff directly. Do not pass this toolobs ref to "
            "read_background_output; runtime episodes are not command sessions."
        )
    else:
        lines.append(
            "This is only a graph-owned route receipt. The graph owns waiting and will inject a typed handoff; "
            "do not poll it with tool_observation_detail or read_background_output."
        )
    rendered = _redact_tool_observation_preview("\n".join(lines).strip())
    if len(rendered) > max_chars:
        rendered = rendered[: max_chars - 32].rstrip() + "\n[truncated]"
    return rendered


def _render_delegation_observation_detail(payload: dict[str, Any], *, max_chars: int) -> str:
    mode = _tool_observation_short_text(payload.get("mode") or "observe", 40)
    lines = [f"Delegation result ({mode})"]
    summary = payload.get("summary") or payload.get("message") or payload.get("error")
    if summary:
        lines.append(f"Summary: {_tool_observation_short_text(summary, 800)}")
    items = payload.get("items") or payload.get("results") or []
    if isinstance(items, list) and items:
        lines.append("Results:")
        for item in items[:12]:
            if not isinstance(item, dict):
                lines.append(f"- {_tool_observation_short_text(item, 320)}")
                continue
            target = item.get("targetLabel") or item.get("agentName") or item.get("targetId") or item.get("agentId") or "subagent"
            status = item.get("status") or item.get("workerStatus") or item.get("dispatchStatus") or "unknown"
            task = item.get("taskGoal") or item.get("goal") or item.get("summary") or item.get("taskBriefId") or "delegated task"
            lines.append(
                f"- {_tool_observation_short_text(target, 120)} | "
                f"{_tool_observation_short_text(status, 60)} | "
                f"{_tool_observation_short_text(task, 420)}"
            )
            tool_policy = item.get("toolPolicy") if isinstance(item.get("toolPolicy"), dict) else {}
            policy_mode = str(tool_policy.get("mode") or "").strip().lower()
            if policy_mode == "none":
                lines.append("  Tool authority: none")
            elif policy_mode == "allowlist":
                allowed = ", ".join(
                    _tool_observation_short_text(name, 100)
                    for name in list(tool_policy.get("allowedTools") or [])
                )
                lines.append(f"  Tool authority: {allowed or 'empty allowlist'}")
            result_text = item.get("resultText")
            if result_text:
                lines.append(f"  Exact result: {_tool_observation_content_excerpt(result_text, 2400)}")
            result_summary = item.get("summary") or item.get("compactTranscript")
            if result_summary and str(result_summary) not in {str(task or ""), str(result_text or "")}:
                lines.append(f"  Result: {_tool_observation_content_excerpt(result_summary, 900)}")
            local_self_check = item.get("localSelfCheck")
            if local_self_check:
                lines.append(f"  Self-check: {_tool_observation_short_text(local_self_check, 520)}")
            artifact_refs = item.get("artifactRefs")
            if isinstance(artifact_refs, list) and artifact_refs:
                refs = ", ".join(_tool_observation_short_text(ref, 160) for ref in artifact_refs[:8])
                lines.append(f"  Evidence: {refs}")
            acceptance = item.get("acceptanceHint")
            if acceptance:
                lines.append(f"  Acceptance: {_tool_observation_short_text(acceptance, 420)}")
            error = item.get("error")
            if error:
                lines.append(f"  Error: {_tool_observation_short_text(error, 320)}")
    next_action = payload.get("recommendedNextAction") or payload.get("nextAction")
    if next_action:
        lines.append(f"Next: {_tool_observation_short_text(next_action, 260)}")
    rendered = _redact_tool_observation_preview("\n".join(lines).strip())
    if len(rendered) > max_chars:
        rendered = rendered[: max_chars - 32].rstrip() + "\n[truncated]"
    return rendered


def _render_research_observation_detail(payload: dict[str, Any], *, raw_ref: str, max_chars: int) -> str | None:
    kind = str(payload.get("kind") or "").strip()
    if kind not in {"research_evidence_bundle", "research_result_pack", "research_experience_pack"} and not any(
        key in payload for key in ("finalExperiencePack", "researchResult", "sourceMatrix")
    ):
        return None

    pack = payload.get("finalExperiencePack") or payload.get("researchResult") or payload
    if not isinstance(pack, dict):
        pack = payload
    answer_pack = payload.get("researchAnswerPack") if isinstance(payload.get("researchAnswerPack"), dict) else {}
    answer = (
        str(answer_pack.get("answer") or "").strip()
        or str(pack.get("researchResult") or "").strip()
        or str(pack.get("answer") or "").strip()
        or str(payload.get("answer") or "").strip()
    )
    findings = pack.get("keyFindings") if isinstance(pack.get("keyFindings"), list) else []
    sources = answer_pack.get("sources") if isinstance(answer_pack.get("sources"), list) else []
    if not sources:
        sources = pack.get("sourceUrls") if isinstance(pack.get("sourceUrls"), list) else []
    if not sources and isinstance(payload.get("sourceMatrix"), list):
        sources = payload.get("sourceMatrix") or []

    lines = [
        "Research result pack",
        "agent: Web Research Architect",
    ]
    question = pack.get("question") or payload.get("question")
    if question:
        lines.append(f"question: {_tool_observation_short_text(question, 220)}")
    confidence = pack.get("confidence") or payload.get("confidence")
    if confidence:
        lines.append(f"confidence: {confidence}")
    score = answer_pack.get("score") if isinstance(answer_pack.get("score"), dict) else {}
    if score:
        lines.append(f"score: {_tool_observation_short_text(score.get('label') or score, 260)}")
    lines.append("")
    lines.append("Final result:")
    if answer:
        lines.append(_tool_observation_content_excerpt(answer, max(900, min(max_chars // 2, 3200))))
    else:
        lines.append("No final source-backed research result was available. Refresh research or provide readable authoritative sources.")

    if findings:
        lines.append("")
        lines.append("Key findings:")
        for item in findings[:8]:
            if isinstance(item, dict):
                claim = _tool_observation_short_text(item.get("claim") or item.get("summary") or item, 360)
                source_title = _tool_observation_short_text(item.get("sourceTitle") or item.get("title"), 100)
                if source_title:
                    lines.append(f"- {claim} ({source_title})")
                else:
                    lines.append(f"- {claim}")
            else:
                lines.append(f"- {_tool_observation_short_text(item, 360)}")

    if sources:
        lines.append("")
        lines.append("Sources:")
        seen: set[str] = set()
        for source in sources[:12]:
            if not isinstance(source, dict):
                continue
            url = str(source.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            title = _tool_observation_short_text(source.get("title") or source.get("sourceTitle") or source.get("host") or url, 140)
            lines.append(f"- {title}: {url}")

    limitations = pack.get("limitations")
    if isinstance(limitations, list) and limitations:
        lines.append("")
        lines.append("Limitations:")
        for item in limitations[:4]:
            lines.append(f"- {_tool_observation_short_text(item, 240)}")

    rendered = "\n".join(line for line in lines if line is not None).strip()
    if len(rendered) > max_chars:
        rendered = rendered[: max_chars - 32].rstrip() + "\n[truncated]"
    return rendered


def _render_web_observation_detail(payload: dict[str, Any], *, raw_ref: str, max_chars: int) -> str | None:
    if not any(key in payload for key in ("text", "textPreview", "rawHtml", "rawHtmlPreview", "uiSnapshot", "links", "media", "extractionQuality")):
        return None

    def _value(*keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if value not in (None, "", [], {}):
                return _tool_observation_short_text(value, max_chars)
        return ""

    lines = ["Web observation detail"]
    title = _value("title", "summary")
    final_url = _value("finalUrl", "url")
    if title:
        lines.append(f"title: {title}")
    if final_url:
        lines.append(f"url: {final_url}")
    quality = []
    for key in ("mode", "extract", "extractionQuality", "contentFormat", "contentChars", "htmlChars", "missingContentReason", "usedBrowserProfile"):
        if payload.get(key) not in (None, "", [], {}):
            quality.append(f"{key}={_tool_observation_short_text(payload.get(key), 80)}")
    if quality:
        lines.append("quality: " + " | ".join(quality))

    content = ""
    for key in ("text", "textPreview", "content", "contentPreview", "rawHtml", "rawHtmlPreview"):
        if payload.get(key) not in (None, "", [], {}):
            content = _tool_observation_content_excerpt(payload.get(key), max(900, min(max_chars // 2, 3600)))
            break
    if content:
        lines.extend(["", "Content:", content])

    snapshot = payload.get("uiSnapshot")
    if isinstance(snapshot, list) and snapshot:
        lines.extend(["", "UI snapshot:"])
        for item in snapshot[:40]:
            if not isinstance(item, dict):
                continue
            parts = [
                _tool_observation_short_text(item.get("tag"), 30),
                _tool_observation_short_text(item.get("role"), 50),
                _tool_observation_short_text(item.get("text"), 180),
                _tool_observation_short_text(item.get("href"), 180),
            ]
            parts = [part for part in parts if part]
            if parts:
                lines.append("- " + " | ".join(parts))
        if len(snapshot) > 40:
            lines.append(f"- … {len(snapshot) - 40} more")

    links = payload.get("links")
    if isinstance(links, list) and links:
        lines.extend(["", "Links:"])
        for item in links[:20]:
            if isinstance(item, dict):
                label = _tool_observation_short_text(item.get("text") or item.get("title") or item.get("url"), 180)
                url = _tool_observation_short_text(item.get("url") or item.get("href"), 220)
                lines.append(f"- {label}: {url}" if url else f"- {label}")

    warnings = payload.get("warnings")
    if isinstance(warnings, list) and warnings:
        lines.extend(["", "Warnings:"])
        for item in warnings[:5]:
            lines.append(f"- {_tool_observation_short_text(item, 240)}")

    rendered = "\n".join(line for line in lines if line is not None).strip()
    if len(rendered) > max_chars:
        rendered = rendered[: max_chars - 32].rstrip() + "\n[truncated]"
    return rendered


def render_tool_observation_detail(raw_ref: str, max_chars: int = 6000) -> str:
    normalized_ref = str(raw_ref or "").strip()
    if not normalized_ref.startswith("toolobs://"):
        return "rawRef invalid: pass the exact toolobs://... rawRef from a prior tool output envelope."

    try:
        requested_chars = int(max_chars or 6000)
    except Exception:
        requested_chars = 6000
    requested_chars = max(500, min(requested_chars, 60000))

    try:
        from core.observability_db import observability_db

        record = observability_db.get_tool_observation_record(normalized_ref)
        if not record:
            return f"rawRef not found: {normalized_ref}"

        raw_body = str(record.get("raw_body_text") or "")
        payload = _parse_tool_observation_json(raw_body)
        if payload and str(record.get("tool_name") or "") == "runtime_broker":
            rendered = _render_runtime_route_observation_detail(payload, max_chars=requested_chars)
            if rendered:
                return rendered
        if payload and str(record.get("tool_name") or "") == "delegation_broker":
            return _render_delegation_observation_detail(payload, max_chars=requested_chars)
        if payload and (
            str(record.get("tool_name") or "") == "research_broker"
            or str(payload.get("kind") or "").startswith("research_")
            or any(key in payload for key in ("finalExperiencePack", "researchResult"))
        ):
            rendered = _render_research_observation_detail(payload, raw_ref=normalized_ref, max_chars=requested_chars)
            if rendered:
                return rendered
        if payload and (str(record.get("tool_name") or "").startswith("web_") or str(record.get("tool_name") or "") == "web_broker"):
            rendered = _render_web_observation_detail(payload, raw_ref=normalized_ref, max_chars=requested_chars)
            if rendered:
                return rendered
        if payload:
            return _render_generic_json_observation_detail(
                payload,
                record=record,
                raw_ref=normalized_ref,
                max_chars=requested_chars,
            )

        raw_preview = raw_body[:requested_chars]
        preview = _redact_tool_observation_preview(raw_preview)
        omitted_chars = max(0, len(raw_body) - len(raw_preview))
        lines = [
            "Tool observation detail",
            f"tool: {record.get('tool_name') or 'unknown'}",
            "",
            "<preview>",
            preview,
            "</preview>",
        ]
        if omitted_chars:
            lines.append(f"[omitted {omitted_chars} chars]")
        if preview != raw_preview:
            lines.append("[secrets redacted]")
        return "\n".join(lines)
    except Exception as e:
        return f"tool observation detail failed: {e}"
