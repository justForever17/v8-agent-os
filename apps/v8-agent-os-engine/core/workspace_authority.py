from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.workspace_resolution import workspace_resolution_service
from runtimes.memory.project_registry import project_registry_service


@dataclass(frozen=True)
class WorkspaceAuthorityDescriptor:
    runtime_kind: str
    workspace_id: str
    project_id: str
    workspace_root: str
    main_workspace_root: str
    source: str
    trust_state: str
    trust_source: str
    uses_scoped_workspace: bool
    is_scoped_override: bool
    is_fallback_to_main: bool
    side_effects_allowed: bool
    capabilities: dict[str, bool]
    path_status: dict[str, Any]
    external_workspace: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "runtimeKind": self.runtime_kind,
            "workspaceId": self.workspace_id,
            "projectId": self.project_id,
            "workspaceRoot": self.workspace_root,
            "mainWorkspaceRoot": self.main_workspace_root,
            "source": self.source,
            "trustState": self.trust_state,
            "trustSource": self.trust_source,
            "usesScopedWorkspace": self.uses_scoped_workspace,
            "isScopedOverride": self.is_scoped_override,
            "isFallbackToMain": self.is_fallback_to_main,
            "sideEffectsAllowed": self.side_effects_allowed,
            "capabilities": dict(self.capabilities),
            "pathStatus": dict(self.path_status or {}),
        }
        if self.external_workspace:
            payload["externalWorkspace"] = dict(self.external_workspace)
        return payload


class WorkspaceAuthorityService:
    @staticmethod
    def _normalize_path(value: str | None) -> str:
        raw = str(value or "").strip()
        return str(Path(raw).expanduser()) if raw else ""

    @staticmethod
    def _same_path(left: str | None, right: str | None) -> bool:
        try:
            return Path(str(left or "")).expanduser().resolve(strict=False) == Path(str(right or "")).expanduser().resolve(strict=False)
        except Exception:
            return str(left or "").strip().rstrip("\\/").lower() == str(right or "").strip().rstrip("\\/").lower()

    def resolve(
        self,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_id: str | None = None,
        explicit_workspace_path: str | None = None,
        explicit_project_id: str | None = None,
        external_workspace: dict[str, Any] | None = None,
    ) -> WorkspaceAuthorityDescriptor:
        descriptor = workspace_resolution_service.resolve_workspace_descriptor(
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_id=explicit_workspace_id,
            explicit_workspace_path=explicit_workspace_path,
            explicit_project_id=explicit_project_id,
        )
        runtime = str(descriptor.get("runtimeKind") or runtime_kind or "").strip()
        workspace_root = self._normalize_path(str(descriptor.get("workspaceRoot") or ""))
        main_root = self._normalize_path(str(descriptor.get("mainWorkspacePath") or workspace_resolution_service.get_main_workspace_path()))
        workspace_id = str(descriptor.get("workspaceId") or explicit_workspace_id or "").strip()
        project_id = str(descriptor.get("ProjectId") or descriptor.get("projectId") or explicit_project_id or "").strip()
        source = str(descriptor.get("source") or "").strip() or "main_workspace"
        uses_scoped = bool(descriptor.get("usesScopedWorkspace"))
        is_scoped_override = bool(descriptor.get("isScopedOverride"))
        has_explicit_scope = any(
            str(value or "").strip()
            for value in (explicit_workspace_id, explicit_workspace_path, explicit_project_id)
        )
        is_fallback_to_main = uses_scoped and source == "main_workspace" and not has_explicit_scope

        trust_state = "restricted"
        trust_source = "restricted_default"
        project = project_registry_service.get_project(project_id) if project_id else None
        if source == "main_workspace" or self._same_path(workspace_root, main_root):
            trust_state = "trusted"
            trust_source = "system_main"
        if project:
            trust_state = str(getattr(project, "workspace_trust_state", "") or "trusted").strip().lower()
            if trust_state not in {"trusted", "restricted"}:
                trust_state = "trusted"
            trust_source = str(getattr(project, "workspace_trust_source", "") or "legacy_auto_trusted").strip()
        elif source == "explicit_workspace_path" and not self._same_path(workspace_root, main_root):
            trust_state = "restricted"
            trust_source = "restricted_default"

        side_effects_allowed = trust_state == "trusted" and not is_fallback_to_main
        capabilities = {
            "localRead": True,
            "localWrite": side_effects_allowed,
            "commandCwd": side_effects_allowed,
            "upload": side_effects_allowed,
            "workspaceRules": side_effects_allowed,
            "externalWorker": side_effects_allowed,
        }
        external_payload = dict(external_workspace or {}) if isinstance(external_workspace, dict) else None
        if external_payload is None and source == "explicit_workspace_path" and not project and not self._same_path(workspace_root, main_root):
            external_payload = {
                "workspacePath": workspace_root,
                "workspaceId": workspace_id or None,
                "projectId": project_id or None,
                "source": "unregistered_workspace_path",
            }
        return WorkspaceAuthorityDescriptor(
            runtime_kind=runtime,
            workspace_id=workspace_id,
            project_id=project_id,
            workspace_root=workspace_root,
            main_workspace_root=main_root,
            source=source,
            trust_state=trust_state,
            trust_source=trust_source,
            uses_scoped_workspace=uses_scoped,
            is_scoped_override=is_scoped_override,
            is_fallback_to_main=is_fallback_to_main,
            side_effects_allowed=side_effects_allowed,
            capabilities=capabilities,
            path_status=dict(descriptor.get("pathStatus") or {}),
            external_workspace=external_payload,
        )

    def resolve_from_context(self, runtime_context: dict[str, Any] | None = None, *, runtime_kind: str | None = None) -> WorkspaceAuthorityDescriptor:
        context = dict(runtime_context or {})
        effective_runtime_kind = str(runtime_kind or context.get("runtime_kind") or context.get("runtimeKind") or "chat").strip() or "chat"
        external_workspace = context.get("external_workspace") or context.get("externalWorkspace")
        return self.resolve(
            runtime_kind=effective_runtime_kind,
            session_id=str(context.get("session_id") or context.get("sessionId") or "").strip() or None,
            explicit_workspace_id=str(context.get("workspace_id") or context.get("workspaceId") or "").strip() or None,
            explicit_workspace_path=str(context.get("workspace_path") or context.get("workspacePath") or "").strip() or None,
            explicit_project_id=str(context.get("project_id") or context.get("projectId") or "").strip() or None,
            external_workspace=external_workspace if isinstance(external_workspace, dict) else None,
        )


workspace_authority_service = WorkspaceAuthorityService()
