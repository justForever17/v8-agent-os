from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import BaseMessage, BaseMessageChunk

from core.observability_db import redact_observability_text
from core.response_normalizer import extract_text_and_reasoning


MAX_PROJECTED_CHARS = 6000


def project_subagent_stream_text(value: str) -> tuple[str, int]:
    """Share a bounded, redacted head/tail view across live and final nodes."""
    redacted = redact_observability_text(value)
    if len(redacted) <= MAX_PROJECTED_CHARS:
        return redacted, 0
    marker = "\n...\n"
    head_chars = MAX_PROJECTED_CHARS // 4
    tail_chars = MAX_PROJECTED_CHARS - head_chars - len(marker)
    return redacted[:head_chars] + marker + redacted[-tail_chars:], len(redacted) - head_chars - tail_chars


@dataclass(slots=True)
class _StreamChannel:
    content: str = ""
    emitted_chars: int = 0
    chunk_count: int = 0
    sequence: int = 0
    last_emitted_at: float = 0.0


class SubagentStreamProgressAggregator:
    """Publish bounded child model progress without exposing provider payloads."""

    FLUSH_INTERVAL_SECONDS = 0.5
    FLUSH_CHAR_THRESHOLD = 320
    MAX_PROJECTED_CHARS = MAX_PROJECTED_CHARS
    ACTIVITY_HEARTBEAT_SECONDS = 8.0

    def __init__(
        self,
        *,
        progress_callback: Callable[[dict[str, Any]], Any],
        agent_id: str,
        agent_name: str,
        delegation_id: str,
        model_turn: int,
    ) -> None:
        self._progress_callback = progress_callback
        self._agent_id = str(agent_id or "subagent").strip() or "subagent"
        self._agent_name = str(agent_name or self._agent_id).strip() or self._agent_id
        self._delegation_id = str(delegation_id or "delegation").strip() or "delegation"
        self._model_turn = max(int(model_turn or 1), 1)
        self._channels = {
            "analysis": _StreamChannel(),
            "text": _StreamChannel(),
        }
        self._raw_chunk_count = 0
        self._last_projected_at = 0.0

    def _node_id(self, kind: str) -> str:
        return (
            f"subagent:{self._delegation_id}:{self._agent_id}:"
            f"model:{self._model_turn}:{kind}"
        )

    @staticmethod
    def _merge_final_content(current: str, final_value: str) -> str:
        return final_value or current

    def observe(self, chunk: Any) -> None:
        text, reasoning = extract_text_and_reasoning(chunk)
        now = time.monotonic()
        self._raw_chunk_count += 1
        previous_sequences = tuple(channel.sequence for channel in self._channels.values())
        snapshot = isinstance(chunk, BaseMessage) and not isinstance(chunk, BaseMessageChunk)
        self._append("analysis", reasoning, now=now, snapshot=snapshot)
        self._append("text", text, now=now, snapshot=snapshot)
        current_sequences = tuple(channel.sequence for channel in self._channels.values())
        if current_sequences != previous_sequences:
            self._last_projected_at = now
            return
        if self._last_projected_at and now - self._last_projected_at < self.ACTIVITY_HEARTBEAT_SECONDS:
            return
        self._last_projected_at = now
        self._progress_callback(
            {
                "stage": "model_stream_active",
                "status": "running",
                "summary": f"{self._agent_name} 正在接收模型响应。",
                "timelineNode": {
                    "id": self._node_id("activity"),
                    "kind": "execution",
                    "executionType": "runtime_progress",
                    "topic": "subagent.model.stream.activity",
                    "content": "",
                    "partial": True,
                    "finalized": False,
                    "streamSequence": self._raw_chunk_count,
                    "ownerStreamKey": self._node_id("activity"),
                    "timestamp": int(time.time() * 1000),
                    "data": {
                        "rawChunkCount": self._raw_chunk_count,
                        "modelTurn": self._model_turn,
                    },
                },
            }
        )

    def _append(self, kind: str, delta: str, *, now: float, snapshot: bool = False) -> None:
        if not delta:
            return
        channel = self._channels[kind]
        if snapshot:
            if channel.content == delta:
                return
            channel.content = str(delta)
            channel.emitted_chars = 0
        else:
            # Native message chunks are deltas. Equal suffixes are legitimate
            # repeated words, whitespace and punctuation, not duplicate events.
            channel.content += str(delta)
        channel.chunk_count += 1
        pending_chars = len(channel.content) - channel.emitted_chars
        if (
            channel.sequence == 0
            or pending_chars >= self.FLUSH_CHAR_THRESHOLD
            or now - channel.last_emitted_at >= self.FLUSH_INTERVAL_SECONDS
        ):
            self._emit(kind, finalized=False, now=now)

    def _emit(self, kind: str, *, finalized: bool, now: float | None = None) -> None:
        channel = self._channels[kind]
        if not channel.content:
            return
        if not finalized and channel.emitted_chars >= len(channel.content):
            return
        channel.sequence += 1
        channel.emitted_chars = len(channel.content)
        channel.last_emitted_at = float(now if now is not None else time.monotonic())
        self._last_projected_at = channel.last_emitted_at
        bounded, omitted_chars = project_subagent_stream_text(channel.content)
        is_analysis = kind == "analysis"
        topic = "subagent.reasoning.delta" if is_analysis else "subagent.text.delta"
        timeline_node = {
            "id": self._node_id(kind),
            "kind": "execution" if is_analysis else "narrative",
            **({"executionType": "reasoning"} if is_analysis else {"role": "assistant"}),
            "topic": topic,
            "content": bounded,
            "partial": not finalized,
            "finalized": finalized,
            "streamSequence": channel.sequence,
            "ownerStreamKey": self._node_id(kind),
            "timestamp": int(time.time() * 1000),
            "data": {
                "rawChunkCount": channel.chunk_count,
                "projectedChars": len(bounded),
                "omittedChars": omitted_chars,
                "modelTurn": self._model_turn,
            },
        }
        self._progress_callback(
            {
                "stage": "reasoning" if is_analysis else "responding",
                "status": "running",
                "summary": (
                    f"{self._agent_name} 正在核对证据。"
                    if is_analysis
                    else f"{self._agent_name} 正在回传进展。"
                ),
                "timelineNode": timeline_node,
            }
        )

    def flush(self, *, finalized: bool = False) -> dict[str, str]:
        for kind in ("analysis", "text"):
            self._emit(kind, finalized=finalized)
        return {
            kind: self._node_id(kind)
            for kind, channel in self._channels.items()
            if channel.content
        }

    def finish(self, response: Any) -> dict[str, str]:
        final_text, final_reasoning = extract_text_and_reasoning(response)
        self._channels["text"].content = self._merge_final_content(
            self._channels["text"].content,
            final_text,
        )
        self._channels["analysis"].content = self._merge_final_content(
            self._channels["analysis"].content,
            final_reasoning,
        )
        return self.flush(finalized=True)
