from __future__ import annotations

import json
import re
from typing import Any

from .budget import JSON_PRIORITY_KEYS, _text_for_token_estimate

WORKER_RESULT_RE = re.compile(
    r"<V8_WORKER_RESULT\b[^>]*>.*?</V8_WORKER_RESULT>",
    re.IGNORECASE | re.DOTALL,
)

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
def _parse_json_object(text: str) -> dict[str, Any] | None:
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
def _looks_like_structured_json_prefix(text: str) -> bool:
    stripped = str(text or "").lstrip()
    if stripped.startswith("{"):
        return True
    if not stripped.startswith("["):
        return False
    # Bracketed control markers such as ``[route required]`` are readable
    # protocol text, not broken JSON. Arrays must begin with a JSON value.
    return bool(re.match(r'^\[\s*(?:[\{\[\"]|-?\d|true\b|false\b|null\b|\])', stripped, re.IGNORECASE))
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
