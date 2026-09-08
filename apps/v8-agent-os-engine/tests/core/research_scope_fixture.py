"""Isolated durable session ownership for Research tool contract tests."""
from types import SimpleNamespace

from core.database import DatabaseManager
from core import workspace_authority
from runtimes.memory.workspace_scope import canonical_workspace_scope
from runtimes.research import access_scope


def research_sessions(monkeypatch, tmp_path):
    database = DatabaseManager(tmp_path / "state.db")
    monkeypatch.setattr(access_scope, "db", database)
    trusts = {}

    def descriptor(**kwargs):
        return {"workspaceRoot": kwargs.get("explicit_workspace_path"), "mainWorkspacePath": str(tmp_path / "main"),
                "projectId": kwargs.get("explicit_project_id"), "source": "explicit_workspace_path", "usesScopedWorkspace": True}

    monkeypatch.setattr(workspace_authority.workspace_resolution_service, "resolve_workspace_descriptor", descriptor)
    monkeypatch.setattr(workspace_authority.project_registry_service, "get_project", lambda project_id:
                        SimpleNamespace(workspace_trust_state=trusts.get(project_id, "restricted"), workspace_trust_source="user_confirmed"))

    def create(session_id, *, user="alice", workspace="alpha", trusted=True):
        path = tmp_path / workspace
        path.mkdir(exist_ok=True)
        trusts[workspace] = "trusted" if trusted else "restricted"
        database.create_or_update_session(session_id, title="Research fixture", user_id=user)
        database.upsert_session_scope_binding({"session_id": session_id, "conversation_id": session_id,
            "user_id": user, "workspace_path": str(path), "project_id": workspace,
            "resolved_scope": canonical_workspace_scope(str(path)), "scope_source": "explicit_workspace_path", "status": "active"})
        return {"session_id": session_id, "user_id": user, "runtime_kind": "chat", "agent_id": "supervisor"}

    return database, create, trusts
