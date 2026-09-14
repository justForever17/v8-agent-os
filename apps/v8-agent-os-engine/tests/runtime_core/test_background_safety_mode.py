from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import ast
import os
from pathlib import Path
import subprocess
import uuid

import pytest

from core.database import db
from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.tools.native.tool_governance import current_safety_approval_mode
from erc.runtime_context import bind_runtime_context, get_runtime_context
from erc.run_service import run_service
from graph.parallel_support import _runtime_context_from_parallel_state
from runtimes.automation.runtime import automation_runtime


@pytest.mark.skipif(not os.environ.get("V8_P0_BASELINE_REF"), reason="optional historical comparison requires an available pre-fix Git ref")
def test_same_mode_oracle_rejects_actual_pre_fix_reconstruction():
    import core.runtime_episode_runner as runner_module
    import runtimes.automation.runtime as automation_module

    def original_method(relative_path, class_name, method_name, namespace):
        source = subprocess.check_output([
            "git", "show", f"{os.environ['V8_P0_BASELINE_REF']}:apps/v8-agent-os-engine/{relative_path}",
        ], cwd=Path(__file__).resolve().parents[2], text=True, encoding="utf-8")
        cls = next(item for item in ast.parse(source).body if isinstance(item, ast.ClassDef) and item.name == class_name)
        method = next(item for item in cls.body if isinstance(item, ast.FunctionDef) and item.name == method_name)
        method.decorator_list = []
        env = dict(namespace)
        exec(compile(ast.Module(body=[method], type_ignores=[]), relative_path, "exec"), env)
        return env[method_name]

    session, run = seed_run("minimal")
    old = original_method("core/runtime_episode_runner.py", "RuntimeEpisodeRunner", "_broker_selected_local_episode_command", vars(runner_module))
    old_state = old(
        {"episodeId": "subagent::fixture::0", "source": "delegation_broker", "targetId": "worker"},
        worker_briefs=[{"goal": "observe"}], session_id=session, run_id=run, workspace_path=None,
    ).goto[0].arg
    with bind_runtime_context(**_runtime_context_from_parallel_state(old_state)):
        with pytest.raises(AssertionError):
            assert current_safety_approval_mode() == "minimal"
    old_automation = original_method("runtimes/automation/runtime.py", "AutomationRuntime", "bind_execution_context", vars(automation_module))
    with old_automation(automation_runtime, runtime_kind="automation_agent", trigger_source="cron",
                        run_handle=SimpleNamespace(session_id=session, run_id=run), user_id="fixture-owner", project_id=None, workspace_id=None):
        with pytest.raises(AssertionError):
            assert current_safety_approval_mode() == "minimal"


def seed_run(mode):
    suffix = uuid.uuid4().hex
    session, run = f"mode-session-{suffix}", f"mode-run-{suffix}"
    db.create_or_update_session(session, "mode fixture", user_id="fixture-owner")
    db.create_run_record(run, session, user_id="fixture-owner", metadata={"safetyApprovalMode": mode} if mode else {})
    return session, run


def restored_branch(session, run, depth):
    episode = {
        "episodeId": "subagent::fixture::0", "source": "delegation_broker", "targetId": "worker",
        "delegationDepth": depth, "inputs": {"safetyApprovalMode": "minimal"},
    }
    command = RuntimeEpisodeRunner._broker_selected_local_episode_command(
        episode, worker_briefs=[{
            "taskBriefId": "fixture", "goal": "observe", "delegationDepth": depth,
            "safetyApprovalMode": "minimal", "context": {"safetyApprovalMode": "minimal"},
        }], session_id=session, run_id=run, workspace_path=None,
    )
    return command.goto[0].arg


@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize("mode", ["manual", "reduced", "minimal", None, "invalid"])
def test_fresh_worker_restores_only_current_run_authorization(mode, depth):
    session, run = seed_run(mode)
    expected = mode if mode in {"manual", "reduced", "minimal"} else "manual"

    def execute():
        assert not get_runtime_context()
        state = restored_branch(session, run, depth)
        with bind_runtime_context(**_runtime_context_from_parallel_state(state)):
            assert current_safety_approval_mode() == expected
        assert state["delegation_contexts"][0]["safetyApprovalMode"] == expected
        assert state["current_route_context"]["safetyApprovalMode"] == expected

    # No inherited asyncio context: the actual database is the only mode source.
    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(execute).result()


def test_reconstruction_reads_changed_metadata_and_never_uses_a_stale_brief():
    session, run = seed_run("minimal")
    assert restored_branch(session, run, 2)["safetyApprovalMode"] == "minimal"
    run_service.update_metadata(run, {"safetyApprovalMode": "manual"})
    assert restored_branch(session, run, 2)["safetyApprovalMode"] == "manual"


@pytest.mark.parametrize("mode", ["manual", "reduced", "minimal", None])
def test_automation_independent_worker_uses_run_mode_and_releases_context(mode):
    session, run = seed_run(mode)

    def execute():
        with bind_runtime_context(run_id="other-run", safety_approval_mode="minimal", safetyApprovalMode="minimal"):
            with automation_runtime.bind_execution_context(
                runtime_kind="automation_agent", trigger_source="cron", run_handle=SimpleNamespace(session_id=session, run_id=run),
                user_id="fixture-owner", project_id=None, workspace_id=None,
            ):
                assert current_safety_approval_mode() == (mode or "manual")
            assert get_runtime_context()["run_id"] == "other-run"
        assert not get_runtime_context()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(execute).result()
