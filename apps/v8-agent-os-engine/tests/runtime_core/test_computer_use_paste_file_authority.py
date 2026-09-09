from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from core import creative_media_resource_authority as authority_module
from core.database import DatabaseManager
from core.tools.native import computer_use as native
from core.tools.native import desktop_governance
from erc.safety_guardian import DEFAULT_SAFETY_GUARDIAN_CONFIG, safety_guardian
from runtimes.computer_use.runtime import ComputerUseRuntime


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    protected = tmp_path / "protected-state"
    protected.mkdir()
    context = {"workspace_path": str(workspace), "session_id": "own-session", "run_id": "own-run",
               "agent_id": "child-fixture", "actor_role": "subagent", "runtime_kind": "chat"}
    database = DatabaseManager(tmp_path / "isolated.db")
    database.create_or_update_session("own-session", "Own", user_id="fixture-owner")
    database.create_or_update_session("other-session", "Other", user_id="other-owner")
    authority = SimpleNamespace(resolve=lambda **_: SimpleNamespace(
        workspace_root=workspace, workspace_id="fixture-workspace", project_id="fixture-project"))
    resolver = authority_module.CreativeMediaResourceAuthorityService(database=database, authority_service=authority)
    monkeypatch.setattr(authority_module, "creative_media_resource_authority", resolver)
    monkeypatch.setattr(native, "get_runtime_context", lambda: context)
    monkeypatch.setattr(desktop_governance, "get_runtime_context", lambda: context)
    config = copy.deepcopy(DEFAULT_SAFETY_GUARDIAN_CONFIG)
    config["fileRules"]["protectedPaths"] = [str(protected)]
    monkeypatch.setattr(safety_guardian, "_config", lambda: config)
    monkeypatch.setattr(safety_guardian, "_current_posture", lambda *_: "minimal")
    effects, decisions = [], []

    def enforce(decision, **_):
        decisions.append(decision)
        return (decision.verdict not in {"block", "review"}, decision.reason)

    monkeypatch.setattr(desktop_governance, "_enforce_safety_decision", enforce)
    monkeypatch.setattr(native, "_computer_use_resolve_app", lambda _: None)
    monkeypatch.setattr(native, "_desktop_route_gate", lambda **_: (True, None, {}))
    monkeypatch.setattr(native, "_computer_use_launch_target_override", lambda **_: {})
    monkeypatch.setattr(native, "_computer_use_prebind_window", lambda **kwargs: (
        effects.append(("bind", kwargs)) or (None, "Owned test window", 42, None)))
    monkeypatch.setattr(native, "_computer_use_execute_single_step", lambda **kwargs: (
        effects.append(("execute", kwargs)) or {"status": "succeeded"}))
    monkeypatch.setattr(native, "_computer_use_compact_response", lambda **_: json.dumps({"ok": True}))
    return SimpleNamespace(workspace=workspace, protected=protected, context=context, database=database,
                           effects=effects, decisions=decisions)


def paste(paths):
    return native.computer_use_paste_files.func(json.dumps(paths, ensure_ascii=False), window_title="Owned test window")


def write_fixture(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("non-private fixture only", encoding="utf-8")
    return path


@pytest.mark.parametrize("actor", ["supervisor", "subagent", "grandchild"])
def test_current_actor_workspace_file_is_normalized_and_seen_by_both_safety_gates(fixture, actor):
    fixture.context["actor_role"] = actor
    file = write_fixture(fixture.workspace / "中文 空格.txt")
    (fixture.workspace / "nested").mkdir()
    assert json.loads(paste(["nested/../中文 空格.txt"]))["ok"]
    assert [kind for kind, _ in fixture.effects] == ["bind", "execute"]
    step = fixture.effects[-1][1]["step"]
    assert step["file_paths"] == [str(file.resolve())]
    assert fixture.decisions[0].details["target"]["file_paths"] == step["file_paths"]
    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    assert runtime._runtime_action_safety_target(action_type="type_text", action_payload=step)["file_paths"] == step["file_paths"]


@pytest.mark.parametrize("outside_path", ["../outside.txt", "absolute"])
def test_bad_second_file_cancels_whole_set_before_prebind(fixture, outside_path):
    good = write_fixture(fixture.workspace / "good.txt")
    outside = write_fixture(fixture.workspace.parent / "outside.txt")
    result = json.loads(paste([str(good), str(outside) if outside_path == "absolute" else outside_path]))
    assert result["reasonCode"] == "workspace_boundary_block" and result["fileIndex"] == 2
    assert fixture.effects == [] and fixture.decisions == []


@pytest.mark.parametrize("bad", [None, {}, 12, "", "  "])
def test_invalid_item_is_not_stringified_or_silently_skipped(fixture, bad):
    good = write_fixture(fixture.workspace / "good.txt")
    assert "非空字符串" in paste([str(good), bad])
    assert fixture.effects == []


@pytest.mark.parametrize("kind", ["source", "artifact"])
@pytest.mark.parametrize("same_session", [True, False])
def test_exact_registered_file_does_not_grant_neighbor_or_other_session(fixture, kind, same_session):
    file = write_fixture(fixture.protected / "screenshots" / "own.png")
    session = "own-session" if same_session else "other-session"
    if kind == "source":
        fixture.database.add_session_source(source_id="source-fixture", session_id=session,
                                            source_kind="upload", workspace_path=str(file))
    else:
        fixture.database.add_runtime_artifact("artifact-fixture", "image", "image/png", session_id=session,
                                             source_path=str(file), metadata={"workspacePath": str(fixture.workspace)})
    result = json.loads(paste([str(file)]))
    if same_session:
        assert result["ok"] and len(fixture.effects) == 2
        fixture.effects.clear()
        neighbor = write_fixture(file.with_name("neighbor.png"))
        result = json.loads(paste([str(file), str(neighbor)]))
    assert result["reasonCode"] == "workspace_boundary_block"
    assert fixture.effects == []


@pytest.mark.parametrize("name", [".ssh/id_ed25519", ".aws/credentials", ".kube/config"])
def test_sensitive_file_inside_workspace_is_blocked_even_in_minimal_posture(fixture, name):
    file = write_fixture(fixture.workspace / name)
    result = paste([str(file)])
    assert "computer_use_sensitive_file_payload" == fixture.decisions[-1].risk_code
    assert fixture.decisions[-1].verdict == "block" and not fixture.decisions[-1].allow_override
    assert "secret" in result and fixture.effects == []


def test_registered_state_database_does_not_bypass_secret_guard(fixture):
    file = write_fixture(fixture.protected / "state.db")
    fixture.database.add_session_source(source_id="unsafe-fixture", session_id="own-session",
                                        source_kind="upload", workspace_path=str(file))
    paste([str(file)])
    assert fixture.decisions[-1].risk_code == "computer_use_sensitive_file_payload"
    assert fixture.effects == []


def test_regular_workspace_database_is_not_a_core_state_database(fixture, monkeypatch):
    # The default workspace may live below the protected V8 home. Its ordinary
    # project files keep the same read boundary as the native file tools.
    config = copy.deepcopy(safety_guardian._config())
    config["fileRules"]["protectedPaths"] = [str(fixture.workspace.parent)]
    monkeypatch.setattr(safety_guardian, "_config", lambda: config)
    file = write_fixture(fixture.workspace / "application.sqlite")
    assert json.loads(paste([str(file)]))["ok"]
    assert len(fixture.effects) == 2


@pytest.mark.parametrize("field", ["file_paths", "file_path", "attachment_paths", "paths"])
def test_runtime_safety_sees_every_existing_clipboard_file_alias(fixture, field):
    path = str(fixture.workspace / ".ssh" / "id_rsa")
    runtime = ComputerUseRuntime.__new__(ComputerUseRuntime)
    target = runtime._runtime_action_safety_target(action_type="type_text", action_payload={field: [path]})
    assert target["file_paths"] == [path]
    decision = safety_guardian.assess_computer_use_action(action_type="type_text", target=target,
                                                         runtime_context=fixture.context)
    assert decision.verdict == "block" and decision.risk_code == "computer_use_sensitive_file_payload"


def test_symlink_cannot_smuggle_outside_file(fixture):
    outside = write_fixture(fixture.workspace.parent / "outside.txt")
    link = fixture.workspace / "link.txt"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc.__class__.__name__}")
    assert json.loads(paste([str(link)]))["reasonCode"] == "workspace_boundary_block"
    assert fixture.effects == []


@pytest.mark.parametrize("existing_directory", [True, False])
def test_missing_file_or_directory_never_reaches_clipboard(fixture, existing_directory):
    file = fixture.workspace / "not-a-file"
    if existing_directory:
        file.mkdir()
    assert json.loads(paste([str(file)]))["reasonCode"] == "paste_file_unavailable"
    assert fixture.effects == []
