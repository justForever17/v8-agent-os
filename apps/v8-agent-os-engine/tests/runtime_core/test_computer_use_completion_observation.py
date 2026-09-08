from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from runtimes.computer_use.episode_agent import ComputerUseEpisodeAgent


@pytest.fixture
def agent(tmp_path):
    return ComputerUseEpisodeAgent(
        episode_id="episode_observation", session_id="session_observation", run_id="run_observation",
        user_id="user_fixture", project_id=None, workspace_id=None, workspace_path=str(tmp_path),
        task_brief={"taskBriefId": "open-browser", "goal": "打开指定的宿主浏览器并访问页面",
                    "acceptanceContract": ["目标页面可见"], "writeSet": []},
        runtime=SimpleNamespace(browser_automation=SimpleNamespace()),
        shortcut_registry_instance=SimpleNamespace(guide_for=lambda **_kwargs: {}),
    )


def present_frame(agent, monkeypatch, round_index=1):
    frame = agent._frame_directory() / f"round-{round_index:02d}-desktop.png"
    Image.new("RGB", (32, 24), color="blue").save(frame)
    monkeypatch.setattr(agent, "_browser_target_alive", lambda: False)
    monkeypatch.setattr(agent, "_capture_desktop_frame", lambda _round: frame)
    context, captured = agent._current_context(round_index)
    messages = agent._model_messages(round_index=round_index, context=context, frame=captured)
    return frame, messages, agent._current_observation["id"]


def complete(observation_id):
    return {"status": "completed", "observation_id": observation_id,
            "summary": "当前目标已满足", "evidence": "当前截图所示目标页面已可见，无需再次启动。"}


def test_running_only_application_binds_its_title_not_a_larger_host_window(agent):
    agent.active_app_query = "Owned fixture"
    agent.active_app = {"displayName": "Owned fixture", "processNames": ["powershell.exe"], "launchCandidates": []}
    assert agent._primary_process_names() == {"powershell.exe"}
    windows = [
        {"handle": 1, "title": "Unrelated console", "bounds": [0, 0, 2000, 1400], "isVisible": True},
        {"handle": 2, "title": "Owned fixture", "bounds": [0, 0, 700, 400], "isVisible": True},
    ]
    agent.runtime.driver = SimpleNamespace(list_windows=lambda **_kwargs: windows)
    assert agent._bind_primary_app_window(force_refresh=True)["handle"] == 2
    windows[1]["title"] = "Owned fixture - changed state"
    assert agent._bind_primary_app_window(force_refresh=True)["handle"] == 2
    windows.pop()
    assert agent._bind_primary_app_window(force_refresh=True) is None
    assert agent.active_window_handle is None


def test_unrecognized_browser_goal_is_not_automatically_completed_or_forced_to_finish(agent):
    verification = agent._validate_completion()
    assert verification["machineConstraintsPassed"] is True
    assert verification["passed"] is False
    assert verification["completionBasis"] == "unverified"
    assert {tool.name for tool in agent._tools_for_next_round()} >= {"desktop_launch", "browser_open", "finish_task"}
    result = agent._dispatch("finish_task", {"summary": "全部完成", "evidence": "已打开目标页面"})
    assert result["accepted"] is False
    assert agent._finished_summary is None


def test_actual_image_is_sent_and_zero_action_completion_is_labeled_as_agent_assessment(agent, monkeypatch):
    frame, messages, observation_id = present_frame(agent, monkeypatch)
    image = next(block for block in messages[1].content if block.get("type") == "image_url")
    assert base64.b64decode(image["image_url"]["url"].split(",", 1)[1]) == frame.read_bytes()
    monkeypatch.setattr(agent, "_record_final_observation", lambda: None)
    result = agent._dispatch("finish_task", complete(observation_id))
    assert result["accepted"] is True and result["verification"]["passed"] is True
    assert not agent.actions
    assert result["verification"]["completionBasis"] == "agent_assessment_with_current_observation"
    assert result["verification"]["agentAssessment"]["observationId"] == observation_id


@pytest.mark.parametrize("fault", ["forged_ref", "not_presented", "action_after_frame", "frame_changed", "missing_evidence"])
def test_prose_or_stale_frame_cannot_become_completion_proof(agent, monkeypatch, fault):
    frame, _, observation_id = present_frame(agent, monkeypatch)
    payload = complete(observation_id)
    if fault == "forged_ref":
        payload["observation_id"] = "cuobs:other_episode:1:invented"
    elif fault == "not_presented":
        agent._current_observation["presented"] = False
    elif fault == "action_after_frame":
        agent.actions.append({"tool": "desktop_input", "ok": True})
    elif fault == "frame_changed":
        Image.new("RGB", (32, 24), color="red").save(frame)
    else:
        payload["evidence"] = ""
    monkeypatch.setattr(agent, "_record_final_observation", lambda: pytest.fail("invalid proof must not be published"))
    assert agent._dispatch("finish_task", payload)["accepted"] is False
    assert agent._validate_completion()["passed"] is False


def test_missing_frame_allows_honest_blocked_report(agent, monkeypatch):
    monkeypatch.setattr(agent, "_browser_target_alive", lambda: False)
    monkeypatch.setattr(agent, "_capture_desktop_frame", lambda _round: None)
    context, frame = agent._current_context(1)
    messages = agent._model_messages(round_index=1, context=context, frame=frame)
    assert isinstance(messages[1].content, str)
    assert agent._dispatch("finish_task", complete("made-up"))["accepted"] is False
    blocked = agent._dispatch("finish_task", {"status": "blocked", "summary": "当前无法获取屏幕，不能确认页面状态"})
    assert blocked["accepted"] is True and blocked["status"] == "blocked"
    assert blocked["verification"]["passed"] is False


def test_invalid_png_bytes_do_not_count_as_a_model_observation(agent, monkeypatch):
    frame = agent._frame_directory() / "round-01-desktop.png"
    frame.write_bytes(b"not a screenshot")
    monkeypatch.setattr(agent, "_browser_target_alive", lambda: False)
    monkeypatch.setattr(agent, "_capture_desktop_frame", lambda _round: frame)
    context, actual = agent._current_context(1)
    assert actual is None and agent._current_observation is None
    assert '"available": false' in context


def test_browser_capture_failure_keeps_error_category_without_private_details(agent, monkeypatch):
    monkeypatch.setattr(agent, "_browser_target_alive", lambda: True)
    monkeypatch.setattr(agent, "_browser_page_snapshot", lambda: {})
    monkeypatch.setattr(agent, "_capture_browser_frame", lambda _round: (_ for _ in ()).throw(
        PermissionError("private profile path and authentication details")))
    context, frame = agent._current_context(1)
    assert frame is None and agent._current_observation is None
    assert json.loads(context)["currentFrame"] == {
        "available": False, "reason": "screenshot_capture_failed", "errorType": "PermissionError"}
    assert "private profile" not in context


def test_only_final_frame_is_registered_with_lineage_and_without_auto_attachment(agent, monkeypatch, tmp_path):
    from core.artifact_store import ArtifactStore
    from core.database import DatabaseManager
    import core.artifact_store as artifact_module

    manager = DatabaseManager(tmp_path / "isolated-db" / "state.db")
    manager.create_or_update_session(agent.session_id, "fixture", user_id=agent.user_id)
    manager.create_run_record(agent.run_id, agent.session_id, user_id=agent.user_id)
    store = ArtifactStore(database=manager)
    monkeypatch.setattr(store, "_emit_artifact_recorded_event", lambda **_kwargs: None)
    monkeypatch.setattr(artifact_module, "artifact_store", store)
    present_frame(agent, monkeypatch, 1)
    frame, _, observation_id = present_frame(agent, monkeypatch, 2)
    assert manager.list_runtime_artifacts(run_id=agent.run_id) == []
    finished = agent._dispatch("finish_task", complete(observation_id))
    assert finished["accepted"] is True, finished
    artifacts = manager.list_runtime_artifacts(run_id=agent.run_id)
    assert len(artifacts) == 1
    record = artifacts[0]
    assert record["source_path"] == str(frame)
    assert record["session_id"] == agent.session_id
    assert record["auto_attach_to_message"] in (0, False)
    assert record["metadata"]["runtimeEpisodeId"] == agent.episode_id
    assert record["metadata"]["observationId"] == observation_id
    assert record["artifactId"] in agent.artifact_refs


def test_evidence_registration_failure_cannot_claim_passed(agent, monkeypatch):
    _, _, observation_id = present_frame(agent, monkeypatch)
    monkeypatch.setattr(agent, "_record_final_observation", lambda: (_ for _ in ()).throw(OSError("fixture")))
    result = agent._dispatch("finish_task", complete(observation_id))
    assert result["accepted"] is False
    assert result["errorCode"] == "completion_evidence_registration_failed"
    assert agent._validate_completion()["passed"] is False


def test_old_finish_only_trajectory_returns_blocked_not_success(agent, monkeypatch):
    from core.model_control_plane import model_control_plane
    from core.model_failover_service import model_failover_service
    from runtimes.computer_use.episode_agent import llm_factory

    monkeypatch.setattr(model_control_plane, "get_config", lambda: {})
    monkeypatch.setattr(model_control_plane, "resolve_model_for_role", lambda *_args: {"resolvedModelRef": "fixture"})
    monkeypatch.setattr(llm_factory, "create_chat_model", lambda *_args, **_kwargs: object())
    frame, _, _ = present_frame(agent, monkeypatch)
    monkeypatch.setattr(agent, "_capture_desktop_frame", lambda _round: frame)
    calls = []
    def respond(**kwargs):
        calls.append(kwargs)
        assert callable(kwargs["stream_observer"])
        assert kwargs["stream_attempt_timeout_seconds"] == 60
        assert kwargs["stream_idle_timeout_seconds"] == 60
        args = {"summary": "全部完成", "evidence": "浏览器页面已可见"} if len(calls) == 1 else {
            "status": "blocked", "summary": "无法从观察确认目标已满足"}
        return SimpleNamespace(tool_calls=[{"name": "finish_task", "args": args, "id": f"call-{len(calls)}"}])
    monkeypatch.setattr(model_failover_service, "invoke_with_failover", respond)
    result = agent.execute()
    assert result["ok"] is False and result["status"] == "blocked"
    assert result["verification"]["passed"] is False
    assert {tool.name for tool in calls[0]["tools"]} >= {"desktop_launch", "browser_open"}
    assert result["actions"][0]["ok"] is False
