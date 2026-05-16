from __future__ import annotations

import json

from langchain_core.messages import ToolMessage

from core.native_tools import _command_session_result_preview_fields, _strip_terminal_bootstrap_noise
from core.tool_surface import apply_tool_surface_budget
from runtimes.chat.runtime import ChatRuntime


def test_command_session_strips_windows_bootstrap_and_exit_sentinel():
    text = "\n".join(
        [
            "@echo off",
            "chcp 65001 >NUL",
            'cd /d "E:\\Projects\\demo"',
            'cmd /c "echo hello"',
            "hello",
            "echo __V8_COMMAND_EXIT_abcdef123456__:%ERRORLEVEL%",
            "__V8_COMMAND_EXIT_abcdef123456__:0",
        ]
    )

    assert _strip_terminal_bootstrap_noise(text) == 'cmd /c "echo hello"\nhello'


def test_command_session_completed_state_returns_agent_visible_result_preview():
    fields = _command_session_result_preview_fields(
        state="failed",
        interactive=False,
        screen_preview="src/index.ts(1,1): error TS2307: Cannot find module './missing'.\nFound 1 error.",
        raw_buffer="",
        raw_frame_preview="",
    )

    assert fields["finalPreview"].startswith("src/index.ts")
    assert fields["finalPreviewTruncated"] is False


def test_run_system_command_agent_visible_surface_is_terminal_first():
    rendered = ChatRuntime._compact_run_system_command_result(
        {
            "ok": False,
            "kind": "command_result",
            "command": "npx tsc -b",
            "returnCode": 2,
            "keyOutput": "",
            "keyErrors": "src/index.ts(1,1): error TS2307: Cannot find module './missing'.",
            "summary": "命令执行失败，退出码 2。",
            "recommendedNextAction": "根据 stderr/stdout 摘要修复问题后重跑。",
        }
    )

    assert rendered.startswith("$ npx tsc -b")
    assert "<stderr>" in rendered
    assert "TS2307" in rendered
    assert "[exit code: 2]" in rendered
    assert '"ok"' not in rendered
    assert "recommendedNextAction" not in rendered


def test_command_session_agent_visible_surface_waiting_for_input():
    rendered = ChatRuntime._compact_command_session_broker_result(
        {
            "ok": True,
            "mode": "observe",
            "kind": "command_session",
            "command": "npx create-vite",
            "sessionId": "abc123",
            "state": "awaiting_input",
            "awaitingInput": True,
            "deltaText": "Ok to proceed? (y)",
            "recommendedNextAction": "input",
        }
    )

    assert rendered.startswith("$ npx create-vite")
    assert "Ok to proceed? (y)" in rendered
    assert "[waiting for input]" in rendered
    assert "recommendedNextAction" not in rendered


def test_command_session_start_does_not_expose_initial_screen_echo():
    rendered = ChatRuntime._compact_command_session_broker_result(
        {
            "ok": True,
            "mode": "start",
            "kind": "command_session",
            "command": "npm install",
            "sessionId": "abc123",
            "state": "running",
            "initialPreview": "C:\\Users\\demo>npm install",
            "recommendedNextAction": "observe_later",
        }
    )

    assert rendered.startswith("$ npm install")
    assert "C:\\Users\\demo" not in rendered
    assert "[still running]" in rendered
    assert "recommendedNextAction" not in rendered


def test_command_session_final_result_strips_command_echo():
    rendered = ChatRuntime._compact_command_session_broker_result(
        {
            "ok": True,
            "mode": "observe",
            "kind": "command_session",
            "command": "python -c \"print('done')\"",
            "sessionId": "abc123",
            "state": "completed",
            "returnCode": 0,
            "finalPreview": "python -c \"print('done')\"\ndone",
        }
    )

    assert rendered.startswith("$ python -c")
    assert "<stdout>\ndone\n</stdout>" in rendered
    assert "python -c \"print('done')\"\ndone" not in rendered


def test_tool_node_budget_projects_command_json_to_terminal_surface():
    raw = json.dumps(
        {
            "ok": True,
            "kind": "command_result",
            "command": "echo hello",
            "summary": "命令执行成功。",
            "returnCode": 0,
            "keyOutput": "hello",
            "recommendedNextAction": "none",
        },
        ensure_ascii=False,
    )
    message = ToolMessage(content=raw, name="run_system_command", tool_call_id="call-test")

    visible = apply_tool_surface_budget(
        message,
        {"agentVisibleBudget": 2500},
        tool_name="run_system_command",
    ).content

    assert visible.startswith("$ echo hello")
    assert "<stdout>\nhello\n</stdout>" in visible
    assert '"ok"' not in visible
    assert "recommendedNextAction" not in visible
