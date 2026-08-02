from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agents.runners.supervisor_runner import SupervisorAgentRunner
from api.models import ChatRequest
from core.context_orchestrator import ContextOrchestrator
from graph.state_channels import reduce_message_deltas
from runtimes.chat.runtime import ChatRuntime


def test_ingress_messages_keep_client_ids_and_generate_stable_legacy_ids() -> None:
    request = ChatRequest(
        session_id="session-stable-ids",
        clientMessageId="client-latest-user",
        messages=[
            {"messageId": "persisted-user", "role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "latest"},
        ],
    )

    first = ChatRuntime()._to_langchain_messages(request)
    second = ChatRuntime()._to_langchain_messages(request)

    assert [message.id for message in first] == [
        "persisted-user",
        first[1].id,
        "client-latest-user",
    ]
    assert first[1].id.startswith("ingress_")
    assert [message.id for message in first] == [message.id for message in second]
    assert all(message.additional_kwargs["v8_ingress_history"] for message in first)


def test_persistent_runner_drops_resent_history_but_keeps_only_new_user() -> None:
    existing = [
        HumanMessage(id="user-1", content="first"),
        AIMessage(id="assistant-1", content="answer"),
    ]

    class _Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": existing})

    incoming = [
        HumanMessage(id="user-1", content="first", additional_kwargs={"v8_ingress_history": True}),
        AIMessage(id="assistant-1", content="answer", additional_kwargs={"v8_ingress_history": True}),
        HumanMessage(id="user-2", content="second", additional_kwargs={"v8_ingress_history": True}),
        ToolMessage(
            id="tool-live",
            content="approved",
            tool_call_id="call-1",
            additional_kwargs={"v8_ingress_tool_output": True},
        ),
    ]

    reconciled, diagnostics = asyncio.run(
        SupervisorAgentRunner()._reconcile_persistent_input(
            graph=_Graph(),
            graph_config={"configurable": {"thread_id": "session"}},
            messages=incoming,
        )
    )

    assert [message.id for message in reconciled] == ["user-2", "tool-live"]
    assert diagnostics["droppedHistoricalMessageCount"] == 2

    existing.append(HumanMessage(id="user-2", content="second"))
    retried, _ = asyncio.run(
        SupervisorAgentRunner()._reconcile_persistent_input(
            graph=_Graph(),
            graph_config={"configurable": {"thread_id": "session"}},
            messages=incoming[:-1],
        )
    )
    assert retried == []


def test_persistent_compaction_replaces_graph_history_with_one_summary(monkeypatch) -> None:
    policy = {
        "compression": {
            "enabled": True,
            "mode": "persistent_baseline",
            "default_context_window_tokens": 2048,
            "trigger_ratio": 0.70,
            "hard_trigger_ratio": 0.70,
            "keep_recent_turns": 1,
            "keep_recent_messages": 2,
            "use_llm_summary": False,
            "max_summary_input_tokens": 20_000,
            "max_summary_input_messages": 60,
            "max_summary_output_tokens": 800,
        }
    }
    monkeypatch.setattr("core.context_orchestrator.storage.get_context_config", lambda: policy)
    monkeypatch.setattr("core.context_orchestrator.storage.get_role_model_id", lambda _role: "")
    monkeypatch.setattr("core.context_orchestrator.llm_factory.get_model_context_window", lambda _model: 2048)
    monkeypatch.setattr(
        "core.context_orchestrator.flush_before_context_compaction",
        lambda _messages: {"ok": True, "skipped": False, "reason": "test"},
    )
    monkeypatch.setattr(
        "core.context_orchestrator.get_runtime_context",
        lambda: {"session_id": "session-persistent", "run_id": "run-persistent"},
    )
    monkeypatch.setattr("core.context_orchestrator.load_compaction_baseline", lambda **_kwargs: None)
    monkeypatch.setattr(
        "core.context_orchestrator.persist_compaction_baseline",
        lambda **kwargs: {
            "snapshotId": "baseline-test",
            "coveredMessageCount": len(kwargs["covered_messages"]),
            "baselineText": kwargs["baseline_text"],
            "summaryMethod": kwargs["summary_method"],
        },
    )
    monkeypatch.setattr(
        "core.context_orchestrator.observability_db.add_conversation_compaction_record",
        lambda _record: {"id": "compaction-test"},
    )
    messages = []
    for index in range(5):
        messages.extend(
            [
                HumanMessage(id=f"user-{index}", content=f"question-{index}:" + "x" * 1800),
                AIMessage(id=f"assistant-{index}", content=f"answer-{index}:" + "y" * 1800),
            ]
        )

    prepared = ContextOrchestrator().prepare(
        messages=messages,
        runtime_kind="chat",
        target_role="supervisor",
        resolved_model_id="test-model",
    )

    assert prepared.audit["persistent_state_compaction"] is True
    compacted = reduce_message_deltas(messages, [prepared.state_message_updates])
    summaries = [
        message
        for message in compacted
        if isinstance(message, SystemMessage)
        and message.additional_kwargs.get("v8_context_summary")
    ]
    assert len(summaries) == 1
    assert summaries[0].id.startswith("v8_context_summary_")
    assert [message.id for message in compacted[-2:]] == ["user-4", "assistant-4"]
    assert len(compacted) == 3


def test_opaque_provider_turn_is_protected_across_long_tool_tail() -> None:
    continuation = {
        "schemaVersion": 1,
        "providerStandard": "openai",
        "contentBlocks": [
            {"type": "reasoning", "id": "reasoning-1", "encrypted_content": "opaque"}
        ],
    }
    messages = [
        HumanMessage(content="work"),
        AIMessage(
            content=[
                continuation["contentBlocks"][0],
                {"type": "text", "text": "calling tools"},
            ],
        ),
        *[
            ToolMessage(content=f"result-{index}", tool_call_id=f"call-{index}")
            for index in range(32)
        ],
    ]

    assert ContextOrchestrator._protect_latest_provider_continuation(messages, len(messages)) == 1
