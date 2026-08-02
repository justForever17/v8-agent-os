from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.database import db
from core.workspace_authority import WorkspaceAuthorityDescriptor, workspace_authority_service
from core.workspace_identity import workspace_path_key


_FOLDER_KINDS = {"production", "episode", "sources", "work", "outputs", "delivery", "custom"}
_MEDIA_TYPES = {"image", "video", "audio", "model_3d", "psd", "motion", "document", "text", "unknown"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _media_type(mime_type: str, path: Path) -> str:
    mime = str(mime_type or "").lower()
    suffix = path.suffix.lower()
    if suffix == ".psd" or "photoshop" in mime:
        return "psd"
    if suffix == ".v8motion" or mime == "application/vnd.v8.motion+zip":
        return "motion"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("text/") or suffix in {".txt", ".md", ".json", ".yaml", ".yml", ".csv"}:
        return "text"
    if suffix in {".glb", ".gltf", ".obj", ".fbx", ".stl", ".usd", ".usdz"}:
        return "model_3d"
    if mime == "application/pdf" or suffix in {".pdf", ".doc", ".docx", ".ppt", ".pptx"}:
        return "document"
    return "unknown"


class WorkspaceMediaLibraryError(ValueError):
    pass


class WorkspaceMediaLibraryService:
    def _authority(self, session_id: str, *, require_write: bool = False) -> WorkspaceAuthorityDescriptor:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id or not db.get_session(normalized_session_id):
            raise WorkspaceMediaLibraryError("Current session is unavailable")
        authority = workspace_authority_service.resolve(runtime_kind="chat", session_id=normalized_session_id)
        if not authority.workspace_root:
            raise WorkspaceMediaLibraryError("Current session has no bound workspace")
        if require_write and not authority.side_effects_allowed:
            raise PermissionError("Current session workspace does not allow media library writes")
        return authority

    @staticmethod
    def _workspace_key(authority: WorkspaceAuthorityDescriptor) -> str:
        key = workspace_path_key(authority.workspace_root)
        if not key:
            raise WorkspaceMediaLibraryError("Current session workspace identity is unavailable")
        return key

    @staticmethod
    def _resolve_candidate(
        authority: WorkspaceAuthorityDescriptor,
        *,
        path_value: str,
        relative_value: str = "",
    ) -> tuple[Path, str]:
        root = Path(authority.workspace_root).expanduser().resolve(strict=False)
        relative = str(relative_value or "").strip().replace("\\", "/").lstrip("/")
        raw = str(path_value or "").strip()
        if relative:
            candidate = (root / relative).resolve(strict=False)
        elif raw:
            path = Path(raw).expanduser()
            candidate = path.resolve(strict=False) if path.is_absolute() else (root / path).resolve(strict=False)
        else:
            raise WorkspaceMediaLibraryError("Media resource has no local workspace locator")
        try:
            workspace_relative_path = candidate.relative_to(root).as_posix()
        except ValueError as exc:
            raise PermissionError("Media resource escapes the current session workspace") from exc
        if not candidate.is_file():
            raise FileNotFoundError("Media resource is unavailable in the current session workspace")
        return candidate, workspace_relative_path

    @staticmethod
    def _asset_id(workspace_key_value: str, workspace_relative_path: str) -> str:
        normalized_locator = os.path.normcase(workspace_relative_path).replace("\\", "/")
        digest = hashlib.sha256(
            f"{workspace_key_value}\n{normalized_locator}".encode("utf-8", errors="ignore")
        ).hexdigest()[:24]
        return f"wma_{digest}"

    def _upsert_asset(
        self,
        *,
        authority: WorkspaceAuthorityDescriptor,
        path: Path,
        workspace_relative_path: str,
        title: str,
        mime_type: str,
        origin_kind: str,
        origin_id: str,
        origin_session_id: str,
        origin_run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workspace_key_value = self._workspace_key(authority)
        asset_id = self._asset_id(workspace_key_value, workspace_relative_path)
        now = _utc_now()
        detected_mime = str(mime_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        payload = {
            "originKind": str(origin_kind or "workspace_file"),
            "originId": str(origin_id or ""),
            "originSessionId": str(origin_session_id or ""),
            "originRunId": str(origin_run_id or ""),
            **dict(metadata or {}),
        }
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO workspace_media_assets (
                    asset_id, workspace_key, workspace_id, project_id, workspace_relative_path,
                    title, media_type, mime_type, byte_size, content_sha256, origin_kind,
                    origin_id, origin_session_id, origin_run_id, metadata_json,
                    created_at, updated_at, deleted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT(asset_id) DO UPDATE SET
                    workspace_id = excluded.workspace_id,
                    project_id = excluded.project_id,
                    title = excluded.title,
                    media_type = excluded.media_type,
                    mime_type = excluded.mime_type,
                    byte_size = excluded.byte_size,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at,
                    deleted_at = NULL
                """,
                (
                    asset_id,
                    workspace_key_value,
                    authority.workspace_id or None,
                    authority.project_id or None,
                    workspace_relative_path,
                    str(title or path.name).strip() or path.name,
                    _media_type(detected_mime, path),
                    detected_mime,
                    path.stat().st_size,
                    str(origin_kind or "workspace_file"),
                    str(origin_id or "") or None,
                    str(origin_session_id or "") or None,
                    str(origin_run_id or "") or None,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()
        return self.get_asset(session_id=origin_session_id, asset_id=asset_id)

    def register_source(self, *, session_id: str, source_id: str, attach_to_session: bool = True) -> dict[str, Any]:
        authority = self._authority(session_id, require_write=True)
        source = db.get_session_source(session_id=session_id, source_id=source_id)
        if not source:
            raise WorkspaceMediaLibraryError("Source is not bound to the current session")
        if str(source.get("sourceKind") or source.get("source_kind") or "").strip() == "canvas_mask":
            raise WorkspaceMediaLibraryError("Internal Canvas masks are not workspace media assets")
        metadata = _record_json(source.get("metadata") or source.get("metadata_json"))
        resource_ref = _record_json(source.get("resourceRef") or source.get("resource_ref") or source.get("resource_ref_json"))
        path, relative = self._resolve_candidate(
            authority,
            path_value=str(source.get("workspacePath") or source.get("workspace_path") or resource_ref.get("workspacePath") or ""),
            relative_value=str(
                source.get("workspaceRelativePath")
                or source.get("workspace_relative_path")
                or resource_ref.get("workspaceRelativePath")
                or metadata.get("workspaceRelativePath")
                or ""
            ),
        )
        asset = self._upsert_asset(
            authority=authority,
            path=path,
            workspace_relative_path=relative,
            title=str(source.get("title") or path.name),
            mime_type=str(source.get("mimeType") or source.get("mime_type") or ""),
            origin_kind="source",
            origin_id=source_id,
            origin_session_id=session_id,
            metadata={"sourceKind": source.get("sourceKind") or source.get("source_kind") or "upload"},
        )
        if attach_to_session:
            self.use_asset(session_id=session_id, asset_id=asset["assetId"])
            asset["adoptedByCurrentSession"] = True
        return asset

    def register_artifact(self, *, session_id: str, artifact_id: str, attach_to_session: bool = True) -> dict[str, Any]:
        authority = self._authority(session_id, require_write=True)
        artifact = db.get_runtime_artifact(artifact_id)
        if not artifact or str(artifact.get("sessionId") or artifact.get("session_id") or "") != session_id:
            raise WorkspaceMediaLibraryError("Artifact is not bound to the current session")
        metadata = _record_json(artifact.get("metadata") or artifact.get("metadata_json"))
        path, relative = self._resolve_candidate(
            authority,
            path_value=str(artifact.get("sourcePath") or artifact.get("source_path") or artifact.get("workspacePath") or ""),
            relative_value=str(artifact.get("workspaceRelativePath") or metadata.get("workspaceRelativePath") or ""),
        )
        asset = self._upsert_asset(
            authority=authority,
            path=path,
            workspace_relative_path=relative,
            title=str(artifact.get("title") or path.name),
            mime_type=str(artifact.get("mimeType") or artifact.get("mime_type") or ""),
            origin_kind="artifact",
            origin_id=artifact_id,
            origin_session_id=session_id,
            origin_run_id=str(artifact.get("runId") or artifact.get("run_id") or ""),
            metadata={"artifactKind": artifact.get("kind") or artifact.get("artifact_kind") or "file"},
        )
        if attach_to_session:
            self.use_asset(session_id=session_id, asset_id=asset["assetId"])
            asset["adoptedByCurrentSession"] = True
        return asset

    def reconcile_session(self, *, session_id: str) -> dict[str, Any]:
        self._authority(session_id, require_write=True)
        registered: list[str] = []
        skipped: list[dict[str, str]] = []
        for source in db.list_session_sources(session_id=session_id, include_unbound=True, limit=500):
            source_id = str(source.get("sourceId") or source.get("id") or "")
            if str(source.get("sourceKind") or "") == "canvas_mask":
                continue
            try:
                registered.append(self.register_source(session_id=session_id, source_id=source_id)["assetId"])
            except (FileNotFoundError, PermissionError, WorkspaceMediaLibraryError) as exc:
                skipped.append({"kind": "source", "id": source_id, "reason": str(exc)})
        for artifact in db.list_runtime_artifacts(session_id=session_id, limit=500):
            artifact_id = str(artifact.get("artifactId") or artifact.get("id") or "")
            try:
                registered.append(self.register_artifact(session_id=session_id, artifact_id=artifact_id)["assetId"])
            except (FileNotFoundError, PermissionError, WorkspaceMediaLibraryError) as exc:
                skipped.append({"kind": "artifact", "id": artifact_id, "reason": str(exc)})
        return {"registeredAssetIds": list(dict.fromkeys(registered)), "skipped": skipped}

    def _asset_row(self, *, session_id: str, asset_id: str) -> tuple[dict[str, Any], WorkspaceAuthorityDescriptor]:
        authority = self._authority(session_id)
        workspace_key_value = self._workspace_key(authority)
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT a.*, i.folder_id,
                       CASE WHEN u.session_id IS NULL THEN 0 ELSE 1 END AS adopted_by_current_session
                FROM workspace_media_assets a
                LEFT JOIN workspace_media_folder_items i ON i.asset_id = a.asset_id
                LEFT JOIN session_media_asset_uses u ON u.asset_id = a.asset_id AND u.session_id = ?
                WHERE a.asset_id = ? AND a.workspace_key = ? AND a.deleted_at IS NULL
                LIMIT 1
                """,
                (session_id, asset_id, workspace_key_value),
            ).fetchone()
        if not row:
            raise WorkspaceMediaLibraryError("Media asset is not in the current session workspace")
        return dict(row), authority

    @staticmethod
    def _project_asset(row: dict[str, Any], *, session_id: str) -> dict[str, Any]:
        metadata = _record_json(row.get("metadata_json"))
        asset_id = str(row.get("asset_id") or "")
        return {
            "assetId": asset_id,
            "id": asset_id,
            "sessionId": session_id,
            "workspaceId": row.get("workspace_id"),
            "projectId": row.get("project_id"),
            "workspaceRelativePath": row.get("workspace_relative_path"),
            "title": row.get("title"),
            "name": row.get("title"),
            "mediaType": row.get("media_type") if row.get("media_type") in _MEDIA_TYPES else "unknown",
            "mimeType": row.get("mime_type") or "application/octet-stream",
            "size": row.get("byte_size"),
            "originKind": row.get("origin_kind"),
            "originId": row.get("origin_id"),
            "originSessionId": row.get("origin_session_id"),
            "originRunId": row.get("origin_run_id"),
            "folderId": row.get("folder_id"),
            "adoptedByCurrentSession": bool(row.get("adopted_by_current_session")),
            "contentUrl": f"/api/workbench/sessions/{session_id}/media/assets/{asset_id}/content",
            "previewUrl": f"/api/workbench/sessions/{session_id}/media/assets/{asset_id}/content",
            "metadata": metadata,
        }

    def get_asset(self, *, session_id: str, asset_id: str) -> dict[str, Any]:
        row, _ = self._asset_row(session_id=session_id, asset_id=asset_id)
        return self._project_asset(row, session_id=session_id)

    def list_assets(self, *, session_id: str, folder_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        authority = self._authority(session_id)
        workspace_key_value = self._workspace_key(authority)
        clauses = ["a.workspace_key = ?", "a.deleted_at IS NULL"]
        params: list[Any] = [session_id, workspace_key_value]
        normalized_folder_id = str(folder_id or "").strip()
        if normalized_folder_id:
            clauses.append("i.folder_id = ?")
            params.append(normalized_folder_id)
        params.append(max(1, min(int(limit or 500), 1000)))
        with db.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT a.*, i.folder_id,
                       CASE WHEN u.session_id IS NULL THEN 0 ELSE 1 END AS adopted_by_current_session
                FROM workspace_media_assets a
                LEFT JOIN workspace_media_folder_items i ON i.asset_id = a.asset_id
                LEFT JOIN session_media_asset_uses u ON u.asset_id = a.asset_id AND u.session_id = ?
                WHERE {' AND '.join(clauses)}
                ORDER BY a.updated_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [self._project_asset(dict(row), session_id=session_id) for row in rows]

    def use_asset(
        self,
        *,
        session_id: str,
        asset_id: str,
        canvas_node_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._asset_row(session_id=session_id, asset_id=asset_id)
        now = _utc_now()
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO session_media_asset_uses(session_id, asset_id, canvas_node_id, context_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, asset_id) DO UPDATE SET
                    canvas_node_id = COALESCE(NULLIF(excluded.canvas_node_id, ''), session_media_asset_uses.canvas_node_id),
                    context_json = excluded.context_json,
                    updated_at = excluded.updated_at
                """,
                (session_id, asset_id, canvas_node_id or None, json.dumps(context or {}, ensure_ascii=False), now, now),
            )
            conn.commit()
        asset = self.get_asset(session_id=session_id, asset_id=asset_id)
        asset["adoptedByCurrentSession"] = True
        return asset

    def resolve_asset_path(self, *, session_id: str, asset_id: str, require_session_use: bool = False) -> Path:
        row, authority = self._asset_row(session_id=session_id, asset_id=asset_id)
        if require_session_use and not bool(row.get("adopted_by_current_session")):
            raise PermissionError("Workspace media asset must be explicitly adopted by the current session")
        path, _ = self._resolve_candidate(
            authority,
            path_value="",
            relative_value=str(row.get("workspace_relative_path") or ""),
        )
        return path

    def resolve_source_path(self, *, session_id: str, source_id: str) -> Path:
        authority = self._authority(session_id)
        source = db.get_session_source(session_id=session_id, source_id=source_id)
        if not source:
            raise WorkspaceMediaLibraryError("Source is not bound to the current session")
        metadata = _record_json(source.get("metadata") or source.get("metadata_json"))
        resource_ref = _record_json(source.get("resourceRef") or source.get("resource_ref") or source.get("resource_ref_json"))
        return self._resolve_candidate(
            authority,
            path_value=str(source.get("workspacePath") or source.get("workspace_path") or resource_ref.get("workspacePath") or ""),
            relative_value=str(resource_ref.get("workspaceRelativePath") or metadata.get("workspaceRelativePath") or ""),
        )[0]

    def resolve_artifact_path(self, *, session_id: str, artifact_id: str) -> Path:
        authority = self._authority(session_id)
        artifact = db.get_runtime_artifact(artifact_id)
        if not artifact or str(artifact.get("sessionId") or artifact.get("session_id") or "") != session_id:
            raise WorkspaceMediaLibraryError("Artifact is not bound to the current session")
        metadata = _record_json(artifact.get("metadata") or artifact.get("metadata_json"))
        return self._resolve_candidate(
            authority,
            path_value=str(artifact.get("sourcePath") or artifact.get("source_path") or artifact.get("workspacePath") or ""),
            relative_value=str(artifact.get("workspaceRelativePath") or metadata.get("workspaceRelativePath") or ""),
        )[0]

    @staticmethod
    def _folder_title(value: str) -> str:
        title = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", str(value or "")).strip()
        if not title:
            raise WorkspaceMediaLibraryError("Folder title is required")
        if len(title) > 64:
            raise WorkspaceMediaLibraryError("Folder title must be 64 characters or fewer")
        return title

    def list_folders(self, *, session_id: str) -> list[dict[str, Any]]:
        authority = self._authority(session_id)
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT f.*, COUNT(i.asset_id) AS asset_count
                FROM workspace_media_folders f
                LEFT JOIN workspace_media_folder_items i ON i.folder_id = f.folder_id
                WHERE f.workspace_key = ?
                GROUP BY f.folder_id
                ORDER BY f.sort_order ASC, f.title COLLATE NOCASE ASC
                """,
                (self._workspace_key(authority),),
            ).fetchall()
        return [{
            "folderId": row["folder_id"],
            "parentFolderId": row["parent_folder_id"],
            "folderKind": row["folder_kind"],
            "title": row["title"],
            "sortOrder": row["sort_order"],
            "assetCount": row["asset_count"],
        } for row in rows]

    def create_folder(
        self,
        *,
        session_id: str,
        title: str,
        parent_folder_id: str = "",
        folder_kind: str = "custom",
    ) -> dict[str, Any]:
        authority = self._authority(session_id, require_write=True)
        workspace_key_value = self._workspace_key(authority)
        normalized_parent = str(parent_folder_id or "").strip()
        normalized_kind = str(folder_kind or "custom").strip().lower()
        normalized_title = self._folder_title(title)
        if normalized_kind not in _FOLDER_KINDS:
            raise WorkspaceMediaLibraryError("Unsupported media folder kind")
        if normalized_parent:
            with db.get_connection() as conn:
                parent = conn.execute(
                    "SELECT folder_id, parent_folder_id FROM workspace_media_folders WHERE folder_id = ? AND workspace_key = ?",
                    (normalized_parent, workspace_key_value),
                ).fetchone()
                if not parent:
                    raise WorkspaceMediaLibraryError("Parent folder is not in the current workspace")
                depth = 1
                current = parent
                while current and current["parent_folder_id"]:
                    depth += 1
                    if depth >= 6:
                        raise WorkspaceMediaLibraryError("Media folder nesting is limited to 6 levels")
                    current = conn.execute(
                        "SELECT folder_id, parent_folder_id FROM workspace_media_folders WHERE folder_id = ? AND workspace_key = ?",
                        (current["parent_folder_id"], workspace_key_value),
                    ).fetchone()
        with db.get_connection() as conn:
            existing = conn.execute(
                """
                SELECT folder_id
                FROM workspace_media_folders
                WHERE workspace_key = ?
                  AND COALESCE(parent_folder_id, '') = ?
                  AND LOWER(title) = LOWER(?)
                LIMIT 1
                """,
                (workspace_key_value, normalized_parent, normalized_title),
            ).fetchone()
        if existing:
            raise WorkspaceMediaLibraryError("A media folder with this title already exists here")
        folder_id = f"wmf_{uuid.uuid4().hex}"
        now = _utc_now()
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO workspace_media_folders(
                    folder_id, workspace_key, workspace_id, project_id, parent_folder_id,
                    folder_kind, title, sort_order, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    folder_id,
                    workspace_key_value,
                    authority.workspace_id or None,
                    authority.project_id or None,
                    normalized_parent or None,
                    normalized_kind,
                    normalized_title,
                    now,
                    now,
                ),
            )
            conn.commit()
        return next(item for item in self.list_folders(session_id=session_id) if item["folderId"] == folder_id)

    def place_asset(self, *, session_id: str, asset_id: str, folder_id: str | None) -> dict[str, Any]:
        authority = self._authority(session_id, require_write=True)
        self._asset_row(session_id=session_id, asset_id=asset_id)
        normalized_folder = str(folder_id or "").strip()
        now = _utc_now()
        with db.get_connection() as conn:
            if normalized_folder:
                folder = conn.execute(
                    "SELECT folder_id FROM workspace_media_folders WHERE folder_id = ? AND workspace_key = ?",
                    (normalized_folder, self._workspace_key(authority)),
                ).fetchone()
                if not folder:
                    raise WorkspaceMediaLibraryError("Target folder is not in the current workspace")
                conn.execute(
                    """
                    INSERT INTO workspace_media_folder_items(asset_id, folder_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(asset_id) DO UPDATE SET folder_id = excluded.folder_id, updated_at = excluded.updated_at
                    """,
                    (asset_id, normalized_folder, now, now),
                )
            else:
                conn.execute("DELETE FROM workspace_media_folder_items WHERE asset_id = ?", (asset_id,))
            conn.commit()
        return self.get_asset(session_id=session_id, asset_id=asset_id)

    def delete_folder(self, *, session_id: str, folder_id: str) -> None:
        authority = self._authority(session_id, require_write=True)
        with db.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM workspace_media_folders WHERE folder_id = ? AND workspace_key = ?",
                (str(folder_id or "").strip(), self._workspace_key(authority)),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise WorkspaceMediaLibraryError("Media folder is not in the current workspace")


workspace_media_library = WorkspaceMediaLibraryService()
