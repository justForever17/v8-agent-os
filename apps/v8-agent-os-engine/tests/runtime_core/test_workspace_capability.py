from __future__ import annotations

from pathlib import Path

from core import workspace_capability as workspace_capability_module
from core import workspace_authority as workspace_authority_module
from core.workspace_capability import preflight_command_workspace, resolve_workspace_tool_path
from erc.runtime_context import bind_runtime_context


class _FakeProject:
    def __init__(self, *, project_id: str, workspace_id: str, workspace_path: str, workspace_trust_state: str = "trusted"):
        self.project_id = project_id
        self.workspace_id = workspace_id
        self.workspace_path = workspace_path
        self.workspace_trust_state = workspace_trust_state
        self.workspace_trust_source = "user_confirmed" if workspace_trust_state == "trusted" else "restricted_default"


def _patch_descriptor(
    monkeypatch,
    *,
    active_root: Path,
    main_root: Path,
    project_id: str = "test2",
    workspace_id: str = "test2",
    source: str = "explicit_workspace_path",
    uses_scoped: bool = True,
    workspace_trust_state: str = "trusted",
) -> None:
    def _descriptor(**_kwargs):
        return {
            "runtimeKind": "chat",
            "projectId": project_id,
            "workspaceId": workspace_id,
            "workspaceRoot": str(active_root),
            "source": source,
            "usesScopedWorkspace": uses_scoped,
            "isScopedOverride": active_root != main_root,
            "mainWorkspacePath": str(main_root),
        }
    fake_project = _FakeProject(
        project_id=project_id,
        workspace_id=workspace_id,
        workspace_path=str(active_root),
        workspace_trust_state=workspace_trust_state,
    ) if project_id else None

    monkeypatch.setattr(
        workspace_capability_module.workspace_resolution_service,
        "resolve_workspace_descriptor",
        _descriptor,
    )
    monkeypatch.setattr(
        workspace_authority_module.project_registry_service,
        "get_project",
        lambda value: fake_project if fake_project and value == project_id else None,
    )


def _patch_home(monkeypatch, home: Path) -> None:
    monkeypatch.setattr(workspace_capability_module.Path, "home", lambda: home)


def test_relative_tool_path_resolves_inside_active_workspace(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    result = resolve_workspace_tool_path("src/app.ts", runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is True
    assert Path(result["resolvedPath"]) == active_root / "src" / "app.ts"
    assert result["relation"] == "inside_active_workspace"


def test_absolute_default_workspace_path_is_blocked_when_scoped(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    result = resolve_workspace_tool_path(str(main_root / "projects" / "wrong" / "package.json"), runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is False
    assert result["error"] == "workspace_boundary_violation"


def test_global_skill_path_is_readable_only_when_explicitly_requested(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    fake_home = tmp_path / "home"
    active_root.mkdir()
    main_root.mkdir()
    skill_file = fake_home / ".agents" / "skills" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Demo Skill\n", encoding="utf-8")
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    _patch_home(monkeypatch, fake_home)

    default_result = resolve_workspace_tool_path(str(skill_file), runtime_context={"workspace_path": str(active_root)})
    read_result = resolve_workspace_tool_path(
        str(skill_file),
        runtime_context={"workspace_path": str(active_root)},
        allow_global_skill_read=True,
    )

    assert default_result["ok"] is False
    assert read_result["ok"] is True
    assert read_result["relation"] == "inside_global_skill_read_execute_root"


def test_command_preflight_blocks_absolute_path_outside_active_workspace(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    command = f'cd /d "{main_root}\\projects\\wrong" && npm install'
    result = preflight_command_workspace(command, runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is False
    assert result["error"] == "workspace_command_path_violation"
    assert result["violations"]


def test_command_preflight_expands_userprofile_paths(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    monkeypatch.setenv("USERPROFILE", str(main_root))

    result = preflight_command_workspace(r"cd %USERPROFILE%\\.v8-agent-os\\workspace && npm install", runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is False
    assert result["error"] == "workspace_command_path_violation"


def test_command_preflight_allows_global_skill_script_execution(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    fake_home = tmp_path / "home"
    active_root.mkdir()
    main_root.mkdir()
    script = fake_home / ".agents" / "skills" / "demo" / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    _patch_home(monkeypatch, fake_home)

    result = preflight_command_workspace(f'python "{script}"', runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is True


def test_command_preflight_blocks_global_skill_mutation(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    fake_home = tmp_path / "home"
    active_root.mkdir()
    main_root.mkdir()
    script = fake_home / ".agents" / "skills" / "demo" / "scripts" / "check.py"
    script.parent.mkdir(parents=True)
    script.write_text("print('ok')\n", encoding="utf-8")
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    _patch_home(monkeypatch, fake_home)

    result = preflight_command_workspace(f'Remove-Item "{script}"', runtime_context={"workspace_path": str(active_root)})

    assert result["ok"] is False
    assert result["error"] == "global_skill_mutation_violation"
    assert result["violations"][0]["relation"] == "global_skill_read_execute_only"


def test_command_preflight_blocks_restricted_project_workspace(tmp_path, monkeypatch):
    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(
        monkeypatch,
        active_root=active_root,
        main_root=main_root,
        workspace_trust_state="restricted",
    )

    result = preflight_command_workspace("npm install", runtime_context={"workspace_path": str(active_root), "project_id": "test2"})

    assert result["ok"] is False
    assert result["kind"] == "workspace_side_effect_blocked"
    assert result["error"] == "workspace_not_trusted"


def test_command_preflight_blocks_scoped_fallback_to_main_workspace(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    main_root.mkdir()
    _patch_descriptor(
        monkeypatch,
        active_root=main_root,
        main_root=main_root,
        project_id="",
        workspace_id="",
        source="main_workspace",
        uses_scoped=True,
    )

    result = preflight_command_workspace("npm install", runtime_context={"runtime_kind": "engineering"})

    assert result["ok"] is False
    assert result["kind"] == "workspace_side_effect_blocked"
    assert result["error"] == "workspace_fallback_to_main"


def test_write_native_file_blocks_default_workspace_when_scoped(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        allowed_result = native_tools.write_native_file.func("src/ok.txt", "ok")
        blocked_result = native_tools.write_native_file.func(str(main_root / "projects" / "wrong.txt"), "bad")

    assert "Successfully Created/Overwritten" in allowed_result
    assert (active_root / "src" / "ok.txt").read_text(encoding="utf-8") == "ok"
    assert "workspace_boundary_block" in blocked_result
    assert not (main_root / "projects" / "wrong.txt").exists()


def test_write_native_file_records_a_session_bound_artifact(tmp_path, monkeypatch):
    from core import native_tools
    from core.tools.native import workspace_file as workspace_file_module

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    recorded: list[dict] = []

    class _ArtifactStore:
        def record_local_file(self, **kwargs):
            recorded.append(kwargs)
            return {"artifactId": "art_test"}

    monkeypatch.setattr(workspace_file_module, "artifact_store", _ArtifactStore())
    monkeypatch.setattr(workspace_file_module.safety_guardian, "assess_file_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workspace_file_module.safety_guardian, "observe_post_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(workspace_file_module, "_enforce_safety_decision", lambda *_args, **_kwargs: (True, None))
    monkeypatch.setattr(workspace_file_module, "mark_workspace_state_stale", lambda *_args, **_kwargs: None)

    with bind_runtime_context(
        runtime_kind="chat",
        session_id="session-a",
        run_id="run-a",
        workspace_path=str(active_root),
        workspace_id="workspace-a",
        project_id="project-a",
    ):
        result = native_tools.write_native_file.func("src/generated.ts", "export const ok = true;\n")

    assert "Successfully Created/Overwritten" in result
    assert len(recorded) == 1
    assert recorded[0]["session_id"] == "session-a"
    assert recorded[0]["run_id"] == "run-a"
    assert recorded[0]["metadata"]["origin"] == "agent_file_write"
    assert recorded[0]["metadata"]["workspaceRelativePath"] == "src/generated.ts"


def test_write_native_file_enforces_delegated_task_write_set(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    with bind_runtime_context(
        runtime_kind="subagent",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
        engineering_capsule_mode="write",
        allowed_write_paths=["src/allowed.txt"],
    ):
        allowed_result = native_tools.write_native_file.func("src/allowed.txt", "ok")
        blocked_result = native_tools.write_native_file.func("src/verify.py", "print('extra')")

    assert "Successfully Created/Overwritten" in allowed_result
    assert (active_root / "src" / "allowed.txt").read_text(encoding="utf-8") == "ok"
    assert "write_set_scope_block" in blocked_result
    assert "path_outside_allowed_write_set" in blocked_result
    assert not (active_root / "src" / "verify.py").exists()


def test_write_native_file_blocks_global_skill_root_even_with_extra_root(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    fake_home = tmp_path / "home"
    active_root.mkdir()
    main_root.mkdir()
    skill_file = fake_home / ".agents" / "skills" / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# Demo Skill\n", encoding="utf-8")
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    _patch_home(monkeypatch, fake_home)

    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
        allowed_extra_roots=[str(skill_file.parent)],
    ):
        read_result = native_tools.read_native_file.func(str(skill_file))
        blocked_result = native_tools.write_native_file.func(str(skill_file), "# changed\n")

    assert "# Demo Skill" in read_result
    assert "global_skill_read_execute_only" in blocked_result
    assert skill_file.read_text(encoding="utf-8") == "# Demo Skill\n"


def test_write_native_file_supports_line_scoped_patch(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    target = active_root / "src" / "longish.txt"
    target.parent.mkdir(parents=True)
    target.write_text("one\nold-a\nold-b\nfour\n", encoding="utf-8")

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        native_tools.read_native_file.func("src/longish.txt")
        result = native_tools.write_native_file.func("src/longish.txt", "new-a\nnew-b", line_start=2, line_end=3)

    assert "scoped_file_patch" in result
    assert target.read_text(encoding="utf-8") == "one\nnew-a\nnew-b\nfour\n"


def test_write_native_file_blocks_existing_long_file_full_overwrite_without_anchor(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    target = active_root / "src" / "very_long.txt"
    target.parent.mkdir(parents=True)
    target.write_text("".join(f"line {index}\n" for index in range(1000)), encoding="utf-8")

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        native_tools.read_native_file.func("src/very_long.txt")
        result = native_tools.write_native_file.func("src/very_long.txt", "replacement")

    assert "long_file_full_overwrite_block" in result
    assert "line 999" in target.read_text(encoding="utf-8")


def test_write_native_file_requires_read_before_modifying_existing_file(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    target = active_root / "src" / "existing.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
    ):
        blocked = native_tools.write_native_file.func("src/existing.txt", "after")
        native_tools.read_native_file.func("src/existing.txt")
        allowed = native_tools.write_native_file.func(
            "src/existing.txt",
            "after",
            expected_old_text="before",
        )
        blocked_again = native_tools.write_native_file.func(
            "src/existing.txt",
            "after-again",
            expected_old_text="after",
        )

    assert "read_before_write_required" in blocked
    assert "scoped_file_patch" in allowed
    assert "read_before_write_required" in blocked_again
    assert target.read_text(encoding="utf-8") == "after"


def test_write_native_file_rejects_stale_read_receipt(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    target = active_root / "src" / "changed.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
    ):
        native_tools.read_native_file.func("src/changed.txt")
        target.write_text("changed elsewhere with a different size", encoding="utf-8")
        result = native_tools.write_native_file.func("src/changed.txt", "after")

    assert "read_before_write_required" in result
    assert "file_changed_after_read" in result
    assert target.read_text(encoding="utf-8") == "changed elsewhere with a different size"
