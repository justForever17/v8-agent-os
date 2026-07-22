from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image

from erc.safety_guardian import DEFAULT_SAFETY_GUARDIAN_CONFIG, SafetyGuardian
from runtimes.computer_use.episode_agent import ComputerUseEpisodeAgent
from runtimes.computer_use.app_profiles import ComputerUseAppProfiles
from runtimes.computer_use.observation_bundle import build_observation_bundle
from runtimes.computer_use.post_action_visual_check import summarize_semantic_post_action_verification
from runtimes.computer_use.runtime import ComputerUseRuntime
from runtimes.computer_use.selector_memory import ComputerUseSelectorMemory
from runtimes.computer_use.shortcut_registry import (
    ComputerUseShortcutRegistry,
    ShortcutRegistryError,
    compile_human_shortcut,
    shortcut_registry,
)
from runtimes.computer_use.shortcut_research import ComputerUseShortcutResearch
from runtimes.computer_use.types import ComputerUseActionResult


def _brief() -> dict:
    return {
        "taskBriefId": "shortcut-test",
        "goal": "启动 QQ音乐，搜索晴天并进入播放页，播放后关闭窗口",
        "context": "执行真实桌面操作。",
        "writeSet": [],
        "acceptanceContract": [],
        "constraints": [],
    }


def test_semantic_interaction_lookup_rejects_unscoped_coordinate_history(monkeypatch: pytest.MonkeyPatch) -> None:
    memory = ComputerUseSelectorMemory()
    monkeypatch.setattr(
        memory,
        "_load",
        lambda: {
            "apps": {
                "app_qqmusic": {
                    "interactions": [
                        {
                            "match": {"actionName": "click"},
                            "patch": {"point": [0.535, 0.92]},
                            "source": "learned_coordinate_interaction",
                            "weight": 96,
                        },
                        {
                            "match": {
                                "actionName": "click",
                                "selectorKey": "search_result_first_song",
                                "targetText": "晴天 周杰伦",
                            },
                            "patch": {"point": [0.195, 0.4]},
                            "source": "learned_coordinate_interaction",
                            "weight": 36,
                        },
                    ]
                }
            }
        },
    )

    result = memory.get_interaction_patch(
        app_id="app_qqmusic",
        action_name="click",
        selector_key="search_result_first_song",
        target_text="晴天 周杰伦",
    )

    assert result["patch"]["point"] == [0.195, 0.4]
    assert len(result["matches"]) == 1
    assert result["matches"][0]["match"]["selectorKey"] == "search_result_first_song"


class _ShortcutDriver:
    def focus_window(self, **_kwargs):
        return {"handle": 42, "title": "晴天 - 周杰伦"}

    def observe_desktop(self, **_kwargs):
        return SimpleNamespace(
            as_dict=lambda: {
                "windowTitle": "晴天 - 周杰伦",
                "focusedElementId": "window",
                "elements": [{"elementId": "window", "role": "Window"}],
            }
        )


class _ShortcutRuntime:
    browser_automation = SimpleNamespace()

    def __init__(self) -> None:
        self.driver = _ShortcutDriver()
        self.calls: list[dict] = []

    def hotkey(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "result": {
                "status": "completed",
                "verification": {
                    "passed": True,
                    "status": "registered_shortcut_verified",
                    "level": "verified",
                    "details": {"stateChanged": True},
                },
            }
        }


class _ShortcutResearch:
    def research(self, **kwargs):
        app = dict(kwargs["app"])
        return {
            "status": "found",
            "appBinding": {
                "appId": app["appId"],
                "displayName": app["displayName"],
                "processNames": list(app["processNames"]),
            },
            "platform": "windows",
            "action": kwargs["action"],
            "allowedSources": ["https://support.example.test/demo/shortcuts"],
            "selectedSource": {
                "url": "https://support.example.test/demo/shortcuts",
                "excerpt": "Press Space to play or pause.",
            },
        }


def _agent(tmp_path, runtime=None) -> ComputerUseEpisodeAgent:
    return ComputerUseEpisodeAgent(
        episode_id="episode_shortcut",
        session_id="session_shortcut",
        run_id="run_shortcut",
        user_id="user_shortcut",
        project_id="project_shortcut",
        workspace_id="workspace_shortcut",
        workspace_path=str(tmp_path),
        task_brief=_brief(),
        runtime=runtime or _ShortcutRuntime(),
    )


def _memory_registry() -> tuple[ComputerUseShortcutRegistry, dict]:
    learned: dict = {"version": 1, "applications": {}}

    def _load() -> dict:
        return deepcopy(learned)

    def _save(payload: dict) -> None:
        learned.clear()
        learned.update(deepcopy(payload))

    return (
        ComputerUseShortcutRegistry(
            learned_profile_loader=_load,
            learned_profile_saver=_save,
        ),
        learned,
    )


def test_registry_projects_only_current_platform_and_app_override() -> None:
    guide = shortcut_registry.guide_for(
        app={"appId": "app_qqmusic", "runningWindows": [{"processName": "QQMusic.exe"}]},
        platform="windows",
    )

    assert guide["platform"] == "windows"
    assert guide["matchedApplication"]["guideId"] == "qqmusic.desktop"
    assert guide["applicationProfile"]["availableActions"] == ["play_pause"]
    assert guide["applicationProfile"]["researchOnMissingAction"] is True
    assert "currently covers only: play_pause" in guide["applicationProfile"]["warning"]
    play = next(item for item in guide["shortcuts"] if item["id"] == "media.play_pause")
    assert play["keys"] == "Space"
    assert play["provenance"] == "local_application_validation"
    assert guide["priorityOrder"] == [
        "registered_shortcut",
        "semantic_control",
        "visual_locator",
        "coordinate",
    ]


def test_registry_rejects_unknown_shortcut() -> None:
    with pytest.raises(ShortcutRegistryError, match="not registered"):
        shortcut_registry.resolve("media.secret_toggle", app={"appId": "app_qqmusic"}, platform="windows")


def test_missing_app_profile_warns_agent_but_unbound_state_does_not() -> None:
    unbound = shortcut_registry.guide_for(app=None, platform="windows")
    unknown = shortcut_registry.guide_for(
        app={"appId": "app_demo", "processNames": ["demo.exe"]},
        platform="windows",
    )

    assert unbound["applicationProfile"]["status"] == "unavailable"
    assert unbound["applicationProfile"]["warning"] is None
    assert unknown["applicationProfile"]["status"] == "missing"
    assert "desktop_shortcut_research" in unknown["applicationProfile"]["warning"]


def test_failed_app_coordinate_action_injects_research_recovery_guidance(tmp_path) -> None:
    agent = _agent(tmp_path)
    agent.actions = [
        {
            "index": 1,
            "tool": "desktop_input",
            "args": {"app": "QQ音乐", "target": "顶部搜索框"},
            "ok": False,
        }
    ]
    policy = shortcut_registry.guide_for(
        app={"appId": "app_qqmusic", "runningWindows": [{"processName": "QQMusic.exe"}]},
        platform="windows",
    )

    guidance = agent._shortcut_recovery_guidance(policy)

    assert guidance["status"] == "research_recommended"
    assert guidance["failedTarget"] == "顶部搜索框"
    assert "desktop_shortcut_research" in guidance["instruction"]


def test_human_shortcut_compiler_accepts_one_app_chord_and_rejects_global_or_printable_keys() -> None:
    assert compile_human_shortcut("Ctrl+L", platform="windows") == {
        "displaySequence": "Ctrl+L",
        "driverSequence": "^l",
    }
    assert compile_human_shortcut("Space", platform="windows")["driverSequence"] == "{SPACE}"
    with pytest.raises(ShortcutRegistryError, match="bare printable"):
        compile_human_shortcut("F", platform="windows")
    with pytest.raises(ShortcutRegistryError, match="cannot be learned"):
        compile_human_shortcut("Alt+F4", platform="windows")
    with pytest.raises(ShortcutRegistryError, match="cannot be learned"):
        compile_human_shortcut("Win+R", platform="windows")


def test_verified_learned_profile_is_auto_injected_and_resolved() -> None:
    registry, learned = _memory_registry()
    app = {
        "appId": "app_demo_player",
        "displayName": "Demo Player",
        "processNames": ["demo-player.exe"],
    }

    profile = registry.learn_verified_shortcut(
        app=app,
        platform="windows",
        shortcut_id="media.play_pause",
        action="play_pause",
        keys="Space",
        source_url="https://docs.example.test/player/shortcuts",
        verification={"passed": True, "details": {"stateChanged": True}},
    )
    guide = registry.guide_for(app=app, platform="windows")
    resolved = registry.resolve("media.play_pause", app=app, platform="windows")

    assert profile["guideId"] in learned["applications"]
    assert guide["applicationProfile"]["status"] == "bound"
    assert guide["applicationProfile"]["source"] == "learned"
    assert resolved["driverSequence"] == "{SPACE}"
    assert resolved["confidence"] == "runtime_verified"


def test_unverified_shortcut_cannot_be_persisted() -> None:
    registry, learned = _memory_registry()
    with pytest.raises(ShortcutRegistryError, match="verified application state change"):
        registry.learn_verified_shortcut(
            app={"appId": "app_demo", "displayName": "Demo", "processNames": ["demo.exe"]},
            platform="windows",
            shortcut_id="media.play_pause",
            action="play_pause",
            keys="Space",
            source_url="https://docs.example.test/shortcuts",
            verification={"passed": True, "details": {"stateChanged": False}},
        )
    assert learned["applications"] == {}


def test_shortcut_research_reuses_web_broker_and_returns_bounded_sources() -> None:
    calls: list[dict] = []

    def _broker(**kwargs):
        calls.append(dict(kwargs))
        if kwargs["mode"] == "search":
            return json.dumps(
                {
                    "ok": True,
                    "results": [
                        {
                            "title": "Demo Player keyboard shortcuts - Help",
                            "url": "https://support.example.test/demo/shortcuts",
                            "snippet": "Press Space to play or pause.",
                            "relevanceScore": 90,
                            "sourceQualityHints": {"authorityScore": 80},
                        }
                    ],
                }
            )
        return json.dumps(
            {
                "ok": True,
                "title": "Keyboard shortcuts",
                "textPreview": "Press Space to play or pause the current track.",
            }
        )

    result = ComputerUseShortcutResearch(broker=_broker).research(
        app={"appId": "app_demo", "displayName": "Demo Player", "processNames": ["demo.exe"]},
        action="play or pause",
        platform="windows",
        tool_call_id="shortcut-research-test",
    )

    assert [call["mode"] for call in calls] == ["search", "read"]
    assert result["allowedSources"] == ["https://support.example.test/demo/shortcuts"]
    assert "Press Space" in result["selectedSource"]["excerpt"]
    assert result["untrustedEvidence"] is True


def test_app_profiles_reference_registered_shortcuts_instead_of_raw_sequences() -> None:
    profiles = ComputerUseAppProfiles()
    assert profiles.get("explorer").selectors["address_bar"]["focus_shortcut_id"] == "app.focus_location"
    assert profiles.get("edge").selectors["address_bar"]["focus_shortcut_id"] == "browser.focus_location"
    qq_search = profiles.selector_for_target("app_qqmusic", "顶部搜索音乐输入框")
    assert qq_search["selector_key"] == "search_box"
    assert qq_search["point_rect"] == [0.20, 0.012, 0.45, 0.065]
    qq_result = profiles.selector_for_target("app_qqmusic", "第一条歌曲结果")
    assert qq_result["selector_key"] == "search_result_first_song"
    assert qq_result["preferred_point"] == [0.195, 0.40]
    assert qq_result["activation"] == "double_click"
    assert all(
        "focus_hotkey_sequence" not in selector
        for profile in profiles.list_profiles()
        for selector in profile.selectors.values()
    )


def test_coordinate_priority_is_narrow_enough_for_song_result_selection() -> None:
    app = {"appId": "app_qqmusic"}

    assert shortcut_registry.preferred_for_target(
        "第一条歌曲结果的行内播放按钮", app=app, platform="windows"
    ) is None
    preferred = shortcut_registry.preferred_for_target(
        "底部播放器栏绿色播放控制", app=app, platform="windows"
    )
    assert preferred and preferred["id"] == "media.play_pause"


def test_episode_dispatches_registered_shortcut_with_root_goal(monkeypatch, tmp_path) -> None:
    runtime = _ShortcutRuntime()
    agent = _agent(tmp_path, runtime)
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("QQ音乐", {"appId": "app_qqmusic"}, "晴天 - 周杰伦", 42),
    )
    monkeypatch.setattr("runtimes.computer_use.episode_agent.time.sleep", lambda _seconds: None)

    result = agent._dispatch_desktop_shortcut(
        {"app": "QQ音乐", "shortcut_id": "media.play_pause"}
    )

    assert result["verification"]["passed"] is True
    assert result["stateChanged"] is True
    assert runtime.calls[0]["sequence"] == "{SPACE}"
    assert runtime.calls[0]["shortcut_resolution"]["guideId"] == "qqmusic.desktop"
    assert runtime.calls[0]["invocation_metadata"]["rootGoal"] == _brief()["goal"]


def test_episode_researches_then_auto_binds_only_after_verified_execution(monkeypatch, tmp_path) -> None:
    registry, learned = _memory_registry()
    runtime = _ShortcutRuntime()
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_learn_shortcut",
        session_id="session_shortcut",
        run_id="run_shortcut",
        user_id="user_shortcut",
        project_id="project_shortcut",
        workspace_id="workspace_shortcut",
        workspace_path=str(tmp_path),
        task_brief=_brief(),
        runtime=runtime,
        shortcut_registry_instance=registry,
        shortcut_research_instance=_ShortcutResearch(),
    )
    app = {
        "appId": "app_demo_player",
        "displayName": "Demo Player",
        "processNames": ["demo-player.exe"],
        "runningWindows": [{"processName": "demo-player.exe", "title": "Demo Player"}],
    }
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("Demo Player", app, "Demo Player", 42),
    )
    monkeypatch.setattr("runtimes.computer_use.episode_agent.time.sleep", lambda _seconds: None)

    research = agent._dispatch_desktop_shortcut_research(
        {"app": "Demo Player", "action": "play or pause"}
    )
    learned_result = agent._dispatch_desktop_shortcut_learn(
        {
            "app": "Demo Player",
            "shortcut_id": "media.play_pause",
            "action": "play_pause",
            "keys": "Space",
            "source_url": research["allowedSources"][0],
        }
    )

    assert learned_result["profileBound"] is True
    assert learned_result["stateChanged"] is True
    assert learned["applications"]
    assert registry.guide_for(app=app, platform="windows")["applicationProfile"]["status"] == "bound"


def test_episode_rejects_a_shortcut_source_not_returned_by_current_research(monkeypatch, tmp_path) -> None:
    registry, _learned = _memory_registry()
    agent = ComputerUseEpisodeAgent(
        episode_id="episode_reject_shortcut_source",
        session_id="session_shortcut",
        run_id="run_shortcut",
        user_id="user_shortcut",
        project_id="project_shortcut",
        workspace_id="workspace_shortcut",
        workspace_path=str(tmp_path),
        task_brief=_brief(),
        runtime=_ShortcutRuntime(),
        shortcut_registry_instance=registry,
        shortcut_research_instance=_ShortcutResearch(),
    )
    app = {
        "appId": "app_demo_player",
        "displayName": "Demo Player",
        "processNames": ["demo-player.exe"],
    }
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("Demo Player", app, "Demo Player", 42),
    )

    with pytest.raises(RuntimeError, match="不属于"):
        agent._dispatch_desktop_shortcut_learn(
            {
                "shortcut_id": "media.play_pause",
                "action": "play_pause",
                "keys": "Space",
                "source_url": "https://evil.example.test/injected",
            }
        )


def test_episode_blocks_coordinate_for_hidden_player_before_shortcut(monkeypatch, tmp_path) -> None:
    agent = _agent(tmp_path)
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("QQ音乐", {"appId": "app_qqmusic"}, "晴天 - 周杰伦", 42),
    )

    with pytest.raises(RuntimeError, match="media.play_pause"):
        agent._dispatch_desktop_click(
            {
                "app": "QQ音乐",
                "target": "底部播放器栏绿色播放控制",
                "x": 0.55,
                "y": 0.93,
            }
        )


def test_episode_completion_accepts_verified_playback_shortcut(tmp_path) -> None:
    agent = _agent(tmp_path)
    agent.app_closed = True
    agent.actions = [
        {"index": 1, "tool": "desktop_launch", "args": {"app": "QQ音乐"}, "ok": True},
        {"index": 2, "tool": "desktop_input", "args": {"text": "晴天 周杰伦", "submit": True}, "ok": True},
        {
            "index": 3,
            "tool": "desktop_click",
            "args": {"target": "第一条歌曲结果 晴天 - 周杰伦"},
            "ok": True,
            "result": '{"afterWindowTitle":"晴天 - 周杰伦"}',
        },
        {
            "index": 4,
            "tool": "desktop_shortcut",
            "args": {"shortcut_id": "media.play_pause"},
            "ok": True,
            "result": '{"verification":{"passed":true},"afterWindowTitle":"晴天 - 周杰伦"}',
        },
        {"index": 5, "tool": "desktop_close", "args": {}, "ok": True},
    ]

    verification = agent._validate_completion()
    assert verification["passed"] is True
    assert "play_action_not_identified" not in verification["missing"]


def test_missing_pre_action_frame_cannot_fake_click_progress() -> None:
    bundle = build_observation_bundle(
        action_type="click",
        action_payload={"target_text": "search"},
        route=None,
        before_observation=None,
        mid_observation=None,
        after_observation={
            "windowTitle": "Demo",
            "treeHash": "tree-after",
            "screenHash": "screen-after",
            "metadata": {"windowHandle": 42},
        },
    )
    semantic = summarize_semantic_post_action_verification(
        action_type="click",
        action_payload={"target_text": "search"},
        verification_details={},
        observation_bundle=bundle,
    )

    assert bundle["diff"]["stateAdvanced"] is False
    assert semantic["passed"] is False
    assert semantic["status"] == "semantic_click_unconfirmed"


def test_coordinate_actions_capture_pre_action_observation() -> None:
    runtime = object.__new__(ComputerUseRuntime)

    for action_type in ("click", "double_click", "right_click", "hover", "drag"):
        assert runtime._should_capture_pre_action_observation(
            action_type=action_type,
            action_payload={},
        ) is True


def test_windows_non_ascii_text_prefers_restoring_clipboard_sendinput() -> None:
    assert ComputerUseRuntime._resolve_sendinput_text_preference({}, "晴天 周杰伦", platform_name="nt") is True
    assert ComputerUseRuntime._resolve_sendinput_text_preference({}, "Jay Chou", platform_name="nt") is False
    assert (
        ComputerUseRuntime._resolve_sendinput_text_preference(
            {"prefer_sendinput_text": False},
            "晴天 周杰伦",
            platform_name="nt",
        )
        is False
    )


def test_episode_rejects_immediate_repeat_of_verified_toggle_shortcut(monkeypatch, tmp_path) -> None:
    agent = _agent(tmp_path)
    agent.actions = [
        {
            "index": 1,
            "tool": "desktop_shortcut",
            "args": {"shortcut_id": "media.play_pause"},
            "ok": True,
        }
    ]
    monkeypatch.setattr(
        agent,
        "_desktop_action_app",
        lambda _requested: ("QQ音乐", {"appId": "app_qqmusic"}, "晴天 - 周杰伦", 42),
    )

    with pytest.raises(RuntimeError, match="刚刚已验证成功"):
        agent._dispatch_desktop_shortcut(
            {"app": "QQ音乐", "shortcut_id": "media.play_pause"}
        )


def test_action_journal_keeps_structured_shortcut_evidence_when_result_is_truncated(tmp_path) -> None:
    agent = _agent(tmp_path)
    item = agent._record_action(
        name="desktop_shortcut",
        args={"shortcut_id": "media.play_pause"},
        ok=True,
        result={
            "verification": {
                "passed": True,
                "status": "registered_shortcut_verified",
                "level": "verified",
                "details": {"stateChanged": True, "beforeScreenHash": "before", "afterScreenHash": "after"},
            },
            "stateChanged": True,
            "shortcutResolution": {
                "id": "media.play_pause",
                "guideId": "qqmusic.desktop",
                "driverSequence": "{SPACE}",
            },
            "rawPayload": "x" * 5000,
        },
    )

    assert item["result"].endswith("…")
    assert item["evidence"]["verification"]["passed"] is True
    assert item["evidence"]["stateChanged"] is True
    assert agent._decode_action_result(item)["shortcutResolution"]["id"] == "media.play_pause"


def test_app_scoped_visual_region_verifies_shortcut_without_accepting_neighbor_control() -> None:
    runtime = object.__new__(ComputerUseRuntime)
    before = Image.new("RGB", (100, 100), "white")
    after_inside = before.copy()
    after_outside = before.copy()
    for x in range(54, 58):
        for y in range(89, 95):
            after_inside.putpixel((x, y), (0, 0, 0))
    for x in range(45, 49):
        for y in range(89, 95):
            after_outside.putpixel((x, y), (0, 0, 0))
    config = {
        "normalizedRegion": [0.535, 0.88, 0.58, 0.955],
        "pixelDeltaThreshold": 24,
        "minimumChangedPixelRatio": 0.005,
    }

    inside = runtime._shortcut_visual_change_evidence(before=before, after=after_inside, config=config)
    outside = runtime._shortcut_visual_change_evidence(before=before, after=after_outside, config=config)

    assert inside["passed"] is True
    assert outside["passed"] is False


def test_coordinate_input_visual_region_is_scoped_around_target_point() -> None:
    config = ComputerUseRuntime._point_visual_verification_config({"point": [0.32, 0.04]})

    assert config["normalizedRegion"] == pytest.approx([0.18, 0.0, 0.46, 0.08])
    assert config["minimumChangedPixelRatio"] == 0.002


def test_episode_does_not_mark_failed_verification_as_success() -> None:
    assert ComputerUseEpisodeAgent._tool_result_ok(
        {"status": "completed", "verification": {"passed": False}}
    ) is False


def test_runtime_requires_state_change_for_registered_toggle_shortcut() -> None:
    runtime = object.__new__(ComputerUseRuntime)
    action_payload = {
        "window_handle": 42,
        "shortcut_resolution": {
            "id": "media.play_pause",
            "guideId": "qqmusic.desktop",
            "platform": "windows",
            "displaySequence": "Space",
            "driverSequence": "{SPACE}",
            "stateChangeRequired": True,
            "preconditions": ["window_focused", "not_text_input"],
            "preconditionEvidence": {"windowBound": True, "windowFocused": True},
        },
    }
    result = ComputerUseActionResult(
        action_id="shortcut_action",
        action_type="hotkey",
        status="completed",
        message="sent",
        target={"windowHandle": 42, "windowTitle": "晴天 - 周杰伦"},
    )

    failed = runtime._verify_action_result(
        action_type="hotkey",
        action_payload=action_payload,
        result=result,
        before_observation={"treeHash": "same", "screenHash": "same"},
        after_observation={"treeHash": "same", "screenHash": "same"},
    )
    passed = runtime._verify_action_result(
        action_type="hotkey",
        action_payload=action_payload,
        result=result,
        before_observation={"treeHash": "before", "screenHash": "before"},
        after_observation={"treeHash": "after", "screenHash": "after"},
    )

    assert failed.passed is False
    assert failed.status == "registered_shortcut_state_unconfirmed"
    assert passed.passed is True
    assert passed.status == "registered_shortcut_verified"

    result.metadata["shortcutVisualEvidence"] = {
        "available": True,
        "passed": True,
        "changedPixelRatio": 0.0148,
    }
    visually_verified = runtime._verify_action_result(
        action_type="hotkey",
        action_payload=action_payload,
        result=result,
        before_observation={"treeHash": "same", "screenHash": "same"},
        after_observation={"treeHash": "same", "screenHash": "same"},
    )

    assert visually_verified.passed is True
    assert visually_verified.details["visualEvidence"]["passed"] is True


def test_only_registered_task_authorized_window_close_bypasses_hotkey_review(monkeypatch) -> None:
    guardian = SafetyGuardian()
    monkeypatch.setattr(guardian, "_config", lambda: deepcopy(DEFAULT_SAFETY_GUARDIAN_CONFIG))
    registered = guardian.assess_computer_use_action(
        action_type="hotkey",
        target={
            "sequence": "%{F4}",
            "shortcut_id": "window.close",
            "shortcut_preconditions": {
                "windowBound": True,
                "windowFocused": True,
                "taskAuthorizesClose": True,
            },
        },
    )
    arbitrary = guardian.assess_computer_use_action(
        action_type="hotkey",
        target={"sequence": "%{F4}"},
    )

    assert registered.verdict == "allow"
    assert registered.risk_code == "computer_use_registered_window_close"
    assert arbitrary.verdict == "review"
    assert arbitrary.risk_code == "computer_use_hotkey_review"
