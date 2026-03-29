from __future__ import annotations

import json
import uuid
from typing import Any, Iterable, Mapping, Tuple


REASONING_KEYS = (
    "reasoning_content",
    "reasoning",
    "thinking",
    "thinking_delta",
    "reasoning_details",
    "thought",
    "analysis",
    "deliberation",
)

SIGNATURE_KEYS = ("thoughtSignature", "thought_signature")
TEXT_BLOCK_TYPES = {"text", "output_text", "plain_text"}
REASONING_BLOCK_TYPES = {"reasoning", "reasoning_content", "thinking", "thought"}


def extract_text_and_reasoning(message: Any) -> Tuple[str, str]:
    """Best-effort normalization for heterogeneous provider outputs."""
    candidate = _unwrap_message_candidate(message)
    if candidate is None:
        return "", ""

    text_parts: list[str] = []
    reasoning_parts: list[str] = []

    content_blocks = _read_field(candidate, "content_blocks")
    if isinstance(content_blocks, Iterable) and not isinstance(content_blocks, (str, bytes, dict)):
        block_text, block_reasoning = _extract_from_content_blocks(content_blocks)
        _append_unique(text_parts, block_text)
        _append_unique(reasoning_parts, block_reasoning)

    content_value = _read_field(candidate, "content")
    content_text, content_reasoning = _extract_from_content(content_value)
    _append_unique(text_parts, content_text)
    _append_unique(reasoning_parts, content_reasoning)

    for container_name in ("additional_kwargs", "response_metadata", "generation_info"):
        container = _read_field(candidate, container_name)
        if isinstance(container, Mapping):
            _append_unique(reasoning_parts, _extract_reasoning_payload(container))

    if isinstance(candidate, Mapping):
        _append_unique(reasoning_parts, _extract_reasoning_payload(candidate))

    return "".join(text_parts), "".join(reasoning_parts)


def ensure_reasoning_content(message: Any) -> Any:
    """Keep reasoning_content available for providers that require it with tool calls."""
    tool_calls = _read_field(message, "tool_calls")
    if not tool_calls:
        return message
    additional_kwargs = dict(_read_field(message, "additional_kwargs") or {})
    if "reasoning_content" not in additional_kwargs:
        _, reasoning = extract_text_and_reasoning(message)
        additional_kwargs["reasoning_content"] = reasoning or ""
    if hasattr(message, "additional_kwargs"):
        message.additional_kwargs = additional_kwargs
    elif isinstance(message, dict):
        message["additional_kwargs"] = additional_kwargs
    return message


def normalize_tool_calls(tool_calls: Any) -> list[dict[str, Any]]:
    if not tool_calls:
        return []

    normalized: list[dict[str, Any]] = []
    iterable = tool_calls if isinstance(tool_calls, list) else [tool_calls]
    for raw_entry in iterable:
        entry = _normalize_tool_call_entry(raw_entry)
        if not entry:
            continue

        if _is_fragment_tool_call(entry) and normalized:
            normalized[-1]["args"] = _merge_tool_args(normalized[-1].get("args"), entry.get("args"))
            continue

        if not entry.get("id"):
            entry["id"] = f"call_{uuid.uuid4().hex[:12]}"
        normalized.append(entry)

    return normalized


def sanitize_model_tool_calls(response: Any) -> Any:
    tool_calls = normalize_tool_calls(_read_field(response, "tool_calls"))
    if tool_calls and hasattr(response, "tool_calls"):
        response.tool_calls = tool_calls

    additional_kwargs = _read_field(response, "additional_kwargs")
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
        additional_kwargs["tool_calls"] = normalize_tool_calls(additional_kwargs.get("tool_calls"))
        if hasattr(response, "additional_kwargs"):
            response.additional_kwargs = additional_kwargs

    return ensure_reasoning_content(response)


def _unwrap_message_candidate(message: Any) -> Any:
    if message is None:
        return None

    if isinstance(message, dict):
        for key in ("message", "output", "chunk", "response"):
            nested = message.get(key)
            if nested is not None:
                return _unwrap_message_candidate(nested)

        generations = message.get("generations")
        if isinstance(generations, list) and generations:
            first_generation = generations[0]
            if isinstance(first_generation, list) and first_generation:
                first_item = first_generation[0]
                nested_message = _read_field(first_item, "message")
                return _unwrap_message_candidate(nested_message or first_item)
            return _unwrap_message_candidate(first_generation)

    return message


def _extract_from_content(content: Any) -> Tuple[str, str]:
    if isinstance(content, str):
        return content, ""

    if isinstance(content, list):
        return _extract_from_content_blocks(content)

    if isinstance(content, dict):
        text_value = _first_string(content, ("text", "content", "value"))
        reasoning_value = _extract_reasoning_payload(content)
        return text_value, reasoning_value

    return "", ""


def _extract_from_content_blocks(blocks: Iterable[Any]) -> Tuple[str, str]:
    text_parts: list[str] = []
    reasoning_parts: list[str] = []

    for block in blocks:
        block_type = str(_read_field(block, "type") or "").lower()
        if block_type in TEXT_BLOCK_TYPES:
            _append_unique(text_parts, _first_string(block, ("text", "content", "value")))
            continue

        if block_type in REASONING_BLOCK_TYPES:
            _append_unique(reasoning_parts, _first_string(block, ("reasoning", "text", "content", "value")))
            continue

        if isinstance(block, Mapping):
            _append_unique(reasoning_parts, _extract_reasoning_payload(block))
            if not block_type:
                _append_unique(text_parts, _first_string(block, ("text", "content")))

    return "".join(text_parts), "".join(reasoning_parts)


def _extract_reasoning_payload(payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in REASONING_KEYS:
        value = payload.get(key)
        _append_unique(parts, _stringify_reasoning_value(value))

    for key in SIGNATURE_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            _append_unique(parts, f"[{key}] {value}")

    return "\n".join(parts)


def _stringify_reasoning_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()

    if isinstance(value, (int, float, bool)):
        return str(value)

    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            _append_unique(parts, _stringify_reasoning_value(item))
        return "\n".join(parts)

    if isinstance(value, Mapping):
        # Prefer human-readable text-bearing keys before falling back to JSON.
        for key in ("text", "content", "value", "summary", "output", "reasoning"):
            candidate = value.get(key)
            rendered = _stringify_reasoning_value(candidate)
            if rendered:
                return rendered
        flattened: list[str] = []
        for nested in value.values():
            _append_unique(flattened, _stringify_reasoning_value(nested))
        if flattened:
            return "\n".join(flattened)
        try:
            return json.dumps(value, ensure_ascii=False)
        except Exception:
            return ""

    return ""


def _normalize_tool_call_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, Mapping):
        return None

    function_payload = entry.get("function") if isinstance(entry.get("function"), Mapping) else {}
    name = (
        _read_field(entry, "name")
        or function_payload.get("name")
        or _read_field(entry, "tool_name")
        or ""
    )
    tool_id = (
        _read_field(entry, "id")
        or _read_field(entry, "tool_call_id")
        or function_payload.get("id")
        or ""
    )
    args = (
        _read_field(entry, "args")
        or entry.get("arguments")
        or function_payload.get("arguments")
        or entry.get("input")
        or {}
    )
    parsed_args = _parse_tool_args(args)
    return {"name": name, "id": tool_id, "args": parsed_args}


def _parse_tool_args(args: Any) -> Any:
    if isinstance(args, str):
        stripped = args.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except Exception:
                return stripped
        return stripped
    return args


def _is_fragment_tool_call(entry: Mapping[str, Any]) -> bool:
    return not entry.get("name") and not entry.get("id")


def _merge_tool_args(previous: Any, new_value: Any) -> Any:
    if isinstance(previous, dict) and isinstance(new_value, dict):
        merged = dict(previous)
        merged.update(new_value)
        return merged
    if isinstance(previous, str) and isinstance(new_value, str):
        return f"{previous}{new_value}"
    if previous in (None, "", {}):
        return new_value
    if new_value in (None, "", {}):
        return previous
    return new_value


def _first_string(obj: Any, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _read_field(obj, key)
        if isinstance(value, str) and value:
            return value
    return ""


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def _read_field(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return obj.get(key)
    if hasattr(obj, key):
        return getattr(obj, key)
    getter = getattr(obj, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except Exception:
            return None
    return None
