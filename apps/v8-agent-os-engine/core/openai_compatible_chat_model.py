from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from typing import Any, AsyncIterator, Iterator, Mapping

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

from core.reasoning_payload_contract import THINK_TAG_PATTERN
from core.reasoning_surface_contract import is_trusted_reasoning_surface


_REASONING_RESPONSE_FIELDS = (
    "reasoning_details",
    "reasoning_content",
    "reasoning",
    "thinking",
    "thinking_delta",
)
_REASONING_CONTINUATION_FIELDS = (
    "reasoning_details",
    "reasoning_content",
)
_PROVIDER_REASONING_WIRE_FIELDS = (
    *_REASONING_RESPONSE_FIELDS,
    "reasoningContent",
    "thought",
    "thoughts",
    "analysis",
    "deliberation",
)
_REASONING_CONTINUATION_KEY = "_v8_reasoning_continuation"
_REASONING_DETAIL_INDEX_PREFIX = "lc_v8_reasoning_"
_REASONING_STREAM_STATE: ContextVar[dict[str, Any] | None] = ContextVar(
    "v8_openai_compatible_reasoning_stream_state",
    default=None,
)


def _reasoning_fields(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    fields: dict[str, Any] = {}
    for key in _REASONING_RESPONSE_FIELDS:
        if key not in value:
            continue
        field_value = value.get(key)
        if field_value is None:
            continue
        if key not in _REASONING_CONTINUATION_FIELDS and field_value in ("", [], {}):
            continue
        fields[key] = field_value
    return fields


def _consume_reasoning_details(previous: Any, current: Any, *, stream_mode: str) -> tuple[Any, Any]:
    """Keep native block identity while emitting additive LangChain chunks."""
    if not isinstance(current, list) or not all(isinstance(item, Mapping) for item in current):
        return current, current

    snapshot = deepcopy(previous) if isinstance(previous, list) else []
    deltas: list[dict[str, Any]] = []
    for position, raw_item in enumerate(current):
        item = dict(raw_item)
        if item.get("index") is not None:
            slot = next((i for i, old in enumerate(snapshot) if old.get("index") == item["index"]), len(snapshot))
        elif item.get("id"):
            slot = next((i for i, old in enumerate(snapshot) if old.get("id") == item["id"]), len(snapshot))
        else:
            slot = min(position, len(snapshot))
        merge_index = item.get("index")
        if merge_index is None and slot < len(snapshot):
            merge_index = snapshot[slot].get("index")
        if not isinstance(merge_index, int) and not (
            isinstance(merge_index, str) and merge_index.startswith("lc_")
        ):
            merge_index = f"{_REASONING_DETAIL_INDEX_PREFIX}{slot}"

        if slot == len(snapshot):
            snapshot.append(deepcopy(item))
            deltas.append({**item, "index": merge_index})
            continue

        previous_item = snapshot[slot]
        delta: dict[str, Any] = {"index": merge_index}
        for key, field_value in item.items():
            if key == "index":
                continue
            if key == "text":
                text_delta, text_snapshot = _consume_reasoning_text(
                    previous_item.get(key, ""), field_value, stream_mode=stream_mode,
                )
                previous_item[key] = text_snapshot
                if text_delta:
                    delta[key] = text_delta
            elif key not in previous_item or field_value != previous_item[key]:
                # Identity/format are block metadata, never text fragments.
                previous_item[key] = deepcopy(field_value)
                delta[key] = deepcopy(field_value)
        if len(delta) > 1:
            deltas.append(delta)
    return deltas, snapshot


def _consume_reasoning_text(
    previous: Any,
    current: Any,
    *,
    stream_mode: str,
) -> tuple[Any, Any]:
    if not isinstance(current, str) or not isinstance(previous, str):
        return current, current
    if stream_mode == "cumulative":
        return (current[len(previous):] if current.startswith(previous) else current), current
    return current, previous + current


class V8OpenAICompatibleChatModel(ChatOpenAI):
    """Keep documented reasoning fields dropped by generic ChatOpenAI.

    OpenAI-compatible providers may return official reasoning fields outside
    the OpenAI message schema. LangChain intentionally discards those fields;
    V8 preserves only the small known set and lets the downstream reasoning
    surface contract decide whether it is trusted and user-visible.
    """

    _v8_model_ref: str = PrivateAttr(default="")
    _v8_reasoning_stream_mode: str = PrivateAttr(default="delta")
    _v8_inline_thinking: bool = PrivateAttr(default=False)

    def __init__(self, *args: Any, v8_model_ref: str = "", v8_reasoning_surface: Mapping[str, Any] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._v8_model_ref = str(v8_model_ref or "").strip()
        surface = dict(v8_reasoning_surface or {})
        trusted = is_trusted_reasoning_surface(surface)
        # Chat Completions delta is additive. Snapshot transport must be an
        # explicit trusted contract; repeated/prefix text cannot identify it.
        if trusted and surface.get("streamMode") == "cumulative":
            self._v8_reasoning_stream_mode = "cumulative"
        self._v8_inline_thinking = bool(
            trusted
            and surface.get("requestStyle") == "minimax_interleaved_thinking"
            and "content[inline_think]" in surface.get("responseFields", [])
        )

    def _reasoning_origin(self) -> dict[str, str]:
        provider_id = ""
        raw_callbacks = self.callbacks
        callbacks = (
            list(raw_callbacks)
            if isinstance(raw_callbacks, (list, tuple))
            else [raw_callbacks]
            if raw_callbacks is not None
            else []
        )
        for callback in callbacks:
            provider_id = str(getattr(callback, "provider_id", "") or "").strip()
            if provider_id:
                break
        return {
            "modelRef": self._v8_model_ref,
            "provider": provider_id,
            "endpoint": str(self.openai_api_base or "https://api.openai.com/v1").rstrip("/"),
            "model": str(self.model_name or "").strip(),
        }

    def _continuation_payload(self, fields: Mapping[str, Any]) -> dict[str, Any]:
        preserved = {
            key: deepcopy(fields[key])
            for key in _REASONING_CONTINUATION_FIELDS
            if key in fields
        }
        return {
            "origin": self._reasoning_origin(),
            "fields": preserved,
            **({"inlineThinking": True} if self._v8_inline_thinking else {}),
        }

    @staticmethod
    def _has_tool_continuation(message: AIMessage) -> bool:
        additional_kwargs = dict(message.additional_kwargs or {})
        return bool(
            message.tool_calls
            or message.invalid_tool_calls
            or additional_kwargs.get("tool_calls")
            or additional_kwargs.get("function_call")
        )

    def _create_chat_result(
        self,
        response: dict | Any,
        generation_info: dict | None = None,
    ) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        response_dict = (
            response
            if isinstance(response, dict)
            else response.model_dump(
                exclude={"choices": {"__all__": {"message": {"parsed"}}}},
                warnings=False,
            )
        )
        choices = list(response_dict.get("choices") or [])
        for generation, choice in zip(result.generations, choices):
            message_payload = choice.get("message") if isinstance(choice, Mapping) else None
            preserved = _reasoning_fields(message_payload)
            if isinstance(generation.message, AIMessage):
                generation.message.additional_kwargs = {
                    **dict(generation.message.additional_kwargs or {}),
                    **preserved,
                }
                continuation_fields = {
                    key: preserved[key]
                    for key in _REASONING_CONTINUATION_FIELDS
                    if key in preserved
                }
                if continuation_fields or self._v8_inline_thinking:
                    generation.message.additional_kwargs[_REASONING_CONTINUATION_KEY] = (
                        self._continuation_payload(continuation_fields)
                    )
        return result

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation is None:
            return None
        choices = list(chunk.get("choices") or chunk.get("chunk", {}).get("choices") or [])
        delta = choices[0].get("delta") if choices and isinstance(choices[0], Mapping) else None
        preserved = _reasoning_fields(delta)
        stream_state = _REASONING_STREAM_STATE.get()
        if stream_state is not None:
            details_key = _REASONING_CONTINUATION_FIELDS[0]
            for field_key in _REASONING_CONTINUATION_FIELDS:
                if field_key not in preserved:
                    continue
                current_value = deepcopy(preserved[field_key])
                if field_key == details_key:
                    stream_value, snapshot = _consume_reasoning_details(
                        stream_state.get(field_key),
                        current_value,
                        stream_mode=self._v8_reasoning_stream_mode,
                    )
                else:
                    stream_value, snapshot = _consume_reasoning_text(
                        stream_state.get(field_key, ""),
                        current_value,
                        stream_mode=self._v8_reasoning_stream_mode,
                    )
                stream_state[field_key] = snapshot
                continuation_fields = stream_state.setdefault("continuation_fields", {})
                continuation_fields[field_key] = snapshot
                if stream_value in ("", [], {}):
                    preserved.pop(field_key)
                else:
                    preserved[field_key] = stream_value
            continuation_fields = stream_state.get("continuation_fields")
            continuation_payload = stream_state.get("continuation_payload")
            if continuation_fields or self._v8_inline_thinking:
                if not isinstance(continuation_payload, dict):
                    continuation_payload = self._continuation_payload({})
                    stream_state["continuation_payload"] = continuation_payload
                    preserved[_REASONING_CONTINUATION_KEY] = continuation_payload
                payload_fields = continuation_payload.get("fields")
                if isinstance(payload_fields, dict):
                    payload_fields.clear()
                    payload_fields.update(deepcopy(dict(continuation_fields or {})))
        additional_kwargs = dict(generation.message.additional_kwargs or {})
        for key in (*_REASONING_RESPONSE_FIELDS, _REASONING_CONTINUATION_KEY):
            additional_kwargs.pop(key, None)
        additional_kwargs.update(preserved)
        generation.message.additional_kwargs = additional_kwargs
        return generation

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        stream_state: dict[str, Any] = {"continuation_fields": {}}
        token = _REASONING_STREAM_STATE.set(stream_state)
        try:
            for generation in super()._stream(*args, **kwargs):
                yield generation
        finally:
            _REASONING_STREAM_STATE.reset(token)

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        stream_state: dict[str, Any] = {"continuation_fields": {}}
        token = _REASONING_STREAM_STATE.set(stream_state)
        try:
            async for generation in super()._astream(*args, **kwargs):
                yield generation
        finally:
            _REASONING_STREAM_STATE.reset(token)

    def _get_request_payload(self, input_: Any, *args: Any, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, *args, **kwargs)
        wire_messages = payload.get("messages")
        if not isinstance(wire_messages, list):
            return payload
        source_messages = self._convert_input(input_).to_messages()
        for source, target in zip(source_messages, wire_messages):
            if not isinstance(source, AIMessage) or not isinstance(target, dict):
                continue
            for key in (*_PROVIDER_REASONING_WIRE_FIELDS, _REASONING_CONTINUATION_KEY):
                target.pop(key, None)
            continuation = source.additional_kwargs.get(_REASONING_CONTINUATION_KEY)
            continuation = continuation if isinstance(continuation, Mapping) else {}
            origin = continuation.get("origin")
            same_origin = isinstance(origin, Mapping) and dict(origin) == self._reasoning_origin()
            if not same_origin and continuation.get("inlineThinking") is True:
                content = target.get("content")
                if isinstance(content, str):
                    # Only the native leading block is private. Literal tags
                    # in the visible body, fenced code and escaped examples stay.
                    start = len(content) - len(content.lstrip())
                    thought = THINK_TAG_PATTERN.match(content, start)
                    if thought:
                        target["content"] = content[:start] + content[thought.end():]
            has_tool_continuation = self._has_tool_continuation(source)
            if not has_tool_continuation:
                continue
            # Some OpenAI-compatible tool APIs require the field to exist even
            # when no provider-native reasoning continuation is available.
            target["reasoning_content"] = ""
            fields = continuation.get("fields")
            if not same_origin:
                continue
            if not isinstance(fields, Mapping):
                continue
            target.update(
                {
                    key: deepcopy(fields[key])
                    for key in _REASONING_CONTINUATION_FIELDS
                    if key in fields
                }
            )
        return payload
