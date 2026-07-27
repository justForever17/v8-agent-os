from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from core.artifact_policy import apply_artifact_surface_policy
from core.database import db
from core.multimodal_payload_adapter import build_artifact_descriptor, normalize_artifact_record, utc_now_iso
from core.workspace_share import resolve_workspace_file_to_share
from erc.event_bus import event_bus
from erc.models import RuntimeSource


class ArtifactStore:
    def __init__(self, *, database: Any | None = None) -> None:
        self.database = database or db

    @staticmethod
    def _build_content_url(artifact_id: str) -> str:
        return f"/v1/artifacts/{artifact_id}/content"

    def _emit_artifact_recorded_event(
        self,
        *,
        descriptor: Dict[str, Any],
        session_id: Optional[str],
        run_id: Optional[str],
        source_component: str,
        node: str,
    ) -> None:
        if not session_id or not run_id or str(descriptor.get("resourceRole") or "artifact") != "artifact":
            return
        emitter = event_bus.create_emitter(
            session_id=session_id,
            conversation_id=session_id,
            run_id=run_id,
            source=RuntimeSource(
                plane="engine",
                component=source_component,
                node=node,
                agent_id=None,
            ),
        )
        emitter.emit("artifact.recorded", descriptor)

    @staticmethod
    def _apply_resource_contract(
        descriptor: Dict[str, Any],
        *,
        session_id: Optional[str],
        run_id: Optional[str],
        resource_role: str,
        source_id: Optional[str],
        auto_attach_to_message: Optional[bool],
    ) -> Dict[str, Any]:
        role = str(resource_role or "artifact").strip()
        if role not in {"artifact", "source_derivative"}:
            role = "artifact"
        metadata = dict(descriptor.get("metadata") or {})
        metadata["resourceRole"] = role
        if source_id:
            metadata["sourceId"] = source_id
        descriptor["metadata"] = metadata
        descriptor["resourceRole"] = role
        descriptor["sourceId"] = source_id
        descriptor = apply_artifact_surface_policy(descriptor, session_id=session_id, run_id=run_id)

        effective_auto_attach = bool(descriptor.get("autoAttachToMessage"))
        if auto_attach_to_message is not None:
            effective_auto_attach = bool(auto_attach_to_message)
        if role != "artifact":
            effective_auto_attach = False
        metadata = dict(descriptor.get("metadata") or {})
        metadata["resourceRole"] = role
        metadata["autoAttachToMessage"] = effective_auto_attach
        if source_id:
            metadata["sourceId"] = source_id
        if not effective_auto_attach:
            metadata["surfaceVisible"] = False
            descriptor["surfaceVisible"] = False
        descriptor["metadata"] = metadata
        descriptor["resourceRole"] = role
        descriptor["sourceId"] = source_id
        descriptor["autoAttachToMessage"] = effective_auto_attach
        return descriptor

    def record_artifact(
        self,
        *,
        artifact_kind: str,
        mime_type: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        message_id: Optional[str] = None,
        resource_role: str = "artifact",
        source_id: Optional[str] = None,
        auto_attach_to_message: Optional[bool] = None,
        title: Optional[str] = None,
        source_path: Optional[str] = None,
        workspace_path: Optional[str] = None,
        external_url: Optional[str] = None,
        preview_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source_component: str = "artifact_store",
        node: str = "artifact_store",
    ) -> Dict[str, Any]:
        artifact_id = f"art_{uuid.uuid4().hex}"
        descriptor = build_artifact_descriptor(
            artifact_id=artifact_id,
            artifact_kind=artifact_kind,
            mime_type=mime_type,
            title=title,
            source_path=source_path,
            workspace_path=workspace_path,
            external_url=external_url,
            preview_url=preview_url or external_url or (self._build_content_url(artifact_id) if source_path else None),
            content_url=self._build_content_url(artifact_id) if source_path else external_url,
            metadata=metadata or {},
        )
        descriptor.update(
            {
                "sessionId": session_id,
                "runId": run_id,
                "messageId": message_id,
                "createdAt": utc_now_iso(),
                "sourceComponent": source_component,
                "supportsInlinePreview": artifact_kind in {"image", "video", "audio"},
                "previewKind": artifact_kind if artifact_kind in {"image", "video", "audio"} else "metadata",
            }
        )
        descriptor = self._apply_resource_contract(
            descriptor,
            session_id=session_id,
            run_id=run_id,
            resource_role=resource_role,
            source_id=source_id,
            auto_attach_to_message=auto_attach_to_message,
        )
        descriptor = normalize_artifact_record(descriptor)
        self.database.add_runtime_artifact(
            artifact_id=artifact_id,
            artifact_kind=descriptor["kind"],
            mime_type=descriptor.get("mimeType"),
            session_id=session_id,
            run_id=run_id,
            message_id=message_id,
            resource_role=descriptor.get("resourceRole") or "artifact",
            source_id=descriptor.get("sourceId"),
            auto_attach_to_message=bool(descriptor.get("autoAttachToMessage")),
            title=descriptor.get("title"),
            source_path=descriptor.get("sourcePath"),
            workspace_path=descriptor.get("workspacePath"),
            external_url=descriptor.get("externalUrl"),
            preview_url=descriptor.get("previewUrl"),
            metadata=descriptor.get("metadata"),
        )
        self._emit_artifact_recorded_event(
            descriptor=descriptor,
            session_id=session_id,
            run_id=run_id,
            source_component=source_component,
            node=node,
        )
        return normalize_artifact_record(descriptor)

    def record_local_file(
        self,
        *,
        file_path: str | Path,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        message_id: Optional[str] = None,
        resource_role: str = "artifact",
        source_id: Optional[str] = None,
        auto_attach_to_message: Optional[bool] = None,
        workspace_path: Optional[str] = None,
        external_url: Optional[str] = None,
        preview_url: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        source_component: str = "artifact_store",
        node: str = "artifact_store",
    ) -> Dict[str, Any]:
        path = Path(file_path)
        artifact_id = f"art_{uuid.uuid4().hex}"
        descriptor = build_artifact_descriptor(
            artifact_id=artifact_id,
            file_path=path,
            workspace_path=workspace_path,
            external_url=external_url,
            preview_url=preview_url or external_url or self._build_content_url(artifact_id),
            content_url=self._build_content_url(artifact_id),
            metadata=metadata or {},
        )
        if not descriptor.get("contentUrl"):
            descriptor["contentUrl"] = self._build_content_url(artifact_id)
        if not descriptor.get("previewUrl"):
            descriptor["previewUrl"] = descriptor.get("contentUrl")
        descriptor.update(
            {
                "sessionId": session_id,
                "runId": run_id,
                "messageId": message_id,
                "createdAt": utc_now_iso(),
                "sourceComponent": source_component,
                "supportsInlinePreview": descriptor.get("kind") in {"image", "video", "audio"},
                "previewKind": descriptor.get("kind") if descriptor.get("kind") in {"image", "video", "audio"} else "metadata",
            }
        )
        descriptor = self._apply_resource_contract(
            descriptor,
            session_id=session_id,
            run_id=run_id,
            resource_role=resource_role,
            source_id=source_id,
            auto_attach_to_message=auto_attach_to_message,
        )
        descriptor = normalize_artifact_record(descriptor)
        self.database.add_runtime_artifact(
            artifact_id=descriptor["artifactId"],
            artifact_kind=descriptor["kind"],
            mime_type=descriptor.get("mimeType"),
            session_id=session_id,
            run_id=run_id,
            message_id=message_id,
            resource_role=descriptor.get("resourceRole") or "artifact",
            source_id=descriptor.get("sourceId"),
            auto_attach_to_message=bool(descriptor.get("autoAttachToMessage")),
            title=descriptor.get("title"),
            source_path=descriptor.get("sourcePath"),
            workspace_path=descriptor.get("workspacePath"),
            external_url=descriptor.get("externalUrl"),
            preview_url=descriptor.get("previewUrl"),
            metadata=descriptor.get("metadata"),
        )
        self._emit_artifact_recorded_event(
            descriptor=descriptor,
            session_id=session_id,
            run_id=run_id,
            source_component=source_component,
            node=node,
        )
        return normalize_artifact_record(descriptor)

    @staticmethod
    def _safe_workspace_relative_path(value: Any) -> str | None:
        normalized = str(value or "").strip().replace("\\", "/")
        if not normalized or normalized.startswith("/"):
            return None
        parts = [part for part in normalized.split("/") if part not in {"", "."}]
        if not parts or any(part == ".." or ":" in part for part in parts):
            return None
        return "/".join(parts)

    def rebind_managed_workspace_artifacts(
        self,
        *,
        run_id: str,
        workspace_root: str | Path,
    ) -> Dict[str, Any]:
        normalized_run_id = str(run_id or "").strip()
        root = Path(workspace_root).expanduser().resolve(strict=False)
        if not normalized_run_id:
            return {
                "ok": False,
                "status": "blocked",
                "rebound": 0,
                "skipped": 0,
                "errorCode": "run_id_required",
            }
        if not root.is_dir():
            return {
                "ok": False,
                "status": "blocked",
                "rebound": 0,
                "skipped": 0,
                "errorCode": "delivery_workspace_missing",
            }

        limit = 10_000
        artifacts = self.database.list_runtime_artifacts(run_id=normalized_run_id, limit=limit)
        if len(artifacts) >= limit:
            return {
                "ok": False,
                "status": "blocked",
                "rebound": 0,
                "skipped": 0,
                "errorCode": "artifact_rebind_limit_exceeded",
            }

        rebound_ids: list[str] = []
        invalidated_ids: list[str] = []
        failures: list[Dict[str, str]] = []
        for artifact in artifacts:
            metadata = dict(artifact.get("metadata") or {})
            if metadata.get("origin") != "agent_file_write" or metadata.get("managedExecution") is not True:
                continue
            artifact_id = str(artifact.get("artifactId") or artifact.get("id") or "").strip()
            relative_path = self._safe_workspace_relative_path(
                metadata.get("workspaceRelativePath") or artifact.get("workspacePath")
            )
            if not artifact_id:
                failures.append({"artifactId": "unknown", "reason": "artifact_id_missing"})
                continue
            unavailable_reason: str | None = None
            target: Path | None = None
            if not relative_path:
                unavailable_reason = "relative_path_missing"
            else:
                target = (root / Path(relative_path)).resolve(strict=False)
                try:
                    target.relative_to(root)
                except ValueError:
                    unavailable_reason = "path_outside_workspace"
                if unavailable_reason is None and not target.is_file():
                    unavailable_reason = "delivered_file_missing"

            if unavailable_reason is not None:
                updated_metadata = {
                    **metadata,
                    "deliveryState": "not_delivered",
                    "surfaceVisible": False,
                    "unavailableReason": unavailable_reason,
                    "workspaceRoot": str(root),
                    "executionWorkspaceRebound": True,
                }
                updated = self.database.update_runtime_artifact_location(
                    artifact_id=artifact_id,
                    source_path=None,
                    workspace_path=relative_path,
                    metadata=updated_metadata,
                )
                if updated:
                    invalidated_ids.append(artifact_id)
                else:
                    failures.append({"artifactId": artifact_id, "reason": "artifact_record_missing"})
                continue

            assert target is not None
            updated_metadata = {
                **metadata,
                "deliveryState": "delivered",
                "workspaceRoot": str(root),
                "workspaceRelativePath": relative_path,
                "executionWorkspaceRebound": True,
            }
            updated = self.database.update_runtime_artifact_location(
                artifact_id=artifact_id,
                source_path=str(target),
                workspace_path=relative_path,
                metadata=updated_metadata,
            )
            if updated:
                rebound_ids.append(artifact_id)
            else:
                failures.append({"artifactId": artifact_id, "reason": "artifact_record_missing"})

        if failures:
            return {
                "ok": False,
                "status": "blocked",
                "rebound": len(rebound_ids),
                "invalidated": len(invalidated_ids),
                "skipped": len(failures),
                "reboundArtifactIds": rebound_ids,
                "invalidatedArtifactIds": invalidated_ids,
                "failures": failures[:20],
                "errorCode": "artifact_rebind_incomplete",
            }
        return {
            "ok": True,
            "status": "rebound" if rebound_ids else "reconciled",
            "rebound": len(rebound_ids),
            "invalidated": len(invalidated_ids),
            "skipped": 0,
            "reboundArtifactIds": rebound_ids,
            "invalidatedArtifactIds": invalidated_ids,
        }

    def adopt_workspace_file(
        self,
        *,
        path: str,
        mode: str = "auto",
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        message_id: Optional[str] = None,
        source_component: str = "artifact_adoption",
        node: str = "artifact_adoption",
    ) -> Dict[str, Any]:
        share = resolve_workspace_file_to_share(path, mode)
        source_path = str(share.get("sourcePath") or "").strip()
        if not source_path:
            raise FileNotFoundError("Workspace adoption resolved no source file.")
        metadata = {
            "origin": "workspace_adopted",
            "storageClass": "workspace",
            "pathPlane": share.get("pathPlane") or ("workspace_artifact" if share.get("previewable") else "workspace_download"),
            "surfaceVisible": True,
            "workspaceRelativePath": share.get("workspaceRelativePath"),
            "workspaceId": share.get("workspaceId"),
            "projectId": share.get("projectId"),
            "viewerKind": share.get("viewerKind"),
            "downloadable": bool(share.get("downloadable", True)),
            "previewable": bool(share.get("previewable", False)),
            "adoptionMode": str(mode or "auto").strip() or "auto",
        }
        artifact = self.record_local_file(
            file_path=source_path,
            session_id=session_id,
            run_id=run_id,
            message_id=message_id,
            workspace_path=str(share.get("workspaceRelativePath") or "").strip() or None,
            metadata=metadata,
            source_component=source_component,
            node=node,
        )
        artifact["origin"] = "workspace_adopted"
        artifact["adoptedFrom"] = share
        return artifact


artifact_store = ArtifactStore()
