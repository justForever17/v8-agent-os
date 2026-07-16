from __future__ import annotations

from types import SimpleNamespace

from core.runtime_tool_access import filter_visible_tools_for_actor
import runtimes.chat.runtime as chat_runtime_module
from runtimes.chat.runtime import ChatRuntime
from runtimes.engineering.service import engineering_lane_service


def test_explicit_engineering_request_is_detected() -> None:
    runtime = ChatRuntime()

    assert runtime._detect_explicit_engineering_runtime_request("请使用 Engineering Runtime 开发这个项目")
    assert runtime._detect_explicit_engineering_runtime_request("这次必须进入工程运行时，不要主管盲写")
    assert not runtime._detect_explicit_engineering_runtime_request("用工程模式做前端实现")
    assert not runtime._detect_explicit_engineering_runtime_request("做一个小的文字说明")
    assert not runtime._detect_explicit_engineering_runtime_request("只写正文，不调用工程运行时")


def test_engineering_work_mode_is_session_posture_not_runtime_route() -> None:
    runtime = ChatRuntime()

    assert runtime._detect_explicit_supervisor_work_mode_request("用工程模式做前端实现") == "engineering"
    assert runtime._detect_explicit_supervisor_work_mode_request("切换到日常模式") == "daily"
    assert runtime._detect_explicit_supervisor_work_mode_request("请使用 Engineering Runtime 开发") is None


def test_engineering_continuation_detects_same_session_debug_signal(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    fake_db = SimpleNamespace(
        list_runtime_episodes=lambda **_: [
            {
                "id": "episode-eng-1",
                "kind": "engineering",
                "state": "completed",
                "run_id": "run-1",
                "workspace_path": str(workspace),
            }
        ],
        list_runtime_artifacts=lambda **_: [],
        list_engineering_proof_entries=lambda **_: [{"id": "proof-1", "workspace_path": str(workspace), "summary": "patched"}],
    )
    monkeypatch.setattr(chat_runtime_module, "db", fake_db)

    assert ChatRuntime._looks_like_engineering_continuation_message("还是不行，控制台报错 TypeError: boom")
    context = ChatRuntime._recent_engineering_continuation_context(
        session_id="session-1",
        workspace_path=str(workspace),
    )

    assert context["active"] is True
    assert context["previousEpisodeId"] == "episode-eng-1"
    assert context["previousRunId"] == "run-1"
    assert context["proofRefs"] == ["proof-1"]


def test_engineering_continuation_rejects_other_workspace(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "project"
    other_workspace = tmp_path / "other"
    workspace.mkdir()
    other_workspace.mkdir()
    fake_db = SimpleNamespace(
        list_runtime_episodes=lambda **_: [
            {
                "id": "episode-eng-1",
                "kind": "engineering",
                "state": "completed",
                "run_id": "run-1",
                "workspace_path": str(other_workspace),
            }
        ],
        list_runtime_artifacts=lambda **_: [],
        list_engineering_proof_entries=lambda **_: [],
    )
    monkeypatch.setattr(chat_runtime_module, "db", fake_db)

    context = ChatRuntime._recent_engineering_continuation_context(
        session_id="session-1",
        workspace_path=str(workspace),
    )

    assert context["active"] is False
    assert context["reason"] == "workspace_mismatch"


def test_engineering_project_creation_workspace_activates_without_git(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(engineering_lane_service, "get_config", lambda: {"enabled": True, "triggerMode": "auto"})
    decision = engineering_lane_service.trigger_decision(
        user_query="请使用 Engineering Runtime 开发一个 AI 狼人杀 Web 应用",
        mode="auto",
        workspace_descriptor={"workspaceRoot": str(tmp_path)},
    )

    assert decision["matched"] is True
    assert decision["active"] is True
    assert decision["workspaceMode"] == "project_creation_workspace"
    assert decision["reason"] == "project_creation_workspace"


def test_direct_subagent_has_broker_and_peer_help_remains_compatibility_grant() -> None:
    broker = SimpleNamespace(name="delegation_broker")
    peer_help = SimpleNamespace(name="request_peer_help")

    default_visible = filter_visible_tools_for_actor([broker, peer_help], actor="subagent")
    granted = filter_visible_tools_for_actor(
        [broker, peer_help],
        actor="subagent",
        runtime_access=["delegation.recursive"],
    )
    grandchild = filter_visible_tools_for_actor(
        [broker, peer_help],
        actor="subagent",
        route_context={"delegationDepth": 2},
        runtime_access=["delegation.recursive"],
    )

    assert [item.name for item in default_visible] == ["delegation_broker"]
    assert [item.name for item in granted] == ["delegation_broker", "request_peer_help"]
    assert grandchild == []
