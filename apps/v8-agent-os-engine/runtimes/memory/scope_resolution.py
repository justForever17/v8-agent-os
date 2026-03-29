from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional

from core.database import db
from core.context.scope import detect_scope
from persistence.repositories.scope_binding_repository import ScopeBindingRepository
from persistence.repositories.scope_resolution_repository import ScopeResolutionRepository
from runtimes.memory.models import (
    ProjectDescriptor,
    ScopeResolutionEvent,
    ScopeResolutionResult,
    SessionScopeBinding,
)
from runtimes.memory.project_registry import ProjectRegistryService, project_registry_service


def _normalize_scope(scope: Optional[str]) -> Optional[str]:
    value = (scope or "").strip()
    return value or None


def _scope_for_project(project_id: Optional[str]) -> Optional[str]:
    if not project_id:
        return None
    return f"project:{project_id}"


def _scope_for_workspace(workspace_id: Optional[str]) -> Optional[str]:
    if not workspace_id:
        return None
    return f"workspace:{workspace_id}"


def _scope_for_workflow(workflow_id: Optional[str]) -> Optional[str]:
    if not workflow_id:
        return None
    return f"workflow:{workflow_id}"


def _scope_for_channel(channel_type: Optional[str], remote_id: Optional[str]) -> Optional[str]:
    if not channel_type or not remote_id:
        return None
    normalized_remote = str(remote_id).replace(":", "_").replace("/", "_")
    return f"channel:{channel_type}:{normalized_remote}"


def build_scope_chain(
    *,
    resolved_scope: str,
    detected_app_scope: Optional[str] = None,
    channel_type: Optional[str] = None,
    channel_remote_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> List[str]:
    chain: List[str] = ["global"]
    app_scope = detected_app_scope if (detected_app_scope or "").startswith("app:") else None
    for item in (
        app_scope,
        _scope_for_channel(channel_type, channel_remote_id),
        _scope_for_workspace(workspace_id),
        _scope_for_project(project_id),
        _scope_for_workflow(workflow_id),
        resolved_scope,
    ):
        if item and item not in chain:
            chain.append(item)
    return chain


class SessionScopeBindingService:
    def __init__(self, repo: Optional[ScopeBindingRepository] = None):
        self.repo = repo or ScopeBindingRepository()

    def get_binding(self, session_id: str) -> Optional[SessionScopeBinding]:
        return self.repo.get_binding(session_id)

    def upsert_binding(self, binding: SessionScopeBinding) -> SessionScopeBinding:
        saved = self.repo.upsert_binding(binding)
        db.update_session_metadata(saved.session_id, saved.metadata_view())
        return saved

    def close_binding(self, session_id: str, status: str = "inactive") -> None:
        self.repo.close_binding(session_id, status=status)


class ScopeResolutionService:
    def __init__(
        self,
        *,
        project_registry: Optional[ProjectRegistryService] = None,
        binding_service: Optional[SessionScopeBindingService] = None,
        resolution_repo: Optional[ScopeResolutionRepository] = None,
    ):
        self.project_registry = project_registry or project_registry_service
        self.binding_service = binding_service or SessionScopeBindingService()
        self.resolution_repo = resolution_repo or ScopeResolutionRepository()

    def resolve(
        self,
        *,
        session_id: str,
        conversation_id: Optional[str] = None,
        thread_id: Optional[str] = None,
        user_id: Optional[str] = None,
        user_query: str = "",
        project_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
        workflow_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        channel_remote_id: Optional[str] = None,
        scope_hint: Optional[str] = None,
        scope_mode: Optional[str] = "mixed",
        run_id: Optional[str] = None,
        force_reresolve: bool = False,
    ) -> ScopeResolutionResult:
        normalized_scope_mode = (scope_mode or "mixed").strip().lower()
        if normalized_scope_mode not in {"explicit", "infer", "mixed"}:
            normalized_scope_mode = "mixed"

        explicit_requested_scope = _normalize_scope(scope_hint) or _scope_for_project(project_id)
        requested_scope = explicit_requested_scope
        if not requested_scope and workspace_id and normalized_scope_mode == "explicit":
            requested_scope = _scope_for_workspace(workspace_id)

        existing = None if force_reresolve else self.binding_service.get_binding(session_id)
        if (
            existing
            and existing.status == "active"
            and normalized_scope_mode != "explicit"
        ):
            detected_app_scope = detect_scope(user_query) if user_query else None
            scope_chain = build_scope_chain(
                resolved_scope=existing.resolved_scope,
                detected_app_scope=detected_app_scope,
                channel_type=existing.channel_type,
                channel_remote_id=existing.channel_remote_id,
                workspace_id=existing.workspace_id,
                project_id=existing.project_id,
                workflow_id=existing.workflow_id,
            )
            self._record_resolution_event(
                session_id=session_id,
                run_id=run_id,
                requested_scope=requested_scope,
                resolved_scope=existing.resolved_scope,
                source="existing_binding",
                confidence=existing.scope_confidence,
                evidence={"existing_binding": existing.model_dump(exclude_none=True)},
            )
            return ScopeResolutionResult(
                binding=existing,
                requested_scope=requested_scope,
                scope_chain=scope_chain,
                evidence={"existing_binding": existing.model_dump(exclude_none=True)},
                reused_existing_binding=True,
            )

        detected_app_scope = detect_scope(user_query) if user_query else "global"
        evidence: Dict[str, Any] = {
            "requested_scope": requested_scope,
            "detected_app_scope": detected_app_scope,
        }

        resolved_project: Optional[ProjectDescriptor] = None
        resolved_scope = "global"
        scope_source = "fallback_default"
        scope_confidence = 1.0

        if normalized_scope_mode != "infer" and (
            explicit_requested_scope or project_id or workspace_id or workflow_id
        ):
            resolved_scope, resolved_project = self._resolve_explicit_scope(
                requested_scope=explicit_requested_scope,
                project_id=project_id,
                workspace_id=workspace_id,
                workspace_path=workspace_path,
                workflow_id=workflow_id,
            )
            scope_source = "request_explicit"
            scope_confidence = 1.0
            evidence["explicit"] = {
                "project_id": project_id,
                "workspace_id": workspace_id,
                "workspace_path": workspace_path,
                "workflow_id": workflow_id,
                "scope_hint": scope_hint,
            }
        else:
            workflow_project = self.project_registry.find_project_for_workflow(workflow_id) if workflow_id else None
            if workflow_project:
                resolved_project = workflow_project
                project_id = project_id or workflow_project.project_id
                resolved_scope = workflow_project.default_scope or _scope_for_project(project_id) or "global"
                scope_source = "workflow_bound"
                scope_confidence = 0.98
                evidence["workflow_bound"] = workflow_project.model_dump(exclude_none=True)
            else:
                channel_project = (
                    self.project_registry.find_project_for_channel(channel_type, channel_remote_id)
                    if channel_type and channel_remote_id
                    else None
                )
                if channel_project:
                    resolved_project = channel_project
                    project_id = project_id or channel_project.project_id
                    resolved_scope = channel_project.default_scope or _scope_for_project(project_id) or "global"
                    scope_source = "channel_bound"
                    scope_confidence = 0.95
                    evidence["channel_bound"] = channel_project.model_dump(exclude_none=True)
                else:
                    workspace_project = self.project_registry.find_project_for_workspace(
                        workspace_id=workspace_id,
                        workspace_path=workspace_path,
                    )
                    if workspace_project:
                        resolved_project = workspace_project
                        project_id = project_id or workspace_project.project_id
                        workspace_id = workspace_id or workspace_project.workspace_id
                        workspace_path = workspace_path or workspace_project.workspace_path
                        resolved_scope = workspace_project.default_scope or _scope_for_project(project_id) or "global"
                        scope_source = "workspace_inferred"
                        scope_confidence = 0.9
                        evidence["workspace_inferred"] = workspace_project.model_dump(exclude_none=True)
                    else:
                        heuristic_scope = detect_scope(
                            user_query,
                            project_name=project_id,
                        ) if user_query else "global"
                        resolved_scope = heuristic_scope or "global"
                        if resolved_scope != "global":
                            scope_source = "heuristic_detected"
                            scope_confidence = 0.65
                        evidence["heuristic"] = {"scope": heuristic_scope}

        if not project_id and resolved_project:
            project_id = resolved_project.project_id
        if not workspace_id and resolved_project and resolved_project.workspace_id:
            workspace_id = resolved_project.workspace_id
        if not workspace_path and resolved_project and resolved_project.workspace_path:
            workspace_path = resolved_project.workspace_path

        binding = SessionScopeBinding(
            session_id=session_id,
            conversation_id=conversation_id or session_id,
            thread_id=thread_id,
            user_id=user_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            project_id=project_id,
            workflow_id=workflow_id,
            channel_type=channel_type,
            channel_remote_id=channel_remote_id,
            scope_hint=scope_hint,
            resolved_scope=resolved_scope,
            scope_source=scope_source,
            scope_confidence=scope_confidence,
            status="active",
        )
        saved = self.binding_service.upsert_binding(binding)
        scope_chain = build_scope_chain(
            resolved_scope=saved.resolved_scope,
            detected_app_scope=detected_app_scope,
            channel_type=saved.channel_type,
            channel_remote_id=saved.channel_remote_id,
            workspace_id=saved.workspace_id,
            project_id=saved.project_id,
            workflow_id=saved.workflow_id,
        )
        evidence["scope_chain"] = scope_chain
        self._record_resolution_event(
            session_id=session_id,
            run_id=run_id,
            requested_scope=requested_scope,
            resolved_scope=saved.resolved_scope,
            source=scope_source,
            confidence=scope_confidence,
            evidence=evidence,
        )
        return ScopeResolutionResult(
            binding=saved,
            requested_scope=requested_scope,
            scope_chain=scope_chain,
            evidence=evidence,
            reused_existing_binding=False,
        )

    def reresolve_session(
        self,
        *,
        session_id: str,
        user_query: str = "",
        project_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
        workflow_id: Optional[str] = None,
        channel_type: Optional[str] = None,
        channel_remote_id: Optional[str] = None,
        scope_hint: Optional[str] = None,
        scope_mode: Optional[str] = "mixed",
        run_id: Optional[str] = None,
    ) -> ScopeResolutionResult:
        return self.resolve(
            session_id=session_id,
            conversation_id=session_id,
            user_query=user_query,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            workflow_id=workflow_id,
            channel_type=channel_type,
            channel_remote_id=channel_remote_id,
            scope_hint=scope_hint,
            scope_mode=scope_mode,
            run_id=run_id,
            force_reresolve=True,
        )

    def get_scope_history(self, session_id: str) -> List[ScopeResolutionEvent]:
        return self.resolution_repo.list_events(session_id)

    def _resolve_explicit_scope(
        self,
        *,
        requested_scope: Optional[str],
        project_id: Optional[str],
        workspace_id: Optional[str],
        workspace_path: Optional[str],
        workflow_id: Optional[str],
    ) -> tuple[str, Optional[ProjectDescriptor]]:
        if requested_scope:
            normalized = requested_scope.strip()
            if normalized.startswith("project:"):
                explicit_project_id = normalized.split(":", 1)[1]
                return normalized, self.project_registry.get_project(explicit_project_id)
            if normalized.startswith("workflow:"):
                return normalized, None
            if normalized.startswith("workspace:"):
                return normalized, self.project_registry.find_project_for_workspace(
                    workspace_id=workspace_id or normalized.split(":", 1)[1],
                    workspace_path=workspace_path,
                )
            return normalized, None

        if workflow_id:
            workflow_project = self.project_registry.find_project_for_workflow(workflow_id)
            return _scope_for_workflow(workflow_id) or "global", workflow_project

        if project_id:
            return _scope_for_project(project_id) or "global", self.project_registry.get_project(project_id)

        if workspace_id:
            workspace_project = self.project_registry.find_project_for_workspace(
                workspace_id=workspace_id,
                workspace_path=workspace_path,
            )
            if workspace_project:
                return workspace_project.default_scope or _scope_for_project(workspace_project.project_id) or "global", workspace_project
            return _scope_for_workspace(workspace_id) or "global", None

        return "global", None

    def _record_resolution_event(
        self,
        *,
        session_id: str,
        run_id: Optional[str],
        requested_scope: Optional[str],
        resolved_scope: str,
        source: str,
        confidence: float,
        evidence: Dict[str, Any],
    ):
        self.resolution_repo.append_event(
            ScopeResolutionEvent(
                id=f"scope_evt_{uuid.uuid4().hex}",
                session_id=session_id,
                run_id=run_id,
                requested_scope=requested_scope,
                resolved_scope=resolved_scope,
                source=source,
                confidence=confidence,
                evidence=evidence,
            )
        )


session_scope_binding_service = SessionScopeBindingService()
scope_resolution_service = ScopeResolutionService(
    project_registry=project_registry_service,
    binding_service=session_scope_binding_service,
)
