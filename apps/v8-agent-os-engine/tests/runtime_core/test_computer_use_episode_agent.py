from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.runtime_episode_runner import RuntimeEpisodeRunner
from core.storage import StorageManager
from runtimes.computer_use.episode_agent import ComputerUseEpisodeAgent


class _FakeRuntime:
    browser_automation = SimpleNamespace()


def _brief(*, write_set: list[str] | None = None, goal: str = "") -> dict:
    return {
        "taskBriefId": "computer-use-test",
        "goal": goal,
        "context": "用户已授权当前 TaskBrief 中的真实操作。",
        "writeSet": list(write_set or []),
        "acceptanceContract": [],
        "constraints": [],
    }


def test_episode_agent_accepts_only_declared_workspace_outputs(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_test",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(write_set=["proof/result.jpg"], goal="下载图片并关闭 Agent 浏览器"),
        runtime=_FakeRuntime(),
    )

    approved = tmp_path / "proof" / "result.jpg"
    approved.parent.mkdir(parents=True)
    approved.write_bytes(b"\xff\xd8\xff" + b"payload")
    agent.browser_closed = True

    assert agent._resolve_browser_output("proof/result.jpg") == approved.resolve()
    with pytest.raises(RuntimeError, match="writeSet"):
        agent._resolve_browser_output("proof/other.jpg")
    verification = agent._validate_completion()
    assert verification["passed"] is True
    assert verification["files"][0]["magic"].startswith("FFD8FF")


def test_episode_agent_narrows_tools_to_cleanup_then_finish(tmp_path: Path) -> None:
    output = tmp_path / "proof" / "result.jpg"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"\xff\xd8\xffpayload")
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_cleanup",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(
            write_set=["proof/result.jpg"],
            goal="打开 https://metaso.cn ，询问太和殿图片，下载图片并关闭 Agent 浏览器",
        ),
        runtime=_FakeRuntime(),
    )
    agent.artifact_refs = ["workspace:proof/result.jpg"]
    agent.actions = [
        {"tool": "browser_open", "args": {"url": "https://metaso.cn"}, "ok": True},
        {"tool": "browser_input", "args": {"text": "太和殿图片", "submit": True}, "ok": True},
        {"tool": "browser_download_image", "args": {"image_index": 1}, "ok": True},
    ]

    assert [item.name for item in agent._tools_for_next_round()] == ["browser_close"]
    agent.browser_closed = True
    assert [item.name for item in agent._tools_for_next_round()] == ["finish_task"]


def test_episode_agent_recognizes_supervisor_rewritten_agent_browser_cleanup(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_supervisor_cleanup",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief={
            **_brief(goal="从 https://metaso.cn 下载图片"),
            "expectedOutputs": ["全部 Agent Browser 标签页关闭确认"],
            "acceptanceContract": ["结束时关闭全部 Agent Browser 标签页与本轮专用浏览器进程。"],
        },
        runtime=_FakeRuntime(),
    )

    assert agent._close_browser_required is True
    assert "agent_browser_not_closed" in agent._validate_completion()["missing"]


def test_episode_agent_requires_playback_action_evidence(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_music",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动 QQ音乐，搜索歌曲、进入播放页、点击播放、关闭窗口"),
        runtime=_FakeRuntime(),
    )

    verification = agent._validate_completion()
    assert verification["passed"] is False
    assert "song_search_not_executed" in verification["missing"]
    assert "desktop_close_not_executed" in verification["missing"]


def test_episode_agent_does_not_prompt_cleanup_before_required_desktop_work(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_music_prompt",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动 QQ音乐，搜索歌曲、进入播放页、点击播放、关闭窗口并关闭进程"),
        runtime=_FakeRuntime(),
    )

    messages = agent._model_messages(round_index=1, context="{}", frame=None)
    system_prompt = str(messages[0].content)
    prompt = str(messages[1].content)

    assert "CURRENT ACCEPTANCE GAPS" in prompt
    assert "Re-plan from each fresh frame" in system_prompt
    assert "never blindly replay a fixed script" in system_prompt
    assert "all task work is complete" not in prompt
    assert "inspect the fresh screenshot before cleanup" not in prompt


def test_episode_agent_keeps_bottom_player_click_available_before_desktop_cleanup(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_music_cleanup",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动 QQ音乐，搜索歌曲、进入播放页、点击播放、关闭窗口并关闭进程"),
        runtime=_FakeRuntime(),
    )
    agent.active_app_query = "QQ音乐"
    agent.actions = [
        {"index": 1, "tool": "desktop_launch", "args": {"app": "QQ音乐"}, "ok": True},
        {"index": 2, "tool": "desktop_input", "args": {"text": "晴天 周杰伦", "submit": True}, "ok": True},
        {
            "index": 3,
            "tool": "desktop_click",
            "args": {"target": "第一条歌曲结果的行内播放按钮"},
            "ok": True,
            "result": '{"afterWindowTitle":"晴天 - 周杰伦"}',
        },
    ]
    agent._process_snapshot = lambda _names: {1234}

    verification = agent._validate_completion()
    assert "play_action_not_identified" in verification["missing"]
    assert {item.name for item in agent._tools_for_next_round()} >= {"desktop_click", "desktop_close"}

    agent.actions.append(
        {
            "index": 4,
            "tool": "desktop_click",
            "args": {"target": "底部播放器栏绿色播放按钮"},
            "ok": True,
            "result": '{"afterWindowTitle":"晴天 - 周杰伦"}',
        }
    )
    verification = agent._validate_completion()
    assert "play_action_not_identified" not in verification["missing"]
    assert [item.name for item in agent._tools_for_next_round()] == [
        "desktop_reveal_controls",
        "desktop_click",
        "desktop_close",
    ]


def test_episode_agent_accepts_explicit_blocked_finish_without_false_success(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_blocked",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(write_set=["proof/result.jpg"], goal="下载图片"),
        runtime=_FakeRuntime(),
    )

    result = agent._dispatch(
        "finish_task",
        {"summary": "BLOCKED: login boundary", "evidence": "login form visible"},
    )

    assert result["accepted"] is True
    assert result["status"] == "blocked"
    assert agent._finished_blocked is True
    assert agent._validate_completion()["passed"] is False


def test_episode_agent_rejects_blocked_summary_after_acceptance_passes(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_completed",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="观察当前应用"),
        runtime=_FakeRuntime(),
    )

    result = agent._dispatch("finish_task", {"summary": "BLOCKED — only finish_task is available"})

    assert result["accepted"] is False
    assert result["verification"]["passed"] is True
    assert agent._finished_summary is None


def test_episode_desktop_input_uses_application_surface_focus_and_journals_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict = {}

    class _InputRuntime(_FakeRuntime):
        def type_text(self, **kwargs):
            captured.update(kwargs)
            return {"result": {"status": "completed", "target": {"role": "CoordinatePoint"}}}

    agent = ComputerUseEpisodeAgent(
        episode_id="episode_input",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="在自绘应用中搜索歌曲"),
        runtime=_InputRuntime(),
    )
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("QQ音乐", {"appId": "app_qqmusic"}, "QQ音乐", 42),
    )

    result = agent._dispatch_desktop_input(
        {"app": "QQ音乐", "text": "晴天 周杰伦", "x": 0.42, "y": 0.04, "submit": True}
    )
    agent._record_action(name="desktop_input", args={"text": "晴天 周杰伦"}, result=result, ok=True)

    assert captured["window_typing"] is True
    assert captured["window_typing_focus_mode"] == "application_surface"
    assert captured["window_handle"] == 42
    assert captured["point"] == [0.42, 0.04]
    journal = tmp_path / ".v8-agent-os" / "artifacts" / "computer-use-episode" / "episode_input" / "actions.jsonl"
    assert journal.exists()
    assert '"tool": "desktop_input"' in journal.read_text(encoding="utf-8")


def test_episode_reveal_controls_moves_pointer_without_clicking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict = {}

    class _RevealRuntime(_FakeRuntime):
        def hover(self, **kwargs):
            captured.update(kwargs)
            return {"result": {"status": "completed"}}

    agent = ComputerUseEpisodeAgent(
        episode_id="episode_reveal",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="播放全屏视频并在控件隐藏时显露播放控件"),
        runtime=_RevealRuntime(),
    )
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("播放器", {"appId": "app_player"}, "播放页", 42),
    )
    monkeypatch.setattr("runtimes.computer_use.episode_agent.time.sleep", lambda _seconds: None)

    result = agent._dispatch_desktop_reveal_controls(
        {"app": "播放器", "x": 0.5, "y": 0.8}
    )

    assert captured["point"] == [0.5, 0.8]
    assert captured["window_handle"] == 42
    assert result["method"] == "pointer_move"


def test_episode_rejects_close_semantics_through_printable_hotkey(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_hotkey_guard",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="关闭播放器"),
        runtime=_FakeRuntime(),
    )
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("播放器", {"appId": "app_player"}, "播放页", 42),
    )

    with pytest.raises(RuntimeError, match="desktop_close"):
        agent._dispatch_desktop_hotkey({"app": "播放器", "sequence": "ALT+F4"})


def test_episode_restores_interrupted_action_journal_without_replaying_from_round_one(
    tmp_path: Path,
) -> None:
    first = ComputerUseEpisodeAgent(
        episode_id="episode_resume",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动 QQ音乐，搜索歌曲并播放后关闭进程"),
        runtime=_FakeRuntime(),
    )
    first._record_action(
        name="desktop_launch",
        args={"app": "QQ音乐"},
        result={
            "appId": "app_qqmusic",
            "ownedProcessIds": [123],
            "windowHandle": 42,
            "windowTitle": "晴天 - 周杰伦",
        },
        ok=True,
    )
    (first._frame_directory() / "round-07-desktop.png").write_bytes(b"frame")

    resumed = ComputerUseEpisodeAgent(
        episode_id="episode_resume",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动 QQ音乐，搜索歌曲并播放后关闭进程"),
        runtime=_FakeRuntime(),
    )

    assert len(resumed.actions) == 1
    assert resumed.actions[0]["index"] == 1
    assert resumed.active_app_query == "QQ音乐"
    assert resumed.app_owned_pids == {123}
    assert resumed.app_baseline_initialized is True
    assert resumed.app_baseline_pids == set()
    assert resumed._round_offset == 7


def test_episode_binds_largest_application_window_instead_of_transient_toast(tmp_path: Path) -> None:
    class _WindowRuntime(_FakeRuntime):
        driver = SimpleNamespace(
            list_windows=lambda **_kwargs: [
                {
                    "handle": 11,
                    "title": "已开始播放提示",
                    "bounds": [0, 0, 180, 48],
                    "isVisible": True,
                },
                {
                    "handle": 22,
                    "title": "晴天 - 周杰伦",
                    "bounds": [0, 0, 1600, 900],
                    "isVisible": True,
                },
            ]
        )

    agent = ComputerUseEpisodeAgent(
        episode_id="episode_window",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="操作自绘应用"),
        runtime=_WindowRuntime(),
    )
    agent.active_app = {
        "launchCandidates": [{"role": "app_path", "executableName": "QQMusic.exe"}],
    }

    selected = agent._bind_primary_app_window(force_refresh=True)

    assert selected is not None
    assert agent.active_window_handle == 22
    assert agent.active_window_title == "晴天 - 周杰伦"


def test_episode_resolves_exact_process_name_before_higher_risk_fuzzy_app(tmp_path: Path) -> None:
    class _CatalogRuntime(_FakeRuntime):
        app_catalog = SimpleNamespace(
            list_apps=lambda **_kwargs: {
                "apps": [
                    {"appId": "app_qq", "displayName": "QQ", "matchScore": 190, "processNames": ["QQ.exe"]},
                    {
                        "appId": "app_qqmusic",
                        "displayName": "QQ音乐",
                        "matchScore": 160,
                        "processNames": ["QQMusic.exe"],
                    },
                ]
            }
        )

    agent = ComputerUseEpisodeAgent(
        episode_id="episode_app",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动 QQ音乐"),
        runtime=_CatalogRuntime(),
    )

    assert agent._resolve_app("QQMusic")["appId"] == "app_qqmusic"


def test_episode_recognizes_explicit_chinese_process_exit_authorization(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_process",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="最后关闭 QQ音乐窗口并关闭本轮启动的 QQMusic 进程"),
        runtime=_FakeRuntime(),
    )

    assert agent._terminate_process_allowed is True


def test_episode_semantic_click_is_primary_even_when_model_supplies_coordinates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    class _ClickRuntime(_FakeRuntime):
        visual_locator_runtime = SimpleNamespace(is_available=lambda: True)

        def click(self, **kwargs):
            calls.append(dict(kwargs))
            return {"result": {"status": "completed", "message": "clicked", "target": {}}}

    agent = ComputerUseEpisodeAgent(
        episode_id="episode_click",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="点击下一首"),
        runtime=_ClickRuntime(),
    )
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("QQ音乐", {"appId": "app_qqmusic"}, "晴天 - 周杰伦", 42),
    )
    monkeypatch.setattr(agent, "_current_window_title", lambda: "晴天 - 周杰伦")

    agent._dispatch_desktop_click(
        {"app": "QQ音乐", "target": "底部播放控制栏的下一首按钮", "x": 0.53, "y": 0.95}
    )

    assert calls[0]["visual_locator"] == "底部播放控制栏的下一首按钮"
    assert calls[0]["point"] is None


def test_episode_uses_screenshot_coordinates_when_semantic_locator_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[dict] = []

    class _ClickRuntime(_FakeRuntime):
        visual_locator_runtime = SimpleNamespace(is_available=lambda: False)

        def click(self, **kwargs):
            calls.append(dict(kwargs))
            return {"result": {"status": "completed", "message": "clicked", "target": {}}}

    agent = ComputerUseEpisodeAgent(
        episode_id="episode_coordinate_click",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="点击下一首"),
        runtime=_ClickRuntime(),
    )
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("QQ音乐", {"appId": "app_qqmusic"}, "晴天 - 周杰伦", 42),
    )
    monkeypatch.setattr(agent, "_current_window_title", lambda: "晴天 - 周杰伦")

    result = agent._dispatch_desktop_click(
        {"app": "QQ音乐", "target": "下一首", "x": 0.53, "y": 0.95}
    )

    assert calls[0]["visual_locator"] is None
    assert calls[0]["point"] == [0.53, 0.95]
    assert result["semanticTargetAttempted"] is False
    assert result["semanticTargetUnavailable"] is True


def test_episode_keeps_first_process_baseline_across_same_app_relaunch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshots = iter([{91}, {91, 101}, {91, 102}])

    class _LaunchRuntime(_FakeRuntime):
        app_catalog = SimpleNamespace(
            list_apps=lambda **_kwargs: {
                "apps": [{
                    "appId": "app_qqmusic",
                    "displayName": "QQ音乐",
                    "matchScore": 100,
                    "processNames": ["QQMusic.exe"],
                    "launchCandidates": [{"role": "app_path", "executableName": "QQMusic.exe"}],
                }]
            }
        )
        driver = SimpleNamespace(list_windows=lambda **_kwargs: [])

        def open_app(self, **_kwargs):
            return {"result": {"status": "completed"}}

    agent = ComputerUseEpisodeAgent(
        episode_id="episode_relaunch",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动并关闭 QQ音乐"),
        runtime=_LaunchRuntime(),
    )
    monkeypatch.setattr(agent, "_process_snapshot", lambda _names: next(snapshots))
    monkeypatch.setattr(agent, "_current_app_state", lambda **_kwargs: {})
    monkeypatch.setattr(agent, "_bind_primary_app_window", lambda **_kwargs: None)
    monkeypatch.setattr("runtimes.computer_use.episode_agent.time.sleep", lambda _seconds: None)

    first = agent._dispatch_desktop_launch({"app": "QQ音乐"})
    second = agent._dispatch_desktop_launch({"app": "QQ音乐"})

    assert agent.app_baseline_pids == {91}
    assert first["ownedProcessIds"] == [101]
    assert second["ownedProcessIds"] == [102]


def test_episode_process_cleanup_catches_short_lived_relaunch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    snapshots = iter([{101}, {102}, set(), set(), set(), set()])
    monotonic_values = iter([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 2.0])
    terminated: list[int] = []

    class _Process:
        def __init__(self, pid: int) -> None:
            self.pid = pid

        def name(self) -> str:
            return "QQMusic.exe"

        def terminate(self) -> None:
            terminated.append(self.pid)

        def kill(self) -> None:
            raise AssertionError("graceful terminate should be sufficient")

    agent = ComputerUseEpisodeAgent(
        episode_id="episode_cleanup_relaunch",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="关闭本轮启动的 QQMusic 进程"),
        runtime=_FakeRuntime(),
    )
    agent.active_app = {
        "launchCandidates": [{"role": "app_path", "executableName": "QQMusic.exe"}],
    }
    agent.app_baseline_initialized = True
    monkeypatch.setattr(agent, "_process_snapshot", lambda _names: set(next(snapshots)))
    monkeypatch.setattr("runtimes.computer_use.episode_agent.psutil.Process", _Process)
    monkeypatch.setattr(
        "runtimes.computer_use.episode_agent.psutil.wait_procs",
        lambda processes, timeout: (list(processes), []),
    )
    monkeypatch.setattr(
        "runtimes.computer_use.episode_agent.time.monotonic",
        lambda: next(monotonic_values, 2.0),
    )
    monkeypatch.setattr("runtimes.computer_use.episode_agent.time.sleep", lambda _seconds: None)

    result = agent._terminate_owned_app_processes()

    assert terminated == [101, 102]
    assert result["terminated"] == [101, 102]
    assert result["remainingOwnedProcessIds"] == []
    assert result["remainingProcessIds"] == []


def test_computer_use_storage_replace_retries_transient_windows_lock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.tmp"
    target = tmp_path / "computer_use.json"
    source.write_text("payload", encoding="utf-8")
    attempts: list[int] = []
    real_replace = os.replace

    def _replace(current: Path, destination: Path) -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise PermissionError("transient Windows file lock")
        real_replace(current, destination)

    monkeypatch.setattr("core.storage.os.replace", _replace)
    monkeypatch.setattr("core.storage.time.sleep", lambda _seconds: None)

    StorageManager._replace_computer_use_file(source, target)

    assert attempts == [1, 2, 3]
    assert target.read_text(encoding="utf-8") == "payload"


def test_episode_music_playback_requires_search_result_play_and_no_remaining_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_switch",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动 QQ音乐，搜索歌曲、进入播放页、点击播放，最后关闭本轮启动的进程"),
        runtime=_FakeRuntime(),
    )
    agent.active_app_query = "QQ音乐"
    agent.active_app = {"launchCandidates": [{"role": "app_path", "executableName": "QQMusic.exe"}]}
    agent.app_closed = True
    monkeypatch.setattr(agent, "_process_snapshot", lambda _names: set())
    agent.actions = [
        {
            "index": 1,
            "tool": "desktop_launch",
            "args": {"app": "QQ音乐"},
            "ok": True,
            "result": "{}",
        },
        {
            "index": 2,
            "tool": "desktop_input",
            "args": {"text": "晴天 周杰伦", "submit": True},
            "ok": True,
            "result": "{}",
        },
        {
            "index": 3,
            "tool": "desktop_click",
            "args": {"target": "第一条歌曲结果‘晴天’"},
            "ok": True,
            "result": "{}",
        },
        {
            "index": 4,
            "tool": "desktop_click",
                "args": {"target": "底部播放器栏播放按钮"},
                "ok": True,
            "result": '{"afterWindowTitle": "晴天 - 周杰伦"}',
        },
        {"index": 5, "tool": "desktop_close", "args": {"terminate_process": True}, "ok": True, "result": "{}"},
    ]

    assert agent._validate_completion()["passed"] is True
    monkeypatch.setattr(agent, "_process_snapshot", lambda _names: {123})
    blocked = agent._validate_completion()
    assert blocked["passed"] is False
    assert "application_processes_still_running:[123]" in blocked["missing"]

    monkeypatch.setattr(agent, "_process_snapshot", lambda _names: set())
    agent.actions = [item for item in agent.actions if item.get("tool") != "desktop_click"]
    missing_actions = agent._validate_completion()
    assert missing_actions["passed"] is False
    assert "song_result_action_not_identified" in missing_actions["missing"]
    assert "play_action_not_identified" in missing_actions["missing"]


def test_episode_music_accepts_verified_recovery_path_after_early_wrong_play(tmp_path: Path) -> None:
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_recovery",
        session_id="session_test",
        run_id="run_test",
        user_id="user_test",
        project_id="project_test",
        workspace_id="workspace_test",
        workspace_path=str(tmp_path),
        task_brief=_brief(goal="启动 QQ音乐，搜索歌曲、进入播放页、点击播放，最后关闭本轮启动的进程"),
        runtime=_FakeRuntime(),
    )
    agent.active_app_query = "QQ音乐"
    agent.active_app = {"launchCandidates": [{"role": "app_path", "executableName": "QQMusic.exe"}]}
    agent.app_closed = True
    agent._process_snapshot = lambda _names: set()
    agent.actions = [
        {"index": 1, "tool": "desktop_launch", "args": {"app": "QQ音乐"}, "ok": True},
        {"index": 2, "tool": "desktop_click", "args": {"target": "底部播放器栏播放按钮"}, "ok": True},
        {"index": 3, "tool": "desktop_input", "args": {"text": "晴天 周杰伦", "submit": True}, "ok": True},
        {
            "index": 4,
            "tool": "desktop_click",
            "args": {"target": "第一条歌曲结果 晴天 - 周杰伦"},
            "ok": True,
            "result": '{"afterWindowTitle":"晴天 - 周杰伦"}',
        },
        {"index": 5, "tool": "desktop_close", "args": {"terminate_process": True}, "ok": True},
        {"index": 6, "tool": "desktop_launch", "args": {"app": "QQ音乐"}, "ok": True},
        {
            "index": 7,
            "tool": "desktop_click",
            "args": {"target": "底部播放器栏播放按钮"},
            "ok": True,
            "result": '{"afterWindowTitle":"晴天 - 周杰伦"}',
        },
        {"index": 8, "tool": "desktop_close", "args": {"terminate_process": True}, "ok": True},
    ]

    verification = agent._validate_completion()
    assert verification["passed"] is True
    assert "desktop_action_sequence_invalid" not in verification["missing"]


def test_runtime_episode_runner_executes_task_briefs_instead_of_observe(monkeypatch, tmp_path: Path) -> None:
    import runtimes.computer_use.episode_agent as episode_agent_module

    captured: dict = {}

    def _execute(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "status": "completed",
            "summary": "done",
            "artifactRefs": ["workspace:proof/result.jpg"],
            "proofRefs": ["workspace:.v8-agent-os/proof.jpg"],
            "verification": {"passed": True},
            "actions": [],
        }

    monkeypatch.setattr(episode_agent_module, "execute_computer_use_task_brief", _execute)
    runner = RuntimeEpisodeRunner()
    episode = {
        "episodeId": "episode_task",
        "session_id": "session_task",
        "run_id": "run_task",
        "need": {"kind": "computer_use"},
        "inputs": {
            "workspacePath": str(tmp_path),
            "workspaceId": "workspace_test",
            "projectId": "project_test",
            "taskBriefs": [_brief(write_set=["proof/result.jpg"], goal="do it")],
        },
    }

    handoff = asyncio.run(runner._execute_computer_use(episode))

    assert captured["workspace_path"] == str(tmp_path)
    assert captured["task_brief"]["taskBriefId"] == "computer-use-test"
    assert handoff["status"] == "ready"
    assert handoff["artifactRefs"] == ["workspace:proof/result.jpg"]
    assert handoff["verificationResults"] == [{"passed": True}]
