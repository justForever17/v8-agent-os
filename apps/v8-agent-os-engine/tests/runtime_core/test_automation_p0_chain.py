from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage

from core import action_executor as action
from core import cron_manager as cron_module
from core import hooks_manager as hooks_module
from core.storage import storage
from core.tools.native import automation as automation_tools
from erc.runtime_context import bind_runtime_context


def job(**overrides):
    return {"id": "daily", "name": "Daily", "enabled": True,
            "cron_expression": "0 9 * * *", "action_type": "agent",
            "action_target": "supervisor", "payload": {"message": "fixture"}, **overrides}


@pytest.mark.parametrize("invalid", [
    {"jobs": [job(cron_expression="not a cron")]},
    {"jobs": [job(), job()]},
    {"jobs": [None]}, {"jobs": "invalid"}, None,
])
def test_invalid_cron_plan_preserves_pending_job_and_payload(monkeypatch, invalid):
    manager = cron_module.CronManager()
    config = {"jobs": [job()]}
    monkeypatch.setattr(storage, "get_cron_config", lambda: config)
    assert manager.sync_jobs_to_scheduler()["status"] == "success"
    original = manager.scheduler.get_job("daily")
    original_trigger = original.trigger
    original_payload = deepcopy(original.kwargs)
    config["jobs"][0]["payload"]["message"] = "edited"
    config = invalid
    assert manager.sync_jobs_to_scheduler()["status"] == "rejected"
    assert manager.scheduler.get_jobs() == [original]
    assert original.trigger is original_trigger
    assert original.kwargs == original_payload


def test_cron_valid_edit_disable_delete_and_reload(monkeypatch):
    config = {"jobs": [job()]}
    monkeypatch.setattr(storage, "get_cron_config", lambda: config)
    manager = cron_module.CronManager()
    manager.sync_jobs_to_scheduler()
    config["jobs"][0]["cron_expression"] = "0 11 * * *"
    manager.sync_jobs_to_scheduler()
    manager.sync_jobs_to_scheduler()
    assert len(manager.scheduler.get_jobs()) == 1
    assert str(manager.scheduler.get_job("daily").trigger.fields[5]) == "11"
    reloaded = cron_module.CronManager()
    assert reloaded.sync_jobs_to_scheduler()["status"] == "success"
    assert len(reloaded.scheduler.get_jobs()) == 1
    config["jobs"][0]["enabled"] = False
    manager.sync_jobs_to_scheduler()
    assert manager.scheduler.get_jobs() == []
    config["jobs"][0]["enabled"] = True
    manager.sync_jobs_to_scheduler()
    config["jobs"] = []
    manager.sync_jobs_to_scheduler()
    assert manager.scheduler.get_jobs() == []


def test_scheduler_rejected_modification_does_not_claim_success(monkeypatch):
    manager = cron_module.CronManager()
    monkeypatch.setattr(storage, "get_cron_config", lambda: {"jobs": [job()]})
    manager.sync_jobs_to_scheduler()
    original = manager.scheduler.get_job("daily")
    monkeypatch.setattr(manager.scheduler, "modify_job", lambda *a, **k: fail("scheduler refused"))
    result = manager.sync_jobs_to_scheduler()
    assert result["status"] == "partial"
    assert result["scheduled"] == []
    assert result["preserved"] == ["daily"]
    assert manager.scheduler.get_jobs() == [original]


def fail(message="fixture failure"):
    raise RuntimeError(message)


@pytest.fixture
def execution(monkeypatch):
    record = SimpleNamespace(completed=[], failed=[], logs=[], released=[], transitions=[], calls=[])
    handle = SimpleNamespace(
        run_id="fixture-run", session_id="fixture-session",
        emit=lambda *a, **k: None,
        fail=lambda *a, **k: record.failed.append(a),
        complete=lambda *a, **k: record.completed.append(k),
        transition=lambda *a, **k: None,
    )
    record.handle = handle
    lane = SimpleNamespace(acquired=True, waited=False, policy="queue", active_run_id=None,
                           interrupted_run_id=None, rejected_by_run_id=None)
    record.lane = lane
    action.ActionExecutor._active_targets.clear()
    monkeypatch.setattr(action.automation_runtime, "begin_or_attach_run", lambda **k: handle)
    monkeypatch.setattr(action.automation_runtime, "attach_run", lambda *a: handle)
    monkeypatch.setattr(action.ActionExecutor, "_activate_automation_stage", lambda **k: None)
    monkeypatch.setattr(action.ActionExecutor, "_consume_automation_control", lambda **k: None)
    monkeypatch.setattr(action.ActionExecutor, "_log_audit_event", lambda *a: None)
    monkeypatch.setattr(action.runtime_stability_service, "session_lane_policy", lambda: "queue")
    monkeypatch.setattr(action.session_admission_service, "acquire", lambda *a, **k: lane)
    monkeypatch.setattr(action.session_admission_service, "acquire_async", AsyncMock(return_value=lane))
    monkeypatch.setattr(action.session_admission_service, "release", lambda *a: record.released.append(a))
    monkeypatch.setattr(action.session_admission_service, "release_async",
                        AsyncMock(side_effect=lambda *a: record.released.append(a)))
    monkeypatch.setattr(action.automation_runtime, "run_preflight", lambda **k: None)
    monkeypatch.setattr(action.automation_runtime, "handle_preflight_decision", lambda **k: None)
    monkeypatch.setattr(action.automation_runtime, "handle_action_decision", lambda **k: None)
    monkeypatch.setattr(action.automation_runtime, "observe_post_action", lambda **k: None)
    monkeypatch.setattr(action.automation_runtime, "refresh_job_context", lambda **k: None)
    monkeypatch.setattr(action.safety_guardian, "assess_automation_action", lambda **k: object())
    monkeypatch.setattr(action.knowledge_db, "log_execution", lambda **k: record.logs.append(k))
    monkeypatch.setattr(action.run_service, "transition_run", lambda *a, **k: record.transitions.append(k))
    monkeypatch.setattr(action.automation_runtime, "build_agent_execution_payload",
                        lambda **k: {"messages": [HumanMessage(content="fixture")],
                                     "hook_context": k["action_payload"].get("hook_context", {})})
    monkeypatch.setattr(action.ActionExecutor, "_begin_execution_side_effect",
                        lambda **k: SimpleNamespace(execute=True))
    monkeypatch.setattr(action.side_effect_idempotency_service, "complete", lambda **k: True)
    monkeypatch.setattr(action.side_effect_idempotency_service, "fail", lambda **k: None)
    return record


def execute(async_mode, target="graph.dummy_auditor", payload=None):
    kwargs = {"trigger": "cron", "cron_job_id": "fixture", "session_id": "fixture-session", "run_id": "fixture-run"}
    if async_mode:
        return asyncio.run(action.ActionExecutor._execute_agent_async(target, payload or {}, **kwargs))
    return action.ActionExecutor._execute_sync("agent", target, payload or {}, kwargs)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("boundary", ["begin", "prepare", "lane", "preflight", "finalize"])
def test_startup_and_finalize_failure_releases_cron_and_admission(monkeypatch, execution, async_mode, boundary):
    if boundary == "begin":
        monkeypatch.setattr(action.automation_runtime, "begin_or_attach_run", lambda **k: fail())
    elif boundary in {"prepare", "finalize"}:
        monkeypatch.setattr(action.ActionExecutor, "_activate_automation_stage",
                            lambda **k: fail() if k["stage"] == boundary else None)
    elif boundary == "lane":
        monkeypatch.setattr(action.session_admission_service, "acquire", lambda *a, **k: fail())
        monkeypatch.setattr(action.session_admission_service, "acquire_async", AsyncMock(side_effect=RuntimeError("lane")))
    else:
        monkeypatch.setattr(action.automation_runtime, "run_preflight", lambda **k: fail())
    if async_mode:
        execute(True)
    else:
        with pytest.raises(RuntimeError):
            execute(False)
    assert not action.ActionExecutor._active_targets
    assert not execution.completed
    assert execution.logs[-1]["status"] == "failed"
    if boundary != "begin":
        assert execution.released == [("fixture-session", "fixture-run")]
        assert execution.failed


@pytest.mark.parametrize("async_mode", [False, True])
def test_lane_rejection_allows_same_cron_job_to_execute_next_time(execution, async_mode):
    execution.lane.acquired = False
    execute(async_mode)
    assert not action.ActionExecutor._active_targets
    assert not execution.completed
    assert execution.logs[-1]["status"] == "rejected"
    execution.lane.acquired = True
    execute(async_mode)
    assert len(execution.completed) == 1
    assert execution.logs[-1]["status"] == "success"


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("target", ["graph", "module_that_does_not_exist"])
def test_missing_executable_never_completes(execution, async_mode, target):
    if async_mode:
        execute(True, target)
    else:
        with pytest.raises(Exception):
            execute(False, target)
    assert execution.failed
    assert not execution.completed
    assert execution.logs[-1]["status"] == "failed"
    assert not action.ActionExecutor._active_targets


@pytest.mark.parametrize("async_mode", [False, True])
def test_real_offline_agent_rejection_is_failure(execution, async_mode):
    payload = {"hook_context": {"tool": "dangerous_system_call"}}
    if async_mode:
        execute(True, payload=payload)
    else:
        with pytest.raises(Exception, match="rejected the context"):
            execute(False, payload=payload)
    assert execution.failed
    assert not execution.completed
    assert execution.logs[-1]["status"] == "failed"


@pytest.mark.parametrize("boundary", ["lane", "graph"])
def test_async_cancellation_is_cancelled_and_releases_resources(monkeypatch, execution, boundary):
    if boundary == "lane":
        monkeypatch.setattr(action.session_admission_service, "acquire_async",
                            AsyncMock(side_effect=asyncio.CancelledError()))
    else:
        graph = SimpleNamespace(ainvoke=AsyncMock(side_effect=asyncio.CancelledError()))
        monkeypatch.setattr(action.ActionExecutor, "_load_agent_graph", lambda target: graph)
    with pytest.raises(asyncio.CancelledError):
        execute(True)
    assert not action.ActionExecutor._active_targets
    assert not execution.completed
    assert execution.logs[-1]["status"] == "cancelled"
    assert execution.transitions[-1]["status"] == "cancelled"
    assert execution.released


@pytest.mark.parametrize("async_mode", [False, True])
def test_release_error_still_clears_cron_mutex(monkeypatch, execution, async_mode):
    monkeypatch.setattr(action.session_admission_service, "release", lambda *a: fail("release failed"))
    monkeypatch.setattr(action.session_admission_service, "release_async",
                        AsyncMock(side_effect=RuntimeError("release failed")))
    with pytest.raises(RuntimeError, match="release failed"):
        execute(async_mode, "graph")
    assert not action.ActionExecutor._active_targets
    assert not execution.completed


def test_background_command_preserves_hook_causal_context(monkeypatch):
    from erc.runtime_context import get_runtime_context

    async def run():
        loop = asyncio.get_running_loop()
        observed = loop.create_future()
        def worker(*args):
            loop.call_soon_threadsafe(observed.set_result, get_runtime_context().get("hook_chain"))
        monkeypatch.setattr(action.ActionExecutor, "_execute_sync", worker)
        with bind_runtime_context(hook_chain=["hook-a"]):
            action.ActionExecutor.execute("command", "fixture", is_async=True)
        assert await asyncio.wait_for(observed, timeout=3) == ["hook-a"]
    asyncio.run(run())


def test_supported_supervisor_uses_real_runner_and_checkpoint(monkeypatch, execution, tmp_path):
    import importlib
    from api.models import EngineConfig
    from core import engine_config_resolver
    from langgraph.graph import StateGraph, START, END
    from langchain_core.messages import AIMessage
    from graph.dummy_auditor import HookState
    from erc.checkpoint_store import CheckpointStore

    runner_module = importlib.import_module("agents.runners.supervisor_runner")
    checkpoint = CheckpointStore(tmp_path / "checkpoint.sqlite")
    monkeypatch.setattr(runner_module, "checkpoint_store", checkpoint)
    created = []

    def offline_graph(config, checkpointer=None):
        assert config.model_name == "offline-fixture"
        assert checkpointer is not None
        graph = StateGraph(HookState)
        def reply(state):
            created.append(state["messages"][-1].content)
            return {"messages": [AIMessage(content="offline done")]}
        graph.add_node("reply", reply)
        graph.add_edge(START, "reply")
        graph.add_edge("reply", END)
        return graph.compile(checkpointer=checkpointer)

    monkeypatch.setattr(runner_module, "create_supervisor_graph", offline_graph)
    monkeypatch.setattr(runner_module, "supervisor_runner", runner_module.SupervisorAgentRunner())
    monkeypatch.setattr(engine_config_resolver, "resolve_engine_config_for_role",
                        lambda role: {"engine_config": EngineConfig(provider="fixture", model_name="offline-fixture")})
    try:
        execute(True, "supervisor")
    finally:
        asyncio.run(checkpoint.close())
    assert created == ["fixture"]
    assert len(execution.completed) == 1
    assert not execution.failed


def test_synchronous_supervisor_is_explicitly_rejected(execution):
    with pytest.raises(Exception, match="async automation execution"):
        execute(False, "supervisor")
    assert execution.failed
    assert not execution.completed


@pytest.mark.parametrize("kind,target", [("command", "fixture-command"), ("python", "missing_python_target")])
def test_command_nonzero_and_missing_python_fail_the_run(monkeypatch, execution, kind, target):
    import subprocess
    monkeypatch.setattr(action, "run_windowless_bounded",
                        lambda *a, **k: subprocess.CompletedProcess(target, 7, "", "fixture refused"))
    with pytest.raises(Exception):
        action.ActionExecutor._execute_sync(kind, target, {}, {"trigger": "cron", "cron_job_id": "fixture"})
    assert execution.failed
    assert not execution.completed
    assert execution.logs[-1]["status"] == "failed"
    assert not action.ActionExecutor._active_targets


@pytest.fixture
def hook_setup(monkeypatch):
    hook = {"id": "hook-1", "name": "audit", "events": ["*"], "target": "echo audit",
            "type": "command", "enabled": True, "async": True}
    state = {"hooks": [hook]}
    calls = []
    monkeypatch.setattr(storage, "get_hooks_config", lambda: state)
    monkeypatch.setattr(storage, "save_hooks_config", lambda value: state.update(value))
    monkeypatch.setattr(action.ActionExecutor, "execute", lambda **k: calls.append(k))
    return state, calls


# Hook occurrence/reentry oracles now use the real SQLite delivery path in
# test_automation_delivery_recovery.py instead of the retired process cache.


def test_manage_hook_lifecycle_uses_guard_and_exact_id(monkeypatch, hook_setup):
    state, _ = hook_setup
    guarded = []
    monkeypatch.setattr(automation_tools.safety_guardian, "assess_hook_mutation",
                        lambda action, **k: guarded.append(action))
    monkeypatch.setattr(automation_tools, "_enforce_safety_decision", lambda *a, **k: (True, ""))
    monkeypatch.setattr(automation_tools.safety_guardian, "observe_post_action", lambda **k: None)
    for verb in ["pause", "resume", "remove"]:
        assert "Successfully" in automation_tools.manage_hook.func(action=verb, name="hook-1")
        if verb != "remove":
            assert state["hooks"][0]["enabled"] == (verb == "resume")
    assert state["hooks"] == []
    assert guarded == ["pause", "resume", "remove"]


@pytest.mark.parametrize("expression,existing", [("invalid", []), ("0 9 * * *", [job()])])
def test_manage_cron_invalid_or_duplicate_add_does_not_save(monkeypatch, expression, existing):
    saved = []
    monkeypatch.setattr(storage, "get_cron_config", lambda: {"jobs": existing})
    monkeypatch.setattr(storage, "save_cron_config", lambda value: saved.append(value))
    monkeypatch.setattr(automation_tools.safety_guardian, "assess_cron_mutation", lambda *a, **k: None)
    monkeypatch.setattr(automation_tools, "_enforce_safety_decision", lambda *a, **k: (True, ""))
    result = automation_tools.manage_cron.func(action="add", job_id="daily", expression=expression,
                                              target="supervisor", action_type="agent", name="Daily")
    assert "Successfully" not in result
    assert not saved
