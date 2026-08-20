from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import workspace_capability as workspace_capability_module
from core import workspace_authority as workspace_authority_module
from core.engineering_sandbox.contracts import SandboxPolicy
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


def test_managed_execution_inherits_authority_but_resolves_only_in_worktree(tmp_path, monkeypatch):
    original_root = tmp_path / "workspace"
    worktree_root = tmp_path / "managed-worktree"
    main_root = tmp_path / "main"
    original_root.mkdir()
    worktree_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=original_root, main_root=main_root)
    policy = SandboxPolicy(
        policy_id="policy-managed",
        lease_id="lease-managed",
        repository_id="repo-managed",
        worktree_id="worktree-managed",
        worktree_root=str(worktree_root),
        original_workspace_root=str(original_root),
        base_commit="a" * 40,
        execution_mode="write",
        actor_role="direct_subagent",
        runtime_kind="engineering",
        write_set=("src/result.ts",),
    )
    context = {
        "workspace_path": str(worktree_root),
        "original_workspace_path": str(original_root),
        "workspace_id": "test2",
        "project_id": "test2",
        "managed_engineering_execution": True,
        "sandbox_lease_id": policy.lease_id,
        "sandbox_policy": policy.as_dict(),
        "sandbox_policy_digest": policy.digest,
    }

    relative_result = resolve_workspace_tool_path("src/result.ts", runtime_context=context)
    original_result = resolve_workspace_tool_path(
        str(original_root / "src" / "result.ts"),
        runtime_context=context,
    )

    assert relative_result["ok"] is True
    assert Path(relative_result["resolvedPath"]) == worktree_root / "src" / "result.ts"
    assert relative_result["binding"]["activeWorkspaceRoot"] == str(worktree_root.resolve())
    assert relative_result["binding"]["authorityWorkspaceRoot"] == str(original_root.resolve())
    assert relative_result["binding"]["managedExecution"] is True
    assert original_result["ok"] is False
    assert original_result["relation"] == "outside_active_workspace"


def test_managed_execution_rejects_policy_digest_mismatch(tmp_path, monkeypatch):
    original_root = tmp_path / "workspace"
    worktree_root = tmp_path / "managed-worktree"
    main_root = tmp_path / "main"
    original_root.mkdir()
    worktree_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=original_root, main_root=main_root)
    policy = SandboxPolicy(
        policy_id="policy-managed",
        lease_id="lease-managed",
        repository_id="repo-managed",
        worktree_id="worktree-managed",
        worktree_root=str(worktree_root),
        original_workspace_root=str(original_root),
        base_commit="a" * 40,
        execution_mode="read",
        actor_role="supervisor",
        runtime_kind="engineering",
    )

    with pytest.raises(RuntimeError, match="policy_digest_mismatch"):
        resolve_workspace_tool_path(
            "README.md",
            runtime_context={
                "workspace_path": str(worktree_root),
                "original_workspace_path": str(original_root),
                "managed_engineering_execution": True,
                "sandbox_policy": policy.as_dict(),
                "sandbox_policy_digest": "tampered",
            },
        )


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


def test_read_native_file_returns_structured_failure_for_missing_file(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
    ):
        result = json.loads(native_tools.read_native_file.func("missing.txt"))

    assert result["ok"] is False
    assert result["kind"] == "file_not_found"
    assert result["error"] == "file_not_found"
    assert Path(result["path"]) == active_root / "missing.txt"


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
    assert recorded[0]["workspace_path"] == "src/generated.ts"
    assert recorded[0]["metadata"]["origin"] == "agent_file_write"
    assert recorded[0]["metadata"]["workspaceRelativePath"] == "src/generated.ts"
    assert recorded[0]["metadata"]["managedExecution"] is False
    assert recorded[0]["metadata"]["deliveryState"] == "authoritative"


def test_managed_write_artifact_records_original_workspace_authority(tmp_path, monkeypatch):
    from core.tools.native import workspace_file as workspace_file_module

    execution_root = tmp_path / "worktree"
    original_root = tmp_path / "workspace"
    target = execution_root / "src" / "generated.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const ok = true;\n", encoding="utf-8")
    original_root.mkdir()
    recorded: list[dict] = []

    class _ArtifactStore:
        def record_local_file(self, **kwargs):
            recorded.append(kwargs)
            return {"artifactId": "art_managed"}

    monkeypatch.setattr(workspace_file_module, "artifact_store", _ArtifactStore())

    artifact = workspace_file_module._record_agent_written_file_artifact(
        {
            "session_id": "session-a",
            "run_id": "run-a",
            "workspace_path": str(execution_root),
            "original_workspace_path": str(original_root),
            "workspace_id": "workspace-a",
            "project_id": "project-a",
            "managed_engineering_execution": True,
            "worktree_id": "worktree-a",
        },
        target,
        operation="create_or_overwrite",
    )

    assert artifact == {"artifactId": "art_managed"}
    assert recorded[0]["workspace_path"] == "src/generated.ts"
    assert recorded[0]["metadata"]["managedExecution"] is True
    assert recorded[0]["metadata"]["deliveryState"] == "candidate"
    assert recorded[0]["metadata"]["workspaceRoot"] == str(original_root)
    assert recorded[0]["metadata"]["worktreeId"] == "worktree-a"


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


def test_read_native_file_supports_broad_code_suffixes_and_special_filenames(tmp_path, monkeypatch):
    from core import native_tools
    from core.tools.native.workspace_file import is_binary, is_readable_native_file

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    java_file = active_root / "src" / "Example.java"
    dockerfile = active_root / "Dockerfile"
    java_file.parent.mkdir(parents=True)
    java_file.write_text("class Example {}\n", encoding="utf-8")
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        java_result = native_tools.read_native_file.func("src/Example.java")
        docker_result = native_tools.read_native_file.func("Dockerfile")

    assert is_readable_native_file("Example.java", "text/x-java") is True
    assert is_readable_native_file("Dockerfile") is True
    assert is_binary(str(java_file)) is False
    assert "class Example" in java_result
    assert "FROM scratch" in docker_result


def test_read_native_file_reuses_document_parser_for_office_documents(tmp_path, monkeypatch):
    from core import native_tools
    from core.document_parser import DocumentParser
    from core.tools.native.workspace_file import is_readable_native_file

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    document = active_root / "brief.docx"
    document.write_bytes(b"test fixture")
    monkeypatch.setattr(
        DocumentParser,
        "ensure_document_ingestion_dependencies",
        classmethod(lambda cls, file_path: None),
    )
    monkeypatch.setattr(
        DocumentParser,
        "parse_file",
        classmethod(lambda cls, file_path: "# Brief\n\nParsed body\n"),
    )

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        result = native_tools.read_native_file.func("brief.docx")

    assert is_readable_native_file("brief.docx") is True
    assert "# Brief" in result
    assert "Parsed body" in result


def test_read_native_file_returns_governed_feature_pack_guidance_when_document_dependencies_are_missing(tmp_path, monkeypatch):
    from core import native_tools
    from core.document_parser import DocumentIngestionDependencyError, DocumentParser

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    document = active_root / "brief.docx"
    document.write_bytes(b"test fixture")

    def _raise_missing(cls, file_path):
        raise DocumentIngestionDependencyError(
            filename=file_path.name,
            suffix=file_path.suffix,
            missing_dependencies=["python-docx"],
        )

    monkeypatch.setattr(DocumentParser, "parse_file", classmethod(_raise_missing))

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        result = json.loads(native_tools.read_native_file.func("brief.docx"))

    assert result["ok"] is False
    assert result["code"] == "document_ingestion_dependencies_missing"
    assert result["details"]["featurePackId"] == "document_ingestion"
    assert "能力包" in result["details"]["recommendedNextAction"]
    assert "不要调用系统 pip" in result["details"]["recommendedNextAction"]


def test_read_native_file_rejects_legacy_binary_office_formats_without_fake_parser_support(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    document = active_root / "legacy.doc"
    document.write_bytes(b"legacy fixture")

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        result = json.loads(native_tools.read_native_file.func("legacy.doc"))

    assert result["ok"] is False
    assert result["code"] == "legacy_document_conversion_required"
    assert result["details"]["requiredFormat"] == "docx"


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


def test_write_native_file_requires_explicit_intent_for_existing_file_full_overwrite(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    target = active_root / "src" / "existing.txt"
    target.parent.mkdir(parents=True)
    target.write_text("one\ntwo\n", encoding="utf-8")

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        native_tools.read_native_file.func("src/existing.txt")
        blocked = native_tools.write_native_file.func("src/existing.txt", "replacement")
        native_tools.read_native_file.func("src/existing.txt")
        allowed = native_tools.write_native_file.func(
            "src/existing.txt",
            "replacement",
            allow_full_replace=True,
        )

    assert "existing_file_full_overwrite_block" in blocked
    assert '"existingLineCount": 2' in blocked
    assert "Created/Overwritten" in allowed
    assert target.read_text(encoding="utf-8") == "replacement"


def test_write_native_file_rejects_truncated_html_without_publishing_a_file(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)

    truncated = "<!doctype html><html><head><style>.card { text-align:"
    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
    ):
        result = json.loads(native_tools.write_native_file.func("broken.html", truncated))

    assert result["ok"] is False
    assert result["kind"] == "incomplete_html_document"
    assert "css_declaration_truncated" in result["issues"]
    assert "html_close_missing" in result["issues"]
    assert not (active_root / "broken.html").exists()


def test_write_native_file_keeps_existing_html_when_replacement_is_truncated(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    target = active_root / "app.html"
    original = "<!doctype html><html><head></head><body>ready</body></html>"
    target.write_text(original, encoding="utf-8")

    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
    ):
        native_tools.read_native_file.func("app.html")
        result = json.loads(
            native_tools.write_native_file.func(
                "app.html",
                "<!doctype html><html><body><script>const value = 1;",
                allow_full_replace=True,
            )
        )

    assert result["ok"] is False
    assert result["kind"] == "incomplete_html_document"
    assert "script_close_missing" in result["issues"]
    assert target.read_text(encoding="utf-8") == original


def test_write_native_file_accepts_complete_html_document(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    complete = (
        "<!doctype html><html><head><style>.card{text-align:center}</style></head>"
        "<body><main class='card'>ready</main><script>void 0</script></body></html>"
    )

    with bind_runtime_context(
        runtime_kind="chat",
        workspace_path=str(active_root),
        workspace_id="test2",
        project_id="test2",
    ):
        result = native_tools.write_native_file.func("app.html", complete)

    assert "Successfully Created/Overwritten" in result
    assert (active_root / "app.html").read_text(encoding="utf-8") == complete


def test_write_native_file_keeps_original_when_atomic_replace_fails(tmp_path, monkeypatch):
    from core import native_tools
    from core.tools.native import workspace_file as workspace_file_module

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    target = active_root / "src" / "existing.txt"
    target.parent.mkdir(parents=True)
    target.write_text("original\ncontent\n", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(workspace_file_module.os, "replace", fail_replace)
    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        native_tools.read_native_file.func("src/existing.txt")
        result = native_tools.write_native_file.func(
            "src/existing.txt",
            "replacement\n",
            allow_full_replace=True,
        )

    assert "Error writing file" in result
    assert target.read_text(encoding="utf-8") == "original\ncontent\n"
    assert list(target.parent.glob(f".{target.name}.*.v8os-tmp")) == []


def test_read_native_file_rejects_out_of_bounds_and_reversed_line_ranges(tmp_path, monkeypatch):
    from core import native_tools

    active_root = tmp_path / "active"
    main_root = tmp_path / "main"
    active_root.mkdir()
    main_root.mkdir()
    _patch_descriptor(monkeypatch, active_root=active_root, main_root=main_root)
    target = active_root / "short.txt"
    target.write_text("one\ntwo\n", encoding="utf-8")

    with bind_runtime_context(runtime_kind="chat", workspace_path=str(active_root), workspace_id="test2", project_id="test2"):
        out_of_bounds = json.loads(native_tools.read_native_file.func("short.txt", start_line=340))
        reversed_range = json.loads(native_tools.read_native_file.func("short.txt", start_line=2, end_line=1))

    assert out_of_bounds["kind"] == "line_range_out_of_bounds"
    assert out_of_bounds["error"] == "start_line_beyond_end_of_file"
    assert "2 lines" in out_of_bounds["summary"]
    assert reversed_range["kind"] == "invalid_line_range"
    assert reversed_range["error"] == "reversed_line_range"


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
