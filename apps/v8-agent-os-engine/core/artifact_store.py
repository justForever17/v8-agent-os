from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from core.database import db
from core.multimodal_payload_adapter import build_artifact_descriptor, normalize_artifact_record, utc_now_iso
from erc.event_bus import event_bus
from erc.models import RuntimeSource


class ArtifactStore:
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
        if not session_id or not run_id:
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

    def record_artifact(
        self,
        *,
        artifact_kind: str,
        mime_type: str,
        session_id: Optional[str] = None,
        run_id: Optional[str] = None,
        message_id: Optional[str] = None,
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
        db.add_runtime_artifact(
            artifact_id=artifact_id,
            artifact_kind=descriptor["kind"],
            mime_type=descriptor.get("mimeType"),
            session_id=session_id,
            run_id=run_id,
            message_id=message_id,
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
        db.add_runtime_artifact(
            artifact_id=descriptor["artifactId"],
            artifact_kind=descriptor["kind"],
            mime_type=descriptor.get("mimeType"),
            session_id=session_id,
            run_id=run_id,
            message_id=message_id,
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


artifact_store = ArtifactStore()
