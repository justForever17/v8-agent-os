from __future__ import annotations

from typing import Optional

from core.database import db
from runtimes.memory.models import SessionScopeBinding, WorkspaceProjectBinding


class ScopeBindingRepository:
    def upsert_binding(self, binding: SessionScopeBinding) -> SessionScopeBinding:
        db.upsert_session_scope_binding(binding.model_dump(exclude_none=True))
        row = db.get_session_scope_binding(binding.session_id)
        return SessionScopeBinding.model_validate(row or binding.model_dump())

    def get_binding(self, session_id: str) -> Optional[SessionScopeBinding]:
        row = db.get_session_scope_binding(session_id)
        return SessionScopeBinding.model_validate(row) if row else None

    def close_binding(self, session_id: str, status: str = "inactive") -> None:
        db.close_session_scope_binding(session_id, status=status)

    def upsert_workspace_binding(self, binding: WorkspaceProjectBinding) -> WorkspaceProjectBinding:
        db.upsert_workspace_project_binding(
            workspace_id=binding.workspace_id,
            workspace_path=binding.workspace_path,
            project_id=binding.project_id,
            source=binding.source,
            confidence=binding.confidence,
        )
        row = db.get_workspace_project_binding(workspace_id=binding.workspace_id)
        return WorkspaceProjectBinding.model_validate(row or binding.model_dump())

    def get_workspace_binding(
        self,
        *,
        workspace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
    ) -> Optional[WorkspaceProjectBinding]:
        row = db.get_workspace_project_binding(
            workspace_id=workspace_id,
            workspace_path=workspace_path,
        )
        return WorkspaceProjectBinding.model_validate(row) if row else None

    def delete_workspace_bindings(self, project_id: str) -> int:
        return db.delete_workspace_project_bindings(project_id)
