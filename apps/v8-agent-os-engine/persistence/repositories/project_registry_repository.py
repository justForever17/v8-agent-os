from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.storage import storage
from runtimes.memory.models import ProjectDescriptor


class ProjectRegistryRepository:
    def list_projects(self) -> List[ProjectDescriptor]:
        registry = storage.get_projects_registry()
        projects = registry.get("projects", [])
        return [ProjectDescriptor.model_validate(item).normalized() for item in projects]

    def get_project(self, project_id: str) -> Optional[ProjectDescriptor]:
        for project in self.list_projects():
            if project.project_id == project_id:
                return project
        return None

    def get_default_project_id(self) -> Optional[str]:
        registry = storage.get_projects_registry()
        return registry.get("defaultProjectId")

    def save_project(self, descriptor: ProjectDescriptor) -> ProjectDescriptor:
        normalized = descriptor.normalized()
        registry = storage.get_projects_registry()
        projects = registry.get("projects", [])
        replaced = False

        serialized = normalized.model_dump(by_alias=True, exclude_none=True)
        for idx, item in enumerate(projects):
            if item.get("id") == normalized.project_id:
                projects[idx] = serialized
                replaced = True
                break

        if not replaced:
            projects.append(serialized)

        registry["projects"] = projects
        storage.save_projects_registry(registry)
        return normalized

    def delete_project(self, project_id: str) -> bool:
        registry = storage.get_projects_registry()
        projects = registry.get("projects", [])
        filtered = [item for item in projects if item.get("id") != project_id]
        if len(filtered) == len(projects):
            return False

        registry["projects"] = filtered
        if registry.get("defaultProjectId") == project_id:
            registry["defaultProjectId"] = None
        storage.save_projects_registry(registry)
        return True

    def set_default_project(self, project_id: Optional[str]):
        registry = storage.get_projects_registry()
        registry["defaultProjectId"] = project_id
        storage.save_projects_registry(registry)

    def list_workspace_presentations(self) -> List[Dict[str, Any]]:
        registry = storage.get_projects_registry()
        items = registry.get("workspacePresentations", [])
        return [dict(item) for item in items if isinstance(item, dict)]

    def save_workspace_presentation(self, path_key: str, presentation: Dict[str, Any]) -> Dict[str, Any]:
        registry = storage.get_projects_registry()
        items = self.list_workspace_presentations()
        serialized = dict(presentation)
        replaced = False
        for index, item in enumerate(items):
            if str(item.get("pathKey") or "") == path_key:
                items[index] = serialized
                replaced = True
                break
        if not replaced:
            items.append(serialized)
        registry["workspacePresentations"] = items
        storage.save_projects_registry(registry)
        return serialized

    def delete_workspace_presentation(self, path_key: str) -> bool:
        registry = storage.get_projects_registry()
        items = self.list_workspace_presentations()
        filtered = [item for item in items if str(item.get("pathKey") or "") != path_key]
        if len(filtered) == len(items):
            return False
        registry["workspacePresentations"] = filtered
        storage.save_projects_registry(registry)
        return True
