"""Research ledger access derived from durable session ownership and workspace binding.

IDs and inherited text are locators only. This adapter reuses session, workspace
authority and collaboration identity; it does not create another grant registry.
"""
from __future__ import annotations

from typing import Any

from core.actor_identity import resolve_collaboration_actor
from core.database import db
from core.workspace_authority import workspace_authority_service
from erc.runtime_context import get_runtime_context
from runtimes.memory.workspace_scope import canonical_workspace_scope


def _text(value: Any) -> str:
    return str(value or "").strip()


class ResearchAccessScope:
    def __init__(self, state: dict[str, Any] | None = None):
        # Runtime ownership wins over injected state and all provider parameters.
        state = dict(state or {})
        route = dict(state.get("route_context") or {})
        runtime = get_runtime_context()
        self.context = {**route, **state, **runtime}
        for source in (route, state, runtime):
            for snake, camel in (("session_id", "sessionId"), ("user_id", "userId"), ("run_id", "runId"), ("episode_id", "episodeId"), ("actor_role", "actorRole"), ("runtime_kind", "runtimeKind")):
                if snake in source or camel in source:
                    self.context[snake] = source.get(snake) or source.get(camel) or ""
                    self.context.pop(camel, None)
        self.session_id = _text(self.context.get("session_id") or self.context.get("sessionId"))
        self._sessions: dict[str, dict[str, Any]] = {}
        self._session_exists: dict[str, bool] = {}
        self._trusts: dict[tuple[str, str], bool] = {}
        self.source_context = self._session_source(self.session_id)
        user = _text(self.context.get("user_id") or self.context.get("userId"))
        if user and user != self.source_context.get("userId"):
            self.source_context = {}
        episode_id = _text(self.context.get("episode_id"))
        if episode_id:
            episode = db.get_runtime_episode(episode_id) or {}
            if episode.get("session_id") != self.session_id or (self.context.get("run_id") and episode.get("run_id") != self.context["run_id"]):
                self.source_context = {}
        self.valid = bool(self.source_context)

    def _session_source(self, session_id: str) -> dict[str, Any]:
        if session_id in self._sessions:
            return self._sessions[session_id]
        result: dict[str, Any] = {}
        session = db.get_session(session_id) if session_id else None
        self._session_exists[session_id] = bool(session)
        binding = db.get_session_scope_binding(session_id) if session else None
        if session:
            user = _text(session.get("user_id"))
            if user and not binding:
                result = {"version": 1, "sessionId": session_id, "userId": user,
                          "workspacePath": "", "workspaceScope": "", "resolvedScope": "", "projectId": ""}
        if session and binding:
            user = _text(session.get("user_id"))
            path = _text(binding.get("workspace_path"))
            resolved = _text(binding.get("resolved_scope"))
            project = _text(binding.get("project_id"))
            canonical = canonical_workspace_scope(path) if path else ""
            # Historical project aliases are accepted only with their exact
            # persisted project; unknown/global scopes never imply all users.
            allowed_aliases = {canonical}
            if project:
                allowed_aliases.add(f"project:{project}")
            binding_user = _text(binding.get("user_id"))
            if user and (not binding_user or binding_user == user) and binding.get("status") == "active":
                verified_workspace = bool(canonical and resolved in allowed_aliases)
                result = {
                    "version": 1, "sessionId": session_id, "userId": user,
                    "workspacePath": path if verified_workspace else "", "workspaceScope": canonical if verified_workspace else "",
                    "resolvedScope": resolved, "projectId": project,
                }
        self._sessions[session_id] = result
        return result

    def _trusted(self, source: dict[str, Any]) -> bool:
        if not source.get("workspaceScope"):
            return False
        # Project identifiers are aliases; current canonical physical workspace
        # identity selects the authoritative trust after a project rename.
        project_id = (self.source_context.get("projectId") if source.get("workspaceScope") == self.source_context.get("workspaceScope") else source.get("projectId"))
        key = (_text(source.get("workspaceScope")), _text(project_id))
        if key in self._trusts:
            return self._trusts[key]
        authority = workspace_authority_service.resolve(
            runtime_kind="research", explicit_workspace_path=source.get("workspacePath"),
            explicit_project_id=project_id or None,
        )
        self._trusts[key] = bool(
            authority.trust_state == "trusted"
            and not authority.is_fallback_to_main
            and canonical_workspace_scope(authority.workspace_root) == source.get("workspaceScope")
        )
        return self._trusts[key]

    def allows(self, item: dict[str, Any]) -> bool:
        if not self.valid:
            return False
        saved = item.get("sourceContext")
        if isinstance(saved, dict) and saved.get("version") == 1:
            source = dict(saved)
            session_id = _text(source.get("sessionId"))
            live = self._session_source(session_id)
            # A surviving session that changed owner/scope cannot lend its new
            # permissions to an old answer. Deleted sessions use saved origin.
            if live and any(live.get(key) != source.get(key) for key in ("userId", "workspaceScope")):
                return False
            if self._session_exists.get(session_id) and not live:
                return False
            if source.get("workspacePath") and canonical_workspace_scope(source["workspacePath"]) != source.get("workspaceScope"):
                return False
        else:
            # Before sourceContext, scope was a session/run ID. Recover only
            # that durable lineage; arbitrary IDs or old global data stay private.
            scope = _text(item.get("scope"))
            source = self._session_source(scope)
            if not source and scope.startswith("run_"):
                run = db.get_run_record(scope) or {}
                source = self._session_source(_text(run.get("session_id")))
        if not source or source.get("userId") != self.source_context.get("userId"):
            return False
        if source.get("workspaceScope") != self.source_context.get("workspaceScope"):
            return False
        if source.get("sessionId") == self.session_id:
            return True
        # Parent IDs/inherited references never widen scope. Actual descendants
        # carry their original session; cross-session reuse needs both trusts.
        return self._trusted(source) and self._trusted(self.source_context)

    def can_mutate(self) -> bool:
        if not self.valid:
            return False
        actor = resolve_collaboration_actor(runtime_context=self.context)
        if actor.is_supervisor:
            return True
        episode_id = _text(self.context.get("episode_id") or self.context.get("episodeId"))
        episode = db.get_runtime_episode(episode_id) if episode_id else None
        return bool(
            actor.role == "runtime_internal"
            and self.context.get("runtime_kind") == "research"
            and episode and episode.get("session_id") == self.session_id
            and episode.get("run_id") == (self.context.get("run_id") or self.context.get("runId"))
            and (episode.get("kind") or episode.get("runtime_kind")) == "research"
        )


def research_access_denied(mode: str) -> dict[str, Any]:
    # No valid locator in a denial: downstream surface rendering must not use
    # the refused ID to reread a ledger behind this boundary.
    return {
        "ok": False, "kind": "research_access_denied", "mode": mode,
        "error": "research_scope_unauthorized",
        "summary": "无法确认当前会话对该调研资料的权限，未读取或修改资料。",
        "recommendedNextAction": "在资料所属用户和已授权工作区的会话中重试。",
    }
