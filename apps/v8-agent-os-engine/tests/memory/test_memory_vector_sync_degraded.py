from __future__ import annotations

import core.memory_store as memory_store_module
from core.memory_store import MemoryStore


class _BrokenVectorStore:
    def add_documents(self, _documents):  # noqa: ANN001
        raise ConnectionResetError(10054, "remote host closed the connection")


def test_vector_sync_transient_failure_is_degraded_and_rate_limited(monkeypatch, caplog) -> None:
    memory_store_module._vector_sync_warning_last_at.clear()
    monkeypatch.setattr("core.vector_store.get_vector_store", lambda: _BrokenVectorStore())
    store = MemoryStore()

    with caplog.at_level("WARNING", logger="v8_agent_os.memory"):
        store._sync_vector_store_document("fact-a", "记忆内容", {"scope": "global"}, operation="add_knowledge")
        store._sync_vector_store_document("fact-b", "记忆内容", {"scope": "global"}, operation="add_knowledge")

    status = store.get_vector_sync_status()
    assert status["state"] == "queued_retry"
    assert status["lastErrorKind"] == "transient_connection"
    assert status["pendingRetry"] is True
    assert status["nextRetryAt"]
    assert caplog.text.count("Vector Store sync degraded") == 1
