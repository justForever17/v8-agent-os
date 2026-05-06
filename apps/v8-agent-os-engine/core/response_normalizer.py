from __future__ import annotations

import hashlib
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
V8_CANONICAL_TOOL_CALL_PREFIX = "call_v8_"
_TOOL_SCOPE_KEY = "v8_tool_scope_key"


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


def normalize_tool_calls(
    tool_calls: Any,
    *,
    provider_standard: str | None = None,
    message_scope_key: str | None = None,
) -> list[dict[str, Any]]:
    if not tool_calls:
        return []

    normalized: list[dict[str, Any]] = []
    iterable = tool_calls if isinstance(tool_calls, list) else [tool_calls]
    effective_provider_standard = _normalize_provider_standard(provider_standard)
    effective_scope_key = str(message_scope_key or "").strip() or f"scope_{uuid.uuid4().hex}"
    for raw_entry in iterable:
        entry = _normalize_tool_call_entry(raw_entry)
        if not entry:
            continue

        if _is_fragment_tool_call(entry) and normalized:
            normalized[-1]["args"] = _merge_tool_args(normalized[-1].get("args"), entry.get("args"))
            if entry.get("providerToolCallId") and not normalized[-1].get("providerToolCallId"):
                normalized[-1]["providerToolCallId"] = entry.get("providerToolCallId")
            if entry.get("providerStandard") and not normalized[-1].get("providerStandard"):
                normalized[-1]["providerStandard"] = entry.get("providerStandard")
            continue

        normalized.append(
            _bind_canonical_tool_call_entry(
                entry,
                provider_standard=effective_provider_standard,
                ordinal=len(normalized),
                message_scope_key=effective_scope_key,
            )
        )

    return normalized


def sanitize_model_tool_calls(response: Any, *, provider_standard: str | None = None) -> Any:
    effective_provider_standard = _resolve_provider_standard(response, provider_standard)
    message_scope_key = _ensure_message_tool_scope_key(response)
    tool_calls = normalize_tool_calls(
        _read_field(response, "tool_calls"),
        provider_standard=effective_provider_standard,
        message_scope_key=message_scope_key,
    )
    if tool_calls and hasattr(response, "tool_calls"):
        response.tool_calls = tool_calls

    additional_kwargs = _read_field(response, "additional_kwargs")
    if isinstance(additional_kwargs, dict) and additional_kwargs.get("tool_calls"):
        additional_kwargs["tool_calls"] = normalize_tool_calls(
            additional_kwargs.get("tool_calls"),
            provider_standard=effective_provider_standard,
            message_scope_key=message_scope_key,
        )
        additional_kwargs[_TOOL_SCOPE_KEY] = message_scope_key
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
            _append_unique(reasoning_parts, _first_string(block, ("reasoning", "thinking", "text", "content", "value")))
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
    explicit_provider_tool_call_id = (
        _read_field(entry, "providerToolCallId")
        or _read_field(entry, "provider_tool_call_id")
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
    canonical_tool_call_id = ""
    if is_v8_canonical_tool_call_id(tool_id):
        canonical_tool_call_id = str(tool_id).strip()
    provider_tool_call_id = str(explicit_provider_tool_call_id or "").strip()
    if not provider_tool_call_id and tool_id and not canonical_tool_call_id:
        provider_tool_call_id = str(tool_id).strip()
    provider_standard = (
        _read_field(entry, "providerStandard")
        or _read_field(entry, "provider_standard")
        or function_payload.get("providerStandard")
        or function_payload.get("provider_standard")
        or ""
    )
    ordinal = _read_field(entry, "ordinal")
    return {
        "name": name,
        "id": canonical_tool_call_id,
        "providerToolCallId": provider_tool_call_id,
        "providerStandard": provider_standard,
        "ordinal": ordinal,
        "args": parsed_args,
    }


def _bind_canonical_tool_call_entry(
    entry: Mapping[str, Any],
    *,
    provider_standard: str,
    ordinal: int,
    message_scope_key: str,
) -> dict[str, Any]:
    tool_name = str(entry.get("name") or "").strip()
    provider_tool_call_id = str(entry.get("providerToolCallId") or "").strip()
    canonical_tool_call_id = str(entry.get("id") or "").strip()
    resolved_provider_standard = _normalize_provider_standard(
        entry.get("providerStandard") or provider_standard
    )
    resolved_ordinal = _coerce_tool_call_ordinal(entry.get("ordinal"), fallback=ordinal)
    if not canonical_tool_call_id:
        canonical_tool_call_id = _build_v8_canonical_tool_call_id(
            provider_standard=resolved_provider_standard,
            provider_tool_call_id=provider_tool_call_id,
            tool_name=tool_name,
            ordinal=resolved_ordinal,
            message_scope_key=message_scope_key,
            args=entry.get("args"),
        )
    normalized = {
        "id": canonical_tool_call_id,
        "name": tool_name,
        "args": entry.get("args"),
        "providerStandard": resolved_provider_standard,
        "ordinal": resolved_ordinal,
    }
    if provider_tool_call_id:
        normalized["providerToolCallId"] = provider_tool_call_id
    return normalized


def _build_v8_canonical_tool_call_id(
    *,
    provider_standard: str,
    provider_tool_call_id: str,
    tool_name: str,
    ordinal: int,
    message_scope_key: str,
    args: Any,
) -> str:
    seed = {
        "messageScopeKey": str(message_scope_key or "").strip(),
        "providerStandard": _normalize_provider_standard(provider_standard),
        "providerToolCallId": str(provider_tool_call_id or "").strip(),
        "toolName": str(tool_name or "").strip(),
        "ordinal": int(ordinal),
    }
    if not seed["providerToolCallId"]:
        seed["argsDigest"] = _stable_value_digest(args)
    digest = hashlib.sha1(
        json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return f"{V8_CANONICAL_TOOL_CALL_PREFIX}{digest}"


def _stable_value_digest(value: Any) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        rendered = str(value)
    return hashlib.sha1(rendered.encode("utf-8")).hexdigest()[:16]


def _coerce_tool_call_ordinal(value: Any, *, fallback: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(fallback)


def _normalize_provider_standard(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"google", "google_genai"}:
        return "gemini"
    return normalized or "openai"


def _resolve_provider_standard(response: Any, explicit: str | None = None) -> str:
    if explicit:
        return _normalize_provider_standard(explicit)
    for container_name in ("response_metadata", "additional_kwargs"):
        container = _read_field(response, container_name)
        if isinstance(container, Mapping):
            for key in ("v8_provider_standard", "providerStandard", "provider_standard"):
                candidate = container.get(key)
                if candidate:
                    return _normalize_provider_standard(candidate)
    return "openai"


def _ensure_message_tool_scope_key(response: Any) -> str:
    existing = _read_existing_message_tool_scope_key(response)
    if existing:
        return existing
    fallback_message_id = str(_read_field(response, "id") or "").strip()
    scope_key = fallback_message_id or f"scope_{uuid.uuid4().hex}"
    additional_kwargs = dict(_read_field(response, "additional_kwargs") or {})
    additional_kwargs[_TOOL_SCOPE_KEY] = scope_key
    if hasattr(response, "additional_kwargs"):
        response.additional_kwargs = additional_kwargs
    elif isinstance(response, dict):
        response["additional_kwargs"] = additional_kwargs
    return scope_key


def _read_existing_message_tool_scope_key(response: Any) -> str:
    for container_name in ("additional_kwargs", "response_metadata"):
        container = _read_field(response, container_name)
        if isinstance(container, Mapping):
            candidate = str(container.get(_TOOL_SCOPE_KEY) or "").strip()
            if candidate:
                return candidate
    return ""


def is_v8_canonical_tool_call_id(value: Any) -> bool:
    normalized = str(value or "").strip()
    return bool(normalized and normalized.startswith(V8_CANONICAL_TOOL_CALL_PREFIX))


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
