from __future__ import annotations

import uuid
from typing import Any

from core.database import db


class SessionLifecycleService:
    """The session creation owner shared by API and project coordination."""

    def list(self, *, root_session_id: str = "", limit: int = 50, after_id: str = "", database=None):
        database = database or db
        if root_session_id:
            return database.list_session_command_assignments(root_session_id, limit=limit, after_id=after_id)
        return database.get_sessions()

    def create(self, data: dict[str, Any], *, assignment=None, binding=None, database=None) -> dict[str, Any]:
        database = database or db
        metadata = dict(data.get("metadata") or {})
        for key in ("externalSurface", "clientGroup", "source"):
            if data.get(key):
                metadata[key] = str(data[key])
        if metadata.get("externalSurface") == "acp_bridge":
            metadata.setdefault("source", "acp_bridge")
            metadata.setdefault("clientGroup", "acp_bridge")
            metadata.setdefault("historyGroup", "external_agent_clients")
        created = database.create_session_placeholder(
            session_id=str(uuid.uuid4()), title=str(data.get("title") or "New Chat"),
            user_id=str(data.get("userId") or "anonymous"), metadata=metadata,
            assignment=assignment, binding=binding,
        )
        session_id = created["sessionId"]
        if not binding and any(data.get(key) for key in ("projectId", "workspaceId", "workspacePath", "scopeHint")):
            from runtimes.memory.scope_resolution import scope_resolution_service

            scope_resolution_service.resolve(
                session_id=session_id, conversation_id=session_id,
                user_id=str(data.get("userId") or "anonymous"), user_query="",
                project_id=data.get("projectId"), workspace_id=data.get("workspaceId"),
                workspace_path=data.get("workspacePath"), thread_id=data.get("threadId"),
                scope_hint=data.get("scopeHint"), scope_mode=data.get("scopeMode", "explicit"),
            )
        return {**created, "session": database.get_session(session_id)}


session_lifecycle_service = SessionLifecycleService()
