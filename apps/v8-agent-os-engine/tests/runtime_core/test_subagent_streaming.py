from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from core.subagent_streaming import SubagentStreamProgressAggregator
from graph.parallel_support import _subagent_timeline_nodes_from_message


def test_subagent_stream_aggregates_reasoning_and_text_with_stable_final_nodes(monkeypatch) -> None:
    ticks = iter([10.0, 10.1, 10.7, 10.8, 10.9, 11.0])
    monkeypatch.setattr("core.subagent_streaming.time.monotonic", lambda: next(ticks))
    emitted: list[dict] = []
    aggregator = SubagentStreamProgressAggregator(
        progress_callback=lambda payload: emitted.append(payload),
        agent_id="worker-one",
        agent_name="Worker One",
        delegation_id="delegation-one",
        model_turn=2,
    )

    aggregator.observe(
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": "先核对"})
    )
    aggregator.observe(
        AIMessageChunk(content="结果", additional_kwargs={"reasoning_content": "证据。"})
    )
    stream_ids = aggregator.finish(
        AIMessage(
            content="结果完成。",
            additional_kwargs={"reasoning_content": "先核对证据。"},
        )
    )

    reasoning_updates = [
        item["timelineNode"]
        for item in emitted
        if item["timelineNode"]["topic"] == "subagent.reasoning.delta"
    ]
    text_updates = [
        item["timelineNode"]
        for item in emitted
        if item["timelineNode"]["topic"] == "subagent.text.delta"
    ]
    assert reasoning_updates[0]["partial"] is True
    assert reasoning_updates[-1]["content"] == "先核对证据。"
    assert reasoning_updates[-1]["finalized"] is True
    assert text_updates[-1]["content"] == "结果完成。"
    assert len({item["id"] for item in reasoning_updates}) == 1

    final_message = AIMessage(
        id="child-final",
        content="结果完成。",
        additional_kwargs={
            "reasoning_content": "先核对证据。",
            "v8_subagent_stream_node_ids": stream_ids,
        },
    )
    final_nodes = _subagent_timeline_nodes_from_message(final_message)
    assert final_nodes[0]["id"] == stream_ids["analysis"]
    assert final_nodes[1]["id"] == stream_ids["text"]
    assert all(node["finalized"] is True for node in final_nodes)


def test_subagent_stream_deduplicates_cumulative_provider_snapshots() -> None:
    emitted: list[dict] = []
    aggregator = SubagentStreamProgressAggregator(
        progress_callback=lambda payload: emitted.append(payload),
        agent_id="worker-one",
        agent_name="Worker One",
        delegation_id="delegation-one",
        model_turn=1,
    )
    aggregator.observe(AIMessage(content="a"))
    aggregator.observe(AIMessage(content="ab"))
    aggregator.finish(AIMessage(content="ab"))

    text_updates = [
        item["timelineNode"]
        for item in emitted
        if item["timelineNode"]["topic"] == "subagent.text.delta"
    ]
    assert text_updates[-1]["content"] == "ab"


@pytest.mark.parametrize("parts", [["a", "a", "ab", ".", "."], ["哈", "哈", "。", "。"], ["word", " ", "word"]])
def test_subagent_preserves_repeated_native_delta_text_and_reasoning(parts):
    emitted = []
    aggregator = SubagentStreamProgressAggregator(
        progress_callback=emitted.append, agent_id="worker", agent_name="Worker",
        delegation_id="delegation", model_turn=1,
    )
    for part in parts:
        aggregator.observe(AIMessageChunk(content=part, additional_kwargs={"reasoning_content": part}))
    # Failure can end before a final snapshot repairs the accumulated content.
    aggregator.flush()
    for topic in ("subagent.text.delta", "subagent.reasoning.delta"):
        latest = [item["timelineNode"] for item in emitted if item["timelineNode"]["topic"] == topic][-1]
        assert latest["content"] == "".join(parts)


def test_long_subagent_progress_keeps_updating_and_final_projection_does_not_shrink():
    emitted = []
    aggregator = SubagentStreamProgressAggregator(
        progress_callback=emitted.append, agent_id="worker", agent_name="Worker",
        delegation_id="delegation", model_turn=1,
    )
    original = "Opening facts.\n" + "verified evidence with conditions\n" * 220
    tail = "Latest observation: conflicting source identity remains unverified."
    aggregator.observe(AIMessageChunk(content=original))
    aggregator.observe(AIMessageChunk(content=tail))
    aggregator.flush()
    last = emitted[-1]["timelineNode"]
    assert last["content"].startswith("Opening facts.")
    assert last["content"].endswith(tail)
    assert len(last["content"]) <= aggregator.MAX_PROJECTED_CHARS
    assert last["data"]["omittedChars"] > 0
    response = AIMessage(content=original + tail)
    ids = aggregator.finish(response)
    response.additional_kwargs["v8_subagent_stream_node_ids"] = ids
    final = _subagent_timeline_nodes_from_message(response)[0]
    assert final["content"] == emitted[-1]["timelineNode"]["content"]
    assert final["content"].endswith(tail)


def test_full_snapshot_revision_is_authoritative_even_when_shorter():
    emitted = []
    aggregator = SubagentStreamProgressAggregator(
        progress_callback=emitted.append, agent_id="worker", agent_name="Worker",
        delegation_id="delegation", model_turn=1,
    )
    aggregator.observe(AIMessage(content="Provisional conclusion needs correction."))
    aggregator.observe(AIMessage(content="Corrected conclusion."))
    aggregator.finish(AIMessage(content="Corrected."))
    assert emitted[-1]["timelineNode"]["content"] == "Corrected."


def test_subagent_stream_projects_bounded_activity_when_chunks_have_no_visible_text(monkeypatch) -> None:
    ticks = iter([10.0, 12.0, 18.1])
    monkeypatch.setattr("core.subagent_streaming.time.monotonic", lambda: next(ticks))
    emitted: list[dict] = []
    aggregator = SubagentStreamProgressAggregator(
        progress_callback=lambda payload: emitted.append(payload),
        agent_id="worker-one",
        agent_name="Worker One",
        delegation_id="delegation-one",
        model_turn=1,
    )

    empty_chunk = AIMessageChunk(content="")
    aggregator.observe(empty_chunk)
    aggregator.observe(empty_chunk)
    aggregator.observe(empty_chunk)

    heartbeats = [
        item["timelineNode"]
        for item in emitted
        if item["timelineNode"]["topic"] == "subagent.model.stream.activity"
    ]
    assert len(heartbeats) == 2
    assert len({item["id"] for item in heartbeats}) == 1
    assert heartbeats[-1]["data"] == {"rawChunkCount": 3, "modelTurn": 1}
    assert all(item["content"] == "" for item in heartbeats)
