from __future__ import annotations

import json
import time
from typing import Any

from api.models import ExternalToolSpec
from runtimes.network_supervisor.anthropic_compat import build_anthropic_message_response
from runtimes.network_supervisor.openai_compat import build_openai_completion_response


class InlineThinkStreamFilter:
    """Statefully split provider inline <think> tags across stream chunks."""

    _START = "<think>"
    _END = "</think>"

    def __init__(self) -> None:
        self._inside_think = False
        self._pending = ""
        self._strip_next_visible_prefix = False

    @staticmethod
    def _suffix_prefix_len(value: str, marker: str) -> int:
        max_len = min(len(value), len(marker) - 1)
        for length in range(max_len, 0, -1):
            if marker.startswith(value[-length:]):
                return length
        return 0

    def feed(self, content: str) -> tuple[str, str]:
        text = self._pending + str(content or "")
        self._pending = ""
        visible_parts: list[str] = []
        reasoning_parts: list[str] = []
        index = 0

        while index < len(text):
            marker = self._END if self._inside_think else self._START
            marker_index = text.find(marker, index)
            target_parts = reasoning_parts if self._inside_think else visible_parts

            if marker_index >= 0:
                target_parts.append(text[index:marker_index])
                index = marker_index + len(marker)
                was_inside_think = self._inside_think
                self._inside_think = not self._inside_think
                if was_inside_think and not self._inside_think:
                    self._strip_next_visible_prefix = True
                continue

            tail = text[index:]
            keep_len = self._suffix_prefix_len(tail, marker)
            if keep_len:
                target_parts.append(tail[:-keep_len])
                self._pending = tail[-keep_len:]
            else:
                target_parts.append(tail)
            break

        visible = "".join(visible_parts)
        if self._strip_next_visible_prefix and visible:
            visible = visible.lstrip()
            if visible:
                self._strip_next_visible_prefix = False
        return visible, "".join(reasoning_parts)

    def flush(self) -> tuple[str, str]:
        if not self._pending:
            return "", ""
        pending = self._pending
        self._pending = ""
        if self._inside_think:
            return "", pending
        if self._strip_next_visible_prefix:
            pending = pending.lstrip()
            if pending:
                self._strip_next_visible_prefix = False
        return pending, ""


def openai_sse_frame(payload: dict[str, object] | str, *, event_id: str | None = None) -> bytes:
    if isinstance(payload, str):
        data = payload
    else:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}data: {data}\n\n".encode("utf-8")


def anthropic_sse_frame(event: str, payload: dict[str, object], *, event_id: str | None = None) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}event: {event}\ndata: {data}\n\n".encode("utf-8")


class OpenAIStreamTimelineEmitter:
    """Small-delta OpenAI SSE emitter for compat routes.

    The route owns execution and lifecycle side effects; this class owns wire
    timeline shape so text, reasoning and tool-call deltas do not get merged or
    reordered by generic chat streaming code.
    """

    def __init__(self, *, response_id: str, model_name: str, created: int | None = None) -> None:
        self.response_id = response_id
        self.model_name = model_name
        self.created = int(created or time.time())
        self._role_emitted = False
        self._event_counter = 0
        self._think_filter = InlineThinkStreamFilter()

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"{self.response_id}:{self._event_counter}"

    def _chunk(self, delta: dict[str, Any], *, finish_reason: str | None = None) -> bytes:
        return openai_sse_frame(
            {
                "id": self.response_id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model_name,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
            },
            event_id=self._next_event_id(),
        )

    def ensure_role(self) -> list[bytes]:
        if self._role_emitted:
            return []
        self._role_emitted = True
        return [self._chunk({"role": "assistant"})]

    def text_delta(self, content: str) -> list[bytes]:
        if not content:
            return []
        visible_text, inline_reasoning = self._think_filter.feed(content)
        frames: list[bytes] = []
        if inline_reasoning:
            frames.extend(self.reasoning_delta(inline_reasoning))
        if visible_text:
            frames.extend([*self.ensure_role(), self._chunk({"content": visible_text})])
        return frames

    def reasoning_delta(self, content: str) -> list[bytes]:
        if not content:
            return []
        return [*self.ensure_role(), self._chunk({"reasoning_content": content})]

    def tool_call_delta(
        self,
        *,
        index: int,
        wire_id: str,
        wire_name: str,
        arguments: str,
    ) -> list[bytes]:
        if not wire_id or not wire_name:
            return []
        return [
            *self.ensure_role(),
            self._chunk(
                {
                    "tool_calls": [
                        {
                            "index": int(index),
                            "id": wire_id,
                            "type": "function",
                            "function": {"name": wire_name, "arguments": arguments},
                        }
                    ]
                }
            ),
        ]

    def approval_notice(self, notice: str) -> list[bytes]:
        return self.text_delta(notice)

    def finish(self, finish_reason: str) -> list[bytes]:
        visible_text, inline_reasoning = self._think_filter.flush()
        frames: list[bytes] = []
        if inline_reasoning:
            frames.extend(self.reasoning_delta(inline_reasoning))
        if visible_text:
            frames.extend([*self.ensure_role(), self._chunk({"content": visible_text})])
        return [*frames, *self.ensure_role(), self._chunk({}, finish_reason=finish_reason), openai_sse_frame("[DONE]", event_id=self._next_event_id())]


class AnthropicStreamTimelineEmitter:
    """Anthropic Messages SSE emitter that keeps block continuity.

    Consecutive text deltas stay inside one text block, consecutive thinking
    deltas stay inside one thinking block, and tool_use blocks close
    immediately as required by the wire format.
    """

    def __init__(self, *, response_id: str, model_name: str, emit_inline_thinking: bool = False) -> None:
        self.response_id = response_id
        self.model_name = model_name
        self.emit_inline_thinking = bool(emit_inline_thinking)
        self._next_block_index = 0
        self._active_block_index: int | None = None
        self._active_block_type = ""
        self._event_counter = 0
        self._think_filter = InlineThinkStreamFilter()

    def _next_event_id(self) -> str:
        self._event_counter += 1
        return f"{self.response_id}:{self._event_counter}"

    def _frame(self, event: str, payload: dict[str, object]) -> bytes:
        return anthropic_sse_frame(event, payload, event_id=self._next_event_id())

    def message_start(self) -> bytes:
        return self._frame(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": self.response_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.model_name,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )

    def _close_active_block(self) -> list[bytes]:
        if self._active_block_index is None:
            return []
        block_index = self._active_block_index
        self._active_block_index = None
        self._active_block_type = ""
        return [self._frame("content_block_stop", {"type": "content_block_stop", "index": block_index})]

    def _ensure_block(self, block_type: str, content_block: dict[str, Any]) -> tuple[list[bytes], int]:
        if self._active_block_index is not None and self._active_block_type == block_type:
            return [], self._active_block_index
        frames = self._close_active_block()
        block_index = self._next_block_index
        self._next_block_index += 1
        self._active_block_index = block_index
        self._active_block_type = block_type
        frames.append(
            self._frame(
                "content_block_start",
                {"type": "content_block_start", "index": block_index, "content_block": content_block},
            )
        )
        return frames, block_index

    def text_delta(self, content: str) -> list[bytes]:
        if not content:
            return []
        content, inline_reasoning = self._think_filter.feed(content)
        frames: list[bytes] = []
        if self.emit_inline_thinking and inline_reasoning:
            frames.extend(self.thinking_delta(inline_reasoning))
        if not content:
            return frames
        block_frames, block_index = self._ensure_block("text", {"type": "text", "text": ""})
        frames.extend(block_frames)
        frames.append(
            self._frame(
                "content_block_delta",
                {"type": "content_block_delta", "index": block_index, "delta": {"type": "text_delta", "text": content}},
            )
        )
        return frames

    def thinking_delta(self, content: str) -> list[bytes]:
        if not content:
            return []
        frames, block_index = self._ensure_block("thinking", {"type": "thinking", "thinking": "", "signature": ""})
        frames.append(
            self._frame(
                "content_block_delta",
                {"type": "content_block_delta", "index": block_index, "delta": {"type": "thinking_delta", "thinking": content}},
            )
        )
        return frames

    def tool_use(self, *, wire_id: str, wire_name: str, input_payload: dict[str, Any]) -> list[bytes]:
        if not wire_id or not wire_name:
            return []
        frames = self._close_active_block()
        block_index = self._next_block_index
        self._next_block_index += 1
        frames.append(
            self._frame(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {"type": "tool_use", "id": wire_id, "name": wire_name, "input": {}},
                },
            )
        )
        frames.append(
            self._frame(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": block_index,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(input_payload or {}, ensure_ascii=False)},
                },
            )
        )
        frames.append(self._frame("content_block_stop", {"type": "content_block_stop", "index": block_index}))
        return frames

    def approval_notice(self, notice: str) -> list[bytes]:
        return self.text_delta(notice)

    def finish(self, stop_reason: str) -> list[bytes]:
        visible_text, inline_reasoning = self._think_filter.flush()
        frames: list[bytes] = []
        if self.emit_inline_thinking and inline_reasoning:
            frames.extend(self.thinking_delta(inline_reasoning))
        if visible_text:
            frames.extend(self.text_delta(visible_text))
        return [
            *frames,
            *self._close_active_block(),
            self._frame(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": 0},
                },
            ),
            self._frame("message_stop", {"type": "message_stop"}),
        ]

    def error(self, message: str) -> bytes:
        return self._frame("error", {"type": "error", "error": {"type": "api_error", "message": message}})


class CompatWireEmitter:
    """Protocol-specific response adapter for external compat clients.

    Ingress filtering and ChatRuntime execution stay separate. This emitter is
    the single place that turns V8 runtime events back into OpenAI/Anthropic
    wire payloads for non-streaming routes; streaming routes can use the same
    contract as they are gradually thinned.
    """

    def openai_chat_completion(
        self,
        *,
        response_id: str,
        model_name: str,
        events: list[dict[str, Any]],
        external_tools: list[ExternalToolSpec] | None = None,
    ) -> dict[str, Any]:
        return build_openai_completion_response(
            response_id=response_id,
            model_name=model_name,
            events=events,
            external_tools=external_tools,
        )

    def anthropic_message(
        self,
        *,
        response_id: str,
        model_name: str,
        events: list[dict[str, Any]],
        external_tools: list[ExternalToolSpec] | None = None,
        include_thinking: bool = False,
    ) -> dict[str, Any]:
        return build_anthropic_message_response(
            response_id=response_id,
            model_name=model_name,
            events=events,
            external_tools=external_tools,
            include_thinking=include_thinking,
        )


compat_wire_emitter = CompatWireEmitter()
