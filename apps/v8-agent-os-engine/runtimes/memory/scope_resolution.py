from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional

from core.database import db
from persistence.repositories.scope_binding_repository import ScopeBindingRepository
from persistence.repositories.scope_resolution_repository import ScopeResolutionRepository
from runtimes.memory.models import (
    ProjectDescriptor,
    ScopeResolutionEvent,
    ScopeResolutionResult,
    SessionScopeBinding,
)
from runtimes.memory.project_registry import ProjectRegistryService, project_registry_service


_SCOPE_REUSE_ANCHORS = (
    "project_id",
    "workspace_id",
    "workspace_path",
    "workflow_id",
    "channel_type",
    "channel_remote_id",
    "thread_id",
    "scope_hint",
)


def _normalize_scope(scope: Optional[str]) -> Optional[str]:
    value = (scope or "").strip()
    return value or None


def _normalize_anchor_value(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _requested_scope_anchors(
    *,
    project_id: Optional[str],
    workspace_id: Optional[str],
    workspace_path: Optional[str],
    workflow_id: Optional[str],
    channel_type: Optional[str],
    channel_remote_id: Optional[str],
    thread_id: Optional[str],
    scope_hint: Optional[str],
) -> Dict[str, Optional[str]]:
    return {
        "project_id": _normalize_anchor_value(project_id),
        "workspace_id": _normalize_anchor_value(workspace_id),
        "workspace_path": _normalize_anchor_value(workspace_path),
        "workflow_id": _normalize_anchor_value(workflow_id),
        "channel_type": _normalize_anchor_value(channel_type),
        "channel_remote_id": _normalize_anchor_value(channel_remote_id),
        "thread_id": _normalize_anchor_value(thread_id),
        "scope_hint": _normalize_anchor_value(scope_hint),
    }


def _binding_scope_anchors(binding: SessionScopeBinding) -> Dict[str, Optional[str]]:
    return {
        "project_id": _normalize_anchor_value(binding.project_id),
        "workspace_id": _normalize_anchor_value(binding.workspace_id),
        "workspace_path": _normalize_anchor_value(binding.workspace_path),
        "workflow_id": _normalize_anchor_value(binding.workflow_id),
        "channel_type": _normalize_anchor_value(binding.channel_type),
        "channel_remote_id": _normalize_anchor_value(binding.channel_remote_id),
        "thread_id": _normalize_anchor_value(binding.thread_id),
        "scope_hint": _normalize_anchor_value(binding.scope_hint),
    }


def _diff_scope_anchors(
    *,
    existing_binding: SessionScopeBinding,
    requested_anchors: Dict[str, Optional[str]],
) -> tuple[Dict[str, str], Dict[str, Dict[str, Optional[str]]], List[str]]:
    existing_anchors = _binding_scope_anchors(existing_binding)
    matched: Dict[str, str] = {}
    changed: Dict[str, Dict[str, Optional[str]]] = {}
    compared: List[str] = []
    for key in _SCOPE_REUSE_ANCHORS:
        requested_value = requested_anchors.get(key)
        if requested_value is None:
            continue
        compared.append(key)
        existing_value = existing_anchors.get(key)
        if existing_value == requested_value:
            matched[key] = requested_value
        else:
            changed[key] = {
                "previous": existing_value,
                "requested": requested_value,
            }
    return matched, changed, compared


def _scope_for_project(project_id: Optional[str]) -> Optional[str]:
    if not project_id:
        return None
    return f"project:{project_id}"


def _scope_for_channel(channel_type: Optional[str], remote_id: Optional[str]) -> Optional[str]:
    if not channel_type or not remote_id:
        return None
    normalized_remote = str(remote_id).replace(":", "_").replace("/", "_")
    return f"channel:{channel_type}:{normalized_remote}"


def build_scope_chain(
    *,
    resolved_scope: str,
    channel_type: Optional[str] = None,
    channel_remote_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    project_id: Optional[str] = None,
    workflow_id: Optional[str] = None,
) -> List[str]:
    chain: List[str] = ["global"]
    for item in (
        _scope_for_channel(channel_type, channel_remote_id),
        _scope_for_project(project_id),
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
        scope_mode: Optional[str] = "explicit",
        run_id: Optional[str] = None,
        force_reresolve: bool = False,
    ) -> ScopeResolutionResult:
        normalized_scope_mode = (scope_mode or "explicit").strip().lower()
        if normalized_scope_mode not in {"explicit", "infer", "mixed"}:
            normalized_scope_mode = "explicit"

        explicit_requested_scope = _normalize_scope(scope_hint) or _scope_for_project(project_id)
        requested_scope = explicit_requested_scope

        requested_anchors = _requested_scope_anchors(
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            workflow_id=workflow_id,
            channel_type=channel_type,
            channel_remote_id=channel_remote_id,
            thread_id=thread_id,
            scope_hint=scope_hint,
        )

        existing = self.binding_service.get_binding(session_id)
        if existing and existing.status == "active":
            matched_anchors, changed_anchors, compared_anchors = _diff_scope_anchors(
                existing_binding=existing,
                requested_anchors=requested_anchors,
            )
            can_reuse_existing_binding = not changed_anchors
            reuse_evidence = {
                "existing_binding": existing.model_dump(exclude_none=True),
                "scope_anchor_comparison": {
                    "compared": compared_anchors,
                    "matched": matched_anchors,
                    "changed": changed_anchors,
                },
                "reuse_reason": (
                    "no_explicit_anchor_changes"
                    if can_reuse_existing_binding
                    else "explicit_scope_anchor_changed"
                ),
            }
            if force_reresolve:
                can_reuse_existing_binding = False
                reuse_evidence["reuse_reason"] = "force_reresolve_requested"
            if can_reuse_existing_binding:
                scope_chain = build_scope_chain(
                    resolved_scope=existing.resolved_scope,
                    channel_type=existing.channel_type,
                    channel_remote_id=existing.channel_remote_id,
                    workspace_id=existing.workspace_id,
                    project_id=existing.project_id,
                    workflow_id=existing.workflow_id,
                )
                reuse_evidence["scope_chain"] = scope_chain
                self._record_resolution_event(
                    session_id=session_id,
                    run_id=run_id,
                    requested_scope=requested_scope,
                    resolved_scope=existing.resolved_scope,
                    source="existing_binding",
                    confidence=existing.scope_confidence,
                    evidence=reuse_evidence,
                )
                return ScopeResolutionResult(
                    binding=existing,
                    requested_scope=requested_scope,
                    scope_chain=scope_chain,
                    evidence=reuse_evidence,
                    reused_existing_binding=True,
                )

        previous_scope = existing.resolved_scope if existing and existing.status == "active" else None
        rebind_reason = None
        if force_reresolve:
            rebind_reason = "force_reresolve_requested"
        elif existing and existing.status == "active":
            _, changed_anchors, compared_anchors = _diff_scope_anchors(
                existing_binding=existing,
                requested_anchors=requested_anchors,
            )
            if changed_anchors:
                rebind_reason = "explicit_scope_anchor_changed"
        else:
            compared_anchors = []
            changed_anchors = {}

        evidence: Dict[str, Any] = {
            "requested_scope": requested_scope,
            "requested_anchors": {key: value for key, value in requested_anchors.items() if value is not None},
        }
        if existing and existing.status == "active":
            evidence["previous_scope"] = previous_scope
            evidence["rebind_reason"] = rebind_reason or "new_scope_resolution"
            evidence["scope_anchor_comparison"] = {
                "compared": compared_anchors,
                "changed": changed_anchors,
            }

        resolved_project: Optional[ProjectDescriptor] = None
        resolved_scope = "global"
        scope_source = "main_workspace_default"
        scope_confidence = 1.0

        if explicit_requested_scope or project_id or workspace_id or workflow_id:
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
                    channel_scope = _scope_for_channel(channel_type, channel_remote_id)
                    if channel_scope:
                        resolved_scope = channel_scope
                        scope_source = "channel_session_default"
                        scope_confidence = 0.82
                        evidence["channel_session_default"] = {
                            "channel_type": channel_type,
                            "channel_remote_id": channel_remote_id,
                            "resolved_scope": channel_scope,
                        }
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
                            scope_source = "workspace_registry_match"
                            scope_confidence = 0.9
                            evidence["workspace_registry_match"] = workspace_project.model_dump(exclude_none=True)

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
            channel_type=saved.channel_type,
            channel_remote_id=saved.channel_remote_id,
            workspace_id=saved.workspace_id,
            project_id=saved.project_id,
            workflow_id=saved.workflow_id,
        )
        evidence["scope_chain"] = scope_chain
        if previous_scope:
            evidence["next_scope"] = saved.resolved_scope
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
        scope_mode: Optional[str] = "explicit",
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
            legacy_prefix, _, legacy_identifier = normalized.partition(":")
            if legacy_prefix == "workflow" and legacy_identifier:
                workflow_project = self.project_registry.find_project_for_workflow(workflow_id or legacy_identifier)
                if workflow_project:
                    return workflow_project.default_scope or _scope_for_project(workflow_project.project_id) or "global", workflow_project
                return "global", None
            if legacy_prefix == "workspace" and legacy_identifier:
                workspace_project = self.project_registry.find_project_for_workspace(
                    workspace_id=workspace_id or legacy_identifier,
                    workspace_path=workspace_path,
                )
                if workspace_project:
                    return workspace_project.default_scope or _scope_for_project(workspace_project.project_id) or "global", workspace_project
                return "global", None
            if normalized.startswith("channel:"):
                return normalized, None
            return normalized, None

        if workflow_id:
            workflow_project = self.project_registry.find_project_for_workflow(workflow_id)
            if workflow_project:
                return workflow_project.default_scope or _scope_for_project(workflow_project.project_id) or "global", workflow_project
            return "global", None

        if project_id:
            return _scope_for_project(project_id) or "global", self.project_registry.get_project(project_id)

        if workspace_id:
            workspace_project = self.project_registry.find_project_for_workspace(
                workspace_id=workspace_id,
                workspace_path=workspace_path,
            )
            if workspace_project:
                return workspace_project.default_scope or _scope_for_project(workspace_project.project_id) or "global", workspace_project
            return "global", None

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
