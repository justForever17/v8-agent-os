from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import ops_routes
from api import system_operation_routes
from core.tools.native import automation as native
from core.tools.native import tool_governance
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.memory_maintenance_contract import SYSTEM_MEMORY_MAINTENANCE_JOB_ID
from erc.runtime_context import bind_runtime_context

spec = importlib.util.spec_from_file_location("scope_delivery_fixture", Path(__file__).with_name("test_automation_delivery_recovery.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


@pytest.fixture
def state(tmp_path, monkeypatch):
    return base.install_state(monkeypatch, tmp_path)


def invoke(kind, item, actor, *, outcome="completed"):
    tool = native.manage_cron if kind == "cron" else native.manage_hook
    with bind_runtime_context(**actor):
        result = tool.invoke({"name": tool.name, "type": "tool_call", "id": "scope-reconcile",
            "args": {"action": "reconcile", "delivery_id": item["delivery_id"], "outcome": outcome,
                     "evidence": {"observation": "synthetic external effect observed"}}})
    return result.content


def system_delivery(state):
    definition = next(item for item in state.storage.get_cron_config()["jobs"] if item["id"] == SYSTEM_MEMORY_MAINTENANCE_JOB_ID)
    manager = base.cron_module.CronManager()
    assert manager.sync_jobs_to_scheduler()["status"] == "success"
    manager._on_submission(SimpleNamespace(job_id=definition["id"], scheduled_run_times=[datetime(2026, 9, 14, 3, tzinfo=timezone.utc)]))
    return state.db.list_automation_deliveries()[0]


def make_unknown(state, item):
    state.db.claim_automation_delivery(item["delivery_id"], owner_id="fixture")
    state.db.transition_automation_delivery(item["delivery_id"], owner_id="fixture", expected_phases=("claimed",), phase="unknown")
    return state.db.get_automation_delivery(item["delivery_id"])


@pytest.fixture
def client(state, monkeypatch):
    monkeypatch.setattr(system_operation_routes, "get_internal_secret", lambda: "synthetic-relay-secret")
    app = FastAPI()
    app.include_router(ops_routes.router, prefix="/v1")
    with TestClient(app) as value:
        yield value


ADMIN = {"x-v8-agent-os-secret": "synthetic-relay-secret", "x-v8-agent-os-user-email": "authenticated-owner", "x-v8-admin-role": "ADMIN"}


def test_system_real_creator_crash_cannot_be_reconciled_by_foreign_or_claimed_system_actor(state, monkeypatch, client):
    item = system_delivery(state)
    assert item["envelope"]["kwargs"]["user_id"] == "system"
    code = '''
import asyncio, importlib.util, os, sys
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('crash_scope_fixture',Path('tests/runtime_core/test_automation_delivery_recovery.py'))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
mp=pytest.MonkeyPatch()
state=fixture.install_state(mp,Path(sys.argv[1]))
def effect(*args,**kwargs):
    with open(state.root/'effect.txt','wb') as output:
        output.write(b'one committed synthetic effect')
        output.flush()
        os.fsync(output.fileno())
    os._exit(73)
mp.setattr(fixture.action.ActionExecutor,'_execute_python',effect)
asyncio.run(fixture.drain(state.service))
raise SystemExit(99)
'''
    result = subprocess.run([sys.executable, "-c", code, str(state.root)], capture_output=True, text=True,
        env=dict(os.environ, V8_AGENT_OS_HOME=str(state.root / "child-state")), timeout=45,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert result.returncode == 73, result.stderr[-1200:]
    row = state.db.get_automation_delivery(item["delivery_id"])
    assert state.db.get_run_record(row["execution_run_id"])["user_id"] == "system"
    # An existing bbeb row may lack all envelope owners. Canonical run/session
    # evidence must still close it instead of treating missing fields as grants.
    envelope = deepcopy(row["envelope"])
    envelope["kwargs"].update(user_id=None, project_id=None, workspace_id=None)
    with state.db.get_connection() as conn:
        conn.execute("UPDATE runtime_automation_deliveries SET envelope_json=? WHERE delivery_id=?", (json.dumps(envelope), row["delivery_id"]))
        conn.commit()
    base.expire(state, row["delivery_id"])
    state.db.reconcile_automation_deliveries()
    row = state.db.get_automation_delivery(row["delivery_id"])
    for mode in ("manual", "minimal"):
        for actor_user in ("unrelated-user", "system"):
            actor = {"runtime_kind": "chat", "agent_id": "supervisor", "session_id": "foreign-session",
                "user_id": actor_user, "project_id": "unrelated-project", "workspace_id": "unrelated-workspace",
                "role": "ADMIN", "is_admin": True, "safety_approval_mode": mode}
            reply = invoke("cron", row, actor)
            assert "automation_admin_reconciliation_required" in reply
            assert state.db.get_automation_delivery(row["delivery_id"])["phase"] == "unknown"
            assert state.db.get_side_effect_receipt(row["receipt_key"])["state"] == "indeterminate"
    response = client.post(f"/v1/automation/deliveries/{row['delivery_id']}/reconcile", headers=ADMIN,
        json={"outcome": "completed", "evidence": {"observation": "fixture target contains exactly one effect"}})
    assert response.status_code == 200, response.text
    assert response.json()["receiptState"] == "completed"
    assert state.db.get_automation_delivery(row["delivery_id"])["phase"] == "completed"
    assert (state.root / "effect.txt").read_bytes() == b"one committed synthetic effect"
    asyncio.run(base.drain(state.service))
    assert not state.effects


@pytest.mark.parametrize("kind,actions", [("cron", ["reconcile"]), ("hook", ["pause", "resume", "remove", "delete", "reconcile"])])
def test_mutation_policy_classifies_new_actions_and_preserves_modes(state, kind, actions):
    assess = native.safety_guardian.assess_cron_mutation if kind == "cron" else native.safety_guardian.assess_hook_mutation
    original = assess("add")
    for action in actions:
        decision = assess(action, target="delivery:fixture:completed")
        assert decision.is_review()
        assert decision.risk_code == original.risk_code
        assert not tool_governance.should_auto_approve_safety_review(decision, mode="manual")
        assert tool_governance.should_auto_approve_safety_review(decision, mode="minimal") == tool_governance.should_auto_approve_safety_review(original, mode="minimal")
    assert assess("list").is_allow()
    first = assess("reconcile", target="delivery:one:completed")
    second = assess("reconcile", target="delivery:two:completed")
    changed = assess("reconcile", target="delivery:one:failed")
    keys = {tool_governance._safety_operation_fingerprint(value, tool_call_id="same") for value in (first, second, changed)}
    assert len(keys) == 3


def test_owned_cron_manual_waits_and_minimal_reconciles_without_widening_scope(state):
    state.storage.save_cron_config({"jobs": [{"id": "owned", "name": "Owned", "enabled": True,
        "cron_expression": "0 9 * * *", "action_type": "command", "action_target": "fixture-effect",
        "session_id": "source-session", "user_id": "user-a"}]})
    definition = next(value for value in state.storage.get_cron_config()["jobs"] if value["id"] == "owned")
    item = make_unknown(state, base.cron_module.CronManager()._enqueue(definition, occurrence="fixture")[0])
    actor = {"user_id": "user-a", "session_id": "source-session", "runtime_kind": "chat", "safety_approval_mode": "manual"}
    with pytest.raises(ModelGovernanceInterventionRequired):
        invoke("cron", item, actor)
    assert state.db.get_automation_delivery(item["delivery_id"])["phase"] == "unknown"
    assert "reconciled as completed" in invoke("cron", item, {**actor, "safety_approval_mode": "minimal"})
    assert not state.effects


def test_persistent_source_scope_corroborates_missing_envelope_owner_and_rejects_conflicts(state):
    base.hook(state)
    item = make_unknown(state, base.emit_hook()[0])
    envelope = deepcopy(item["envelope"])
    envelope["kwargs"].update(user_id=None, project_id=None, workspace_id=None)
    with state.db.get_connection() as conn:
        conn.execute("UPDATE runtime_automation_deliveries SET envelope_json=? WHERE delivery_id=?", (json.dumps(envelope), item["delivery_id"]))
        conn.commit()
    item = state.db.get_automation_delivery(item["delivery_id"])
    owner = state.service._owner_scope(item)
    assert owner["scope"]["user_id"] == "user-a"
    assert not owner["unresolved"]
    state.db.create_or_update_session("foreign-session", "Other", user_id="user-b")
    for user, session in [("user-b", "foreign-session"), ("user-a", "foreign-session"), ("user-a", "")]:
        with pytest.raises(PermissionError):
            state.service.authorize_reconciliation(delivery_id=item["delivery_id"], kind="hook", context={"user_id": user, "session_id": session})
    assert state.service.authorize_reconciliation(delivery_id=item["delivery_id"], kind="hook",
        context={"user_id": "user-a", "session_id": "source-session"})
    state.db.create_run_record(item["execution_run_id"], "source-session", user_id="conflicting-user", run_type="automation", status="failed")
    with pytest.raises(PermissionError, match="admin_reconciliation"):
        state.service.authorize_reconciliation(delivery_id=item["delivery_id"], kind="hook", context={"user_id": "user-a", "session_id": "source-session"})


def test_admin_auth_projection_pagination_and_non_echoing_validation(state, client):
    system = make_unknown(state, system_delivery(state))
    # Put user-owned records ahead of the system items, across a storage page.
    base.hook(state)
    user = make_unknown(state, base.emit_hook()[0])
    with state.db.get_connection() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(runtime_automation_deliveries)")]
        original = dict(conn.execute("SELECT * FROM runtime_automation_deliveries WHERE delivery_id=?", (user["delivery_id"],)).fetchone())
        conn.execute("DELETE FROM runtime_automation_deliveries WHERE delivery_id=?", (user["delivery_id"],))
        for index in range(130):
            row = {**original, "delivery_id": f"a-user-{index:03}", "definition_id": f"user-{index}",
                   "execution_run_id": f"user-run-{index}", "created_at": "2026-01-01"}
            conn.execute(f"INSERT INTO runtime_automation_deliveries({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [row[key] for key in columns])
        original = dict(conn.execute("SELECT * FROM runtime_automation_deliveries WHERE delivery_id=?", (system["delivery_id"],)).fetchone())
        for index in range(3):
            row = {**original, "delivery_id": f"z-system-{index}", "definition_id": f"system-{index}", "execution_run_id": f"system-run-{index}", "created_at": "2026-02-01"}
            payload = json.loads(row["envelope_json"])
            payload["kwargs"]["task_name"] = "token=synthetic-secret-canary-123456 label"
            payload["payload"]["secret"] = "private-payload-canary"
            row["envelope_json"] = json.dumps(payload)
            row["last_error"] = "private-error-canary"
            conn.execute(f"INSERT INTO runtime_automation_deliveries({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [row[key] for key in columns])
        conn.commit()
    for headers, expected in [({}, 401), ({"x-v8-admin-role": "ADMIN"}, 401), ({**ADMIN, "x-v8-admin-role": "USER"}, 403)]:
        assert client.get("/v1/automation/deliveries", headers=headers).status_code == expected
    first = client.get("/v1/automation/deliveries?limit=2", headers=ADMIN)
    assert first.status_code == 200
    payload = first.json()
    assert len(payload["items"]) == 2 and payload["nextCursor"]
    all_ids = [row["deliveryId"] for row in payload["items"]]
    next_page = client.get("/v1/automation/deliveries", params={"limit": 2, "after": payload["nextCursor"]}, headers=ADMIN).json()
    all_ids += [row["deliveryId"] for row in next_page["items"]]
    assert len(all_ids) == len(set(all_ids)) == 4
    assert next_page["nextCursor"] is None
    assert all(row["ownership"] in {"system", "unresolved"} for row in payload["items"] + next_page["items"])
    rendered = first.text + json.dumps(next_page)
    for forbidden in ("private-payload-canary", "private-error-canary", "synthetic-secret-canary", "envelope", "kwargs"):
        assert forbidden not in rendered
    url = f"/v1/automation/deliveries/{system['delivery_id']}/reconcile"
    for body in ({"outcome": "completed", "evidence": {"observation": " "}},
                 {"outcome": "completed", "evidence": {"observation": "fixture"}, "role": "ADMIN", "secret": "echo-secret-canary"}):
        result = client.post(url, headers=ADMIN, json=body)
        assert result.status_code == 422 and "echo-secret-canary" not in result.text
    assert client.get("/v1/automation/deliveries?after=invalid", headers=ADMIN).status_code == 422
    assert client.post("/v1/automation/deliveries/missing/reconcile", headers=ADMIN,
        json={"outcome": "completed", "evidence": {"observation": "fixture"}}).status_code == 404
    assert state.db.get_automation_delivery(system["delivery_id"])["phase"] == "unknown"
    valid = {"outcome": "completed", "evidence": {"observation": "fixture outcome confirmed"}}
    assert client.post(url, headers=ADMIN, json=valid).status_code == 200
    assert client.post(url, headers=ADMIN, json=valid).status_code == 409
    assert state.db.get_automation_delivery(system["delivery_id"])["phase"] == "completed"

