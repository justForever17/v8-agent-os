"""Pure provider/SDK usage normalization; no pricing or cache-hit inference."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any


_CACHE_READ_KEYS = {
    "cached_tokens", "cachedTokens", "cached_input_tokens", "cachedInputTokens",
    "cache_read_input_tokens", "cacheReadInputTokens", "prompt_cache_hit_tokens",
    "promptCacheHitTokens", "cachedContentTokenCount", "cache_read",
    "priority_cache_read", "flex_cache_read",
}
_CACHE_WRITE_KEYS = {
    "cache_creation_input_tokens", "cacheCreationInputTokens", "cache_write_tokens",
    "cacheWriteTokens", "cache_write_input_tokens", "cacheWriteInputTokens",
    "cache_creation", "cache_write", "priority_cache_creation", "flex_cache_creation",
}


def token_count(value: Any) -> int | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
        return int(value) if math.isfinite(number) and number >= 0 and number.is_integer() else None
    except (TypeError, ValueError, OverflowError):
        return None


def _find_count(value: Any, keys: set[str], *, depth: int = 0) -> int | None:
    if depth > 6:
        return None
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in keys and (count := token_count(item)) is not None:
                return count
        for item in value.values():
            if (count := _find_count(item, keys, depth=depth + 1)) is not None:
                return count
    elif isinstance(value, (list, tuple)):
        for item in value:
            if (count := _find_count(item, keys, depth=depth + 1)) is not None:
                return count
    return None


def cache_token_counts(value: Any) -> dict[str, int | None]:
    """Read reported counts; absent/invalid remains unknown, including old logs.

    Do not sum copies of usage repeated in response metadata and SDK metadata.
    The canonical persisted record takes precedence over legacy field aliases.
    """
    canonical = value.get("cacheUsage") if isinstance(value, Mapping) else None
    if isinstance(canonical, Mapping):
        return {"readTokens": token_count(canonical.get("readTokens")),
                "writeTokens": token_count(canonical.get("writeTokens"))}
    return {"readTokens": _find_count(value, _CACHE_READ_KEYS),
            "writeTokens": _find_count(value, _CACHE_WRITE_KEYS)}


def normalize_usage_mapping(payload: Mapping[str, Any]) -> dict[str, int]:
    def maximum(keys: tuple[str, ...]) -> int:
        return max((token_count(payload.get(key)) or 0 for key in keys), default=0)

    input_tokens = maximum(("prompt_tokens", "input_tokens", "inputTokenCount", "prompt_token_count", "promptTokenCount"))
    output_tokens = maximum(("completion_tokens", "output_tokens", "candidates_token_count", "outputTokenCount", "candidatesTokenCount"))
    total_tokens = maximum(("total_tokens", "totalTokenCount", "total_token_count"))
    # Raw Anthropic usage excludes cached tokens from input_tokens. LangChain's
    # normalized input_tokens already includes them: never add its details twice.
    if ("input_tokens" in payload and "prompt_tokens" not in payload
            and not isinstance(payload.get("input_token_details"), Mapping)
            and any(key in payload for key in ("cache_read_input_tokens", "cache_creation_input_tokens"))):
        input_tokens += token_count(payload.get("cache_read_input_tokens")) or 0
        input_tokens += token_count(payload.get("cache_creation_input_tokens")) or 0
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "total_tokens": max(total_tokens, input_tokens + output_tokens)}
