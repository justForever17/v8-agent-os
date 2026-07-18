from __future__ import annotations

import hashlib
import json
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from core.database import db
from core.prompt_cache_segments import PROMPT_CACHE_SEGMENT_TYPES, hash_prompt_segment
from core.time_truth import utc_now_iso


_PROFILE_PATH = Path(__file__).resolve().parent / "model_catalog" / "prompt_cache_profiles.json"
_SCHEMA_VERSION = "prompt-cache-gateway-v1"
_SAFETY_POLICY_VERSION = "prompt-cache-safety-v1"
_DEFAULT_RESPONSE_CACHE_TTL_SECONDS = 600

_DYNAMIC_MARKERS = (
    "<current_time",
    "<dynamic_context",
    "<memory",
    "<passive_memory",
    "<passive_rag",
    "<artifact_awareness",
    "<todos",
    "<route_context",
    "<runtime_tool_grants",
    "<tool_observations",
    "current time:",
    "当前时间",
    "本轮用户",
)

_UNSAFE_CACHE_MARKERS = (
    "api key",
    "apikey",
    "secret key",
    "private key",
    "password",
    "credential",
    "access token",
    "bearer ",
    "绕过安全",
    "绕过 policy",
    "越狱",
    "密钥",
    "凭据",
)

_TEXT_BLOCK_CACHE_STYLES = {"anthropic_content_blocks"}
_PROMPT_CACHE_KEY_STYLES = {"prompt_cache_key", "prompt_cache_key_and_header"}
_OBSERVE_ONLY_STYLES = {"observe_only", "implicit_observe_only"}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_text(_json_dumps(value))


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                parts.append(str(item.get("text") or item.get("content") or item.get("value") or ""))
            else:
                parts.append(str(getattr(item, "text", "") or getattr(item, "content", "") or item))
        return "\n".join(part for part in parts if part)
    return str(value)


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, SystemMessage):
        return "system"
    if isinstance(message, HumanMessage):
        return "user"
    if isinstance(message, AIMessage):
        return "assistant"
    if isinstance(message, ToolMessage):
        return "tool"
    return str(getattr(message, "type", "") or message.__class__.__name__).lower()


def _message_to_hash_payload(message: BaseMessage) -> dict[str, Any]:
    return {
        "role": _message_role(message),
        "content": getattr(message, "content", ""),
        "name": getattr(message, "name", None),
        "tool_call_id": getattr(message, "tool_call_id", None),
        "tool_calls": getattr(message, "tool_calls", None),
        "additional_kwargs": getattr(message, "additional_kwargs", None),
    }


def _tool_schema_hash(bound_tools: Sequence[Any] | None) -> str:
    if not bound_tools:
        return _hash_json([])
    payload: list[dict[str, Any]] = []
    for tool in bound_tools:
        if isinstance(tool, Mapping):
            payload.append(dict(tool))
            continue
        payload.append(
            {
                "name": str(getattr(tool, "name", "") or getattr(tool, "__name__", "") or tool.__class__.__name__),
                "description": str(getattr(tool, "description", "") or getattr(tool, "__doc__", "") or ""),
                "args_schema": str(getattr(tool, "args_schema", "") or ""),
            }
        )
    return _hash_json(payload)


def _truncate_key(value: str, length: int = 128) -> str:
    return value[:length]


def _load_profiles_payload() -> dict[str, Any]:
    try:
        with _PROFILE_PATH.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {"version": 1, "profiles": []}
    except Exception:
        return {"version": 1, "profiles": []}


def load_prompt_cache_profiles() -> dict[str, Any]:
    return deepcopy(_load_profiles_payload())


def prompt_cache_profile_for_provider(provider_id: str) -> dict[str, Any]:
    target = str(provider_id or "").strip().lower()
    fallback: dict[str, Any] = {}
    for profile in list((_load_profiles_payload().get("profiles") or [])):
        if not isinstance(profile, dict):
            continue
        provider_ids = {str(item).strip().lower() for item in list(profile.get("providerIds") or [])}
        if target and target in provider_ids:
            return deepcopy(profile)
        if str(profile.get("id") or "") == "implicit_or_unknown":
            fallback = dict(profile)
    return deepcopy(fallback)


def prompt_cache_profile_id_for_provider(provider_id: str) -> str:
    return str(prompt_cache_profile_for_provider(provider_id).get("id") or "")


@dataclass
class PromptSegment:
    segment_type: str
    source: str
    content_hash: str
    char_count: int
    start_offset: int | None = None
    end_offset: int | None = None
    scope: str = ""

    def public_dict(self) -> dict[str, Any]:
        payload = {
            "type": self.segment_type,
            "source": self.source,
            "hash": self.content_hash,
            "charCount": self.char_count,
            "estimatedTokens": max(1, self.char_count // 4) if self.char_count else 0,
        }
        if self.start_offset is not None:
            payload["startOffset"] = self.start_offset
        if self.end_offset is not None:
            payload["endOffset"] = self.end_offset
        if self.scope:
            payload["scope"] = self.scope
        return payload


@dataclass
class PreparedPromptCacheRequest:
    messages: list[BaseMessage]
    kwargs: dict[str, Any]
    diagnostics: dict[str, Any]
    cache_hit_message: AIMessage | None = None


class PromptCacheGateway:
    def _structured_segments_for_message(self, message: BaseMessage, *, index: int, role: str) -> list[PromptSegment]:
        kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
        raw_segments = kwargs.get("v8_prompt_segments") or kwargs.get("prompt_cache_segments")
        if not isinstance(raw_segments, list):
            return []
        text = _safe_text(getattr(message, "content", ""))
        segments: list[PromptSegment] = []
        for raw in raw_segments:
            if not isinstance(raw, Mapping):
                continue
            segment_type = str(raw.get("type") or raw.get("segment_type") or "dynamic").strip()
            if segment_type not in PROMPT_CACHE_SEGMENT_TYPES:
                segment_type = "dynamic"
            start_offset: int | None = None
            end_offset: int | None = None
            try:
                if raw.get("startOffset") is not None:
                    start_offset = max(0, int(raw.get("startOffset")))
                if raw.get("endOffset") is not None:
                    end_offset = max(0, int(raw.get("endOffset")))
            except Exception:
                start_offset = None
                end_offset = None
            if start_offset is not None and end_offset is not None and end_offset >= start_offset:
                segment_text = text[start_offset:end_offset]
                char_count = len(segment_text)
                content_hash = str(raw.get("hash") or raw.get("content_hash") or hash_prompt_segment(segment_text))
            else:
                char_count = int(raw.get("charCount") or raw.get("char_count") or 0)
                content_hash = str(raw.get("hash") or raw.get("content_hash") or "")
                if not content_hash:
                    continue
            segments.append(
                PromptSegment(
                    segment_type,
                    str(raw.get("source") or f"{index}:{role}:structured"),
                    content_hash,
                    char_count,
                    start_offset=start_offset,
                    end_offset=end_offset,
                    scope=str(raw.get("scope") or ""),
                )
            )
        return segments

    def _segment_messages(self, messages: Sequence[BaseMessage]) -> list[PromptSegment]:
        segments: list[PromptSegment] = []
        seen_system = False
        for index, message in enumerate(messages):
            role = _message_role(message)
            text = _safe_text(getattr(message, "content", ""))
            if isinstance(message, ToolMessage):
                segments.append(PromptSegment("unsafe", f"{index}:{role}", _hash_text(text), len(text)))
                continue
            if any(marker in text.lower() for marker in _UNSAFE_CACHE_MARKERS):
                segments.append(PromptSegment("unsafe", f"{index}:{role}", _hash_text(text), len(text)))
                continue
            structured_segments = self._structured_segments_for_message(message, index=index, role=role)
            if structured_segments:
                segments.extend(structured_segments)
                if isinstance(message, SystemMessage):
                    seen_system = True
                continue
            if isinstance(message, SystemMessage):
                lowered = text.lower()
                marker_positions = [lowered.find(marker) for marker in _DYNAMIC_MARKERS if lowered.find(marker) >= 0]
                split_at = min(marker_positions) if marker_positions else -1
                if split_at >= 0:
                    static_text = text[:split_at].rstrip()
                    dynamic_text = text[split_at:].lstrip()
                    if static_text:
                        segments.append(
                            PromptSegment("stable_static" if not seen_system else "scoped_static", f"{index}:{role}", _hash_text(static_text), len(static_text))
                        )
                    if dynamic_text:
                        segments.append(PromptSegment("dynamic", f"{index}:{role}", _hash_text(dynamic_text), len(dynamic_text)))
                else:
                    segments.append(
                        PromptSegment("stable_static" if not seen_system else "scoped_static", f"{index}:{role}", _hash_text(text), len(text))
                    )
                seen_system = True
                continue
            segments.append(PromptSegment("dynamic", f"{index}:{role}", _hash_text(text), len(text)))
        return segments

    def _stable_prefix_key(
        self,
        *,
        provider_id: str,
        model_id: str,
        role: str,
        profile: Mapping[str, Any],
        segments: Sequence[PromptSegment],
        tool_schema_hash: str,
        workspace_rules_hash: str,
        runtime_grants_hash: str,
    ) -> str:
        payload = {
            "schemaVersion": _SCHEMA_VERSION,
            "providerId": provider_id,
            "modelId": model_id,
            "role": role,
            "staticSegmentHashes": [
                segment.content_hash
                for segment in segments
                if segment.segment_type in {"stable_static", "scoped_static"}
            ],
            "toolSchemaHash": tool_schema_hash,
            "workspaceRulesHash": workspace_rules_hash,
            "runtimeGrantsHash": runtime_grants_hash,
            "profileVersion": f"{profile.get('id') or 'none'}:{profile.get('version') or 1}",
        }
        return _hash_json(payload)

    def _dynamic_request_hash(self, *, messages: Sequence[BaseMessage], kwargs: Mapping[str, Any], stop: Any) -> str:
        dynamic_payload = {
            "messages": [_message_to_hash_payload(message) for message in messages if not isinstance(message, SystemMessage)],
            "systemDynamicHashes": [
                segment.public_dict()
                for segment in self._segment_messages(messages)
                if segment.segment_type in {"dynamic", "unsafe"}
            ],
            "stop": stop,
            "responseFormat": kwargs.get("response_format") or kwargs.get("responseFormat"),
        }
        return _hash_json(dynamic_payload)

    def _sampling_hash(self, *, kwargs: Mapping[str, Any], model_kwargs: Mapping[str, Any]) -> str:
        keys = ("temperature", "top_p", "top_k", "presence_penalty", "frequency_penalty", "seed", "max_tokens", "max_output_tokens")
        payload = {key: kwargs.get(key, model_kwargs.get(key)) for key in keys if kwargs.get(key, model_kwargs.get(key)) is not None}
        return _hash_json(payload)

    def _workspace_rules_hash(self, meta: Mapping[str, Any]) -> str:
        for key in ("workspaceRulesHash", "workspace_rules_hash", "agents_hash", "agentsHash"):
            value = str(meta.get(key) or "").strip()
            if value:
                return value
        return _hash_json({})

    def _runtime_grants_hash(self, meta: Mapping[str, Any]) -> str:
        for key in ("runtimeToolGrants", "runtime_tool_grants", "routeRuntimeGrants", "route_runtime_grants"):
            value = meta.get(key)
            if value:
                return _hash_json(value)
        return _hash_json([])

    def _has_private_or_multimodal_content(self, messages: Sequence[BaseMessage]) -> bool:
        for message in messages:
            content = getattr(message, "content", None)
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, Mapping):
                        kind = str(item.get("type") or "").lower()
                        if kind and kind not in {"text"}:
                            return True
                        if any(key in item for key in ("image_url", "file_id", "file", "input_audio")):
                            return True
        return False

    def _structured_cache_control_blocks(
        self,
        *,
        message: BaseMessage,
        index: int,
        profile: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], int]:
        text = _safe_text(getattr(message, "content", ""))
        if not text or isinstance(getattr(message, "content", None), list):
            return [], 0
        segments = [
            segment
            for segment in self._structured_segments_for_message(message, index=index, role="system")
            if segment.start_offset is not None and segment.end_offset is not None
        ]
        if not segments:
            return [], 0
        segments.sort(key=lambda item: int(item.start_offset or 0))
        blocks: list[dict[str, Any]] = []
        cursor = 0
        breakpoints = 0
        max_breakpoints = max(1, min(int(profile.get("maxBreakpoints") or 4), 4))
        for segment in segments:
            start = max(0, int(segment.start_offset or 0))
            end = max(start, int(segment.end_offset or start))
            if start > cursor:
                blocks.append({"type": "text", "text": text[cursor:start]})
            segment_text = text[start:end]
            if segment_text:
                block: dict[str, Any] = {"type": "text", "text": segment_text}
                if segment.segment_type in {"stable_static", "scoped_static"} and breakpoints < max_breakpoints:
                    block["cache_control"] = {"type": "ephemeral"}
                    breakpoints += 1
                blocks.append(block)
            cursor = end
        if cursor < len(text):
            blocks.append({"type": "text", "text": text[cursor:]})
        return blocks, breakpoints

    def _has_unsafe_cache_content(self, messages: Sequence[BaseMessage]) -> bool:
        for message in messages:
            text = _safe_text(getattr(message, "content", "")).lower()
            if any(marker in text for marker in _UNSAFE_CACHE_MARKERS):
                return True
        return False

    def _temperature_value(self, *, kwargs: Mapping[str, Any], model_kwargs: Mapping[str, Any]) -> float | None:
        value = kwargs.get("temperature", model_kwargs.get("temperature"))
        if value in (None, ""):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def _response_cache_skip_reason(
        self,
        *,
        messages: Sequence[BaseMessage],
        kwargs: Mapping[str, Any],
        model_kwargs: Mapping[str, Any],
        bound_tools: Sequence[Any] | None,
        streaming: bool,
    ) -> str:
        if streaming:
            return "streaming_request"
        if bound_tools:
            return "tool_bound_request"
        if any(isinstance(message, ToolMessage) for message in messages):
            return "contains_tool_result"
        if self._has_private_or_multimodal_content(messages):
            return "contains_file_or_image_content"
        if self._has_unsafe_cache_content(messages):
            return "unsafe_or_secret_content"
        temperature = self._temperature_value(kwargs=kwargs, model_kwargs=model_kwargs)
        if temperature is None:
            return "temperature_unknown"
        if temperature > 0.05:
            return "temperature_not_deterministic"
        return ""

    def _apply_provider_patch(
        self,
        *,
        messages: Sequence[BaseMessage],
        kwargs: Mapping[str, Any],
        profile: Mapping[str, Any],
        prefix_key: str,
    ) -> tuple[list[BaseMessage], dict[str, Any], dict[str, Any]]:
        patched_messages = list(messages)
        patched_kwargs = dict(kwargs)
        request_style = str(profile.get("requestStyle") or "observe_only")
        patch: dict[str, Any] = {"requestStyle": request_style}
        cache_key_value = _truncate_key(prefix_key)

        if request_style in _PROMPT_CACHE_KEY_STYLES:
            patched_kwargs.setdefault("prompt_cache_key", cache_key_value)
            patch["prompt_cache_key"] = cache_key_value

        if request_style == "prompt_cache_key_and_header":
            extra_headers = dict(patched_kwargs.get("extra_headers") or {})
            extra_headers.setdefault("x-grok-conv-id", cache_key_value[:64])
            patched_kwargs["extra_headers"] = extra_headers
            patch["extra_headers"] = {"x-grok-conv-id": extra_headers.get("x-grok-conv-id")}

        if request_style == "extra_body_caching":
            extra_body = dict(patched_kwargs.get("extra_body") or {})
            caching = dict(extra_body.get("caching") or {})
            caching.setdefault("type", "enabled")
            caching.setdefault("prefix", True)
            extra_body["caching"] = caching
            patched_kwargs["extra_body"] = extra_body
            patch["extra_body"] = {"caching": caching}

        if request_style in _TEXT_BLOCK_CACHE_STYLES:
            for index, message in enumerate(patched_messages):
                if not isinstance(message, SystemMessage):
                    continue
                structured_blocks, structured_breakpoints = self._structured_cache_control_blocks(
                    message=message,
                    index=index,
                    profile=profile,
                )
                if structured_blocks:
                    patched_messages[index] = SystemMessage(
                        content=structured_blocks,
                        additional_kwargs=dict(getattr(message, "additional_kwargs", {}) or {}),
                    )
                    patch["cache_control"] = {"breakpoints": structured_breakpoints, "scope": f"messages[{index}].structured_segments"}
                    break
                text = _safe_text(message.content)
                if not text or isinstance(message.content, list):
                    break
                lowered = text.lower()
                marker_positions = [lowered.find(marker) for marker in _DYNAMIC_MARKERS if lowered.find(marker) >= 0]
                split_at = min(marker_positions) if marker_positions else -1
                static_text = text[:split_at].rstrip() if split_at >= 0 else text
                dynamic_text = text[split_at:].lstrip() if split_at >= 0 else ""
                blocks: list[dict[str, Any]] = []
                if static_text:
                    blocks.append({"type": "text", "text": static_text, "cache_control": {"type": "ephemeral"}})
                if dynamic_text:
                    blocks.append({"type": "text", "text": dynamic_text})
                if blocks:
                    patched_messages[index] = SystemMessage(content=blocks)
                    patch["cache_control"] = {"breakpoints": 1, "scope": f"messages[{index}].content[-1]"}
                break

        if request_style in _OBSERVE_ONLY_STYLES or request_style == "implicit_or_unknown":
            patch["observeOnly"] = True

        return patched_messages, patched_kwargs, patch

    def prepare_request(
        self,
        *,
        messages: Sequence[BaseMessage],
        kwargs: Mapping[str, Any] | None,
        stop: Any = None,
        provider_id: str,
        model_id: str,
        model_ref: str = "",
        role: str = "",
        model_kwargs: Mapping[str, Any] | None = None,
        meta: Mapping[str, Any] | None = None,
        bound_tools: Sequence[Any] | None = None,
        streaming: bool = False,
        record: bool = True,
        lookup_response_cache: bool = True,
    ) -> PreparedPromptCacheRequest:
        started = time.perf_counter()
        meta = dict(meta or {})
        model_kwargs = dict(model_kwargs or {})
        kwargs_in = dict(kwargs or {})
        profile = prompt_cache_profile_for_provider(provider_id)
        profile_id = str(profile.get("id") or "implicit_or_unknown")
        segments = self._segment_messages(messages)
        tool_hash = _tool_schema_hash(bound_tools)
        workspace_rules_hash = self._workspace_rules_hash(meta)
        runtime_grants_hash = self._runtime_grants_hash(meta)
        prefix_key = self._stable_prefix_key(
            provider_id=provider_id,
            model_id=model_ref or model_id,
            role=role,
            profile=profile,
            segments=segments,
            tool_schema_hash=tool_hash,
            workspace_rules_hash=workspace_rules_hash,
            runtime_grants_hash=runtime_grants_hash,
        )
        dynamic_hash = self._dynamic_request_hash(messages=messages, kwargs=kwargs_in, stop=stop)
        sampling_hash = self._sampling_hash(kwargs=kwargs_in, model_kwargs=model_kwargs)
        response_key = _hash_json(
            {
                "schemaVersion": _SCHEMA_VERSION,
                "prefixKey": prefix_key,
                "dynamicRequestHash": dynamic_hash,
                "samplingParams": sampling_hash,
                "streaming": bool(streaming),
                "toolBound": bool(bound_tools),
                "scope": str(meta.get("cacheScope") or meta.get("scope") or "model"),
                "safetyPolicyVersion": _SAFETY_POLICY_VERSION,
            }
        )
        skip_reason = self._response_cache_skip_reason(
            messages=messages,
            kwargs=kwargs_in,
            model_kwargs=model_kwargs,
            bound_tools=bound_tools,
            streaming=streaming,
        )
        provider_messages, provider_kwargs, provider_patch = self._apply_provider_patch(
            messages=messages,
            kwargs=kwargs_in,
            profile=profile,
            prefix_key=prefix_key,
        )
        event_id = str(uuid.uuid4())
        diagnostics = {
            "eventId": event_id,
            "profileId": profile_id,
            "providerId": provider_id,
            "modelId": model_id,
            "modelRef": model_ref or "",
            "role": role,
            "staticPrefixKey": prefix_key,
            "responseCacheKey": response_key,
            "dynamicRequestHash": dynamic_hash,
            "toolSchemaHash": tool_hash,
            "workspaceRulesHash": workspace_rules_hash,
            "runtimeGrantsHash": runtime_grants_hash,
            "segments": [segment.public_dict() for segment in segments],
            "providerRequestPatch": provider_patch,
            "usageFields": list(profile.get("usageFields") or []),
            "responseCacheDecision": "skipped" if skip_reason else "eligible",
            "skipReason": skip_reason,
            "safetyPolicyVersion": _SAFETY_POLICY_VERSION,
        }
        cache_hit_message: AIMessage | None = None
        if not skip_reason and lookup_response_cache:
            cached = db.get_llm_response_cache(response_key)
            if cached:
                response_payload = dict(cached.get("response") or {})
                metadata = dict(response_payload.get("response_metadata") or {})
                metadata["v8_prompt_cache"] = {**diagnostics, "responseCacheDecision": "hit"}
                cache_hit_message = AIMessage(
                    content=response_payload.get("content") or "",
                    additional_kwargs=dict(response_payload.get("additional_kwargs") or {}),
                    response_metadata=metadata,
                )
                diagnostics["responseCacheDecision"] = "hit"
                db.increment_llm_response_cache_hit(response_key)
            else:
                diagnostics["responseCacheDecision"] = "miss"

        if record:
            db.add_prompt_cache_event(
                {
                    "id": event_id,
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "model_ref": model_ref or "",
                    "role": role,
                    "profile_id": profile_id,
                    "static_prefix_key": prefix_key,
                    "response_cache_key": response_key,
                    "decision": diagnostics["responseCacheDecision"],
                    "skip_reason": skip_reason,
                    "provider_patch": provider_patch,
                    "metadata": {
                        "durationMs": round((time.perf_counter() - started) * 1000, 3),
                        "streaming": bool(streaming),
                        "toolBound": bool(bound_tools),
                    "dynamicRequestHash": dynamic_hash,
                    "toolSchemaHash": tool_hash,
                    "workspaceRulesHash": workspace_rules_hash,
                    "runtimeGrantsHash": runtime_grants_hash,
                },
                    "created_at": utc_now_iso(),
                }
            )
            db.add_prompt_cache_segments(event_id, [segment.public_dict() for segment in segments])
        return PreparedPromptCacheRequest(
            messages=provider_messages,
            kwargs=provider_kwargs,
            diagnostics=diagnostics,
            cache_hit_message=cache_hit_message,
        )

    def decorate_response(self, message: AIMessage, diagnostics: Mapping[str, Any]) -> AIMessage:
        response_metadata = dict(getattr(message, "response_metadata", {}) or {})
        response_metadata["v8_prompt_cache"] = dict(diagnostics or {})
        message.response_metadata = response_metadata
        return message

    def store_response(self, message: AIMessage, diagnostics: Mapping[str, Any]) -> None:
        if str(diagnostics.get("responseCacheDecision") or "") not in {"miss", "eligible"}:
            return
        if str(diagnostics.get("skipReason") or ""):
            return
        response_key = str(diagnostics.get("responseCacheKey") or "")
        if not response_key:
            return
        now = utc_now_iso()
        db.upsert_llm_response_cache(
            {
                "response_cache_key": response_key,
                "static_prefix_key": diagnostics.get("staticPrefixKey") or "",
                "provider_id": diagnostics.get("providerId") or "",
                "model_id": diagnostics.get("modelId") or "",
                "model_ref": diagnostics.get("modelRef") or "",
                "role": diagnostics.get("role") or "",
                "ttl_seconds": _DEFAULT_RESPONSE_CACHE_TTL_SECONDS,
                "response": {
                    "content": getattr(message, "content", ""),
                    "additional_kwargs": dict(getattr(message, "additional_kwargs", {}) or {}),
                    "response_metadata": {
                        key: value
                        for key, value in dict(getattr(message, "response_metadata", {}) or {}).items()
                        if key != "v8_prompt_cache"
                    },
                },
                "metadata": {
                    "schemaVersion": _SCHEMA_VERSION,
                    "safetyPolicyVersion": _SAFETY_POLICY_VERSION,
                    "profileId": diagnostics.get("profileId") or "",
                },
                "created_at": now,
            }
        )

    def dry_run(
        self,
        *,
        messages: Sequence[BaseMessage],
        provider_id: str,
        model_id: str,
        model_ref: str = "",
        role: str = "",
        kwargs: Mapping[str, Any] | None = None,
        model_kwargs: Mapping[str, Any] | None = None,
        meta: Mapping[str, Any] | None = None,
        bound_tools: Sequence[Any] | None = None,
        streaming: bool = False,
    ) -> dict[str, Any]:
        prepared = self.prepare_request(
            messages=messages,
            kwargs=kwargs or {},
            provider_id=provider_id,
            model_id=model_id,
            model_ref=model_ref,
            role=role,
            model_kwargs=model_kwargs or {},
            meta=meta or {},
            bound_tools=bound_tools,
            streaming=streaming,
            record=False,
            lookup_response_cache=False,
        )
        return {
            "normalizedMessages": [
                {"role": _message_role(message), "contentHash": _hash_text(_safe_text(getattr(message, "content", "")))}
                for message in prepared.messages
            ],
            "cacheDiagnostics": prepared.diagnostics,
        }


prompt_cache_gateway = PromptCacheGateway()
