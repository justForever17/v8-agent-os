from __future__ import annotations

import asyncio
import subprocess
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from core.cron_manager import CronManager
from core import action_executor as action_executor_module
from core.hooks_manager import hooks_manager
from core.runtime_projection import build_projection_summary
from core.storage import storage
from core.tools.native import automation as automation_tools
from core.tools.native.automation import manage_cron
from erc.runtime_context import bind_runtime_context
from graph import tool_routing
from runtimes.chat import runtime as chat_runtime_module
from runtimes.chat.runtime import ChatRuntime, ChatStreamState


def test_automation_command_uses_windowless_runner(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(action_executor_module, "run_windowless_bounded", fake_run)

    result = action_executor_module.ActionExecutor._execute_command(
        "echo hook",
        {},
        event_name="on_tool_execute_start",
    )

    assert result.returncode == 0
    assert captured["command"] == "echo hook"
    assert captured["kwargs"]["shell"] is True
    assert captured["kwargs"]["env"]["V8_AGENT_OS_HOOK_EVENT"] == "on_tool_execute_start"
    assert captured["kwargs"]["timeout"] == 60


def test_automation_command_timeout_surfaces_to_the_governed_failure_path(monkeypatch):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(action_executor_module, "run_windowless_bounded", fake_run)

    with pytest.raises(subprocess.TimeoutExpired) as error:
        action_executor_module.ActionExecutor._execute_command("stalled-hook", {})

    assert error.value.timeout == 60


def test_manage_cron_add_binds_agent_created_job_to_current_session(monkeypatch):
    saved: dict[str, object] = {}

    monkeypatch.setattr(automation_tools.safety_guardian, "assess_cron_mutation", lambda *args, **kwargs: object())
    monkeypatch.setattr(automation_tools, "_enforce_safety_decision", lambda *args, **kwargs: (True, ""))
    monkeypatch.setattr(storage, "get_cron_config", lambda: {"jobs": []})
    monkeypatch.setattr(storage, "save_cron_config", lambda data: saved.setdefault("data", data))

    from core import cron_manager as cron_manager_module

    monkeypatch.setattr(cron_manager_module.cron_manager, "sync_jobs_to_scheduler", lambda: None)
    monkeypatch.setattr(automation_tools.safety_guardian, "observe_post_action", lambda *args, **kwargs: None)

    with bind_runtime_context(runtime_kind="chat", session_id="session-current", run_id="run-current"):
        result = manage_cron.func(
            action="add",
            job_id="daily-digest",
            expression="0 9 * * *",
            target="supervisor",
            action_type="agent",
            payload={"task": "整理当前会话的项目状态"},
            name="每日项目摘要",
        )

    assert "Successfully added cron job" in result
    jobs = saved["data"]["jobs"]  # type: ignore[index]
    assert jobs[0]["session_id"] == "session-current"
    assert jobs[0]["conversation_id"] == "session-current"
    assert jobs[0]["sourceGroup"] == "web"
    assert jobs[0]["sourceMetadata"]["configuredBySessionId"] == "session-current"


def test_cron_manager_passes_bound_session_into_action_executor(monkeypatch):
    captured: dict[str, object] = {}
    manager = CronManager()

    def fake_execute(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("core.cron_manager.ActionExecutor.execute", fake_execute)

    asyncio.run(
        manager.execute_job(
            {
                "id": "daily-digest",
                "name": "Daily digest",
                "action_type": "agent",
                "action_target": "supervisor",
                "payload": {"task": "整理当前会话的项目状态"},
                "session_id": "session-current",
                "conversation_id": "session-current",
                "workspace_id": "workspace-main",
            }
        )
    )

    assert captured["trigger"] == "cron"
    assert captured["cron_job_id"] == "daily-digest"
    assert captured["session_id"] == "session-current"
    assert captured["conversation_id"] == "session-current"
    assert captured["workspace_id"] == "workspace-main"


def test_attached_cron_session_keeps_web_history_group():
    attached_summary = build_projection_summary(
        session={
            "id": "session-current",
            "title": "当前项目对话",
            "metadata": {"source": "web", "sourceGroup": "web", "attachedAutomation": True},
        },
        snapshot=None,
        workflow={},
        approvals=[],
        latest_seq=0,
        source="cron",
    )
    standalone_summary = build_projection_summary(
        session={
            "id": "cron:daily-digest",
            "title": "Cron · Daily digest",
            "metadata": {"runtime": "automation", "trigger_source": "cron"},
        },
        snapshot=None,
        workflow={},
        approvals=[],
        latest_seq=0,
        source="cron",
    )

    assert attached_summary["sourceGroup"] == "web"
    assert standalone_summary["sourceGroup"] == "cron"


def test_supervisor_thinking_hooks_fire_once_per_model_run(monkeypatch):
    calls: list[tuple[str, dict]] = []
    runtime = ChatRuntime()
    stream_state = ChatStreamState()
    chat_run = SimpleNamespace(session_id="session-current", active_run_id="run-current")

    async def fake_flush(*args, **kwargs):
        return []

    def fake_emit_reasoning_delta(*args, **kwargs):
        return {"type": "reasoning_chunk"}

    monkeypatch.setattr(runtime, "_flush_pending_text_aggregator", fake_flush)
    monkeypatch.setattr(runtime, "_emit_reasoning_delta", fake_emit_reasoning_delta)
    monkeypatch.setattr(
        "core.automation.hooks.hooks_manager.execute_hook",
        lambda event_name, **kwargs: calls.append((event_name, kwargs)),
    )
    monkeypatch.setattr(
        chat_runtime_module.canonical_model_event_adapter,
        "normalize_chat_model_stream",
        lambda *args, **kwargs: [
            SimpleNamespace(
                event_type="reasoning_delta",
                delta="thinking",
                model_run_id="model-run-1",
                snapshot="thinking",
                diagnostics={},
            )
        ],
    )
    monkeypatch.setattr(
        chat_runtime_module.canonical_model_event_adapter,
        "normalize_chat_model_end",
        lambda *args, **kwargs: [],
    )

    async def run_case():
        await runtime.handle_stream_event(chat_run, stream_state, {"event": "on_chat_model_stream", "data": {}, "metadata": {}})
        await runtime.handle_stream_event(chat_run, stream_state, {"event": "on_chat_model_stream", "data": {}, "metadata": {}})
        await runtime.handle_stream_event(chat_run, stream_state, {"event": "on_chat_model_end", "run_id": "model-run-1", "data": {}, "metadata": {}})

    asyncio.run(run_case())

    assert [name for name, _payload in calls] == ["on_supervisor_thinking_start", "on_supervisor_thinking_end"]
    assert calls[0][1]["parent_session_id"] == "session-current"
    assert calls[0][1]["parent_run_id"] == "run-current"
    assert "run_id" not in calls[0][1]
    assert calls[1][1]["reason"] == "chat_model_end"


def test_tool_wrapper_still_emits_tool_execute_hooks(monkeypatch):
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr("core.hooks_manager.hooks_manager.execute_hook", lambda event_name, **kwargs: calls.append((event_name, kwargs)))
    monkeypatch.setattr(tool_routing, "tool_output_budget_for_request", lambda *args, **kwargs: {})
    monkeypatch.setattr(tool_routing, "apply_tool_surface_budget", lambda result, *args, **kwargs: result)
    monkeypatch.setattr(tool_routing, "apply_agent_visible_budget", lambda result, *args, **kwargs: result)

    async def execute(_request):
        return ToolMessage(content="ok", name="demo_tool", tool_call_id="call-1")

    request = SimpleNamespace(tool_call={"name": "demo_tool", "id": "call-1"})
    result = asyncio.run(tool_routing.async_tool_call_wrapper(request, execute))

    assert isinstance(result, ToolMessage)
    assert [name for name, _payload in calls] == ["on_tool_execute_start", "on_tool_execute_end"]
    assert calls[0][1]["tool"] == "demo_tool"


def test_hooks_manager_preserves_current_runtime_context_as_parent(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        storage,
        "get_hooks_config",
        lambda: {
            "hooks": [
                {
                    "name": "tool-audit",
                    "events": ["on_tool_execute_start"],
                    "type": "command",
                    "target": "echo hook",
                    "enabled": True,
                    "async": False,
                }
            ]
        },
    )
    monkeypatch.setattr(
        "core.hooks_manager.ActionExecutor.execute",
        lambda **kwargs: captured.update(kwargs),
    )

    with bind_runtime_context(session_id="session-current", run_id="run-current", workspace_id="workspace-main"):
        hooks_manager.execute_hook("on_tool_execute_start", tool="demo_tool")

    assert captured["event_name"] == "on_tool_execute_start"
    assert "session_id" not in captured
    assert "run_id" not in captured
    assert captured["parent_session_id"] == "session-current"
    assert captured["parent_run_id"] == "run-current"
    assert captured["workspace_id"] == "workspace-main"
    assert captured["trigger"] == "hook:on_tool_execute_start"
