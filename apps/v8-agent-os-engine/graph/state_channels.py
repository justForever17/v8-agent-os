from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


MESSAGE_DELTA_SNAPSHOT_FREQUENCY = 32


def reduce_message_deltas(
    state: list[AnyMessage],
    writes: Sequence[list[AnyMessage]],
) -> list[AnyMessage]:
    """Bulk `add_messages` reducer required by LangGraph DeltaChannel.

    Applying writes one batch at a time is equivalent to applying the same
    writes in one call. This preserves message-id replacement and
    RemoveMessage semantics while allowing checkpoints to store only deltas.
    """

    result: list[AnyMessage] = list(state or [])
    for write in writes:
        if write:
            result = list(add_messages(result, list(write)))
    return result


def message_state_digest_payload(messages: Sequence[AnyMessage]) -> list[dict[str, Any]]:
    """Stable semantic projection used by retention equivalence checks."""

    payload: list[dict[str, Any]] = []
    for message in messages:
        if hasattr(message, "model_dump"):
            item = message.model_dump(mode="json")
        elif isinstance(message, dict):
            item = dict(message)
        else:
            item = {"type": type(message).__name__, "content": str(message)}
        payload.append(item)
    return payload


__all__ = [
    "MESSAGE_DELTA_SNAPSHOT_FREQUENCY",
    "message_state_digest_payload",
    "reduce_message_deltas",
]
