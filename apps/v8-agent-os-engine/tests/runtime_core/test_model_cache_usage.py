import uuid
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, SystemMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from core import model_telemetry as telemetry
from core.database import DatabaseManager
from core.model_usage import cache_token_counts
from core.observability_db import ObservabilityDatabaseManager


@pytest.mark.parametrize("message", [
    AIMessage(content="ok", usage_metadata={"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010,
              "input_token_details": {"cache_read": 800, "cache_creation": 100}}),
    AIMessage(content="ok", response_metadata={"token_usage": {"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1010,
              "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}}}),
    AIMessage(content="ok", response_metadata={"usage": {"input_tokens": 100, "output_tokens": 10,
              "cache_read_input_tokens": 800, "cache_creation_input_tokens": 100}}),
])
def test_provider_and_sdk_input_totals_include_cache_once(message):
    usage, _, reported = telemetry.extract_token_usage_details(message)
    assert reported
    assert usage == {"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010}
    cached = telemetry.extract_cache_usage(message)
    assert cached["readTokens"] == 800 and cached["writeTokens"] == 100
    assert cached["readReported"] and cached["writeReported"]


@pytest.mark.parametrize("value,expected", [
    ({}, {"readTokens": None, "writeTokens": None}),
    ({"input_token_details": {"cache_read": 0, "cache_creation": 0}}, {"readTokens": 0, "writeTokens": 0}),
    ({"input_token_details": {"priority_cache_read": 800}}, {"readTokens": 800, "writeTokens": None}),
    ({"usage": {"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 100}}, {"readTokens": 900, "writeTokens": None}),
    ({"cachedContentTokenCount": 64}, {"readTokens": 64, "writeTokens": None}),
    ({"cached_tokens": None, "cache_creation_input_tokens": -1}, {"readTokens": None, "writeTokens": None}),
    ({"cached_tokens": "unknown", "cache_creation": True}, {"readTokens": None, "writeTokens": None}),
    ({"cached_tokens": float("nan"), "cache_creation": 1.2}, {"readTokens": None, "writeTokens": None}),
])
def test_cache_usage_unknown_zero_and_supported_aliases(value, expected):
    assert cache_token_counts(value) == expected


def test_sdk_read_does_not_hide_provider_write_detail_or_add_duplicate_counts():
    message = AIMessage(content="ok", usage_metadata={"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010,
        "input_token_details": {"cache_read": 0}}, response_metadata={"token_usage": {
            "prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1010,
            "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 100}}})
    assert telemetry.extract_cache_usage(message)["readTokens"] == 0
    assert telemetry.extract_cache_usage(message)["writeTokens"] == 100


def test_terminal_stream_usage_is_persisted_without_summing_metadata_copies(monkeypatch):
    rows = []
    class Db:
        def add_model_invocation_log(self, row): rows.append(row)
        def upsert_usage_ledger(self, row): pass
        def add_provider_health_log(self, row): pass

    monkeypatch.setattr(telemetry, "db", Db())
    callback = telemetry.ModelTelemetryCallback(model_id="fixture", provider_id="compatible", provider_name="Fixture", is_streaming=True)
    run_id = uuid.uuid4()
    callback.on_chat_model_start({}, [[SystemMessage(content="fixture")]], run_id=run_id)
    callback.on_llm_new_token("o", run_id=run_id, chunk=AIMessageChunk(content="o", usage_metadata={
        "input_tokens": 100, "output_tokens": 0, "total_tokens": 100, "input_token_details": {"cache_read": 0}}))
    raw = {"prompt_tokens": 1000, "completion_tokens": 10, "total_tokens": 1010,
           "prompt_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 100}}
    message = AIMessage(content="ok", usage_metadata={"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010,
        "input_token_details": {"cache_read": 800, "cache_creation": 100}}, response_metadata={"token_usage": raw})
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]], llm_output={"token_usage": raw}), run_id=run_id)
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 1000
    cached = rows[0]["metadata"]["cacheUsage"]
    assert cached["readTokens"] == 800 and cached["writeTokens"] == 100
    public = telemetry._public_invocation_record(rows[0], prefix_use_counts={})
    assert public["cacheUsage"]["readRate"] == 0.8
    assert public["costEstimate"]["isProviderBill"] is False
    assert public["costEstimate"]["cacheAdjustmentApplied"] is False


@pytest.mark.parametrize("local_hit", [False, True])
def test_callback_does_not_bill_absent_usage_or_replayed_provider_usage(monkeypatch, local_hit):
    rows = []
    class Db:
        def add_model_invocation_log(self, row): rows.append(row)
        def upsert_usage_ledger(self, row): pass
        def add_provider_health_log(self, row): pass

    monkeypatch.setattr(telemetry, "db", Db())
    callback = telemetry.ModelTelemetryCallback(model_id="fixture", provider_id="compatible", provider_name="Fixture",
                                                cost_per_input=10, cost_per_output=20)
    run_id = uuid.uuid4()
    callback.on_chat_model_start({}, [[SystemMessage(content="fixture")]], run_id=run_id)
    metadata = {"token_usage": {"prompt_tokens": 1000, "completion_tokens": 10,
        "prompt_tokens_details": {"cached_tokens": 800}},
        "v8_prompt_cache": {"responseCacheDecision": "hit"}} if local_hit else {}
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok", response_metadata=metadata))]]), run_id=run_id)
    assert rows[0]["input_tokens"] == 0 and rows[0]["total_tokens"] == 0
    assert rows[0]["cost_total"] == 0
    assert rows[0]["metadata"]["usageReported"] is False
    assert rows[0]["metadata"]["cacheUsage"]["readTokens"] is None
    assert rows[0]["metadata"]["cacheUsage"]["writeTokens"] is None


def test_no_usage_is_unknown_even_with_reused_prefix_and_local_cache_hit():
    cached = telemetry.extract_cache_usage(AIMessage(content="ok"))
    assert cached["readTokens"] is None and not cached["readReported"]
    public = telemetry._public_invocation_record({"input_tokens": 1000, "metadata": {
        "promptCache": {"staticPrefixKey": "same", "responseCacheDecision": "hit"}}}, prefix_use_counts={"same": 20})
    assert public["promptCache"]["staticPrefixReused"] is True
    assert public["cacheUsage"]["readTokens"] is None
    assert public["cacheUsage"]["readRate"] is None


def _invocation(manager, identifier, input_tokens, metadata, days_ago=0):
    started = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    manager.add_model_invocation_log({"id": identifier, "provider_id": "fixture", "model_id": "fixture",
        "status": "completed", "input_tokens": input_tokens, "output_tokens": 10, "total_tokens": input_tokens + 10,
        "metadata": metadata, "started_at": started, "finished_at": started})


def test_cache_window_aggregates_known_legacy_zero_and_unknown_without_claiming_full_coverage(tmp_path):
    manager = ObservabilityDatabaseManager(tmp_path / "observability.db")
    _invocation(manager, "new", 1000, {"cacheUsage": {"readTokens": 800, "writeTokens": 100}})
    _invocation(manager, "zero", 200, {"cacheUsage": {"readTokens": 0, "writeTokens": 0}})
    _invocation(manager, "legacy-unknown", 100, {})
    _invocation(manager, "legacy-known", 500, {"usage": {"prompt_tokens_details": {"cached_tokens": 400}}})
    _invocation(manager, "outside-window", 9000, {"cacheUsage": {"readTokens": 9000}}, days_ago=10)
    summary = manager.get_prompt_cache_stats(days=7)["providerUsage"]
    assert summary["invocations"] == 4
    assert summary["cachedInputTokens"] == 1200
    assert summary["cacheWriteInputTokens"] == 100
    assert summary["inputTokensWithCacheReadReport"] == 1700
    assert summary["cachedInputTokenRate"] == 0.7059
    assert summary["cacheReadReportedInvocations"] == 3
    assert summary["cacheReadUnknownInvocations"] == 1
    assert summary["cacheWriteUnknownInvocations"] == 2
    assert summary["rateBasis"] == "reported_invocations_only"


def test_all_unknown_history_does_not_turn_into_zero_cache_hits(tmp_path):
    manager = ObservabilityDatabaseManager(tmp_path / "observability.db")
    _invocation(manager, "old", 1000, {})
    summary = manager.get_prompt_cache_stats()["providerUsage"]
    assert summary["cachedInputTokens"] is None
    assert summary["cacheWriteInputTokens"] is None
    assert summary["cachedInputTokenRate"] is None
    assert summary["cacheReadUnknownInvocations"] == 1


def test_historical_nonobject_usage_cannot_break_cache_aggregation(tmp_path):
    manager = ObservabilityDatabaseManager(tmp_path / "observability.db")
    _invocation(manager, "malformed-usage", 1000, {"cacheUsage": True, "usage": "unknown",
        "usage_metadata": 42, "token_usage": [], "response_metadata": None})
    summary = manager.get_prompt_cache_stats()["providerUsage"]
    assert summary["invocations"] == 1
    assert summary["cachedInputTokens"] is None
    assert summary["cacheReadUnknownInvocations"] == 1


def test_legacy_input_underreporting_does_not_produce_impossible_cache_rate(tmp_path):
    manager = ObservabilityDatabaseManager(tmp_path / "observability.db")
    _invocation(manager, "legacy-raw-anthropic", 100, {"usage": {"cache_read_input_tokens": 800}})
    summary = manager.get_prompt_cache_stats()["providerUsage"]
    assert summary["cachedInputTokens"] == 800
    assert summary["cachedInputTokenRate"] is None


def test_observe_only_event_is_not_a_provider_request_patch(tmp_path):
    manager = ObservabilityDatabaseManager(tmp_path / "observability.db")
    for index, patch in enumerate((
        {"requestStyle": "observe_only", "observeOnly": True},
        {"requestStyle": "implicit_observe_only", "observeOnly": True},
        {"prompt_cache_key": "prefix"},
        {"requestStyle": "anthropic_content_blocks", "cache_control": {"breakpoints": 1}},
    )):
        manager.add_prompt_cache_event({"id": str(index), "provider_id": "fixture", "model_id": "fixture",
            "decision": "skipped", "provider_patch": patch})
    summary = manager.get_prompt_cache_stats()
    assert summary["totals"]["providerPatchEvents"] == 2
    assert summary["rates"]["providerPatchRate"] == 0.5


def test_legacy_empty_provider_patch_remains_readable(tmp_path):
    manager = ObservabilityDatabaseManager(tmp_path / "observability.db")
    manager.add_prompt_cache_event({"id": "empty-patch", "decision": "skipped"})
    with manager.get_connection() as conn:
        conn.execute("UPDATE prompt_cache_events SET provider_patch_json = '' WHERE id = 'empty-patch'")
        conn.commit()
    summary = manager.get_prompt_cache_stats()
    assert summary["totals"]["providerPatchEvents"] == 0
    assert summary["rates"]["providerPatchRate"] == 0


def test_callback_to_public_dashboard_preserves_cache_and_marks_pruned_history(monkeypatch, tmp_path):
    manager = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(telemetry, "db", manager)
    callback = telemetry.ModelTelemetryCallback(model_id="fixture", provider_id="compatible", provider_name="Fixture",
                                                cost_per_input=1, cost_per_output=2)
    run_id = uuid.uuid4()
    callback.on_chat_model_start({}, [[SystemMessage(content="fixture")]], run_id=run_id)
    message = AIMessage(content="ok", response_metadata={"usage": {
        "input_tokens": 100, "output_tokens": 10, "cache_read_input_tokens": 800, "cache_creation_input_tokens": 100}})
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id)
    overview = telemetry.model_telemetry_service.build_dashboard_overview(days=1)
    row = overview["recentInvocations"][0]
    assert row["input_tokens"] == 1000
    assert row["total_tokens"] == 1010
    assert row["cacheUsage"]["readTokens"] == 800
    assert row["cacheUsage"]["writeTokens"] == 100
    assert row["cacheUsage"]["readRate"] == 0.8
    assert overview["promptCache"]["providerUsage"]["cachedInputTokens"] == 800
    assert overview["stats"]["recentWindowInvocations"] == 1
    assert row["estimatedCost"] > 0 and row["costEstimate"]["isProviderBill"] is False

    # Log retention cannot reconstruct cache counts from a total-token ledger.
    with manager.observability_db.get_connection() as conn:
        conn.execute("DELETE FROM model_invocation_logs")
        conn.commit()
    pruned = telemetry.model_telemetry_service.build_dashboard_overview(days=1)
    assert pruned["stats"]["recentWindowInvocations"] == 1
    assert pruned["stats"]["recentWindowTokens"] == 1010
    assert pruned["promptCache"]["providerUsage"]["recordBasis"] == "retained_invocation_logs"
    assert pruned["promptCache"]["providerUsage"]["invocations"] == 0
    assert pruned["promptCache"]["providerUsage"]["cachedInputTokens"] is None
    assert pruned["promptCache"]["providerUsage"]["cachedInputTokenRate"] is None
