from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from core.database import DatabaseManager
from core.conversation_recovery import ConversationConflict, create_branch, revise_message
from erc.chat_canonical_transcript import _stable_turn_id, group_canonical_turn_rows, format_canonical_message


@pytest.fixture
def database(tmp_path):
    database = DatabaseManager(tmp_path / "state.sqlite")
    database.create_session_placeholder(session_id="source", title="需求👩‍💻讨论", user_id="owner", metadata={"model": "configured", "secretRef": "do-not-copy"})
    database.upsert_session_scope_binding({"session_id": "source", "conversation_id": "source", "thread_id": "source", "user_id": "owner", "workspace_id": "ws", "workspace_path": str(tmp_path), "project_id": "project", "resolved_scope": "project", "scope_source": "user"})
    for ordinal, (role, content) in enumerate((("user", "old premise"), ("assistant", "old answer"), ("user", "follow up"), ("assistant", "later answer")), 1):
        nodes = [{"id": f"n{ordinal}", "kind": "narrative", "content": content}]
        if ordinal == 4:
            nodes.insert(0, {"id": "proof", "kind": "execution", "executionType": "tool_result", "toolCallId": "receipt", "result": {"written": True}})
        database.create_chat_canonical_message(message_id=f"m{ordinal}", session_id="source", run_id=None, ordinal=ordinal,
            role=role, state="completed", nodes=nodes, content_text=content, metadata={})
    return database


def revision(database, message_id="m4", content="replacement", **overrides):
    args = dict(session_id="source", message_id=message_id, owner="owner", content=content,
                expected_message_version=database.get_chat_canonical_message(message_id)["version"],
                expected_transcript_revision=database.get_chat_transcript_state("source")["transcript_revision"])
    return revise_message(database, **{**args, **overrides})


def branch(database, **overrides):
    turn = group_canonical_turn_rows(database.get_chat_canonical_messages("source"))[0]
    args = dict(session_id="source", owner="owner", turn_id=_stable_turn_id("source", turn),
                expected_transcript_revision=database.get_chat_transcript_state("source")["transcript_revision"])
    return create_branch(database, **{**args, **overrides})


def test_revision_cas_ledger_proof_and_restart(database):
    before = database.get_chat_canonical_message("m4")
    state = database.get_chat_transcript_state("source")
    result = revision(database, content="  # Edited\n\nexact **Markdown**\n")
    row = database.get_chat_canonical_message("m4")
    assert row["nodes"][0] == before["nodes"][0]
    assert format_canonical_message(row)["content"] == "  # Edited\n\nexact **Markdown**\n"
    assert result["contextEpoch"] == 1 and result["transcriptRevision"] > state["transcript_revision"]
    assert row["metadata"]["editedBy"] == "user"
    with database.get_connection() as conn:
        ledger = conn.execute("SELECT * FROM chat_message_revisions").fetchone()
        assert json.loads(ledger["previous_snapshot_json"])["content_text"] == "later answer"
        event = json.loads(conn.execute("SELECT payload_json FROM runtime_events").fetchone()[0])
        assert "later answer" not in json.dumps(event) and "Markdown" not in json.dumps(event)
    with pytest.raises(ConversationConflict, match="message_revision_conflict"):
        revision(database, expected_message_version=1, expected_transcript_revision=state["transcript_revision"])
    reopened = DatabaseManager(database.db_path)
    assert reopened.get_chat_canonical_message("m4")["content_text"] == row["content_text"]
    assert reopened.get_chat_transcript_state("source") == database.get_chat_transcript_state("source")


def test_historical_revision_requires_explicit_truncate_and_advances_revision(database):
    for _ in range(12):
        database.update_chat_canonical_message("m4", content_text="high version")
    old_max = database.get_chat_canonical_max_version("source")
    before = database.get_chat_transcript_state("source")
    with pytest.raises(ConversationConflict, match="message_has_descendants"):
        revision(database, "m1")
    result = revision(database, "m1", tail_policy="truncate")
    assert [r["id"] for r in database.get_chat_canonical_messages("source")] == ["m1"]
    assert result["truncatedMessageIds"] == ["m2", "m3", "m4"]
    assert database.get_chat_canonical_max_version("source") == old_max
    assert result["transcriptRevision"] > before["transcript_revision"]


def test_two_clients_exactly_one_revision_commits(database):
    expected = database.get_chat_transcript_state("source")["transcript_revision"]
    def save(content):
        try:
            return revision(database, content=content, expected_message_version=1, expected_transcript_revision=expected)
        except ConversationConflict as error:
            return error.detail["code"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["web edit", "phone edit"]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "message_revision_conflict" in results


def test_branch_is_exact_inert_prefix_scope_isolated_and_survives_parent_deletion(database):
    source = database.get_chat_canonical_messages("source")
    with database.get_connection() as conn:
        conn.execute("INSERT INTO plugin_grants (id,plugin_id,scope,session_id,grantee_type,grantee_id,component_ids_json,created_at) VALUES ('grant','plugin','session','source','supervisor','owner','[]','now')")
        conn.commit()
    result = branch(database)
    child = result["sessionId"]
    copied = database.get_chat_canonical_messages(child)
    assert [r["content_text"] for r in copied] == ["old premise", "old answer"]
    assert all(r["run_id"] is None and r["id"] not in {s["id"] for s in source} for r in copied)
    assert all(r["metadata"]["branchInherited"] for r in copied)
    assert database.get_chat_canonical_messages("source") == source
    assert database.get_session_scope_binding(child)["project_id"] == "project"
    assert "secretRef" not in database.get_session(child)["metadata"]
    with database.get_connection() as conn:
        for table in ("run_records", "runtime_episodes", "plugin_grants", "pending_approvals", "chat_user_message_queue"):
            assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (child,)).fetchone()[0] == 0
    database.delete_session("source")
    assert database.get_chat_canonical_messages(child) == copied


def test_concurrent_branch_numbers_and_branch_of_branch(database):
    with ThreadPoolExecutor(max_workers=2) as pool:
        children = list(pool.map(lambda _: branch(database), range(2)))
    assert {r["branchNumber"] for r in children} == {1, 2}
    child = children[0]["sessionId"]
    rows = database.get_chat_canonical_messages(child)
    result = create_branch(database, session_id=child, owner="owner", turn_id=_stable_turn_id(child, rows),
                           expected_transcript_revision=database.get_chat_transcript_state(child)["transcript_revision"])
    assert result["title"] == "需求👩‍💻讨论(3)"


def test_branch_with_revision_drops_causally_old_answer(database):
    result = branch(database, message_id="m1", expected_message_version=1, content="new premise")
    rows = database.get_chat_canonical_messages(result["sessionId"])
    assert [(r["role"], r["content_text"]) for r in rows] == [("user", "new premise")]
    assert database.get_chat_canonical_message("m1")["content_text"] == "old premise"


def test_revision_rollback_on_event_failure(database):
    before = database.get_chat_canonical_messages("source")
    state = database.get_chat_transcript_state("source")
    with patch("core.conversation_recovery._event", side_effect=OSError("synthetic disk failure")):
        with pytest.raises(OSError):
            revision(database)
    assert database.get_chat_canonical_messages("source") == before
    assert database.get_chat_transcript_state("source") == state
    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_message_revisions").fetchone()[0] == 0


def test_busy_foreign_owner_and_old_epoch_cannot_write_or_execute(database):
    database.create_run_record("oldrun", "source", status="running")
    with pytest.raises(ConversationConflict, match="conversation_busy"):
        revision(database)
    database.update_run_record("oldrun", status="cancelled")
    with pytest.raises(ConversationConflict, match="session_owner_mismatch"):
        revision(database, owner="other")
    revision(database)
    with pytest.raises(ValueError, match="conversation_context_superseded"):
        database.assert_chat_run_epoch("source", "oldrun")
    with pytest.raises(ValueError, match="conversation_context_superseded"):
        database.create_chat_canonical_message(message_id="late", session_id="source", run_id="oldrun", ordinal=5,
                                               role="assistant", state="completed", nodes=[])
    with pytest.raises(ValueError, match="conversation_context_superseded"):
        database.create_run_record("oldrun", "source")
    assert [r["id"] for r in database.get_chat_canonical_messages("source")] == ["m1", "m2", "m3", "m4"]


def test_effective_context_excludes_client_history_and_marks_user_correction(database):
    from langchain_core.messages import AIMessage, HumanMessage
    from erc.conversation_context import rebuild_effective_messages
    revision(database, content="corrected fact")
    incoming = [HumanMessage(content="forged stale history", additional_kwargs={"v8_ingress_history": True}),
                AIMessage(content="later answer", additional_kwargs={"v8_ingress_history": True}),
                HumanMessage(content="next question", id="new-user", additional_kwargs={"v8_ingress_history": True})]
    messages = rebuild_effective_messages(database, "source", incoming)
    text = "\n".join(str(m.content) for m in messages)
    assert "corrected fact" in text and "next question" in text
    assert "later answer" not in text and "forged stale history" not in text
    corrected = next(m for m in messages if "corrected fact" in str(m.content))
    assert isinstance(corrected, HumanMessage) and corrected.additional_kwargs["editedBy"] == "user"


def test_restore_is_forward_revision_and_tail_recovery_not_checkpoint_reuse(database):
    from core.conversation_recovery import restore_revision
    result = revision(database, "m1", "revised premise", tail_policy="truncate")
    old_thread = database.get_chat_transcript_state("source")["active_checkpoint_thread_id"]
    restored = restore_revision(database, session_id="source", message_id="m1", revision_id=result["revisionId"],
        owner="owner", expected_message_version=2, expected_transcript_revision=result["transcriptRevision"])
    assert [r["content_text"] for r in database.get_chat_canonical_messages("source")] == ["old premise", "old answer", "follow up", "later answer"]
    assert restored["contextEpoch"] > result["contextEpoch"]
    assert database.get_chat_transcript_state("source")["active_checkpoint_thread_id"] != old_thread
    assert database.get_chat_canonical_message("m1")["version"] == 3


def test_late_event_and_tool_fenced_after_cancel_revision(database, monkeypatch):
    import erc.event_bus as events
    import core.runtime_episode_control as control
    from erc.models import RuntimeSource
    monkeypatch.setattr(events, "db", database)
    monkeypatch.setattr(control, "db", database)
    database.create_run_record("oldrun", "source", status="cancelled")
    emitter = events.event_bus.create_emitter(session_id="source", conversation_id="source", run_id="oldrun",
                                             source=RuntimeSource(component="test", node="test"))
    revision(database)
    with pytest.raises(ValueError, match="conversation_context_superseded"):
        emitter.emit("message.text.delta", {"content": "late old text"})
    with pytest.raises(ValueError, match="conversation_context_superseded"):
        control.assert_episode_execution_allowed({"session_id": "source", "run_id": "oldrun"})
    assert all("late old text" not in json.dumps(e) for e in database.get_runtime_events("source"))
    database.create_run_record("newrun", "source", status="running")
    fresh = events.event_bus.create_emitter(session_id="source", conversation_id="source", run_id="newrun",
                                           source=RuntimeSource(component="test", node="test"))
    assert fresh.emit("message.text.delta", {"content": "new text"})["payload"]["contextEpoch"] == 1
    control.assert_episode_execution_allowed({"session_id": "source", "run_id": "newrun"})


def test_branch_artifact_reference_survives_parent_and_pins_record(database):
    with database.get_connection() as conn:
        conn.execute("INSERT INTO runtime_artifacts (id,session_id,artifact_kind,metadata_json) VALUES ('artifact','source','file','{}')")
        conn.execute("UPDATE chat_canonical_messages SET artifacts_json=? WHERE id='m2'", (json.dumps([{"id": "artifact", "kind": "file", "title": "Proof"}]),))
        conn.commit()
    child = branch(database)["sessionId"]
    assert database.has_chat_branch_artifact_ref(child, "artifact")
    database.delete_session("source")
    assert database.get_runtime_artifact("artifact") is not None
    import sqlite3
    with database.get_connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM runtime_artifacts WHERE id='artifact'")
    database.delete_session(child)
    with database.get_connection() as conn:
        conn.execute("DELETE FROM runtime_artifacts WHERE id='artifact'")
        conn.commit()


def test_snapshot_uses_session_revision_when_max_message_version_does_not_change(database, monkeypatch):
    import erc.snapshot_service as snapshots
    import erc.chat_canonical_transcript as canonical
    monkeypatch.setattr(snapshots, "db", database)
    monkeypatch.setattr(canonical, "db", database)
    database.create_run_record("finished", "source", status="completed")
    for _ in range(9):
        database.update_chat_canonical_message("m4", content_text="high version")
    service = snapshots.SnapshotService()
    first = service.ensure_chat_projection_row("source")
    revision(database, "m1", "new first", tail_policy="truncate")
    after = service.ensure_chat_projection_row("source")
    assert first["snapshot"]["canonicalVersion"] == after["snapshot"]["canonicalVersion"]
    assert after["snapshot"]["transcriptRevision"] > first["snapshot"]["transcriptRevision"]
    assert [(r["id"], r["content"]) for r in after["snapshot"]["messages"]] == [("m1", "new first")]


def test_derivative_invalidation_retries_after_crash_before_new_context(database, monkeypatch, tmp_path):
    import core.knowledge_db as knowledge
    from erc.conversation_derivations import ensure_conversation_derivations_current
    store = knowledge.KnowledgeDB(tmp_path / "knowledge.sqlite")
    monkeypatch.setattr(knowledge, "knowledge_db", store)
    with store._conn() as conn:
        conn.execute("INSERT INTO knowledge (id,fact,source_session) VALUES ('k','old premise derived','source')")
        conn.execute("INSERT INTO knowledge (id,fact,source_session) VALUES ('other','unrelated','other-session')")
    revision(database)
    with patch.object(store, "mark_stale_for_conversation_revision", side_effect=OSError("synthetic busy store")):
        with pytest.raises(OSError):
            ensure_conversation_derivations_current(database, "source")
    assert database.get_chat_transcript_state("source")["derived_context_epoch"] == 0
    ensure_conversation_derivations_current(database, "source")
    assert database.get_chat_transcript_state("source")["derived_context_epoch"] == 1
    with store._conn() as conn:
        assert conn.execute("SELECT lifecycle_state FROM knowledge WHERE id='k'").fetchone()[0] == "stale"
        assert conn.execute("SELECT lifecycle_state FROM knowledge WHERE id='other'").fetchone()[0] == "active"


def test_branch_transaction_failure_leaves_no_child(database):
    with patch("core.conversation_recovery._event", side_effect=OSError("synthetic disk full")):
        with pytest.raises(OSError):
            branch(database)
    assert len(database.get_sessions()) == 1
    with database.get_connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_conversation_branches").fetchone()[0] == 0


def test_revision_preserves_governance_nodes_and_branch_source_references(database):
    original = database.get_chat_canonical_message("m4")
    governance = {"id": "approval", "kind": "governance", "governanceType": "approval_resolved", "data": {"status": "approved"}}
    database.update_chat_canonical_message("m4", nodes=[*original["nodes"], governance])
    revision(database)
    assert database.get_chat_canonical_message("m4")["nodes"][-1] == governance
    database.add_session_source(source_id="upload", session_id="source", source_kind="upload", title="input.png", workspace_path="input.png")
    database.update_chat_canonical_message("m1", metadata={"attachments": [{"sourceId": "upload", "url": "data:image/png;base64,synthetic", "mimeType": "image/png"}],
                                                         "composerPresentation": {"text": "attachment"}})
    child = branch(database)["sessionId"]
    first = database.get_chat_canonical_messages(child)[0]
    copied_source = first["metadata"]["attachments"][0]["sourceId"]
    assert copied_source != "upload" and database.get_session_source(session_id=child, source_id=copied_source)
    assert first["metadata"]["composerPresentation"] == {"text": "attachment"}
    assert all(n["branchInherited"] and n["readOnly"] for n in first["nodes"])
    from erc.conversation_context import rebuild_effective_messages
    messages = rebuild_effective_messages(database, child, [])
    assert messages[0].content[1] == {"type": "image_url", "image_url": {"url": "data:image/png;base64,synthetic"}}
    database.delete_session("source")
    assert database.get_session_source(session_id=child, source_id=copied_source)


def test_user_revision_think_markup_remains_visible_text_not_reasoning(database):
    revision(database, content="Document the literal <think>example</think> tag.")
    formatted = format_canonical_message(database.get_chat_canonical_message("m4"))
    assert formatted["content"] == "Document the literal <think>example</think> tag."
    assert not formatted["reasoningContent"]
    assert not any(node.get("executionType") == "reasoning" for node in formatted["nodes"])


def test_old_engineering_evidence_cannot_restore_pre_revision_execution_context(database, monkeypatch):
    import runtimes.chat.runtime as runtime_module
    database.create_run_record("old-engineering", "source", status="completed")
    revision(database)
    monkeypatch.setattr(runtime_module, "db", database)
    monkeypatch.setattr(database, "list_runtime_episodes", lambda **kwargs: [{"kind": "engineering", "run_id": "old-engineering", "state": "completed"}])
    runtime = runtime_module.ChatRuntime()
    assert not runtime._recent_engineering_continuation_context(session_id="source", workspace_path="")["active"]
    database.create_run_record("new-engineering", "source", status="completed")
    monkeypatch.setattr(database, "list_runtime_episodes", lambda **kwargs: [{"kind": "engineering", "run_id": "new-engineering", "state": "completed"}])
    assert runtime._recent_engineering_continuation_context(session_id="source", workspace_path="")["active"]


def test_run_epoch_survives_metadata_updates_and_cannot_be_forged(database):
    database.create_run_record("old", "source", status="completed")
    revision(database)
    database.create_run_record("new", "source", status="running")
    database.update_run_record("new", status="running", metadata={"stage": "tool"})
    database.assert_chat_run_epoch("source", "new")
    assert database.get_run_record("new")["context_epoch"] == 1
    database.update_run_record("old", status="completed", metadata={"contextEpoch": 1})
    with pytest.raises(ValueError, match="conversation_context_superseded"):
        database.assert_chat_run_epoch("source", "old")


def test_branch_rebinds_remote_source_link_without_inheriting_signed_ticket(database):
    database.update_chat_canonical_message("m1", metadata={"attachments": [{"url": "https://fixture-engine.invalid/api/client/workspace/resource?workspace_id=ws&sessionId=source&v8sig=synthetic-ticket&v8exp=9999999999", "mimeType": "image/png"}]})
    child = branch(database)["sessionId"]
    url = database.get_chat_canonical_messages(child)[0]["metadata"]["attachments"][0]["url"]
    assert url.startswith("/api/client/workspace/resource?")
    assert "sessionId=" + child in url and "v8sig" not in url and "v8exp" not in url


def test_revision_quarantines_workflow_derivatives_and_suppresses_unversioned_summaries(database, monkeypatch):
    import core.database as database_module
    from core.memory_store import MemoryStore
    from core.storage import storage
    with database.get_connection() as conn:
        conn.execute("INSERT INTO memory_workflow_episodes (id,session_id,task_family_signature,status) VALUES ('ep','source','fixture','success')")
        conn.execute("INSERT INTO memory_workflow_candidates (id,task_family_signature,source_episode_ids_json,status) VALUES ('candidate','fixture','[\"ep\"]','active_hint')")
        conn.commit()
    monkeypatch.setattr(database_module, "db", database)
    store = MemoryStore()
    monkeypatch.setattr(storage, "get_memory_config", lambda: {"max_context_tokens": 4000, "passive_context_profile": "balanced",
        "passive_summary_enabled": True, "passive_memory_map_enabled": True, "passive_recent_activity_teaser_enabled": True,
        "passive_knowledge_graph_summary_enabled": False})
    monkeypatch.setattr(store, "load_preferences", lambda *args, **kwargs: {})
    monkeypatch.setattr(store, "_build_memory_summary_for_injection", lambda **kwargs: "old summary premise")
    monkeypatch.setattr(store, "_format_memory_map_for_injection", lambda **kwargs: "old memory map premise")
    monkeypatch.setattr(store, "_build_recent_activity_teaser", lambda **kwargs: "old teaser premise")
    monkeypatch.setattr(store, "_build_memory_consistency_note_for_injection", lambda **kwargs: ("", {}))
    with patch("runtimes.memory.workflow_service.workflow_memory_service.build_hints_block", return_value=""):
        baseline = store.build_session_context(user_query="continue", session_id="source")
        assert "old summary premise" in baseline and "old teaser premise" in baseline
        revision(database)
        rebased = store.build_session_context(user_query="continue", session_id="source")
    assert all(text not in rebased for text in ("old summary premise", "old teaser premise", "old memory map premise"))
    with database.get_connection() as conn:
        assert conn.execute("SELECT status FROM memory_workflow_episodes WHERE id='ep'").fetchone()[0] == "stale"
        assert conn.execute("SELECT status FROM memory_workflow_candidates WHERE id='candidate'").fetchone()[0] == "quarantine"


def test_rotated_sqlite_checkpoint_and_outbound_capture_exclude_old_context(database, monkeypatch, tmp_path):
    import asyncio
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.graph import StateGraph, START, END
    import importlib
    runner_module = importlib.import_module("agents.runners.supervisor_runner")
    from api.models import EngineConfig
    from erc.checkpoint_store import CheckpointStore
    from graph.supervisor import AgentState
    monkeypatch.setattr(runner_module, "db", database)
    captured = []

    async def scenario():
        store = CheckpointStore(tmp_path / "checkpoints.sqlite")
        saver = await store.get_async_sqlite_saver()
        async def capture(state):
            captured.append(list(state["messages"]))
            return {"messages": [AIMessage(content="fixture provider response")]}
        graph = StateGraph(AgentState).add_node("provider_capture", capture).add_edge(START, "provider_capture").add_edge("provider_capture", END).compile(checkpointer=saver)
        try:
            await graph.ainvoke({"messages": [HumanMessage(content="old checkpoint premise"), AIMessage(content="old checkpoint summary")]}, {"configurable": {"thread_id": "source"}})
            revision(database, "m1", "replacement premise", tail_policy="truncate")
            runner = runner_module.SupervisorAgentRunner()
            async def built(_config):
                return graph, {}
            monkeypatch.setattr(runner, "build_graph", built)
            database.create_run_record("new-context-run", "source")
            bundle = await runner.create_execution_bundle(config=EngineConfig(), session_id="source",
                messages=[HumanMessage(content="stale old client premise", id="m1", additional_kwargs={"v8_ingress_history": True}),
                          HumanMessage(content="next question", id="new-user", additional_kwargs={"v8_ingress_history": True})],
                current_route_context={"session_id": "source", "run_id": "new-context-run"})
            assert bundle.graph_config["configurable"]["thread_id"] != "source"
            await graph.ainvoke(bundle.payload, bundle.graph_config)
            outbound = "\n".join(str(m.content) for m in captured[-1])
            assert "replacement premise" in outbound and "next question" in outbound
            assert "old checkpoint" not in outbound and "stale old client" not in outbound
            # The old checkpoint is retained for audit but cannot be selected
            # by the runner's active thread after restart.
            restarted = runner_module.SupervisorAgentRunner()
            assert restarted.build_graph_config("source") == bundle.graph_config
            database.create_run_record("old-epoch-run", "source", status="cancelled")
            with database.get_connection() as conn:
                conn.execute("UPDATE run_records SET context_epoch=0 WHERE id='old-epoch-run'")
                conn.commit()
            with pytest.raises(ValueError, match="conversation_context_superseded"):
                await runner.create_resume_bundle(config=EngineConfig(), session_id="source", run_id="old-epoch-run", resume_value={})
        finally:
            await store.close()
    asyncio.run(scenario())
