import json

import pytest

from api.models import ChatMessage, ChatRequest, EngineConfig
from core.tools.native import command, workspace_file
from erc.runtime_context import bind_runtime_context
from runtimes.chat.runtime import ChatRuntime
from runtimes.memory.project_registry import project_registry_service
from tests.chat_runtime import test_session_command_service as base


@pytest.fixture
def assigned(tmp_path, monkeypatch):
    value = base.harness.__wrapped__(tmp_path, monkeypatch)
    assignment = base.create()["assignment"]
    assert base.send(assignment)["ok"]
    project_registry_service.save_project({
        "id": "fixture-project", "name": "Fixture", "workspaceId": "fixture-workspace",
        "workspacePath": str(value.workspace), "workspaceTrustState": "trusted",
        "workspaceTrustSource": "test_user_authorized",
    })
    value.assignment = assignment
    value.context = dict(runtime_kind="chat", agent_id="supervisor", session_id=assignment["childSessionId"],
                         user_id=base.USER, run_id="run-child-0", workspace_path=str(value.workspace),
                         workspace_id="fixture-workspace", project_id="fixture-project")
    monkeypatch.setattr("runtimes.chat.runtime.db", value.db)
    for module in (workspace_file, command):
        monkeypatch.setattr(module, "workspace_safety_decision", lambda *a, **k: None)
        monkeypatch.setattr(module, "_enforce_safety_decision", lambda *a, **k: (True, None))
    for name in ("assess_file_write", "assess_system_command", "assess_background_command", "observe_post_action"):
        monkeypatch.setattr(workspace_file.safety_guardian, name, lambda *a, **k: None)
    return value


def change_authority(assigned, kind, monkeypatch):
    child = assigned.assignment["childSessionId"]
    if kind == "revoke":
        result = base.invoke({"mode": "revoke", "assignmentId": assigned.assignment["assignmentId"], "revision": 1})
        assert result["ok"]
    elif kind == "human":
        runtime = ChatRuntime()
        monkeypatch.setattr(runtime, "_resolve_engine_config", lambda request: None)
        runtime.prepare_request(ChatRequest(messages=[ChatMessage(role="user", content="Read-only review now.")],
                                             config=EngineConfig(), session_id=child, user_id=base.USER))
    else:
        binding = assigned.db.get_session_scope_binding(child)
        new_root = assigned.workspace.parent / "new-root"
        new_root.mkdir()
        binding["workspace_path"] = str(new_root)
        assigned.db.upsert_session_scope_binding(binding)


@pytest.mark.parametrize("kind", ["revoke", "human", "workspace"])
@pytest.mark.parametrize("point", ["safety", "staged"])
def test_native_write_rechecks_authority_after_safety_and_before_publication(assigned, monkeypatch, kind, point):
    changed = []
    def change():
        change_authority(assigned, kind, monkeypatch)
        changed.append(True)
    if point == "safety":
        def decide(*args, **kwargs):
            change()
            return True, None
        monkeypatch.setattr(workspace_file, "_enforce_safety_decision", decide)
    else:
        fsync = workspace_file.os.fsync
        def staged(descriptor):
            fsync(descriptor)
            change()
        monkeypatch.setattr(workspace_file.os, "fsync", staged)
    with bind_runtime_context(**assigned.context):
        result = workspace_file.write_native_file.func("page.txt", "must not publish")
    assert changed, result
    assert not (assigned.workspace / "page.txt").exists(), result
    assert not list(assigned.workspace.rglob("*.v8os-tmp"))


@pytest.mark.parametrize("entry", ["sync", "argv", "session"])
def test_command_does_not_spawn_after_assignment_revoked_during_safety(assigned, monkeypatch, entry):
    reached = []
    spawned = []
    def decide(*args, **kwargs):
        reached.append(True)
        change_authority(assigned, "revoke", monkeypatch)
        return True, None
    def spawn(*args, **kwargs):
        spawned.append(True)
        raise RuntimeError("fixture detected unauthorized process spawn")
    monkeypatch.setattr(command, "_enforce_safety_decision", decide)
    monkeypatch.setattr(command, "run_windowless_bounded", spawn)
    monkeypatch.setattr(command.subprocess, "Popen", spawn)
    monkeypatch.setattr(command, "BackgroundProcess", spawn)
    with bind_runtime_context(**assigned.context):
        if entry == "sync":
            result = command.execute_system_command.func("python --version", cwd=str(assigned.workspace))
        elif entry == "argv":
            result = command.execute_governed_argv(["python", "--version"], cwd=str(assigned.workspace))
        else:
            try:
                result = command._launch_background_command("python --version", cwd=str(assigned.workspace), terminal_mode="pipe")
            except RuntimeError as error:
                result = str(error)
    assert reached, result
    assert not spawned, result
    assert "assignment_revoked" in (result if isinstance(result, str) else json.dumps(result))
