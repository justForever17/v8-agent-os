from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.v8_agent_os_paths import WORKSPACE_HOME
from core.database import db
from core.time_truth import utc_now_iso
from persistence.repositories.project_registry_repository import ProjectRegistryRepository
from persistence.repositories.scope_binding_repository import ScopeBindingRepository
from runtimes.memory.models import ChannelBinding, ProjectDescriptor, WorkflowBinding, WorkspaceProjectBinding


DEFAULT_AGENTS_TEMPLATE = "\n".join(
    [
        "# Workspace Rules",
        "",
        "Add concise runtime instructions for this workspace here.",
        "Keep this file under 10000 estimated tokens.",
        "",
    ]
)


class WorkspaceTrustRequiredError(ValueError):
    pass


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
        project_id = self.get_effective_default_project_id()
        if not project_id:
            return None
        return self.get_project(project_id)

    def get_effective_default_project_id(self) -> Optional[str]:
        """Resolve the project attached to the canonical default workspace.

        The workspace config is the product truth. ``defaultProjectId`` can be
        stale after tests or an older client changes the default path, so it is
        only a fallback when it still points at that same physical workspace.
        """

        from core.storage import storage
        from core.workspace_identity import get_main_workspace_path, workspace_path_key

        workspace_config = storage.get_workspace_config() or {}
        configured_project_id = str(
            workspace_config.get("projectId") or workspace_config.get("project_id") or ""
        ).strip()
        main_path_key = workspace_path_key(get_main_workspace_path())
        projects = [project for project in self.list_projects() if bool(project.active)]

        if configured_project_id:
            configured = next(
                (project for project in projects if project.project_id == configured_project_id),
                None,
            )
            if configured and workspace_path_key(configured.workspace_path) == main_path_key:
                return configured.project_id

        path_match = next(
            (project for project in projects if workspace_path_key(project.workspace_path) == main_path_key),
            None,
        )
        if path_match:
            return path_match.project_id

        stored_project_id = str(self.project_repo.get_default_project_id() or "").strip()
        stored = next((project for project in projects if project.project_id == stored_project_id), None)
        if stored and workspace_path_key(stored.workspace_path) == main_path_key:
            return stored.project_id
        return None

    def save_project(self, payload: Dict[str, Any]) -> ProjectDescriptor:
        prepared_payload = dict(payload or {})
        payload_has_trust = any(key in prepared_payload for key in ("workspaceTrustState", "workspace_trust_state"))
        project_id = str(prepared_payload.get("id") or "").strip()
        resolved_workspace_path = str(
            prepared_payload.get("workspacePath") or prepared_payload.get("workspace_path") or ""
        ).strip()
        user_supplied_workspace_path = bool(resolved_workspace_path)
        existing_project = self.get_project(project_id) if project_id else None
        resolved_name = self._derive_project_name(
            name=prepared_payload.get("name"),
            workspace_path=resolved_workspace_path,
            project_id=project_id,
        )
        prepared_payload["name"] = resolved_name
        if not project_id and resolved_workspace_path:
            existing_project = self.find_project_for_workspace(workspace_path=resolved_workspace_path)
            if existing_project:
                project_id = existing_project.project_id
                if not prepared_payload.get("name"):
                    resolved_name = existing_project.name
                    prepared_payload["name"] = resolved_name

        if not project_id:
            project_id = self._generate_project_id(
                name=resolved_name,
                workspace_path=resolved_workspace_path,
            )
        prepared_payload["id"] = project_id
        if not str(prepared_payload.get("workspaceId") or "").strip():
            prepared_payload["workspaceId"] = project_id
        workspace_path = resolved_workspace_path
        if not workspace_path:
            workspace_path = str(self._default_project_workspace_path(project_id))
            prepared_payload["workspacePath"] = workspace_path
            prepared_payload["name"] = self._derive_project_name(
                name=resolved_name,
                workspace_path=workspace_path,
                project_id=project_id,
            )
        prepared_payload["defaultScope"] = f"project:{project_id}"
        self._apply_workspace_trust_defaults(
            prepared_payload,
            workspace_path=workspace_path,
            user_supplied_workspace_path=user_supplied_workspace_path,
            existing_project=existing_project,
            payload_has_trust=payload_has_trust,
        )

        workspace_root = Path(workspace_path).expanduser()

        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
            self._ensure_workspace_skeleton(workspace_path)
        except Exception as exc:
            raise RuntimeError(f"Failed to create project workspace directory: {workspace_path}") from exc

        # Project registration deliberately stops at the filesystem boundary.
        # Enabling Git parallel isolation is a separate, explicit operation
        # because it creates a repository and baseline commit. A non-Git
        # workspace remains a fully valid V8OS workspace.

        descriptor = ProjectDescriptor.model_validate(prepared_payload).normalized()
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
        current_path = str(current.workspace_path or "").strip()
        next_path = str((updates or {}).get("workspacePath") or (updates or {}).get("workspace_path") or current_path).strip()
        if next_path and current_path and self._normalize_path_key(next_path) != self._normalize_path_key(current_path):
            if not any(key in (updates or {}) for key in ("workspaceTrustState", "workspace_trust_state")):
                merged.pop("workspaceTrustState", None)
                merged.pop("workspace_trust_state", None)
                merged.pop("workspaceTrustSource", None)
                merged.pop("workspace_trust_source", None)
        merged.update(updates or {})
        merged["id"] = project_id
        return self.save_project(merged)

    def delete_project(self, project_id: str) -> bool:
        deleted = self.project_repo.delete_project(project_id)
        if deleted:
            self.scope_repo.delete_workspace_bindings(project_id)
            db.delete_project_descriptor_cache(project_id)
        return deleted

    def set_default_project(self, project_id: Optional[str]):
        self.project_repo.set_default_project(project_id)

    def list_workspace_presentations(self) -> List[Dict[str, Any]]:
        presentations: List[Dict[str, Any]] = []
        for item in self.project_repo.list_workspace_presentations():
            workspace_path = str(item.get("workspacePath") or "").strip()
            if not workspace_path:
                continue
            path_key = self._normalize_path_key(workspace_path)
            display_name = str(item.get("displayName") or "").strip()
            presentations.append(
                {
                    "workspacePath": workspace_path,
                    "pathKey": path_key,
                    "displayName": display_name,
                    "pinned": bool(item.get("pinned")),
                    "pinnedAt": str(item.get("pinnedAt") or "").strip() or None,
                    "updatedAt": str(item.get("updatedAt") or "").strip() or None,
                }
            )
        return presentations

    def get_workspace_presentation(self, workspace_path: str) -> Optional[Dict[str, Any]]:
        path_key = self._normalize_path_key(workspace_path)
        if not path_key:
            return None
        return next(
            (item for item in self.list_workspace_presentations() if item.get("pathKey") == path_key),
            None,
        )

    def patch_workspace_presentation(self, workspace_path: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        raw_path = str(workspace_path or "").strip()
        if not raw_path:
            raise ValueError("workspace_path_required")
        if len(raw_path) > 4096:
            raise ValueError("workspace_path_too_long")

        path_key = self._normalize_path_key(raw_path)
        current = self.get_workspace_presentation(raw_path) or {
            "workspacePath": raw_path,
            "pathKey": path_key,
            "displayName": "",
            "pinned": False,
            "pinnedAt": None,
        }
        next_value = dict(current)
        if "displayName" in updates or "display_name" in updates:
            display_name = str(updates.get("displayName") or updates.get("display_name") or "").strip()
            if len(display_name) > 80:
                raise ValueError("workspace_display_name_too_long")
            next_value["displayName"] = display_name
        if "pinned" in updates:
            pinned = bool(updates.get("pinned"))
            next_value["pinned"] = pinned
            next_value["pinnedAt"] = (
                str(current.get("pinnedAt") or "").strip() or utc_now_iso()
            ) if pinned else None

        next_value.update(
            {
                "workspacePath": raw_path,
                "pathKey": path_key,
                "updatedAt": utc_now_iso(),
            }
        )
        if not next_value.get("displayName") and not next_value.get("pinned"):
            self.project_repo.delete_workspace_presentation(path_key)
            return {
                "workspacePath": raw_path,
                "pathKey": path_key,
                "displayName": "",
                "pinned": False,
                "pinnedAt": None,
                "updatedAt": next_value["updatedAt"],
            }
        return self.project_repo.save_workspace_presentation(path_key, next_value)

    def bind_workspace(
        self,
        *,
        project_id: str,
        workspace_id: str,
        workspace_path: str,
        workspace_trust_state: str | None = None,
        workspace_trust_source: str | None = None,
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
                **({"workspaceTrustState": workspace_trust_state} if workspace_trust_state else {}),
                **({"workspaceTrustSource": workspace_trust_source} if workspace_trust_source else {}),
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
            bound_project = self.get_project(binding.project_id)
            if bound_project is not None:
                return bound_project

        normalized_path = self._normalize_path_key(workspace_path or "")
        for project in self.list_projects():
            if workspace_id and project.workspace_id == workspace_id:
                return project
            if normalized_path and project.workspace_path:
                project_path = self._normalize_path_key(project.workspace_path)
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
                "workspace_trust_state": descriptor.workspace_trust_state,
                "workspace_trust_source": descriptor.workspace_trust_source,
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

    @staticmethod
    def _ensure_workspace_skeleton(workspace_path: str) -> None:
        normalized_workspace_path = str(workspace_path or "").strip()
        if not normalized_workspace_path:
            return
        workspace_root = Path(normalized_workspace_path).expanduser()
        agents_root = workspace_root / ".agents"
        rules_root = agents_root / "rules"
        skills_root = agents_root / "skills"
        rules_root.mkdir(parents=True, exist_ok=True)
        skills_root.mkdir(parents=True, exist_ok=True)
        agents_file = rules_root / "AGENTS.md"
        if not agents_file.exists():
            agents_file.write_text(DEFAULT_AGENTS_TEMPLATE, encoding="utf-8")

    def _generate_project_id(self, *, name: Any, workspace_path: Any) -> str:
        raw_name = str(name or "").strip()
        raw_workspace_path = str(workspace_path or "").strip().rstrip("\\/")
        workspace_leaf = re.split(r"[\\/]+", raw_workspace_path)[-1] if raw_workspace_path else ""
        preferred_seed = raw_name or workspace_leaf
        slug = re.sub(r"[^a-z0-9]+", "-", preferred_seed.lower()).strip("-")
        if not slug:
            slug = f"project-{uuid.uuid4().hex[:8]}"

        existing_ids = {project.project_id for project in self.list_projects()}
        if slug not in existing_ids:
            return slug

        suffix = 2
        while f"{slug}-{suffix}" in existing_ids:
            suffix += 1
        return f"{slug}-{suffix}"

    @staticmethod
    def _derive_project_name(*, name: Any, workspace_path: Any, project_id: Any) -> str:
        raw_name = str(name or "").strip()
        if raw_name:
            return raw_name
        raw_workspace_path = str(workspace_path or "").strip().rstrip("\\/")
        workspace_leaf = re.split(r"[\\/]+", raw_workspace_path)[-1] if raw_workspace_path else ""
        if workspace_leaf:
            return workspace_leaf
        fallback_project_id = str(project_id or "").strip()
        return fallback_project_id or "project"

    @staticmethod
    def _default_project_workspace_path(project_id: str) -> Path:
        return ProjectRegistryService._main_workspace_root() / "projects" / str(project_id or "").strip()

    @staticmethod
    def _normalize_path_key(value: str) -> str:
        try:
            return str(Path(str(value or "")).expanduser().resolve(strict=False)).rstrip("\\/").lower()
        except Exception:
            return str(value or "").strip().rstrip("\\/").lower()

    @staticmethod
    def _is_under_main_workspace(workspace_path: str) -> bool:
        try:
            path = Path(str(workspace_path or "")).expanduser().resolve(strict=False)
            main = ProjectRegistryService._main_workspace_root()
            path.relative_to(main)
            return True
        except Exception:
            return False

    @staticmethod
    def _main_workspace_root() -> Path:
        try:
            from core.storage import storage

            configured = str((storage.get_workspace_config() or {}).get("agent_workspace_path") or "").strip()
            if configured:
                return Path(configured).expanduser().resolve(strict=False)
        except Exception:
            pass
        return WORKSPACE_HOME.expanduser().resolve(strict=False)

    def _apply_workspace_trust_defaults(
        self,
        payload: Dict[str, Any],
        *,
        workspace_path: str,
        user_supplied_workspace_path: bool,
        existing_project: ProjectDescriptor | None,
        payload_has_trust: bool,
    ) -> None:
        raw_state = str(payload.get("workspaceTrustState") or payload.get("workspace_trust_state") or "").strip().lower()
        if raw_state:
            if raw_state not in {"trusted", "restricted"}:
                raise ValueError("workspace_trust_state_invalid")
            payload["workspaceTrustState"] = raw_state
            payload["workspaceTrustSource"] = (
                str(payload.get("workspaceTrustSource") or payload.get("workspace_trust_source") or "").strip()
                or ("user_confirmed" if raw_state == "trusted" else "restricted_default")
            )
            return

        existing_same_workspace = (
            existing_project is not None
            and self._normalize_path_key(str(existing_project.workspace_path or "")) == self._normalize_path_key(workspace_path)
        )
        if existing_same_workspace and not payload_has_trust:
            payload["workspaceTrustState"] = str(existing_project.workspace_trust_state or "trusted").strip() or "trusted"
            payload["workspaceTrustSource"] = str(existing_project.workspace_trust_source or "legacy_auto_trusted").strip() or "legacy_auto_trusted"
            return

        if user_supplied_workspace_path and workspace_path and not self._is_under_main_workspace(workspace_path):
            raise WorkspaceTrustRequiredError("workspace_trust_required")

        payload["workspaceTrustState"] = "trusted"
        payload["workspaceTrustSource"] = "system_main" if self._is_under_main_workspace(workspace_path) else "legacy_auto_trusted"


project_registry_service = ProjectRegistryService()
