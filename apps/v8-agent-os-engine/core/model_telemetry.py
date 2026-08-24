from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult

from core.database import db
from core.response_normalizer import extract_text_and_reasoning
from core.time_truth import utc_now_iso
from erc.runtime_context import get_runtime_context


def _utc_now() -> str:
    return utc_now_iso()


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(value)
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def _extract_usage_from_mapping(payload: Mapping[str, Any]) -> Dict[str, int]:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0

    for key in ("prompt_tokens", "input_tokens", "inputTokenCount", "prompt_token_count"):
        input_tokens = max(input_tokens, _safe_int(payload.get(key)))
    for key in ("completion_tokens", "output_tokens", "candidates_token_count", "outputTokenCount"):
        output_tokens = max(output_tokens, _safe_int(payload.get(key)))
    for key in ("total_tokens", "totalTokenCount", "total_token_count"):
        total_tokens = max(total_tokens, _safe_int(payload.get(key)))

    if not total_tokens:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


_USAGE_FIELD_KEYS = {
    "prompt_tokens",
    "input_tokens",
    "inputTokenCount",
    "prompt_token_count",
    "completion_tokens",
    "output_tokens",
    "candidates_token_count",
    "outputTokenCount",
    "total_tokens",
    "totalTokenCount",
    "total_token_count",
}


def _usage_from_mapping_tree(
    payload: Mapping[str, Any],
    *,
    source: str,
    depth: int = 0,
) -> tuple[Dict[str, int], str, bool]:
    direct_reported = any(key in payload for key in _USAGE_FIELD_KEYS)
    best = _extract_usage_from_mapping(payload)
    best_source = source if direct_reported else ""
    reported = direct_reported
    if depth >= 5:
        return best, best_source, reported
    for key, value in payload.items():
        if not isinstance(value, Mapping):
            continue
        nested, nested_source, nested_reported = _usage_from_mapping_tree(
            value,
            source=f"{source}.{key}" if source else str(key),
            depth=depth + 1,
        )
        reported = reported or nested_reported
        if nested["total_tokens"] > best["total_tokens"]:
            best = nested
            best_source = nested_source
        elif not best_source and nested_reported:
            best_source = nested_source
    return best, best_source, reported


def extract_token_usage_details(response: Any) -> tuple[Dict[str, int], str, bool]:
    candidates: list[tuple[Dict[str, int], str, bool]] = []
    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, Mapping):
        candidates.append(_usage_from_mapping_tree(llm_output, source="llm_output"))
    for label, candidate in (
        ("response.usage_metadata", getattr(response, "usage_metadata", None)),
        ("response.response_metadata", getattr(response, "response_metadata", None)),
    ):
        if isinstance(candidate, Mapping):
            candidates.append(_usage_from_mapping_tree(candidate, source=label))

    generations = getattr(response, "generations", None) or []
    for group_index, generation_group in enumerate(generations):
        if not isinstance(generation_group, (list, tuple)):
            continue
        for generation_index, generation in enumerate(generation_group):
            message = getattr(generation, "message", None)
            for label, candidate in (
                ("message.usage_metadata", getattr(message, "usage_metadata", None)),
                ("message.response_metadata", getattr(message, "response_metadata", None)),
                ("generation.generation_info", getattr(generation, "generation_info", None)),
            ):
                if isinstance(candidate, Mapping):
                    candidates.append(
                        _usage_from_mapping_tree(
                            candidate,
                            source=f"generations[{group_index}][{generation_index}].{label}",
                        )
                    )

    if not candidates:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "", False
    usage, source, reported = max(candidates, key=lambda item: item[0]["total_tokens"])
    if not source:
        source = next((item[1] for item in candidates if item[2] and item[1]), "")
    return usage, source, any(item[2] for item in candidates)


def extract_token_usage(response: Any) -> Dict[str, int]:
    usage, _source, _reported = extract_token_usage_details(response)
    return usage


def _collapse_repeated_string(value: Any) -> Any:
    if not isinstance(value, str) or len(value) < 2:
        return value
    length = len(value)
    for unit_size in range(1, (length // 2) + 1):
        if length % unit_size != 0:
            continue
        unit = value[:unit_size]
        if unit and unit * (length // unit_size) == value:
            return unit
    return value


def _normalize_prompt_cache_payload(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    return {str(key): _collapse_repeated_string(item) for key, item in dict(value).items()}


def _short_hash(value: Any, length: int = 10) -> str:
    text = str(value or "")
    return text[:length] if text else ""


def _provider_patch_kind(patch: Any) -> str:
    if not isinstance(patch, Mapping) or not patch:
        return "none"
    if patch.get("prompt_cache_key") and patch.get("extra_headers"):
        return "prompt_cache_key+header"
    if patch.get("prompt_cache_key"):
        return "prompt_cache_key"
    if patch.get("cache_control"):
        return "cache_control"
    extra_body = patch.get("extra_body")
    if isinstance(extra_body, Mapping) and extra_body.get("caching"):
        return "extra_body.caching"
    if patch.get("observeOnly"):
        return "observe_only"
    return "provider_patch"


def _segment_token_summary(segments: Any) -> Dict[str, Any]:
    summary: Dict[str, Dict[str, int]] = {}
    if not isinstance(segments, list):
        segments = []
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        segment_type = str(segment.get("type") or segment.get("segment_type") or "unknown")
        bucket = summary.setdefault(segment_type, {"segments": 0, "estimatedTokens": 0})
        bucket["segments"] += 1
        bucket["estimatedTokens"] += _safe_int(segment.get("estimatedTokens") or segment.get("estimated_tokens"))
    static_tokens = sum(
        int(summary.get(key, {}).get("estimatedTokens") or 0)
        for key in ("stable_static", "scoped_static")
    )
    dynamic_tokens = int(summary.get("dynamic", {}).get("estimatedTokens") or 0)
    unsafe_tokens = int(summary.get("unsafe", {}).get("estimatedTokens") or 0)
    return {
        "byType": summary,
        "staticTokens": static_tokens,
        "dynamicTokens": dynamic_tokens,
        "unsafeTokens": unsafe_tokens,
        "totalTokens": static_tokens + dynamic_tokens + unsafe_tokens,
    }


_CACHED_TOKEN_KEYS = {
    "cached_tokens",
    "cachedTokens",
    "cached_input_tokens",
    "cachedInputTokens",
    "cache_read_input_tokens",
    "cacheReadInputTokens",
    "prompt_cache_hit_tokens",
    "promptCacheHitTokens",
}


def _find_cached_input_tokens(value: Any, *, depth: int = 0) -> int | None:
    if depth > 4:
        return None
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key) in _CACHED_TOKEN_KEYS:
                return _safe_int(item)
            found = _find_cached_input_tokens(item, depth=depth + 1)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_cached_input_tokens(item, depth=depth + 1)
            if found is not None:
                return found
    return None


def _public_prompt_cache_summary(metadata: Mapping[str, Any] | None, *, prefix_use_counts: Mapping[str, int], input_tokens: int) -> Dict[str, Any] | None:
    if not isinstance(metadata, Mapping):
        return None
    prompt_cache = metadata.get("promptCache")
    if not isinstance(prompt_cache, Mapping):
        return None
    prefix_key = str(prompt_cache.get("staticPrefixKey") or "")
    cached_tokens = _find_cached_input_tokens(metadata)
    return {
        "eventId": str(prompt_cache.get("eventId") or ""),
        "profileId": str(prompt_cache.get("profileId") or ""),
        "responseCacheDecision": str(prompt_cache.get("responseCacheDecision") or ""),
        "skipReason": str(prompt_cache.get("skipReason") or ""),
        "providerPatchKind": _provider_patch_kind(prompt_cache.get("providerRequestPatch")),
        "staticPrefixKeyShort": _short_hash(prefix_key),
        "staticPrefixReused": bool(prefix_key and int(prefix_use_counts.get(prefix_key) or 0) > 1),
        "staticPrefixUseCount": int(prefix_use_counts.get(prefix_key) or 0) if prefix_key else 0,
        "segments": _segment_token_summary(prompt_cache.get("segments")),
        "providerCachedTokensReported": cached_tokens is not None,
        "providerCachedInputTokens": cached_tokens,
        "cachedInputTokenRate": round(float(cached_tokens) / float(input_tokens), 4) if cached_tokens is not None and input_tokens > 0 else None,
    }


def _public_invocation_record(item: Mapping[str, Any], *, prefix_use_counts: Mapping[str, int]) -> Dict[str, Any]:
    input_tokens = _safe_int(item.get("input_tokens"))
    public = {
        "id": item.get("id"),
        "model_id": item.get("model_id"),
        "provider_name": item.get("provider_name"),
        "status": item.get("status"),
        "input_tokens": input_tokens,
        "output_tokens": _safe_int(item.get("output_tokens")),
        "total_tokens": _safe_int(item.get("total_tokens")),
        "latency_ms": _safe_float(item.get("latency_ms")),
        "started_at": item.get("started_at"),
        "role": item.get("role"),
    }
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    telemetry_keys = (
        "timeToFirstChunkMs",
        "timeToFirstContentChunkMs",
        "streamChunkCount",
        "streamChunkCharCount",
        "maxInterChunkGapMs",
        "streamActiveMs",
        "tailAfterLastChunkMs",
        "usageReported",
        "usageSource",
        "finishReason",
        "serviceTier",
        "requestedMaxTokens",
        "streamUsageRequested",
    )
    telemetry = {
        key: metadata.get(key)
        for key in telemetry_keys
        if metadata.get(key) not in (None, "")
    }
    if telemetry:
        public["telemetry"] = telemetry
    prompt_cache = _public_prompt_cache_summary(
        metadata,
        prefix_use_counts=prefix_use_counts,
        input_tokens=input_tokens,
    )
    if prompt_cache:
        public["promptCache"] = prompt_cache
    return public


def _runtime_diagnostics_from_mapping(payload: Mapping[str, Any]) -> Dict[str, Any]:
    diagnostics: Dict[str, Any] = {}
    if payload.get("v8_provider_adapter"):
        diagnostics["providerAdapter"] = payload.get("v8_provider_adapter")
    if payload.get("v8_provider_adapter_label"):
        diagnostics["providerAdapterLabel"] = payload.get("v8_provider_adapter_label")
    if payload.get("v8_effective_capability_matrix"):
        diagnostics["effectiveCapabilityMatrix"] = payload.get("v8_effective_capability_matrix")
    if payload.get("v8_tool_calling_mode"):
        diagnostics["toolCallingMode"] = payload.get("v8_tool_calling_mode")
    if payload.get("v8_structured_output_mode"):
        diagnostics["structuredOutputMode"] = payload.get("v8_structured_output_mode")
    if payload.get("v8_stream_mode"):
        diagnostics["streamMode"] = payload.get("v8_stream_mode")
    if payload.get("v8_prompt_cache"):
        diagnostics["promptCache"] = _normalize_prompt_cache_payload(payload.get("v8_prompt_cache"))
    if payload.get("promptCache"):
        diagnostics.setdefault("promptCache", _normalize_prompt_cache_payload(payload.get("promptCache")))
    for key in ("finish_reason", "finishReason", "stop_reason", "stopReason"):
        if payload.get(key) not in (None, ""):
            diagnostics["finishReason"] = str(payload.get(key))[:120]
            break
    for key in ("service_tier", "serviceTier"):
        if payload.get(key) not in (None, ""):
            diagnostics["serviceTier"] = str(payload.get(key))[:120]
            break
    return diagnostics


def _is_repeated_string_update(existing: Any, value: Any) -> bool:
    if not isinstance(existing, str) or not isinstance(value, str):
        return False
    if not existing or value == existing:
        return value == existing
    return len(value) > len(existing) and len(value) % len(existing) == 0 and value == existing * (len(value) // len(existing))


def _merge_runtime_diagnostics(base: Dict[str, Any], update: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not update:
        return base
    for key, value in update.items():
        if value is None or value == "":
            continue
        if key == "promptCache" and isinstance(value, Mapping):
            existing = dict(base.get("promptCache") or {})
            for nested_key, nested_value in dict(_normalize_prompt_cache_payload(value)).items():
                if nested_value is None or nested_value == "":
                    continue
                current_value = existing.get(nested_key)
                if current_value not in (None, "") and _is_repeated_string_update(current_value, nested_value):
                    continue
                existing[nested_key] = nested_value
            base["promptCache"] = existing
        elif key in {"finishReason", "serviceTier"}:
            # Provider chunks may expose a provisional value before the
            # terminal response carries the authoritative reason/tier.
            base[key] = value
        elif key not in base:
            base[key] = value
    return base


def _extract_runtime_diagnostics_from_any(value: Any) -> Dict[str, Any]:
    diagnostics: Dict[str, Any] = {}
    if value is None:
        return diagnostics
    if isinstance(value, Mapping):
        _merge_runtime_diagnostics(diagnostics, _runtime_diagnostics_from_mapping(value))
        for key in ("response_metadata", "responseMetadata", "generation_info", "generationInfo", "llm_output", "llmOutput"):
            nested = value.get(key)
            if isinstance(nested, Mapping):
                _merge_runtime_diagnostics(diagnostics, _extract_runtime_diagnostics_from_any(nested))
        return diagnostics
    for attr in ("response_metadata", "additional_kwargs", "generation_info", "llm_output"):
        candidate = getattr(value, attr, None)
        if isinstance(candidate, Mapping):
            _merge_runtime_diagnostics(diagnostics, _extract_runtime_diagnostics_from_any(candidate))
    message = getattr(value, "message", None)
    if message is not None and message is not value:
        _merge_runtime_diagnostics(diagnostics, _extract_runtime_diagnostics_from_any(message))
    return diagnostics


def extract_runtime_diagnostics(response: Any) -> Dict[str, Any]:
    diagnostics: Dict[str, Any] = {}
    _merge_runtime_diagnostics(diagnostics, _extract_runtime_diagnostics_from_any(response))
    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, Mapping):
        _merge_runtime_diagnostics(diagnostics, _extract_runtime_diagnostics_from_any(llm_output))
    generations = getattr(response, "generations", None) or []
    for generation_group in generations:
        if not isinstance(generation_group, list):
            continue
        for generation in generation_group:
            _merge_runtime_diagnostics(diagnostics, _extract_runtime_diagnostics_from_any(generation))
            message = getattr(generation, "message", None)
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                diagnostics["toolCallCount"] = len(tool_calls)
    return diagnostics


def _estimate_cost(tokens: int, unit_price: Any) -> float:
    price = _safe_float(unit_price)
    if price <= 0 or tokens <= 0:
        return 0.0
    # 当前 models.json 沿用 Admin 里的 costPerInput / costPerOutput 约定，按每百万 token 估算。
    return (tokens / 1_000_000.0) * price


def _resolve_scope(ctx: Dict[str, Any]) -> tuple[str, str]:
    if ctx.get("project_id"):
        return "project", str(ctx["project_id"])
    if ctx.get("session_id"):
        return "session", str(ctx["session_id"])
    if ctx.get("user_id"):
        return "user", str(ctx["user_id"])
    return "global", "global"


@dataclass
class _InvocationStart:
    started_at: float
    started_at_iso: str
    context: Dict[str, Any]
    message_batches: int
    first_chunk_at: float | None = None
    first_content_chunk_at: float | None = None
    last_chunk_at: float | None = None
    chunk_count: int = 0
    chunk_char_count: int = 0
    max_inter_chunk_gap_ms: float = 0.0


class ModelTelemetryCallback(BaseCallbackHandler):
    raise_error = False
    run_inline = True

    def __init__(
        self,
        *,
        model_id: str,
        provider_id: str,
        provider_name: str,
        role: str = "",
        capability_class: str = "",
        request_kind: str = "chat",
        cost_per_input: Any = None,
        cost_per_output: Any = None,
        is_streaming: bool = False,
        provider_adapter: str = "",
        effective_capability_matrix: Optional[Dict[str, Any]] = None,
        tool_calling_mode: str = "",
        structured_output_mode: str = "",
        stream_mode: str = "",
        requested_max_tokens: int = 0,
        stream_usage_requested: bool = False,
    ):
        self.model_id = model_id
        self.provider_id = provider_id
        self.provider_name = provider_name
        self.role = role
        self.capability_class = capability_class
        self.request_kind = request_kind
        self.cost_per_input = cost_per_input
        self.cost_per_output = cost_per_output
        self.is_streaming = is_streaming
        self.provider_adapter = provider_adapter
        self.effective_capability_matrix = dict(effective_capability_matrix or {})
        self.tool_calling_mode = tool_calling_mode
        self.structured_output_mode = structured_output_mode
        self.stream_mode = stream_mode
        self.requested_max_tokens = max(0, _safe_int(requested_max_tokens))
        self.stream_usage_requested = bool(stream_usage_requested)
        self._starts: Dict[str, _InvocationStart] = {}
        self._streaming_diagnostics: Dict[str, Dict[str, Any]] = {}

    @property
    def ignore_chat_model(self) -> bool:
        return False

    def _record_start(self, run_id: Any, message_batches: int) -> None:
        key = str(run_id)
        if key in self._starts:
            return
        self._starts[key] = _InvocationStart(
            started_at=time.perf_counter(),
            started_at_iso=_utc_now(),
            context=get_runtime_context(),
            message_batches=max(int(message_batches or 0), 0),
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs: Any,
    ) -> None:
        self._record_start(run_id, sum(len(batch) for batch in messages or []))

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        **kwargs: Any,
    ) -> None:
        # Some compatible adapters expose the generic LLM callback contract
        # instead of the chat-model contract. Keep the same timing truth for
        # both surfaces without resetting a chat start event for one run.
        self._record_start(run_id, len(prompts or []))

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        **kwargs: Any,
    ) -> None:
        start = self._starts.pop(str(run_id), None)
        ctx = start.context if start else get_runtime_context()
        usage, usage_source, usage_reported = extract_token_usage_details(response)
        runtime_diagnostics = extract_runtime_diagnostics(response)
        stream_diagnostics = self._streaming_diagnostics.pop(str(run_id), {})
        if stream_diagnostics:
            merged_diagnostics = dict(stream_diagnostics)
            # The terminal LLMResult is authoritative for finish reason and
            # service tier; retain stream-only diagnostics alongside it.
            _merge_runtime_diagnostics(merged_diagnostics, runtime_diagnostics)
            runtime_diagnostics = merged_diagnostics
        finished_at = time.perf_counter()
        latency_ms = (finished_at - start.started_at) * 1000 if start else 0.0
        cost_input = _estimate_cost(usage["input_tokens"], self.cost_per_input)
        cost_output = _estimate_cost(usage["output_tokens"], self.cost_per_output)
        cost_total = cost_input + cost_output
        self._record_invocation(
            ctx=ctx,
            status="completed",
            started_at=(start.started_at_iso if start else _utc_now()),
            latency_ms=latency_ms,
            usage=usage,
            cost_input=cost_input,
            cost_output=cost_output,
            cost_total=cost_total,
            error_code=None,
            error_message=None,
            metadata={
                "message_batches": start.message_batches if start else 0,
                "llm_output_keys": sorted((getattr(response, "llm_output", {}) or {}).keys()),
                "providerAdapter": runtime_diagnostics.get("providerAdapter") or self.provider_adapter,
                "providerAdapterLabel": runtime_diagnostics.get("providerAdapterLabel") or self.provider_adapter,
                "effectiveCapabilityMatrix": runtime_diagnostics.get("effectiveCapabilityMatrix") or self.effective_capability_matrix,
                "toolCallingMode": runtime_diagnostics.get("toolCallingMode") or self.tool_calling_mode,
                "structuredOutputMode": runtime_diagnostics.get("structuredOutputMode") or self.structured_output_mode,
                "streamMode": runtime_diagnostics.get("streamMode") or self.stream_mode,
                "toolCallCount": runtime_diagnostics.get("toolCallCount", 0),
                "promptCache": runtime_diagnostics.get("promptCache") or {},
                "finishReason": runtime_diagnostics.get("finishReason") or "",
                "serviceTier": runtime_diagnostics.get("serviceTier") or "",
                "usageReported": usage_reported,
                "usageSource": usage_source,
                "requestedMaxTokens": self.requested_max_tokens,
                "streamUsageRequested": self.stream_usage_requested,
                **self._stream_timing_metadata(start, finished_at),
            },
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        **kwargs: Any,
    ) -> None:
        start = self._starts.pop(str(run_id), None)
        stream_diagnostics = self._streaming_diagnostics.pop(str(run_id), {})
        ctx = start.context if start else get_runtime_context()
        finished_at = time.perf_counter()
        latency_ms = (finished_at - start.started_at) * 1000 if start else 0.0
        self._record_invocation(
            ctx=ctx,
            status="failed",
            started_at=(start.started_at_iso if start else _utc_now()),
            latency_ms=latency_ms,
            usage={"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            cost_input=0.0,
            cost_output=0.0,
            cost_total=0.0,
            error_code=str(getattr(error, "code", "") or ""),
            error_message=str(error),
            metadata={
                "exception_type": error.__class__.__name__,
                "message_batches": start.message_batches if start else 0,
                "providerAdapter": self.provider_adapter,
                "effectiveCapabilityMatrix": self.effective_capability_matrix,
                "toolCallingMode": self.tool_calling_mode,
                "structuredOutputMode": self.structured_output_mode,
                "streamMode": self.stream_mode,
                "finishReason": stream_diagnostics.get("finishReason") or "",
                "serviceTier": stream_diagnostics.get("serviceTier") or "",
                "usageReported": False,
                "usageSource": "",
                "requestedMaxTokens": self.requested_max_tokens,
                "streamUsageRequested": self.stream_usage_requested,
                **self._stream_timing_metadata(start, finished_at),
            },
        )

    def on_llm_new_token(
        self,
        token: str,
        *,
        run_id,
        parent_run_id=None,
        chunk=None,
        **kwargs: Any,
    ) -> None:
        run_key = str(run_id)
        start = self._starts.get(run_key)
        now = time.perf_counter()
        if start is not None:
            if start.first_chunk_at is None:
                start.first_chunk_at = now
            if start.last_chunk_at is not None:
                start.max_inter_chunk_gap_ms = max(
                    start.max_inter_chunk_gap_ms,
                    (now - start.last_chunk_at) * 1000,
                )
            start.last_chunk_at = now
            start.chunk_count += 1
            text, reasoning = extract_text_and_reasoning(chunk)
            visible_chars = max(len(str(token or "")), len(text) + len(reasoning))
            start.chunk_char_count += visible_chars
            if visible_chars > 0 and start.first_content_chunk_at is None:
                start.first_content_chunk_at = now
        diagnostics = _extract_runtime_diagnostics_from_any(chunk)
        for candidate_key in ("generation_info", "generationInfo", "response_metadata", "responseMetadata", "llm_output", "llmOutput"):
            candidate = kwargs.get(candidate_key)
            if isinstance(candidate, Mapping):
                _merge_runtime_diagnostics(diagnostics, _extract_runtime_diagnostics_from_any(candidate))
        if diagnostics:
            existing = self._streaming_diagnostics.setdefault(run_key, {})
            _merge_runtime_diagnostics(existing, diagnostics)

    @staticmethod
    def _stream_timing_metadata(
        start: _InvocationStart | None,
        finished_at: float,
    ) -> Dict[str, Any]:
        if start is None:
            return {
                "timeToFirstChunkMs": None,
                "timeToFirstContentChunkMs": None,
                "streamChunkCount": 0,
                "streamChunkCharCount": 0,
                "maxInterChunkGapMs": 0.0,
                "streamActiveMs": 0.0,
                "tailAfterLastChunkMs": None,
            }
        first_chunk = start.first_chunk_at
        first_content = start.first_content_chunk_at
        last_chunk = start.last_chunk_at
        return {
            "timeToFirstChunkMs": round((first_chunk - start.started_at) * 1000, 2) if first_chunk is not None else None,
            "timeToFirstContentChunkMs": round((first_content - start.started_at) * 1000, 2) if first_content is not None else None,
            "streamChunkCount": start.chunk_count,
            "streamChunkCharCount": start.chunk_char_count,
            "maxInterChunkGapMs": round(start.max_inter_chunk_gap_ms, 2),
            "streamActiveMs": round((last_chunk - first_chunk) * 1000, 2) if first_chunk is not None and last_chunk is not None else 0.0,
            "tailAfterLastChunkMs": round((finished_at - last_chunk) * 1000, 2) if last_chunk is not None else None,
        }

    def _record_invocation(
        self,
        *,
        ctx: Dict[str, Any],
        status: str,
        started_at: str,
        latency_ms: float,
        usage: Dict[str, int],
        cost_input: float,
        cost_output: float,
        cost_total: float,
        error_code: Optional[str],
        error_message: Optional[str],
        metadata: Dict[str, Any],
    ) -> None:
        invocation_id = str(uuid.uuid4())
        finished_at = _utc_now()
        scope_type, scope_id = _resolve_scope(ctx)
        log_record = {
            "id": invocation_id,
            "run_id": ctx.get("run_id"),
            "session_id": ctx.get("session_id"),
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "model_id": self.model_id,
            "role": self.role,
            "capability_class": self.capability_class,
            "request_kind": self.request_kind,
            "status": status,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "cost_input": cost_input,
            "cost_output": cost_output,
            "cost_total": cost_total,
            "latency_ms": latency_ms,
            "error_code": error_code,
            "error_message": error_message,
            "is_streaming": self.is_streaming,
            "metadata": metadata,
            "started_at": started_at,
            "finished_at": finished_at,
        }
        db.add_model_invocation_log(log_record)
        db.upsert_usage_ledger(
            {
                "id": str(uuid.uuid4()),
                "bucket_date": finished_at[:10],
                "scope_type": scope_type,
                "scope_id": scope_id,
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "role": self.role or "unassigned",
                "capability_class": self.capability_class,
                "invocations": 1,
                "success_count": 1 if status == "completed" else 0,
                "error_count": 0 if status == "completed" else 1,
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
                "cost_total": cost_total,
                "latency_ms_total": latency_ms,
            }
        )
        db.add_provider_health_log(
            {
                "id": str(uuid.uuid4()),
                "provider_id": self.provider_id or "unknown",
                "provider_name": self.provider_name or self.provider_id or "unknown",
                "model_id": self.model_id,
                "run_id": ctx.get("run_id"),
                "session_id": ctx.get("session_id"),
                "status": "healthy" if status == "completed" else "failed",
                "error_code": error_code,
                "error_message": error_message,
                "latency_ms": latency_ms,
                "detail": {
                    "role": self.role,
                    "capabilityClass": self.capability_class,
                    "requestKind": self.request_kind,
                },
            }
        )


class ModelTelemetryService:
    def build_chat_callback(
        self,
        *,
        model_id: str,
        provider_id: str,
        provider_name: str,
        role: str = "",
        capability_class: str = "",
        request_kind: str = "chat",
        cost_per_input: Any = None,
        cost_per_output: Any = None,
        is_streaming: bool = False,
        provider_adapter: str = "",
        effective_capability_matrix: Optional[Dict[str, Any]] = None,
        tool_calling_mode: str = "",
        structured_output_mode: str = "",
        stream_mode: str = "",
        requested_max_tokens: int = 0,
        stream_usage_requested: bool = False,
    ) -> ModelTelemetryCallback:
        return ModelTelemetryCallback(
            model_id=model_id,
            provider_id=provider_id,
            provider_name=provider_name,
            role=role,
            capability_class=capability_class,
            request_kind=request_kind,
            cost_per_input=cost_per_input,
            cost_per_output=cost_per_output,
            is_streaming=is_streaming,
            provider_adapter=provider_adapter,
            effective_capability_matrix=effective_capability_matrix,
            tool_calling_mode=tool_calling_mode,
            structured_output_mode=structured_output_mode,
            stream_mode=stream_mode,
            requested_max_tokens=requested_max_tokens,
            stream_usage_requested=stream_usage_requested,
        )

    def record_aux_model_invocation(
        self,
        *,
        model_id: str,
        provider_id: str,
        provider_name: str,
        role: str,
        capability_class: str,
        request_kind: str,
        latency_ms: float,
        status: str = "completed",
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        ctx = get_runtime_context()
        scope_type, scope_id = _resolve_scope(ctx)
        finished_at = _utc_now()
        invocation_id = str(uuid.uuid4())
        record = {
            "id": invocation_id,
            "run_id": ctx.get("run_id"),
            "session_id": ctx.get("session_id"),
            "provider_id": provider_id,
            "provider_name": provider_name,
            "model_id": model_id,
            "role": role,
            "capability_class": capability_class,
            "request_kind": request_kind,
            "status": status,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cost_input": 0.0,
            "cost_output": 0.0,
            "cost_total": 0.0,
            "latency_ms": latency_ms,
            "error_code": error_code,
            "error_message": error_message,
            "is_streaming": False,
            "metadata": metadata or {},
            "started_at": finished_at,
            "finished_at": finished_at,
        }
        db.add_model_invocation_log(record)
        db.upsert_usage_ledger(
            {
                "id": str(uuid.uuid4()),
                "bucket_date": finished_at[:10],
                "scope_type": scope_type,
                "scope_id": scope_id,
                "provider_id": provider_id,
                "model_id": model_id,
                "role": role or "unassigned",
                "capability_class": capability_class,
                "invocations": 1,
                "success_count": 1 if status == "completed" else 0,
                "error_count": 0 if status == "completed" else 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "cost_total": 0.0,
                "latency_ms_total": latency_ms,
            }
        )
        db.add_provider_health_log(
            {
                "id": str(uuid.uuid4()),
                "provider_id": provider_id or "unknown",
                "provider_name": provider_name or provider_id or "unknown",
                "model_id": model_id,
                "run_id": ctx.get("run_id"),
                "session_id": ctx.get("session_id"),
                "status": "healthy" if status == "completed" else "failed",
                "error_code": error_code,
                "error_message": error_message,
                "latency_ms": latency_ms,
                "detail": metadata or {},
            }
        )

    def build_dashboard_overview(self, days: int = 7) -> Dict[str, Any]:
        days = max(1, min(int(days or 1), 30))
        counts = db.get_counts_snapshot()
        daily = db.get_daily_telemetry_activity(days=days)
        usage_distribution = db.get_model_usage_distribution(days=days, limit=10)
        provider_health = db.get_provider_health_summary(days=days)
        recent_invocations = db.get_recent_model_invocations(limit=50, days=days)
        prompt_cache_stats = db.get_prompt_cache_stats(limit=20, days=days)
        prompt_prefix_counts = db.get_prompt_cache_prefix_use_counts(days=days)
        window_totals = db.get_model_invocation_window_totals(days=days)

        total_tokens = int(window_totals.get("total_tokens") or 0)
        estimated_cost = float(window_totals.get("cost_total") or 0.0)

        charts = {
            "dailyActivity": [
                {
                    "date": row["day"][5:].replace("-", "/"),
                    "messages": int(row.get("messages") or 0),
                    "runs": int(row.get("runs") or 0),
                    "invocations": int(row.get("invocations") or 0),
                    "tokens": int(row.get("total_tokens") or 0),
                }
                for row in daily
            ],
            "modelUsage": [
                {
                    "name": item.get("model_id") or "unknown",
                    "provider": item.get("provider_name") or item.get("provider_id") or "unknown",
                    "value": int(item.get("invocations") or 0),
                    "tokens": int(item.get("total_tokens") or 0),
                    "cost": float(item.get("cost_total") or 0.0),
                }
                for item in usage_distribution
            ],
            "providerHealth": [
                {
                    "providerId": item.get("provider_id") or "unknown",
                    "providerName": item.get("provider_name") or item.get("provider_id") or "unknown",
                    "events": int(item.get("events") or 0),
                    "successCount": int(item.get("success_count") or 0),
                    "errorCount": int(item.get("error_count") or 0),
                    "avgLatencyMs": round(float(item.get("avg_latency_ms") or 0.0), 2),
                    "lastSeenAt": item.get("last_seen_at"),
                }
                for item in provider_health
            ],
        }

        return {
            "stats": {
                "totalSessions": counts.get("sessions", 0),
                "totalMessages": counts.get("messages", 0),
                "totalRuns": counts.get("runs", 0),
                "totalInvocations": counts.get("invocations", 0),
                "pendingApprovals": counts.get("pending_approvals", 0),
                "activeRuns": counts.get("active_runs", 0),
                "recentWindowDays": days,
                "recentWindowTokens": total_tokens,
                "recentWindowEstimatedCost": round(estimated_cost, 6),
                "recentWindowInvocations": int(window_totals.get("invocations") or 0),
            },
            "charts": charts,
            "promptCache": prompt_cache_stats,
            "recentInvocations": [
                _public_invocation_record(item, prefix_use_counts=prompt_prefix_counts)
                for item in recent_invocations
            ],
        }


model_telemetry_service = ModelTelemetryService()
