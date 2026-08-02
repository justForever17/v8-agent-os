from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from core.reasoning_payload_contract import (
    REASONING_KEY_SET,
    SIGNATURE_KEY_SET,
    VISIBLE_TEXT_KEYS,
    VISIBLE_BLOCK_TYPES,
    REASONING_BLOCK_TYPES,
    THINK_TAG_PATTERN,
)

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}|\[[\s\S]*\]")


@dataclass(frozen=True)
class BackgroundModelOutput:
    text: str
    visible_chars: int
    reasoning_stripped: bool
    reasoning_chars: int
    stripped_keys: tuple[str, ...]
    source_kind: str = "unknown"
    no_visible_text_reason: str | None = None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "backgroundOutputSanitized": True,
            "reasoningStripped": self.reasoning_stripped,
            "reasoningChars": self.reasoning_chars,
            "strippedKeys": list(self.stripped_keys),
            "visibleChars": self.visible_chars,
            "sourceKind": self.source_kind,
            "parseFailureReason": self.no_visible_text_reason,
        }


def sanitize_background_model_output(response: Any) -> BackgroundModelOutput:
    """Extract only visible model text for background agents.

    Foreground chat surfaces may display provider reasoning through their own
    contract. Background agents must not consume reasoning as business output.
    """

    state = _SanitizeState()
    text = _extract_visible_text(response, state=state, source_kind="response")
    text = _strip_think_tags(text, state)
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return BackgroundModelOutput(
        text=normalized,
        visible_chars=len(normalized),
        reasoning_stripped=state.reasoning_chars > 0 or bool(state.stripped_keys),
        reasoning_chars=state.reasoning_chars,
        stripped_keys=tuple(sorted(state.stripped_keys)),
        source_kind=state.source_kind,
        no_visible_text_reason=None if normalized else "background_output_no_visible_text",
    )


def extract_reasoning_token_count(details: Mapping[str, Any] | None) -> int | None:
    """Read provider token accounting without exposing reasoning payload text."""

    normalized = details if isinstance(details, Mapping) else {}
    for key in ("reasoning_tokens", "reasoning"):
        try:
            value = int(normalized.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def parse_background_json_object(response: Any) -> tuple[dict[str, Any] | None, BackgroundModelOutput, str | None]:
    sanitized = sanitize_background_model_output(response)
    if not sanitized.text:
        return None, sanitized, "background_output_no_visible_text"
    for candidate in _json_candidates(sanitized.text):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed, sanitized, None
    return None, sanitized, "background_json_parse_failed"


def parse_background_json_array(response: Any) -> tuple[list[Any] | None, BackgroundModelOutput, str | None]:
    sanitized = sanitize_background_model_output(response)
    if not sanitized.text:
        return None, sanitized, "background_output_no_visible_text"
    for candidate in _json_candidates(sanitized.text):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, list):
            return parsed, sanitized, None
    return None, sanitized, "background_json_parse_failed"


class _SanitizeState:
    def __init__(self) -> None:
        self.reasoning_chars = 0
        self.stripped_keys: set[str] = set()
        self.source_kind = "unknown"


def _extract_visible_text(value: Any, *, state: _SanitizeState, source_kind: str) -> str:
    if value is None:
        state.source_kind = source_kind
        return ""
    content = getattr(value, "content", None)
    if content is not None and value is not content:
        _collect_reasoning_from_object(value, state=state)
        state.source_kind = value.__class__.__name__
        return _extract_visible_text(content, state=state, source_kind="message_content")
    if isinstance(value, str):
        state.source_kind = source_kind
        return value
    if isinstance(value, list):
        state.source_kind = source_kind
        parts: list[str] = []
        for item in value:
            text = _extract_visible_text_from_block(item, state=state)
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, Mapping):
        state.source_kind = source_kind
        return _extract_visible_text_from_block(value, state=state)
    state.source_kind = source_kind
    return str(value or "")


def _extract_visible_text_from_block(block: Any, *, state: _SanitizeState) -> str:
    if block is None:
        return ""
    if isinstance(block, str):
        return block
    if isinstance(block, list):
        return "\n".join(_extract_visible_text_from_block(item, state=state) for item in block).strip()
    if not isinstance(block, Mapping):
        return str(block or "")

    block_type = str(block.get("type") or block.get("kind") or "").strip()
    lowered_type = block_type.lower()
    if lowered_type in REASONING_BLOCK_TYPES:
        _record_reasoning(block, state=state, key=block_type or "reasoning_block")
        return ""

    for key in REASONING_KEY_SET | SIGNATURE_KEY_SET:
        if key in block:
            _record_reasoning(block.get(key), state=state, key=key)

    for key in VISIBLE_TEXT_KEYS:
        value = block.get(key)
        if isinstance(value, str) and value.strip():
            if lowered_type and lowered_type not in VISIBLE_BLOCK_TYPES and key in {"content", "value"}:
                continue
            return value
    return ""


def _collect_reasoning_from_object(value: Any, *, state: _SanitizeState) -> None:
    for container_name in ("additional_kwargs", "response_metadata", "generation_info"):
        container = getattr(value, container_name, None)
        if isinstance(container, Mapping):
            for key in REASONING_KEY_SET | SIGNATURE_KEY_SET:
                if key in container:
                    _record_reasoning(container.get(key), state=state, key=key)
    for key in REASONING_KEY_SET | SIGNATURE_KEY_SET:
        if hasattr(value, key):
            _record_reasoning(getattr(value, key), state=state, key=key)


def _record_reasoning(value: Any, *, state: _SanitizeState, key: str) -> None:
    state.stripped_keys.add(str(key))
    state.reasoning_chars += len(_stringify_reasoning_for_count(value))


def _stringify_reasoning_for_count(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _strip_think_tags(text: str, state: _SanitizeState) -> str:
    def _replace(match: re.Match[str]) -> str:
        state.stripped_keys.add("think_tag")
        state.reasoning_chars += len(match.group(0))
        return ""

    return THINK_TAG_PATTERN.sub(_replace, str(text or ""))


def _json_candidates(text: str) -> Iterable[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    candidates: list[str] = [normalized]
    fenced = normalized.replace("```json", "```")
    if "```" in fenced:
        candidates.extend(segment.strip() for segment in fenced.split("```") if segment.strip())
    match = _JSON_BLOCK_RE.search(normalized)
    if match:
        candidates.append(match.group(0).strip())
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped
