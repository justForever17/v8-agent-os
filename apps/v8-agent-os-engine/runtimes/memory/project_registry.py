from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.database import db
from persistence.repositories.project_registry_repository import ProjectRegistryRepository
from persistence.repositories.scope_binding_repository import ScopeBindingRepository
from runtimes.memory.models import ChannelBinding, ProjectDescriptor, WorkflowBinding, WorkspaceProjectBinding


class ProjectRegistryService:
    def __init__(
        self,
        project_repo: Optional[ProjectRegistryRepository] = None,
        scope_repo: Optional[ScopeBindingRepository] = None,
    ):
        self.project_repo = project_repo or ProjectRegistryRepository()
        self.scope_repo = scope_repo or ScopeBindingRepository()

    def list_projects(self) -> List[ProjectDescriptor]:
        return self.project_repo.list_projects()

    def get_project(self, project_id: str) -> Optional[ProjectDescriptor]:
        return self.project_repo.get_project(project_id)

    def get_default_project(self) -> Optional[ProjectDescriptor]:
        project_id = self.project_repo.get_default_project_id()
        if not project_id:
            return None
        return self.get_project(project_id)

    def save_project(self, payload: Dict[str, Any]) -> ProjectDescriptor:
        descriptor = ProjectDescriptor.model_validate(payload).normalized()
        saved = self.project_repo.save_project(descriptor)
        if not self.project_repo.get_default_project_id():
            self.project_repo.set_default_project(saved.project_id)
        self._sync_project_cache(saved)
        self._sync_workspace_binding(saved)
        return saved

    def patch_project(self, project_id: str, updates: Dict[str, Any]) -> Optional[ProjectDescriptor]:
        current = self.get_project(project_id)
        if current is None:
            return None
        merged = current.model_dump(by_alias=True, exclude_none=True)
        merged.update(updates or {})
        merged["id"] = project_id
        return self.save_project(merged)

    def delete_project(self, project_id: str) -> bool:
        deleted = self.project_repo.delete_project(project_id)
        if deleted:
            db.delete_project_descriptor_cache(project_id)
        return deleted

    def set_default_project(self, project_id: Optional[str]):
        self.project_repo.set_default_project(project_id)

    def bind_workspace(
        self,
        *,
        project_id: str,
        workspace_id: str,
        workspace_path: str,
        source: str = "project_registry",
        confidence: float = 1.0,
    ) -> Optional[ProjectDescriptor]:
        project = self.get_project(project_id)
        if project is None:
            return None
        updated = self.patch_project(
            project_id,
            {
                "workspaceId": workspace_id,
                "workspacePath": workspace_path,
            },
        )
        self.scope_repo.upsert_workspace_binding(
            WorkspaceProjectBinding(
                workspace_id=workspace_id,
                workspace_path=workspace_path,
                project_id=project_id,
                source=source,
                confidence=confidence,
            )
        )
        return updated

    def bind_channel(
        self,
        *,
        project_id: str,
        channel_type: str,
        remote_id: str,
        mode: str = "default",
    ) -> Optional[ProjectDescriptor]:
        project = self.get_project(project_id)
        if project is None:
            return None

        bindings = list(project.channel_bindings)
        match_idx = next(
            (
                idx
                for idx, item in enumerate(bindings)
                if item.channel_type == channel_type and item.remote_id == remote_id
            ),
            None,
        )
        binding = ChannelBinding(channelType=channel_type, remoteId=remote_id, mode=mode)
        if match_idx is None:
            bindings.append(binding)
        else:
            bindings[match_idx] = binding

        return self.patch_project(
            project_id,
            {
                "channelBindings": [item.model_dump(by_alias=True) for item in bindings],
            },
        )

    def bind_workflow(
        self,
        *,
        project_id: str,
        workflow_id: str,
        mode: str = "default",
    ) -> Optional[ProjectDescriptor]:
        project = self.get_project(project_id)
        if project is None:
            return None

        bindings = list(project.workflow_bindings)
        match_idx = next(
            (idx for idx, item in enumerate(bindings) if item.workflow_id == workflow_id),
            None,
        )
        binding = WorkflowBinding(workflowId=workflow_id, mode=mode)
        if match_idx is None:
            bindings.append(binding)
        else:
            bindings[match_idx] = binding

        return self.patch_project(
            project_id,
            {
                "workflowBindings": [item.model_dump(by_alias=True) for item in bindings],
            },
        )

    def find_project_for_workspace(
        self,
        *,
        workspace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> Optional[ProjectDescriptor]:
        binding = self.scope_repo.get_workspace_binding(
            workspace_id=workspace_id,
            workspace_path=workspace_path,
        )
        if binding:
            return self.get_project(binding.project_id)

        normalized_path = (workspace_path or "").replace("\\", "/").rstrip("/")
        for project in self.list_projects():
            if workspace_id and project.workspace_id == workspace_id:
                return project
            if normalized_path and project.workspace_path:
                project_path = project.workspace_path.replace("\\", "/").rstrip("/")
                if project_path == normalized_path:
                    return project
        return None

    def find_project_for_channel(self, channel_type: str, remote_id: str) -> Optional[ProjectDescriptor]:
        for project in self.list_projects():
            for binding in project.channel_bindings:
                if binding.channel_type == channel_type and binding.remote_id == remote_id:
                    return project
        return None

    def find_project_for_workflow(self, workflow_id: str) -> Optional[ProjectDescriptor]:
        for project in self.list_projects():
            for binding in project.workflow_bindings:
                if binding.workflow_id == workflow_id:
                    return project
        return None

    def _sync_project_cache(self, descriptor: ProjectDescriptor):
        db.sync_project_descriptor_cache(
            {
                "project_id": descriptor.project_id,
                "name": descriptor.name,
                "workspace_id": descriptor.workspace_id,
                "workspace_path": descriptor.workspace_path,
                "default_scope": descriptor.default_scope or f"project:{descriptor.project_id}",
                "tags": descriptor.tags,
                "active": descriptor.active,
            }
        )

    def _sync_workspace_binding(self, descriptor: ProjectDescriptor):
        if not descriptor.workspace_id or not descriptor.workspace_path:
            return
        self.scope_repo.upsert_workspace_binding(
            WorkspaceProjectBinding(
                workspace_id=descriptor.workspace_id,
                workspace_path=descriptor.workspace_path,
                project_id=descriptor.project_id,
                source="project_registry_sync",
                confidence=1.0,
            )
        )


project_registry_service = ProjectRegistryService()
