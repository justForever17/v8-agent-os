from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.database import db
from core.multimodal_payload_adapter import normalize_artifact_record
from core.workspace_authority import WorkspaceAuthorityDescriptor, workspace_authority_service
from core.workspace_media_library import workspace_media_library


class CreativeMediaResourceAuthorityError(PermissionError):
    """A media resource could not be proven to belong to the active scope."""

    def __init__(self, *, reason_code: str = "media_resource_not_authorized") -> None:
        super().__init__("Media resource is not available in the current session scope")
        self.reason_code = str(reason_code or "media_resource_not_authorized")


@dataclass(frozen=True, slots=True)
class CreativeMediaAuthorityScope:
    session_id: str
    workspace_id: str
    project_id: str
    workspace_root: Path
    authority: WorkspaceAuthorityDescriptor


@dataclass(frozen=True, slots=True)
class AuthorizedCreativeMediaResource:
    resource_kind: str
    resource_id: str
    scope: CreativeMediaAuthorityScope
    path: Path | None = None
    external_url: str | None = None
    record: dict[str, Any] | None = None

    def as_transport(self) -> dict[str, Any]:
        """Return the only resource shape provider adapters should consume."""

        return {
            "schema": "v8.creative_media.authorized_resource.v1",
            "resourceKind": self.resource_kind,
            "resourceId": self.resource_id,
            "sessionId": self.scope.session_id,
            "workspaceId": self.scope.workspace_id or None,
            "projectId": self.scope.project_id or None,
            "localPath": str(self.path) if self.path is not None else None,
            "externalUrl": self.external_url,
        }


def _record_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _clean(value: Any) -> str:
    return str(value or "").strip()


class CreativeMediaResourceAuthorityService:
    """Resolve media inputs against one canonical Session/Workspace authority."""

    def __init__(
        self,
        *,
        database: Any | None = None,
        authority_service: Any | None = None,
        media_library: Any | None = None,
    ) -> None:
        self._database = database or db
        self._authority_service = authority_service or workspace_authority_service
        self._media_library = media_library or workspace_media_library

    @staticmethod
    def resolve_path_value(value: Any, *, workspace_root: Path | None = None) -> Path | None:
        raw = _clean(value)
        if not raw:
            return None
        try:
            path = Path(raw).expanduser()
            if workspace_root is not None and not path.is_absolute():
                path = workspace_root / path
            return path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return None

    @staticmethod
    def path_is_absolute(value: Any) -> bool:
        try:
            return Path(_clean(value)).expanduser().is_absolute()
        except (OSError, RuntimeError, ValueError):
            return False

    @staticmethod
    def path_is_within(path: Path, workspace_root: Path) -> bool:
        try:
            path.relative_to(workspace_root)
            return True
        except ValueError:
            return False

    @staticmethod
    def _same_path(left: Any, right: Path) -> bool:
        resolved = CreativeMediaResourceAuthorityService.resolve_path_value(left)
        return resolved is not None and resolved == right

    def resolve_scope(
        self,
        *,
        session_id: str,
        workspace_id: str | None = None,
        project_id: str | None = None,
        workspace_path: str | None = None,
        runtime_kind: str = "creative_media",
        session_lookup: Callable[[str], Any] | None = None,
        authority_resolver: Callable[..., Any] | None = None,
    ) -> CreativeMediaAuthorityScope:
        normalized_session_id = _clean(session_id)
        lookup = session_lookup or self._database.get_session
        if not normalized_session_id or lookup(normalized_session_id) is None:
            raise CreativeMediaResourceAuthorityError(reason_code="session_authority_unavailable")

        resolve_authority = authority_resolver or self._authority_service.resolve
        try:
            authority = resolve_authority(runtime_kind=runtime_kind, session_id=normalized_session_id)
        except Exception as exc:
            raise CreativeMediaResourceAuthorityError(reason_code="workspace_authority_unavailable") from exc
        resolved_root = self.resolve_path_value(getattr(authority, "workspace_root", ""))
        if resolved_root is None:
            raise CreativeMediaResourceAuthorityError(reason_code="workspace_authority_unavailable")

        authority_workspace_id = _clean(getattr(authority, "workspace_id", ""))
        authority_project_id = _clean(getattr(authority, "project_id", ""))
        if _clean(workspace_id) and _clean(workspace_id) != authority_workspace_id:
            raise CreativeMediaResourceAuthorityError(reason_code="workspace_authority_mismatch")
        if _clean(project_id) and _clean(project_id) != authority_project_id:
            raise CreativeMediaResourceAuthorityError(reason_code="project_authority_mismatch")
        if _clean(workspace_path) and not self._same_path(workspace_path, resolved_root):
            raise CreativeMediaResourceAuthorityError(reason_code="workspace_root_mismatch")
        return CreativeMediaAuthorityScope(
            session_id=normalized_session_id,
            workspace_id=authority_workspace_id,
            project_id=authority_project_id,
            workspace_root=resolved_root,
            authority=authority,
        )

    def artifact_matches_scope(self, artifact: dict[str, Any], *, scope: CreativeMediaAuthorityScope) -> bool:
        normalized = normalize_artifact_record(artifact)
        if _clean(normalized.get("sessionId")) != scope.session_id:
            return False

        metadata = _record_json(normalized.get("metadata"))
        artifact_workspace_id = _clean(normalized.get("workspaceId"))
        artifact_project_id = _clean(normalized.get("projectId"))
        if artifact_workspace_id and artifact_workspace_id != scope.workspace_id:
            return False
        if artifact_project_id and artifact_project_id != scope.project_id:
            return False
        has_workspace_evidence = bool(
            (artifact_workspace_id and artifact_workspace_id == scope.workspace_id)
            or (artifact_project_id and artifact_project_id == scope.project_id)
        )

        declared_root = _clean(normalized.get("workspaceRoot"))
        if declared_root:
            if not self._same_path(declared_root, scope.workspace_root):
                return False
            has_workspace_evidence = True

        metadata_workspace_path = _clean(metadata.get("workspacePath") or metadata.get("workspace_path"))
        if metadata_workspace_path:
            if not self._same_path(metadata_workspace_path, scope.workspace_root):
                return False
            has_workspace_evidence = True

        source_value = _clean(normalized.get("sourcePath"))
        source_path = self.resolve_path_value(source_value, workspace_root=scope.workspace_root)
        if source_value and source_path is None:
            return False
        source_is_within = bool(source_path and self.path_is_within(source_path, scope.workspace_root))
        if source_path and not self.path_is_absolute(source_value) and not source_is_within:
            return False
        has_workspace_evidence = has_workspace_evidence or source_is_within

        workspace_value = _clean(normalized.get("workspacePath"))
        workspace_path = self.resolve_path_value(workspace_value, workspace_root=scope.workspace_root)
        if workspace_value and workspace_path is None:
            return False
        if workspace_path:
            if not self.path_is_within(workspace_path, scope.workspace_root):
                return False
            if source_path and workspace_path != scope.workspace_root and workspace_path != source_path:
                return False
            has_workspace_evidence = True

        relative_value = _clean(normalized.get("workspaceRelativePath"))
        relative_path = self.resolve_path_value(relative_value, workspace_root=scope.workspace_root)
        if relative_value and relative_path is None:
            return False
        if relative_path:
            if self.path_is_absolute(relative_value) or not self.path_is_within(relative_path, scope.workspace_root):
                return False
            if source_path and source_path != relative_path:
                return False
            has_workspace_evidence = True

        storage_class = _clean(normalized.get("storageClass")).lower()
        path_plane = _clean(normalized.get("pathPlane")).lower()
        if (storage_class == "workspace" or path_plane.startswith("workspace_")) and not source_is_within:
            return False
        return has_workspace_evidence

    def resolve_artifact(
        self,
        *,
        session_id: str,
        artifact_id: str,
        workspace_id: str | None = None,
        project_id: str | None = None,
        workspace_path: str | None = None,
        require_local: bool = False,
        artifact_lookup: Callable[[str], Any] | None = None,
    ) -> AuthorizedCreativeMediaResource:
        scope = self.resolve_scope(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            workspace_path=workspace_path,
        )
        normalized_artifact_id = _clean(artifact_id)
        lookup = artifact_lookup or self._database.get_runtime_artifact
        artifact = lookup(normalized_artifact_id) if normalized_artifact_id else None
        if not artifact or not self.artifact_matches_scope(dict(artifact), scope=scope):
            raise CreativeMediaResourceAuthorityError()
        normalized = normalize_artifact_record(dict(artifact))
        path = self.resolve_path_value(normalized.get("sourcePath"), workspace_root=scope.workspace_root)
        if require_local and (path is None or not path.is_file()):
            raise CreativeMediaResourceAuthorityError(reason_code="local_media_resource_unavailable")
        return AuthorizedCreativeMediaResource(
            resource_kind="artifact",
            resource_id=normalized_artifact_id,
            scope=scope,
            path=path,
            external_url=_clean(normalized.get("externalUrl")) or None,
            record=normalized,
        )

    @staticmethod
    def _record_scope_matches(record: dict[str, Any], *, scope: CreativeMediaAuthorityScope) -> bool:
        metadata = _record_json(record.get("metadata") or record.get("metadata_json"))
        resource_ref = _record_json(record.get("resourceRef") or record.get("resource_ref") or record.get("resource_ref_json"))
        declared_workspace_id = _clean(
            record.get("workspaceId")
            or record.get("workspace_id")
            or metadata.get("workspaceId")
            or metadata.get("workspace_id")
            or resource_ref.get("workspaceId")
        )
        declared_project_id = _clean(
            record.get("projectId")
            or record.get("project_id")
            or metadata.get("projectId")
            or metadata.get("project_id")
            or resource_ref.get("projectId")
        )
        if declared_workspace_id and declared_workspace_id != scope.workspace_id:
            return False
        if declared_project_id and declared_project_id != scope.project_id:
            return False
        return True

    def resolve_source(
        self,
        *,
        session_id: str,
        source_id: str,
        workspace_id: str | None = None,
        project_id: str | None = None,
        workspace_path: str | None = None,
        require_local: bool = True,
    ) -> AuthorizedCreativeMediaResource:
        scope = self.resolve_scope(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            workspace_path=workspace_path,
        )
        normalized_source_id = _clean(source_id)
        source = self._database.get_session_source(session_id=scope.session_id, source_id=normalized_source_id)
        if not source or _clean(source.get("sessionId") or source.get("session_id")) != scope.session_id:
            raise CreativeMediaResourceAuthorityError()
        if not self._record_scope_matches(dict(source), scope=scope):
            raise CreativeMediaResourceAuthorityError()
        metadata = _record_json(source.get("metadata") or source.get("metadata_json"))
        resource_ref = _record_json(source.get("resourceRef") or source.get("resource_ref") or source.get("resource_ref_json"))
        path_value = _clean(
            source.get("workspacePath")
            or source.get("workspace_path")
            or resource_ref.get("workspacePath")
        )
        relative_value = _clean(
            source.get("workspaceRelativePath")
            or source.get("workspace_relative_path")
            or resource_ref.get("workspaceRelativePath")
            or metadata.get("workspaceRelativePath")
        )
        path = self.resolve_path_value(relative_value or path_value, workspace_root=scope.workspace_root)
        if path is None or not self.path_is_within(path, scope.workspace_root):
            raise CreativeMediaResourceAuthorityError()
        if require_local and not path.is_file():
            raise CreativeMediaResourceAuthorityError(reason_code="local_media_resource_unavailable")
        return AuthorizedCreativeMediaResource(
            resource_kind="source",
            resource_id=normalized_source_id,
            scope=scope,
            path=path,
            external_url=_clean(source.get("externalUrl") or source.get("external_url")) or None,
            record=dict(source),
        )

    def resolve_workspace_asset(
        self,
        *,
        session_id: str,
        asset_id: str,
        workspace_id: str | None = None,
        project_id: str | None = None,
        workspace_path: str | None = None,
    ) -> AuthorizedCreativeMediaResource:
        scope = self.resolve_scope(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            workspace_path=workspace_path,
        )
        normalized_asset_id = _clean(asset_id)
        try:
            asset = self._media_library.get_asset(session_id=scope.session_id, asset_id=normalized_asset_id)
            path = self._media_library.resolve_asset_path(
                session_id=scope.session_id,
                asset_id=normalized_asset_id,
                require_session_use=True,
            )
        except Exception as exc:
            raise CreativeMediaResourceAuthorityError() from exc
        if not self._record_scope_matches(dict(asset), scope=scope):
            raise CreativeMediaResourceAuthorityError()
        resolved = self.resolve_path_value(path)
        if resolved is None or not self.path_is_within(resolved, scope.workspace_root) or not resolved.is_file():
            raise CreativeMediaResourceAuthorityError(reason_code="local_media_resource_unavailable")
        return AuthorizedCreativeMediaResource(
            resource_kind="workspace_asset",
            resource_id=normalized_asset_id,
            scope=scope,
            path=resolved,
            record=dict(asset),
        )

    def resolve_path(
        self,
        *,
        session_id: str,
        path: str,
        workspace_id: str | None = None,
        project_id: str | None = None,
        workspace_path: str | None = None,
    ) -> AuthorizedCreativeMediaResource:
        scope = self.resolve_scope(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            workspace_path=workspace_path,
        )
        resolved = self.resolve_path_value(path, workspace_root=scope.workspace_root)
        if resolved is None or not self.path_is_within(resolved, scope.workspace_root) or not resolved.is_file():
            raise CreativeMediaResourceAuthorityError(reason_code="local_media_resource_unavailable")
        return AuthorizedCreativeMediaResource(
            resource_kind="path",
            resource_id=resolved.relative_to(scope.workspace_root).as_posix(),
            scope=scope,
            path=resolved,
        )

    def resolve_output_path(
        self,
        *,
        session_id: str,
        path: str,
        workspace_id: str | None = None,
        project_id: str | None = None,
        workspace_path: str | None = None,
    ) -> AuthorizedCreativeMediaResource:
        """Authorize a not-yet-created output while keeping it inside the workspace."""

        scope = self.resolve_scope(
            session_id=session_id,
            workspace_id=workspace_id,
            project_id=project_id,
            workspace_path=workspace_path,
        )
        resolved = self.resolve_path_value(path, workspace_root=scope.workspace_root)
        if resolved is None or not self.path_is_within(resolved, scope.workspace_root):
            raise CreativeMediaResourceAuthorityError(reason_code="media_output_path_not_authorized")
        return AuthorizedCreativeMediaResource(
            resource_kind="output_path",
            resource_id=resolved.relative_to(scope.workspace_root).as_posix(),
            scope=scope,
            path=resolved,
        )

    def authorize_request_resources(self, request: dict[str, Any]) -> list[AuthorizedCreativeMediaResource]:
        """Preflight declared resource inputs without performing any side effect."""

        payload = dict(request or {})
        session_id = _clean(payload.get("sessionId") or payload.get("session_id"))
        workspace_id = _clean(payload.get("workspaceId") or payload.get("workspace_id"))
        project_id = _clean(payload.get("projectId") or payload.get("project_id"))
        workspace_path = _clean(payload.get("workspacePath") or payload.get("workspace_path"))
        resolved: list[AuthorizedCreativeMediaResource] = []
        seen: set[tuple[str, str]] = set()

        def append(kind: str, value: Any) -> None:
            identifier = _clean(value)
            if not identifier or (kind, identifier) in seen:
                return
            seen.add((kind, identifier))
            common = {
                "session_id": session_id,
                "workspace_id": workspace_id,
                "project_id": project_id,
                "workspace_path": workspace_path,
            }
            if kind == "artifact":
                resolved.append(self.resolve_artifact(artifact_id=identifier, **common))
            elif kind == "source":
                resolved.append(self.resolve_source(source_id=identifier, **common))
            elif kind == "workspace_asset":
                resolved.append(self.resolve_workspace_asset(asset_id=identifier, **common))
            elif kind == "output_path":
                resolved.append(self.resolve_output_path(path=identifier, **common))
            else:
                resolved.append(self.resolve_path(path=identifier, **common))

        artifact_keys = {"artifactId", "artifact_id", "candidateArtifactId", "referenceArtifactId"}
        artifact_list_keys = {"artifactIds", "artifact_ids", "referenceAssetIds", "reference_asset_ids"}
        source_keys = {"sourceId", "source_id", "maskSourceId", "mask_source_id"}
        workspace_asset_keys = {"workspaceAssetId", "workspace_asset_id"}
        path_keys = {
            "sourcePath",
            "source_path",
            "path",
            "candidatePath",
            "candidate_path",
            "referencePath",
            "reference_path",
        }
        output_path_keys = {"outputPath", "output_path", "previewPath", "preview_path"}

        def visit(value: Any, depth: int = 0) -> None:
            if depth > 6:
                raise CreativeMediaResourceAuthorityError(reason_code="media_resource_manifest_too_deep")
            if isinstance(value, dict):
                for key, nested in value.items():
                    if key in artifact_keys:
                        append("artifact", nested)
                    elif key in artifact_list_keys:
                        for item in list(nested or []) if not isinstance(nested, str) else [nested]:
                            append("artifact", item.get("artifactId") if isinstance(item, dict) else item)
                    elif key in source_keys:
                        append("source", nested)
                    elif key in workspace_asset_keys:
                        append("workspace_asset", nested)
                    elif key in path_keys:
                        append("path", nested)
                    elif key in output_path_keys:
                        append("output_path", nested)
                    elif key == "artifacts" and isinstance(nested, list):
                        for item in nested:
                            if isinstance(item, str):
                                append("artifact", item)
                    visit(nested, depth + 1)
            elif isinstance(value, list):
                if len(value) > 200:
                    raise CreativeMediaResourceAuthorityError(
                        reason_code="media_resource_manifest_too_large"
                    )
                for nested in value:
                    visit(nested, depth + 1)

        visit(payload)
        if resolved:
            self.resolve_scope(
                session_id=session_id,
                workspace_id=workspace_id,
                project_id=project_id,
                workspace_path=workspace_path,
            )
        return resolved


creative_media_resource_authority = CreativeMediaResourceAuthorityService()


__all__ = [
    "AuthorizedCreativeMediaResource",
    "CreativeMediaAuthorityScope",
    "CreativeMediaResourceAuthorityError",
    "CreativeMediaResourceAuthorityService",
    "creative_media_resource_authority",
]
