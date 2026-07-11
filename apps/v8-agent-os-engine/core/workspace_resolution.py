from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.storage import storage
from core.workspace_guard import build_workspace_path_status, legacy_workspace_residue_status
from core.v8_agent_os_paths import WORKSPACE_HOME
from runtimes.memory.project_registry import project_registry_service
from runtimes.memory.scope_resolution import session_scope_binding_service


_SCOPED_WORKSPACE_RUNTIMES = {
    "chat",
    "engineering",
    "research",
    "creative_media",
    "computer_use",
    "rpa",
    "automation",
    "automation_agent",
}

class WorkspaceResolutionService:
    @staticmethod
    def _normalize_runtime_kind(runtime_kind: str | None) -> str:
        return str(runtime_kind or "").strip().lower()

    @staticmethod
    def _normalize_workspace_path(value: str | None) -> str:
        raw = str(value or "").strip()
        return str(Path(raw).expanduser()) if raw else ""

    def get_main_workspace_path(self) -> str:
        configured = str(storage.get_workspace_config().get("agent_workspace_path") or "").strip()
        if configured:
            expanded = str(Path(configured).expanduser())
            residue = legacy_workspace_residue_status(expanded)
            if not residue["isLegacyResidue"]:
                return expanded
        return str(WORKSPACE_HOME.expanduser())

    def runtime_uses_scoped_workspace(self, runtime_kind: str | None) -> bool:
        normalized = self._normalize_runtime_kind(runtime_kind)
        return normalized in _SCOPED_WORKSPACE_RUNTIMES

    def _resolve_project_workspace_binding(
        self,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
    ) -> tuple[str, str | None, str | None]:
        if workspace_id or workspace_path:
            project = project_registry_service.find_project_for_workspace(
                workspace_id=str(workspace_id or "").strip() or None,
                workspace_path=str(workspace_path or "").strip() or None,
            )
            if project and str(project.workspace_path or "").strip():
                return (
                    self._normalize_workspace_path(project.workspace_path),
                    str(project.project_id or "").strip() or project_id,
                    str(project.workspace_id or "").strip() or workspace_id,
                )

        if project_id:
            project = project_registry_service.get_project(str(project_id).strip())
            if project and str(project.workspace_path or "").strip():
                return (
                    self._normalize_workspace_path(project.workspace_path),
                    str(project.project_id or "").strip() or project_id,
                    str(project.workspace_id or "").strip() or workspace_id,
                )

        return "", str(project_id or "").strip() or None, str(workspace_id or "").strip() or None

    def resolve_workspace_descriptor(
        self,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> dict:
        normalized_runtime = self._normalize_runtime_kind(runtime_kind) or None
        main_path = self.get_main_workspace_path()
        uses_scoped_workspace = self.runtime_uses_scoped_workspace(normalized_runtime)

        explicit_path = self._normalize_workspace_path(explicit_workspace_path)
        explicit_workspace_id = str(explicit_workspace_id or "").strip() or None
        explicit_project_id = str(explicit_project_id or "").strip() or None

        resolved_path = ""
        source = "main_workspace"
        project_id = explicit_project_id
        workspace_id = explicit_workspace_id

        if explicit_path:
            resolved_path = explicit_path
            source = "explicit_workspace_path"
            resolved_project_id, resolved_workspace_id = project_id, workspace_id
            project_match = project_registry_service.find_project_for_workspace(
                workspace_id=explicit_workspace_id,
                workspace_path=explicit_path,
            )
            if project_match:
                resolved_project_id = str(project_match.project_id or "").strip() or resolved_project_id
                resolved_workspace_id = str(project_match.workspace_id or "").strip() or resolved_workspace_id
            project_id, workspace_id = resolved_project_id, resolved_workspace_id
        elif explicit_workspace_id:
            resolved_path, project_id, workspace_id = self._resolve_project_workspace_binding(
                project_id=explicit_project_id,
                workspace_id=explicit_workspace_id,
            )
            if resolved_path:
                source = "explicit_workspace_binding"
        elif session_id and uses_scoped_workspace:
            binding = session_scope_binding_service.get_binding(session_id)
            scoped_path = self._normalize_workspace_path(
                str(getattr(binding, "workspace_path", "") or "").strip() if binding else ""
            )
            if scoped_path:
                resolved_path = scoped_path
                source = "session_scope_binding"
                project_id = str(getattr(binding, "project_id", "") or "").strip() or project_id
                workspace_id = str(getattr(binding, "workspace_id", "") or "").strip() or workspace_id
            elif binding:
                resolved_path, project_id, workspace_id = self._resolve_project_workspace_binding(
                    project_id=str(getattr(binding, "project_id", "") or "").strip() or project_id,
                    workspace_id=str(getattr(binding, "workspace_id", "") or "").strip() or workspace_id,
                )
                if resolved_path:
                    source = "session_scope_project_binding"
        if not resolved_path and uses_scoped_workspace:
            resolved_path, project_id, workspace_id = self._resolve_project_workspace_binding(
                project_id=explicit_project_id,
                workspace_id=explicit_workspace_id,
                workspace_path=explicit_path,
            )
            if resolved_path:
                source = "project_binding"

        if not resolved_path:
            resolved_path = main_path
            source = "main_workspace"

        path_status = build_workspace_path_status(resolved_path)
        return {
            "runtimeKind": normalized_runtime,
            "projectId": project_id,
            "workspaceId": workspace_id,
            "workspaceRoot": resolved_path,
            "source": source,
            "usesScopedWorkspace": uses_scoped_workspace,
            "isScopedOverride": Path(resolved_path) != Path(main_path),
            "pathStatus": path_status,
            "mainWorkspacePath": main_path,
            "resolvedWorkspacePath": resolved_path,
        }

    def resolve_workspace_path(
        self,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> str:
        descriptor = self.resolve_workspace_descriptor(
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        return str(descriptor.get("workspaceRoot") or self.get_main_workspace_path())

    def build_workspace_view(
        self,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
    ) -> dict:
        descriptor = self.resolve_workspace_descriptor(
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        return dict(descriptor)


workspace_resolution_service = WorkspaceResolutionService()
