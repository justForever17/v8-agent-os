from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage


def test_run_level_compaction_records_observability_and_dynamic_segment(monkeypatch):
    from core.context_orchestrator import ContextOrchestrator
    import core.context_orchestrator as module
    from core.prompt_cache_gateway import PromptCacheGateway

    records: list[dict] = []

    class FakeObservability:
        def add_conversation_compaction_record(self, record):
            record = dict(record)
            record["id"] = "cmp_test"
            records.append(record)
            return record

    monkeypatch.setattr(module, "observability_db", FakeObservability())
    monkeypatch.setattr(module.storage, "get_context_config", lambda: {
        "schema_version": 3,
        "compression": {
            "enabled": True,
            "mode": "persistent_baseline",
            "trigger_ratio": 0.1,
            "keep_recent_turns": 1,
            "keep_recent_messages": 2,
            "use_llm_summary": False,
            "default_context_window_tokens": 400,
        },
    })
    monkeypatch.setattr(module.storage, "get_role_model_id", lambda role: "")
    monkeypatch.setattr(module.llm_factory, "get_model_context_window", lambda model_id: 400)
    monkeypatch.setattr(module, "get_runtime_context", lambda: {
        "session_id": "sess_1",
        "run_id": "run_1",
        "runtime_kind": "chat",
        "latest_seq": 7,
    })
    monkeypatch.setattr(module, "flush_before_context_compaction", lambda messages: {"ok": True, "skipped": False, "reason": "test"})
    monkeypatch.setattr(module, "load_compaction_baseline", lambda session_id, target_role: None)
    monkeypatch.setattr(
        module,
        "persist_compaction_baseline",
        lambda **kwargs: {
            "snapshotId": "ctxb_test",
            "coveredMessagesHash": module.digest_messages(kwargs["covered_messages"]),
            "coveredMessageCount": len(kwargs["covered_messages"]),
            "baselineText": kwargs["baseline_text"],
            "summaryMethod": kwargs["summary_method"],
        },
    )

    messages = []
    for index in range(8):
        messages.append(HumanMessage(content=f"用户请求 {index} " + ("x" * 120)))
        messages.append(AIMessage(content=f"助手响应 {index} " + ("y" * 120)))

    prepared = ContextOrchestrator().prepare(
        messages=messages,
        runtime_kind="chat",
        target_role="supervisor",
        resolved_model_id="test-model",
    )

    assert records
    assert records[0]["session_id"] == "sess_1"
    assert records[0]["run_id"] == "run_1"
    assert records[0]["target_role"] == "supervisor"
    assert records[0]["covered_message_count"] > 0
    assert prepared.audit["compactionRecordId"] == "cmp_test"
    assert prepared.audit["baselineSnapshotRef"] == "ctxb_test"

    history_messages = [
        message
        for message in prepared.messages
        if "[CONTEXT BLOCK: HISTORY_SUMMARY]" in str(getattr(message, "content", ""))
    ]
    assert history_messages
    segments = PromptCacheGateway()._segment_messages(history_messages)
    assert segments
    assert {segment.segment_type for segment in segments} == {"dynamic"}

