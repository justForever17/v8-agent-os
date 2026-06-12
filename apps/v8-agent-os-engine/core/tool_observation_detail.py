from __future__ import annotations

import json
import re
from typing import Any


_TOOL_OBSERVATION_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([^\s,;\"']{8,})"),
    re.compile(r"(?i)(bearer)\s+([A-Za-z0-9._~+/=-]{12,})"),
]


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

    lines.append("")
    lines.append(f"rawRef: {raw_ref}")
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

    lines.extend(["", f"rawRef: {raw_ref}"])
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

        raw_preview = raw_body[:requested_chars]
        preview = _redact_tool_observation_preview(raw_preview)
        omitted_chars = max(0, len(raw_body) - len(raw_preview))
        lines = [
            "Tool observation detail",
            f"rawRef: {normalized_ref}",
            f"tool: {record.get('tool_name') or 'unknown'}",
            f"runtime: {record.get('runtime_kind') or 'unknown'}",
            f"chars: raw={record.get('raw_chars') or 0}, visible={record.get('visible_chars') or 0}",
            f"sha256: {record.get('raw_sha256') or 'unknown'}",
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
