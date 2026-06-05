from __future__ import annotations

from agents.memory_agent import (
    _canonical_message_to_transcript_entry,
    _durable_message_to_transcript_entry,
    _projection_message_to_transcript_entry,
)


def test_projection_transcript_omits_reasoning_parts() -> None:
    entry = _projection_message_to_transcript_entry(
        {
            "id": "m1",
            "role": "assistant",
            "parts": [
                {"type": "reasoning", "content": "hidden chain should not be memorized"},
                {"type": "text", "content": "visible answer"},
            ],
        },
        0,
    )

    assert entry is not None
    assert "visible answer" in entry["content"]
    assert "hidden chain" not in entry["content"]


def test_durable_transcript_omits_reasoning_content_field() -> None:
    entry = _durable_message_to_transcript_entry(
        {
            "id": "m2",
            "role": "assistant",
            "content": "visible content",
            "reasoning_content": "hidden durable reasoning",
        },
        0,
    )

    assert entry is not None
    assert "visible content" in entry["content"]
    assert "hidden durable reasoning" not in entry["content"]


def test_canonical_transcript_omits_reasoning_nodes_and_fields() -> None:
    entry = _canonical_message_to_transcript_entry(
        {
            "id": "m3",
            "role": "assistant",
            "reasoning_text": "hidden canonical reasoning",
            "nodes": [
                {"kind": "execution", "executionType": "reasoning", "content": "hidden node reasoning"},
                {"kind": "narrative", "content": "visible narrative"},
            ],
        },
        0,
    )

    assert entry is not None
    assert "visible narrative" in entry["content"]
    assert "hidden canonical reasoning" not in entry["content"]
    assert "hidden node reasoning" not in entry["content"]
