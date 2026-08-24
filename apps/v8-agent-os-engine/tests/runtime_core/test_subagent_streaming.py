from __future__ import annotations

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
    aggregator.observe(AIMessageChunk(content="a"))
    aggregator.observe(AIMessageChunk(content="ab"))
    aggregator.finish(AIMessage(content="ab"))

    text_updates = [
        item["timelineNode"]
        for item in emitted
        if item["timelineNode"]["topic"] == "subagent.text.delta"
    ]
    assert text_updates[-1]["content"] == "ab"
