from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from core.database import DatabaseManager
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from core.system_operations.service import SystemOperationError, SystemOperationService


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from core.system_operations import accounts
    monkeypatch.setattr(accounts, "validate_account", lambda action, username, domain: (username, domain))
    database = DatabaseManager(tmp_path / "state.db")
    database.create_or_update_session("session", "Controlled operation test", user_id="owner")
    database.create_run_record("run", "session", user_id="owner")
    credentials = CredentialRefStore(MemoryCredentialBackend())
    executions = []

    def executor(payload, **kwargs):
        executions.append((payload, kwargs))
        return {"ok": True, "verified": True, "status": "unlocked", "locked": False, "summary": "Done"}

    service = SystemOperationService(database, credentials, executor)
    service.configure(owner="owner", action="unlock", username="account", domain="", password='fixture-"secret')
    context = {"user_id": "owner", "session_id": "session", "run_id": "run", "agent_id": "supervisor", "runtime_kind": "chat"}
    return service, context, executions


def execute(rig, *, authorize=lambda operation: None, payload=None, tool_call_id="call", **overrides):
    service, context, _ = rig
    return service.execute(payload=payload or {"action": "unlock"}, context={**context, **overrides}, tool_call_id=tool_call_id, authorize=authorize)


def test_approval_resume_is_single_use_and_never_persists_password(rig):
    observed = []
    def approve(operation):
        observed.append(operation)
        if len(observed) == 1:
            raise RuntimeError("waiting for actual UI approval")
    with pytest.raises(RuntimeError, match="waiting"):
        execute(rig, authorize=approve)
    assert not rig[2]
    first = execute(rig, authorize=approve)
    replay = execute(rig)
    assert first == replay and first["ok"]
    assert len(rig[2]) == 1
    assert observed[0]["operationId"] == observed[1]["operationId"]
    with rig[0].database.get_connection() as conn:
        rows = conn.execute("SELECT * FROM system_operation_requests").fetchall()
        profiles = conn.execute("SELECT * FROM system_operation_credentials").fetchall()
    persisted = json.dumps([dict(row) for row in [*rows, *profiles]])
    assert "fixture-" not in persisted
    assert rig[2][0][1]["password"] == 'fixture-"secret'


@pytest.mark.parametrize("overrides", [{"user_id": "other"}, {"session_id": "other"}, {"run_id": "other"}, {"agent_id": "worker", "actor_role": "direct_subagent"}, {"actor_role": "grandchild"}])
def test_wrong_owner_or_actor_cannot_use_configured_credentials(rig, overrides):
    with pytest.raises(SystemOperationError):
        execute(rig, **overrides)
    assert not rig[2]


def test_credential_change_during_approval_does_not_execute_new_account(rig):
    def change_account(operation):
        rig[0].configure(owner="owner", action="unlock", username="new-account", domain="", password="replacement")
    with pytest.raises(SystemOperationError, match="账户配置已变化"):
        execute(rig, authorize=change_account)
    assert not rig[2]


def test_expired_request_cannot_reuse_approval(rig):
    def pending(operation):
        raise RuntimeError("pending")
    with pytest.raises(RuntimeError):
        execute(rig, authorize=pending)
    with rig[0].database.get_connection() as conn:
        conn.execute("UPDATE system_operation_requests SET expires_at=0")
        conn.commit()
    with pytest.raises(SystemOperationError, match="过期"):
        execute(rig)
    assert not rig[2]


def test_concurrent_approval_callbacks_cannot_double_execute(rig):
    barrier = Barrier(2)
    def submit():
        try:
            return execute(rig, authorize=lambda _: barrier.wait(timeout=5))
        except SystemOperationError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert len(rig[2]) == 1
    assert "system_operation_already_claimed" in results


def test_crash_after_claim_is_not_replayed(rig):
    def pending(operation):
        raise RuntimeError("pending")
    with pytest.raises(RuntimeError):
        execute(rig, authorize=pending)
    with rig[0].database.get_connection() as conn:
        conn.execute("UPDATE system_operation_requests SET state='running'")
        conn.commit()
    restarted = SystemOperationService(rig[0].database, rig[0].credentials, rig[0].executor)
    with pytest.raises(SystemOperationError, match="不会重复执行"):
        execute((restarted, rig[1], rig[2]))
    assert not rig[2]


def test_unverified_success_is_not_a_success(rig):
    rig[0].executor = lambda *args, **kwargs: {"ok": True, "summary": "Request sent"}
    result = execute(rig)
    assert not result["ok"] and result["code"] == "system_operation_unverified"


@pytest.mark.parametrize("result", [{"ok": True, "verified": True}, {"ok": True, "verified": True, "locked": True, "status": "unlocked"}])
def test_unlock_success_requires_observed_unlocked_state(rig, result):
    rig[0].executor = lambda *args, **kwargs: result
    assert execute(rig)["code"] == "system_operation_unverified"


def test_helper_echo_and_exception_cannot_leak_secret(rig):
    rig[0].executor = lambda *args, **kwargs: {"ok": False, "stderr": kwargs["password"]}
    assert execute(rig)["stderr"] == "[REDACTED]"
    def fail(*args, **kwargs):
        raise RuntimeError(kwargs["password"])
    rig[0].executor = fail
    assert "fixture" not in json.dumps(execute(rig, tool_call_id="second"))


def test_credential_store_corruption_preserves_previous_configuration(rig, monkeypatch):
    service = rig[0]
    previous = service.credential("owner", "unlock")
    monkeypatch.setattr(service.credentials, "resolve", lambda _: "corrupted")
    with pytest.raises(SystemOperationError, match="内容不一致"):
        service.configure(owner="owner", action="unlock", username="account", domain="", password="new-fixture")
    assert service.credential("owner", "unlock")["version"] == previous["version"]


def test_terminal_run_cannot_execute_even_with_existing_credentials(rig):
    with rig[0].database.get_connection() as conn:
        conn.execute("UPDATE run_records SET status='cancelled' WHERE id='run'")
        conn.commit()
    with pytest.raises(SystemOperationError, match="已结束"):
        execute(rig)
    assert not rig[2]


def test_tool_schema_never_exposes_account_or_secret_inputs():
    from core.tools.native.system_operations import system_operations
    from core.runtime_tool_access import SUBAGENT_ALWAYS_HIDDEN_TOOL_NAMES
    properties = system_operations.tool_call_schema.model_json_schema()["properties"]
    assert set(properties) == {"action", "command", "cwd", "shell_dialect", "timeout_seconds"}
    assert "system_operations" in SUBAGENT_ALWAYS_HIDDEN_TOOL_NAMES


def test_approval_replay_fingerprint_binds_operation_not_only_command():
    from core.tools.native.tool_governance import _safety_operation_fingerprint
    from erc.safety_guardian import SafetyDecision
    first = SafetyDecision(verdict="review", details={"command": "same command", "operationId": "first", "runtime_context": {"run_id": "run"}})
    second = SafetyDecision(verdict="review", details={**first.details, "operationId": "second"})
    for include_call in (False, True):
        assert _safety_operation_fingerprint(first, include_tool_call_id=include_call) != _safety_operation_fingerprint(second, include_tool_call_id=include_call)
