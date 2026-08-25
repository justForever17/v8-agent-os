from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from core.observability_db import redact_observability_text
from core.response_normalizer import extract_text_and_reasoning


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
    MAX_PROJECTED_CHARS = 6000
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
        if not final_value:
            return current
        if not current or final_value.startswith(current):
            return final_value
        if current.startswith(final_value):
            return current
        return final_value

    def observe(self, chunk: Any) -> None:
        text, reasoning = extract_text_and_reasoning(chunk)
        now = time.monotonic()
        self._raw_chunk_count += 1
        previous_sequences = tuple(channel.sequence for channel in self._channels.values())
        self._append("analysis", self._delta_for("analysis", reasoning), now=now)
        self._append("text", self._delta_for("text", text), now=now)
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

    def _delta_for(self, kind: str, value: str) -> str:
        """Normalize providers that send cumulative snapshots instead of deltas."""
        delta = str(value or "")
        if not delta:
            return ""
        current = self._channels[kind].content
        if not current:
            return delta
        if delta.startswith(current):
            return delta[len(current):]
        if delta == current or current.endswith(delta):
            return ""
        return delta

    def _append(self, kind: str, delta: str, *, now: float) -> None:
        if not delta:
            return
        channel = self._channels[kind]
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
        redacted = redact_observability_text(channel.content)
        bounded = (
            redacted
            if len(redacted) <= self.MAX_PROJECTED_CHARS
            else redacted[: self.MAX_PROJECTED_CHARS - 1].rstrip() + "..."
        )
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
