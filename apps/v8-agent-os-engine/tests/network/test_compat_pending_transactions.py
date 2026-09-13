"""Adversarial persistence tests using the real Network state file and SQLite."""
from copy import deepcopy
import asyncio
import hashlib
import json
import multiprocessing
import os
import sqlite3
import time

import pytest


@pytest.fixture
def service(tmp_path, monkeypatch):
    from core.database import DatabaseManager
    from runtimes.network_supervisor import service as module
    database = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(module, "db", database)
    monkeypatch.setattr(module, "V8_AGENT_OS_HOME", tmp_path)
    monkeypatch.setattr(module, "NETWORK_SUPERVISOR_STATE_PATH", tmp_path / "network.json")
    monkeypatch.setattr(module, "NETWORK_SUPERVISOR_SECRETS_PATH", tmp_path / "secrets.json")
    monkeypatch.setattr(module.run_ledger_service, "record_event", lambda **_: None)
    return module.NetworkSupervisorService()


def _register(service):
    service.record_pending_external_tool(protocol="openai", run_id="r", compat_session_id="s",
        wire_tool_call_id="wire", internal_alias_name="network_read", external_wire_name="Read",
        origin_token_hash="owner", checkpoint_resume_supported=True)


def _claim(service):
    return service.claim_external_tool_results(protocol="openai", wire_tool_call_ids=["wire"],
        origin_token_hash="owner", tool_results=[{"wireToolCallId": "wire", "content": "fixture"}])


def test_other_network_domain_stale_write_cannot_erase_pending(service):
    stale_discovery = deepcopy(service.read_state())
    _register(service)
    stale_discovery["discoveredPeers"] = {"peer_other": {"online": True}}
    service.write_state(stale_discovery)
    assert len(_claim(service)["matched"]) == 1
    assert service.read_state()["discoveredPeers"]["peer_other"]["online"]


def test_old_network_snapshot_cannot_resurrect_consumed_result(service):
    _register(service)
    stale_delegations = deepcopy(service.read_state())
    assert len(_claim(service)["matched"]) == 1
    service.write_state(stale_delegations)
    assert _claim(service)["matched"] == []


@pytest.mark.parametrize("same_run,checkpoint", [(False, True), (True, True), (False, False)])
def test_batch_resumes_only_one_checkpoint_without_consuming_conflicting_runs(service, same_run, checkpoint):
    for index in (1, 2):
        service.record_pending_external_tool(protocol="openai", run_id="run-one" if same_run or index == 1 else "run-two",
            compat_session_id="session-one" if same_run or index == 1 else "session-two", wire_tool_call_id=f"wire-{index}",
            internal_alias_name=f"network_read_{index}", external_wire_name=f"Read{index}",
            external_thread_id="thread", origin_token_hash="owner", checkpoint_resume_supported=checkpoint)
    kwargs = {"protocol": "openai", "origin_token_hash": "owner", "external_thread_id": "thread"}
    results = [{"wireToolCallId": f"wire-{index}", "content": f"result-{index}"} for index in (1, 2)]
    batch = service.claim_external_tool_results(**kwargs, wire_tool_call_ids=["wire-1", "wire-2"], tool_results=results)
    if checkpoint and not same_run:
        assert batch["matched"] == []
        assert batch["pendingMissReason"] == "multiple_checkpoint_runs"
        assert all(row["status"] == "waiting_external_tool" for row in service.pending_external_tools_snapshot().values())
        for index in (1, 2):
            separate = service.claim_external_tool_results(**kwargs, wire_tool_call_ids=[f"wire-{index}"], tool_results=[results[index-1]])
            assert separate["resumeRunId"] == ("run-one" if index == 1 else "run-two")
            assert separate["resumeValue"]["toolResults"][0]["content"] == f"result-{index}"
    else:
        assert len(batch["matched"]) == 2
        assert batch["resumeRunId"] == ("run-one" if checkpoint else None)
        if checkpoint:
            assert [row["content"] for row in batch["resumeValue"]["toolResults"]] == ["result-1", "result-2"]


def _process_claim(root, barrier, ready, queue):
    # A dedicated test state root; no credentials or real state are inherited.
    os.environ["V8_AGENT_OS_HOME"] = root
    from runtimes.network_supervisor import service as module
    module.run_ledger_service.record_event = lambda **_: None
    worker = module.NetworkSupervisorService()
    ready.put(True)
    barrier.wait(timeout=30)
    queue.put(len(_claim(worker)["matched"]))


def test_two_processes_claim_once_and_a_fresh_instance_keeps_tombstone(service, tmp_path):
    _register(service)
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    ready = context.Queue()
    queue = context.Queue()
    workers = [context.Process(target=_process_claim, args=(str(tmp_path), barrier, ready, queue)) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
            # Initialize unrelated Memory schema serially; the claim itself is
            # released simultaneously by the barrier in separate processes.
            assert ready.get(timeout=30)
        counts = [queue.get(timeout=45) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
        assert sorted(counts) == [0, 1]
        assert _claim(type(service)())["matched"] == []
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=5)


def test_legacy_waits_import_once_and_stale_json_never_reimports(service, tmp_path):
    legacy = tmp_path / "network.json"
    row = {"protocol": "openai", "runId": "r", "compatSessionId": "s", "wireToolCallId": "wire",
        "originTokenHash": "owner", "internalAliasName": "network_read", "externalWireName": "Read",
        "status": "waiting_external_tool", "expiresAtTs": 9999999999}
    payload = {"pendingExternalTools": {"openai:s:wire": row}, "discoveredPeers": {"peer_other": {}}}
    legacy.write_text(json.dumps(payload), encoding="utf-8")
    assert len(_claim(service)["matched"]) == 1
    # Simulate an old process overwriting the file after the database commit.
    legacy.write_text(json.dumps(payload), encoding="utf-8")
    assert _claim(type(service)())["matched"] == []
    service.write_state(service.read_state())
    assert "pendingExternalTools" not in json.loads(legacy.read_text(encoding="utf-8"))
    assert service.read_state()["discoveredPeers"] == {"peer_other": {}}


def test_failed_migration_and_failed_batch_leave_no_partial_commit(service, tmp_path):
    legacy = tmp_path / "network.json"
    legacy.write_text('{"pendingExternalTools":', encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        service.read_state()
    with sqlite3.connect(tmp_path / "state.db") as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='network_compat_pending_tools'").fetchone()
    legacy.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="injected"):
        with service._pending_store().transaction() as pending:
            pending["uncommitted"] = {"status": "waiting_external_tool"}
            raise RuntimeError("injected failure before commit")
    with sqlite3.connect(tmp_path / "state.db") as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='network_compat_pending_tools'").fetchone()
    assert service.pending_external_tools_snapshot() == {}
    _register(service)
    rejected = service.claim_external_tool_results(protocol="openai", origin_token_hash="owner", wire_tool_call_ids=["wire", "unknown"])
    assert rejected["matched"] == []
    assert len(_claim(type(service)())["matched"]) == 1


def test_status_read_does_not_compete_with_writer_lock(service, tmp_path):
    _register(service)
    with sqlite3.connect(tmp_path / "state.db") as writer:
        writer.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        assert service.pending_external_tools_snapshot()["openai:s:wire"]["status"] == "waiting_external_tool"
        assert time.monotonic() - started < 1
        writer.rollback()


def test_status_and_claim_transactions_do_not_decode_historical_full_receipts(service, monkeypatch):
    _register(service)
    full = "fixture-body-" * 40000 + "FULL-RECEIPT-END-CANARY"
    service.claim_external_tool_results(protocol="openai", wire_tool_call_ids=["wire"], origin_token_hash="owner",
        tool_results=[{"wireToolCallId": "wire", "content": full}])
    import core.network_compat_pending as store_module
    original = store_module.json.loads
    decoded = []
    def observe(value, *args, **kwargs):
        decoded.append(value)
        return original(value, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(store_module.json, "loads", observe)
        snapshot = service.pending_external_tools_snapshot()
        summary = service.pending_external_tools_summary()
        with service._pending_store().transaction() as pending:
            assert pending["openai:s:wire"]["resultStored"]
    assert all("FULL-RECEIPT-END-CANARY" not in value for value in decoded)
    assert "toolResultReceipt" not in snapshot["openai:s:wire"]
    assert "FULL-RECEIPT-END-CANARY" not in json.dumps(summary)
    assert service._pending_store().receipt("openai:s:wire")["content"] == full


def test_inline_receipt_schema_is_split_without_losing_result(service, tmp_path):
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute("CREATE TABLE network_compat_pending_tools(id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)")
        conn.execute("INSERT INTO network_compat_pending_tools VALUES (?,?)", ("receipt", json.dumps({
            "status": "external_tool_result_received", "receiptId": "receipt", "toolResultReceipt": {"content": "preserved"}})))
        conn.commit()
    snapshot = service.pending_external_tools_snapshot()
    assert snapshot["receipt"]["resultStored"] and "toolResultReceipt" not in snapshot["receipt"]
    assert service._pending_store().receipt("receipt")["content"] == "preserved"


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("rejection", ["unknown_alias", "bad_schema", "rate_limit"])
def test_invalid_request_cannot_consume_pending_before_corrected_retry(service, monkeypatch, protocol, rejection):
    from api import network_supervisor_routes as routes
    from runtimes.network_supervisor.models import NetworkSupervisorRuntimeConfig
    from runtimes.network_supervisor.compat_model_budget import model_control_plane
    from fastapi import HTTPException
    token = "fixture-token"
    owner = hashlib.sha256(token.encode()).hexdigest()
    service.record_pending_external_tool(protocol=protocol, run_id="source-run", compat_session_id="s",
        wire_tool_call_id="wire", internal_alias_name="network_read", external_wire_name="Read", origin_token_hash=owner)
    config = NetworkSupervisorRuntimeConfig(enabled=True, openaiCompat={"enabled": True, "modelAliases": ["v8os"]})
    monkeypatch.setattr(routes, "network_supervisor_service", service)
    monkeypatch.setattr(routes, "get_internal_secret", lambda: "fixture-relay")
    monkeypatch.setattr(service, "get_config_model", lambda: config)
    monkeypatch.setattr(service, "verify_openai_compat_token", lambda _: {})
    monkeypatch.setattr(model_control_plane, "resolve_model_for_role", lambda _: {"resolvedModelRef": "fixture", "resolvedModelId": "fixture", "resolvedModel": {"contextWindow": 128000}})
    def admission(_):
        if rejection == "rate_limit":
            raise HTTPException(429, "fixture capacity")
        return {}
    monkeypatch.setattr(service, "begin_openai_compat_request", admission)
    monkeypatch.setattr(service, "finish_openai_compat_request", lambda _: None)
    async def events(*_, **__):
        yield {"type": "text-delta", "delta": "fixture delivered"}
        yield {"type": "done", "status": "completed"}
    monkeypatch.setattr(routes, "_iterate_chat_events_with_timeout", events)
    monkeypatch.setattr(routes.network_supervisor_memory_adapter, "record_openai_compat_delta", lambda **_: {})
    monkeypatch.setattr(routes, "_record_openai_memory_adapter_status", lambda _: None)
    full = "fixture;" * 1100 + "IMPORTANT-RECEIPT-TAIL"
    schema = {"type": "object", "properties": {}}
    if protocol == "openai":
        body = {"model": "v8os", "tools": [{"type": "function", "function": {"name": "Read", "parameters": schema}}],
            "messages": [{"role": "user", "content": "Use result"}, {"role": "tool", "tool_call_id": "wire", "name": "Read", "content": full}]}
    else:
        body = {"model": "v8os", "tools": [{"name": "Read", "input_schema": schema}],
            "messages": [{"role": "user", "content": "Use result"}, {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "wire", "content": full}]}]}
    invalid = deepcopy(body)
    if rejection == "unknown_alias":
        invalid["model"] = "unknown"
    elif rejection == "bad_schema":
        target = invalid["tools"][0]["function"]["parameters"] if protocol == "openai" else invalid["tools"][0]["input_schema"]
        target["properties"] = []
    class Request:
        headers = {}
        def __init__(self, payload):
            self.payload = payload
        async def json(self):
            return self.payload
    route = routes.post_network_supervisor_openai_chat_completions if protocol == "openai" else routes.post_network_supervisor_anthropic_messages
    kwargs = {"authorization": "Bearer " + token, "x_v8_agent_os_secret": "fixture-relay"}
    kwargs.update({"x_v8_compat_memory": None} if protocol == "openai" else {"x_api_key": None})
    with pytest.raises(HTTPException) as rejected:
        asyncio.run(route(Request(invalid), **kwargs))
    assert rejected.value.status_code == {"unknown_alias": 404, "bad_schema": 400, "rate_limit": 429}[rejection]
    assert service.pending_external_tools_snapshot()[f"{protocol}:s:wire"]["status"] == "waiting_external_tool"
    monkeypatch.setattr(service, "begin_openai_compat_request", lambda _: {})
    assert asyncio.run(route(Request(body), **kwargs)).status_code == 200
    stored = type(service)().pending_external_tools_snapshot()[f"{protocol}:s:wire"]
    assert "toolResultReceipt" not in stored
    assert service._pending_store().receipt(stored["receiptId"])["content"] == full
    assert stored["deliveryRunId"].startswith("run_") and stored["runId"] == "source-run"
    summary = service.pending_external_tools_summary()
    assert "toolResultReceipt" not in summary["recent"][0]
    assert "IMPORTANT-RECEIPT-TAIL" not in json.dumps(summary)
    assert summary["recent"][0]["deliveryState"] == "recovery_required"  # injected stream has no real run
    with pytest.raises(HTTPException) as duplicate:
        asyncio.run(route(Request(body), **kwargs))
    receipt_notice = duplicate.value.detail["receipts"][0]
    assert duplicate.value.status_code == 409 and receipt_notice["recoveryRequired"]
    assert receipt_notice["originalRunId"] == "source-run" and receipt_notice["resultStored"]
    assert "IMPORTANT-RECEIPT-TAIL" not in json.dumps(duplicate.value.detail)
    with pytest.raises(HTTPException) as foreign:
        asyncio.run(route(Request(body), **{**kwargs, "authorization": "Bearer different-fixture-key"}))
    assert foreign.value.status_code == 409 and foreign.value.detail["receipts"] == []
