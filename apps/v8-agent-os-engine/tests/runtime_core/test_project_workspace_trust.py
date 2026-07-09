from __future__ import annotations

from pathlib import Path

import pytest

from runtimes.memory.project_registry import ProjectRegistryService, WorkspaceTrustRequiredError


class _FakeProjectRepo:
    def __init__(self):
        self.projects = {}
        self.default_project_id = None

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


def _service(monkeypatch, main_root: Path) -> ProjectRegistryService:
    monkeypatch.setattr(
        ProjectRegistryService,
        "_main_workspace_root",
        staticmethod(lambda: main_root.expanduser().resolve(strict=False)),
    )
    return ProjectRegistryService(project_repo=_FakeProjectRepo(), scope_repo=_FakeScopeRepo())


def test_save_project_requires_explicit_trust_for_user_external_workspace(tmp_path, monkeypatch):
    main_root = tmp_path / "main"
    external_root = tmp_path / "external"
    main_root.mkdir()
    service = _service(monkeypatch, main_root)

    with pytest.raises(WorkspaceTrustRequiredError, match="workspace_trust_required"):
        service.save_project({"workspacePath": str(external_root)})


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

