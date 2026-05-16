from __future__ import annotations


def test_tool_observation_records_list_and_reveal_are_redacted(tmp_path):
    from core.observability_db import ObservabilityDatabaseManager

    db = ObservabilityDatabaseManager(tmp_path / "observability.db")
    db.add_tool_observation_record(
        {
            "id": "toolobs_test",
            "raw_ref": "toolobs://toolobs_test",
            "tool_name": "computer_use_execute_task",
            "tool_call_id": "call_1",
            "runtime_kind": "computer_use",
            "surface": "tool_node",
            "raw_chars": 80,
            "visible_chars": 20,
            "raw_sha256": "abc",
            "raw_body": "api_key=sk-secretsecretsecret\nsafe line\n" + ("x" * 2000),
            "budget": {"wasBudgetTruncated": True},
            "metadata": {"runId": "run_1", "sessionId": "sess_1"},
        }
    )

    listed = db.list_tool_observation_records(run_id="run_1", limit=10, preview_chars=40)
    assert len(listed["items"]) == 1
    item = listed["items"][0]
    assert item["rawRef"] == "toolobs://toolobs_test"
    assert "sk-secret" not in item["preview"]
    assert "<redacted>" in item["preview"]
    assert item["omittedChars"] > 0

    revealed = db.reveal_tool_observation_record("toolobs_test", max_chars=4000)
    assert revealed
    assert "safe line" in revealed["preview"]
    assert "sk-secret" not in revealed["preview"]


def test_observability_routes_delegate_filters_and_reveal(monkeypatch):
    import api.observability_routes as routes

    calls: list[tuple[str, dict]] = []

    class FakeObservability:
        def list_tool_observation_records(self, **filters):
            calls.append(("list", filters))
            return {"items": [{"id": "toolobs_test"}], "nextCursor": None, "hasMore": False}

        def reveal_tool_observation_record(self, raw_ref_or_id, *, max_chars=12000):
            calls.append(("reveal", {"id": raw_ref_or_id, "max_chars": max_chars}))
            return {"id": raw_ref_or_id, "preview": "ok"}

        def add_audit_log(self, source_type, action, status, details):
            calls.append(("audit", {"source_type": source_type, "action": action, "status": status, "details": details}))

        def list_conversation_compaction_records(self, **filters):
            calls.append(("compactions", filters))
            return {"items": [{"id": "cmp_test"}], "nextCursor": None, "hasMore": False}

    monkeypatch.setattr(routes, "observability_db", FakeObservability())

    result = routes.list_tool_observations(
        runId="run_1",
        sessionId=None,
        toolName="grep_search",
        runtimeKind=None,
        surface=None,
        cursor=None,
        limit=5,
    )
    assert result["items"][0]["id"] == "toolobs_test"
    assert calls[0][1]["run_id"] == "run_1"
    assert calls[0][1]["tool_name"] == "grep_search"

    detail = routes.reveal_tool_observation("toolobs_test", {"maxChars": 3000})
    assert detail["preview"] == "ok"
    assert calls[-1][0] == "audit"

    compactions = routes.list_compactions(runId="run_1", sessionId=None, targetRole=None, cursor=None, limit=3)
    assert compactions["items"][0]["id"] == "cmp_test"
