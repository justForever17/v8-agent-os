from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from runtimes.memory.project_registry import ProjectRegistryService, WorkspaceTrustRequiredError


class _FakeProjectRepo:
    def __init__(self):
        self.projects = {}
        self.default_project_id = None
        self.workspace_presentations = {}

    def list_projects(self):
        return list(self.projects.values())

    def get_project(self, project_id: str):
        return self.projects.get(project_id)

    def save_project(self, descriptor):
        self.projects[descriptor.project_id] = descriptor
        return descriptor

    def get_default_project_id(self):
        return self.default_project_id

    def set_default_project(self, project_id):
        self.default_project_id = project_id

    def delete_project(self, project_id: str):
        return self.projects.pop(project_id, None) is not None

    def list_workspace_presentations(self):
        return list(self.workspace_presentations.values())

    def save_workspace_presentation(self, path_key: str, presentation):
        self.workspace_presentations[path_key] = dict(presentation)
        return dict(presentation)

    def delete_workspace_presentation(self, path_key: str):
        return self.workspace_presentations.pop(path_key, None) is not None


class _FakeScopeRepo:
    def __init__(self):
        self.bindings = []

    def get_workspace_binding(self, *, workspace_id=None, workspace_path=None):
        for binding in self.bindings:
            if workspace_id and binding.workspace_id == workspace_id:
                return binding
            if workspace_path and binding.workspace_path == workspace_path:
                return binding
        return None

    def upsert_workspace_binding(self, binding):
        self.bindings.append(binding)

    def delete_workspace_bindings(self, project_id: str):
        before = len(self.bindings)
        self.bindings = [binding for binding in self.bindings if binding.project_id != project_id]
        return before - len(self.bindings)


class _FakeDatabase:
    def __init__(self):
        self.cached_projects = set()

    def sync_project_descriptor_cache(self, project):
        self.cached_projects.add(project["project_id"])

    def delete_project_descriptor_cache(self, project_id: str):
        self.cached_projects.discard(project_id)


def _service(monkeypatch, main_root: Path) -> ProjectRegistryService:
    monkeypatch.setattr(
        ProjectRegistryService,
        "_main_workspace_root",
        staticmethod(lambda: main_root.expanduser().resolve(strict=False)),
    )
    monkeypatch.setattr("runtimes.memory.project_registry.db", _FakeDatabase())
    return ProjectRegistryService(project_repo=_FakeProjectRepo(), scope_repo=_FakeScopeRepo())


def test_save_project_requires_explicit_trust_for_user_external_workspace(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    external_root = tmp_path / "external"
    main_root.mkdir()
    service = _service(monkeypatch, main_root)

    with pytest.raises(WorkspaceTrustRequiredError, match="workspace_trust_required"):
        service.save_project({"workspacePath": str(external_root)})


def test_workspace_presentation_is_keyed_by_path_without_renaming_directory(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    workspace = main_root / "project-alpha"
    workspace.mkdir(parents=True)
    service = _service(monkeypatch, main_root)

    saved = service.patch_workspace_presentation(
        str(workspace),
        {"displayName": "Alpha 展示名", "pinned": True},
    )

    assert saved["workspacePath"] == str(workspace)
    assert saved["displayName"] == "Alpha 展示名"
    assert saved["pinned"] is True
    assert workspace.name == "project-alpha"
    assert service.get_workspace_presentation(str(workspace).upper())["displayName"] == "Alpha 展示名"

    reset = service.patch_workspace_presentation(str(workspace), {"displayName": "", "pinned": False})
    assert reset["displayName"] == ""
    assert reset["pinned"] is False
    assert service.get_workspace_presentation(str(workspace)) is None


def test_save_project_allows_user_confirmed_external_workspace(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    external_root = tmp_path / "external"
    main_root.mkdir()
    service = _service(monkeypatch, main_root)

    project = service.save_project(
        {
            "workspacePath": str(external_root),
            "workspaceTrustState": "trusted",
            "workspaceTrustSource": "user_confirmed",
        }
    )

    assert project.workspace_path == str(external_root)
    assert project.workspace_trust_state == "trusted"
    assert project.workspace_trust_source == "user_confirmed"
    assert (external_root / ".agents" / "rules" / "AGENTS.md").exists()
    assert not (external_root / ".git").exists()


def test_find_project_normalizes_repeated_workspace_separators(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    external_root = tmp_path / "external" / "project"
    main_root.mkdir()
    service = _service(monkeypatch, main_root)
    project = service.save_project(
        {
            "workspacePath": str(external_root),
            "workspaceTrustState": "trusted",
            "workspaceTrustSource": "user_confirmed",
        }
    )
    service.scope_repo.bindings.clear()

    raw_path = str(external_root)
    if raw_path.startswith("/"):
        equivalent_path = "/" + raw_path[1:].replace("/", "//")
    else:
        equivalent_path = raw_path.replace("\\", "\\\\")

    resolved = service.find_project_for_workspace(workspace_path=equivalent_path)

    assert resolved is not None
    assert resolved.project_id == project.project_id


def test_find_project_ignores_stale_workspace_binding(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    external_root = tmp_path / "external" / "project"
    main_root.mkdir()
    service = _service(monkeypatch, main_root)
    project = service.save_project(
        {
            "workspacePath": str(external_root),
            "workspaceTrustState": "trusted",
            "workspaceTrustSource": "user_confirmed",
        }
    )
    service.scope_repo.bindings = [
        SimpleNamespace(
            workspace_id="deleted-project",
            workspace_path=str(external_root),
            project_id="deleted-project",
        )
    ]

    resolved = service.find_project_for_workspace(workspace_path=str(external_root))

    assert resolved is not None
    assert resolved.project_id == project.project_id


def test_delete_project_removes_workspace_lookup_truth(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    external_root = tmp_path / "external" / "project"
    main_root.mkdir()
    service = _service(monkeypatch, main_root)
    project = service.save_project(
        {
            "workspacePath": str(external_root),
            "workspaceTrustState": "trusted",
            "workspaceTrustSource": "user_confirmed",
        }
    )

    assert service.scope_repo.get_workspace_binding(workspace_id=project.workspace_id) is not None

    assert service.delete_project(project.project_id) is True
    assert service.scope_repo.get_workspace_binding(workspace_id=project.workspace_id) is None


def test_default_project_uses_configured_main_workspace_root(tmp_path, monkeypatch):
    main_root = tmp_path / "configured-main"
    service = _service(monkeypatch, main_root)

    project = service.save_project({"id": "demo"})

    assert Path(project.workspace_path) == main_root / "projects" / "demo"
    assert project.workspace_trust_state == "trusted"
    assert project.workspace_trust_source == "system_main"


def test_patch_changed_external_workspace_requires_fresh_trust(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    first_root = tmp_path / "external-1"
    second_root = tmp_path / "external-2"
    main_root.mkdir()
    service = _service(monkeypatch, main_root)
    project = service.save_project(
        {
            "workspacePath": str(first_root),
            "workspaceTrustState": "trusted",
            "workspaceTrustSource": "user_confirmed",
        }
    )

    with pytest.raises(WorkspaceTrustRequiredError, match="workspace_trust_required"):
        service.patch_project(project.project_id, {"workspacePath": str(second_root)})
