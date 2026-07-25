from __future__ import annotations

import base64
import json
from copy import deepcopy
from typing import Any, Iterable, Mapping


PRIVATE_PROVIDER_CONTINUATION_KEY = "_v8_provider_continuation"
_GEMINI_SIGNATURE_MAP_KEY = "__gemini_function_call_thought_signatures__"
_EPHEMERAL_BLOCK_KEYS = {"index", "sub_index"}


def normalize_provider_standard(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"google", "google_genai", "gemini"}:
        return "gemini"
    if normalized.startswith("anthropic"):
        return "anthropic"
    if normalized.startswith("openai") or normalized in {"azure", "azure_openai"}:
        return "openai"
    return normalized


def _read_field(value: Any, key: str) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _json_safe_exact(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, Mapping):
        return {str(key): _json_safe_exact(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe_exact(item) for item in value]
    if hasattr(value, "model_dump"):
        try:
            return _json_safe_exact(value.model_dump(exclude_none=True, mode="json"))
        except Exception:
            try:
                return _json_safe_exact(value.model_dump(exclude_none=True))
            except Exception:
                return str(value)
    return str(value)


def _message_candidates(value: Any) -> Iterable[Any]:
    if value is None:
        return
    yield value
    if isinstance(value, Mapping):
        for key in ("chunk", "message", "response"):
            nested = value.get(key)
            if nested is not None and nested is not value:
                yield from _message_candidates(nested)
        output = value.get("output")
        if output is not None and output is not value:
            yield from _message_candidates(output)
        generations = value.get("generations")
        if generations is not None:
            yield from _message_candidates(generations)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _message_candidates(item)
        return
    message = getattr(value, "message", None)
    if message is not None and message is not value:
        yield from _message_candidates(message)
    generations = getattr(value, "generations", None)
    if generations is not None and generations is not value:
        yield from _message_candidates(generations)


def _iter_content_blocks(candidate: Any) -> Iterable[dict[str, Any]]:
    for field_name in ("content", "content_blocks"):
        content = _read_field(candidate, field_name)
        if not isinstance(content, (list, tuple)):
            continue
        for item in content:
            normalized = _json_safe_exact(item)
            if isinstance(normalized, dict):
                yield normalized

    if isinstance(candidate, Mapping):
        output = candidate.get("output")
        if isinstance(output, (list, tuple)):
            for item in output:
                normalized = _json_safe_exact(item)
                if isinstance(normalized, dict):
                    yield normalized


def is_provider_continuation_block(block: Any) -> bool:
    if not isinstance(block, Mapping):
        return False
    block_type = str(block.get("type") or "").strip().lower()
    if block_type == "reasoning" and str(block.get("encrypted_content") or "").strip():
        return True
    if block_type == "thinking" and str(block.get("signature") or "").strip():
        return True
    if block_type == "redacted_thinking" and str(block.get("data") or "").strip():
        return True
    if str(block.get("thoughtSignature") or block.get("thought_signature") or "").strip():
        return True
    extras = block.get("extras")
    return isinstance(extras, Mapping) and bool(
        str(
            extras.get("signature")
            or extras.get("thoughtSignature")
            or extras.get("thought_signature")
            or ""
        ).strip()
    )


def _provider_for_block(block: Mapping[str, Any]) -> str:
    block_type = str(block.get("type") or "").strip().lower()
    if block_type == "reasoning" and block.get("encrypted_content"):
        return "openai"
    if block_type == "redacted_thinking" or (block_type == "thinking" and block.get("signature")):
        return "anthropic"
    extras = block.get("extras") if isinstance(block.get("extras"), Mapping) else {}
    if (
        block.get("thoughtSignature")
        or block.get("thought_signature")
        or extras.get("signature")
        or extras.get("thoughtSignature")
        or extras.get("thought_signature")
    ):
        return "gemini"
    return ""


def _provider_from_candidate(candidate: Any) -> str:
    for container_name in ("response_metadata", "additional_kwargs"):
        container = _read_field(candidate, container_name)
        if not isinstance(container, Mapping):
            continue
        raw = str(
            container.get("v8_provider_standard")
            or container.get("providerStandard")
            or container.get("provider_standard")
            or container.get("model_provider")
            or ""
        ).strip().lower()
        if raw in {"google", "google_genai", "gemini"}:
            return "gemini"
        if raw.startswith("anthropic"):
            return "anthropic"
        if raw.startswith("openai"):
            return "openai"
    return ""


def _block_identity(block: Mapping[str, Any]) -> str:
    block_id = str(block.get("id") or "").strip()
    block_type = str(block.get("type") or "").strip().lower()
    if block_id:
        return f"{block_type}:{block_id}"
    block_index = block.get("index")
    if isinstance(block_index, int) and not isinstance(block_index, bool):
        return f"{block_type}:index:{block_index}"
    extras = block.get("extras") if isinstance(block.get("extras"), Mapping) else {}
    signature = str(
        block.get("signature")
        or block.get("thoughtSignature")
        or block.get("thought_signature")
        or extras.get("signature")
        or extras.get("thoughtSignature")
        or extras.get("thought_signature")
        or ""
    ).strip()
    if signature:
        return f"{block_type}:sig:{signature[:80]}"
    rendered = json.dumps(block, ensure_ascii=False, sort_keys=True, default=str)
    return f"{block_type}:raw:{rendered}"


def _merge_value(previous: Any, incoming: Any) -> Any:
    if previous in (None, "", [], {}):
        return deepcopy(incoming)
    if incoming in (None, "", [], {}):
        return deepcopy(previous)
    if isinstance(previous, Mapping) and isinstance(incoming, Mapping):
        merged = {str(key): deepcopy(value) for key, value in previous.items()}
        for key, value in incoming.items():
            merged[str(key)] = _merge_value(merged.get(str(key)), value)
        return merged
    if isinstance(previous, list) and isinstance(incoming, list):
        previous_size = len(json.dumps(previous, ensure_ascii=False, default=str))
        incoming_size = len(json.dumps(incoming, ensure_ascii=False, default=str))
        return deepcopy(incoming if incoming_size >= previous_size else previous)
    return deepcopy(incoming)


def merge_provider_continuations(
    previous: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> dict[str, Any]:
    left = dict(previous or {})
    right = dict(incoming or {})
    if not left:
        return deepcopy(right)
    if not right:
        return deepcopy(left)

    merged = {**deepcopy(left), **deepcopy(right)}
    block_order: list[str] = []
    blocks_by_id: dict[str, dict[str, Any]] = {}
    for block in [*list(left.get("contentBlocks") or []), *list(right.get("contentBlocks") or [])]:
        if not isinstance(block, Mapping):
            continue
        identity = _block_identity(block)
        if identity not in blocks_by_id:
            block_order.append(identity)
            blocks_by_id[identity] = dict(block)
        else:
            blocks_by_id[identity] = _merge_value(blocks_by_id[identity], dict(block))
    merged["contentBlocks"] = [blocks_by_id[identity] for identity in block_order]
    merged["additionalKwargs"] = _merge_value(
        dict(left.get("additionalKwargs") or {}),
        dict(right.get("additionalKwargs") or {}),
    )
    merged["kinds"] = list(
        dict.fromkeys([*list(left.get("kinds") or []), *list(right.get("kinds") or [])])
    )
    merged["wireProtocols"] = list(
        dict.fromkeys([*list(left.get("wireProtocols") or []), *list(right.get("wireProtocols") or [])])
    )
    return merged


def extract_provider_continuation(value: Any) -> dict[str, Any]:
    continuation: dict[str, Any] = {}
    provider_standard = ""
    response_id = ""
    blocks: list[dict[str, Any]] = []
    signature_map: dict[str, Any] = {}
    replay_additional_kwargs: dict[str, Any] = {}
    wire_protocols: list[str] = []

    for candidate in _message_candidates(value):
        provider_standard = provider_standard or _provider_from_candidate(candidate)
        response_metadata = _read_field(candidate, "response_metadata")
        response_metadata = response_metadata if isinstance(response_metadata, Mapping) else {}
        response_id = response_id or str(
            _read_field(candidate, "id")
            or response_metadata.get("id")
            or response_metadata.get("response_id")
            or response_metadata.get("codexResponseId")
            or ""
        ).strip()
        for block in _iter_content_blocks(candidate):
            if not is_provider_continuation_block(block):
                continue
            blocks.append(block)
            provider_standard = provider_standard or _provider_for_block(block)
            block_provider = _provider_for_block(block)
            block_protocol = {
                "openai": "openai.responses",
                "anthropic": "anthropic.messages",
                "gemini": "gemini.generate_content",
            }.get(block_provider)
            if block_protocol and block_protocol not in wire_protocols:
                wire_protocols.append(block_protocol)
        additional_kwargs = _read_field(candidate, "additional_kwargs")
        if isinstance(additional_kwargs, Mapping):
            raw_signature_map = additional_kwargs.get(_GEMINI_SIGNATURE_MAP_KEY)
            if isinstance(raw_signature_map, Mapping):
                signature_map.update(_json_safe_exact(raw_signature_map))
                provider_standard = provider_standard or "gemini"
            for signature_key in ("thoughtSignature", "thought_signature"):
                signature = additional_kwargs.get(signature_key)
                if signature not in (None, "", b""):
                    replay_additional_kwargs[signature_key] = _json_safe_exact(signature)
                    provider_standard = provider_standard or "gemini"

    if not blocks and not signature_map and not replay_additional_kwargs:
        return {}
    kinds = []
    for block in blocks:
        block_type = str(block.get("type") or "provider_continuation").strip()
        if block_type not in kinds:
            kinds.append(block_type)
    if signature_map:
        kinds.append("gemini_function_call_thought_signatures")
    if replay_additional_kwargs:
        kinds.append("gemini_part_thought_signature")
    continuation = {
        "schemaVersion": 1,
        "providerStandard": provider_standard or "unknown",
        "contentBlocks": [],
        "additionalKwargs": {
            **replay_additional_kwargs,
            **({_GEMINI_SIGNATURE_MAP_KEY: signature_map} if signature_map else {}),
        },
        "kinds": kinds,
        "wireProtocols": wire_protocols,
    }
    if response_id:
        continuation["responseId"] = response_id
    for block in blocks:
        continuation = merge_provider_continuations(
            continuation,
            {
                "schemaVersion": 1,
                "providerStandard": provider_standard or _provider_for_block(block) or "unknown",
                "contentBlocks": [block],
                "additionalKwargs": {},
                "kinds": [str(block.get("type") or "provider_continuation")],
                "wireProtocols": wire_protocols,
            },
        )
    return continuation


def provider_continuation_matches_target(
    continuation: Mapping[str, Any] | None,
    *,
    target_provider: str,
    target_wire_protocol: str = "",
    target_provider_adapter: str = "",
) -> bool:
    """Return whether opaque provider state can be sent to the selected model.

    Provider continuation is private state, not generic chat content.  A known
    wire protocol must match exactly; OpenAI encrypted reasoning is only valid
    on the Responses protocol (or the explicit Codex Responses adapter).  If
    no target metadata is available callers may choose a legacy compatibility
    path, but the prepared chat runtime always supplies the catalog metadata.
    """
    normalized = dict(continuation or {})
    if not normalized:
        return True
    source_provider = normalize_provider_standard(normalized.get("providerStandard"))
    destination_provider = normalize_provider_standard(target_provider)
    if source_provider and destination_provider and source_provider != destination_provider:
        return False

    required_protocols = [
        str(item).strip().lower()
        for item in list(normalized.get("wireProtocols") or [])
        if str(item).strip()
    ]
    if not required_protocols:
        for block in list(normalized.get("contentBlocks") or []):
            if isinstance(block, Mapping):
                provider = _provider_for_block(block)
                protocol = {
                    "openai": "openai.responses",
                    "anthropic": "anthropic.messages",
                    "gemini": "gemini.generate_content",
                }.get(provider)
                if protocol and protocol not in required_protocols:
                    required_protocols.append(protocol)

    destination_protocol = str(target_wire_protocol or "").strip().lower()
    if destination_protocol:
        return all(protocol == destination_protocol for protocol in required_protocols)
    if source_provider == "openai" and any(protocol == "openai.responses" for protocol in required_protocols):
        return str(target_provider_adapter or "").strip().lower() == "openai-codex-responses"
    return True


def provider_continuation_from_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    value = dict(metadata or {}).get(PRIVATE_PROVIDER_CONTINUATION_KEY)
    return deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def strip_private_provider_continuation(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    public_metadata = dict(metadata or {})
    public_metadata.pop(PRIVATE_PROVIDER_CONTINUATION_KEY, None)
    return public_metadata


def replay_content_blocks(continuation: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for item in list(dict(continuation or {}).get("contentBlocks") or []):
        if not isinstance(item, Mapping) or not is_provider_continuation_block(item):
            continue
        blocks.append({key: deepcopy(value) for key, value in item.items() if key not in _EPHEMERAL_BLOCK_KEYS})
    return blocks


def build_replay_ai_message_payload(
    visible_content: str,
    continuation: Mapping[str, Any] | None,
) -> tuple[str | list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    normalized = dict(continuation or {})
    blocks = replay_content_blocks(normalized)
    content: str | list[dict[str, Any]] = str(visible_content or "")
    if blocks:
        content = list(blocks)
        signed_visible_text = "".join(
            str(block.get("text") or block.get("content") or "")
            for block in blocks
            if str(block.get("text") or block.get("content") or "")
        )
        if visible_content and str(visible_content) not in signed_visible_text:
            content.append({"type": "text", "text": str(visible_content)})
    additional_kwargs = deepcopy(dict(normalized.get("additionalKwargs") or {}))
    response_metadata: dict[str, Any] = {}
    provider_standard = str(normalized.get("providerStandard") or "").strip()
    if provider_standard:
        response_metadata["v8_provider_standard"] = provider_standard
    response_id = str(normalized.get("responseId") or "").strip()
    if response_id:
        response_metadata["id"] = response_id
    return content, additional_kwargs, response_metadata


def has_provider_continuation(value: Any) -> bool:
    if isinstance(value, Mapping) and PRIVATE_PROVIDER_CONTINUATION_KEY in value:
        return bool(provider_continuation_from_metadata(value))
    return bool(extract_provider_continuation(value))


def estimate_provider_continuation_tokens(value: Any) -> int:
    continuation = (
        provider_continuation_from_metadata(value)
        if isinstance(value, Mapping) and PRIVATE_PROVIDER_CONTINUATION_KEY in value
        else extract_provider_continuation(value)
    )
    if not continuation:
        return 0
    rendered = json.dumps(continuation, ensure_ascii=False, separators=(",", ":"), default=str)
    return max(1, len(rendered) // 4)
