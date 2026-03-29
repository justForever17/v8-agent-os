from __future__ import annotations

import mimetypes
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def infer_media_kind(mime_type: str) -> str:
    lowered = (mime_type or "").lower()
    if lowered.startswith("image/"):
        return "image"
    if lowered.startswith("video/"):
        return "video"
    if lowered.startswith("audio/"):
        return "audio"
    if lowered.startswith("text/") or lowered in {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }:
        return "document"
    return "file"


def guess_mime_type(file_path: str | Path, fallback: str = "application/octet-stream") -> str:
    mime_type, _ = mimetypes.guess_type(str(file_path))
    return mime_type or fallback


def build_multimodal_content(
    *,
    prompt: str,
    media_url: str,
    mime_type: str,
    api_standard: str = "openai",
    transport_mode: str = "url_reference",
) -> List[Dict[str, Any]]:
    normalized_api = (api_standard or "openai").lower()
    normalized_transport = (transport_mode or "url_reference").lower()
    kind = infer_media_kind(mime_type)
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]

    if kind == "image":
        if normalized_transport not in {"url_reference", "inline_base64_image"}:
            raise ValueError(f"不支持的多模态传输模式：{transport_mode}")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": media_url},
            }
        )
        return content

    if normalized_transport == "inline_base64_image":
        raise ValueError("只有图片支持 inline_base64_image 传输模式。")

    if normalized_api in {"google", "gemini"}:
        content.append(
            {
                "type": "media",
                "file_uri": media_url,
                "mime_type": mime_type,
            }
        )
        return content

    if kind == "video":
        content.append(
            {
                "type": "video_url",
                "video_url": {"url": media_url},
            }
        )
        return content

    content.append(
        {
            "type": "file_url",
            "file_url": {
                "url": media_url,
                "mime_type": mime_type,
            },
        }
    )
    return content


def build_multimodal_payload_metadata(
    *,
    mime_type: str,
    media_url: str = "",
    api_standard: str,
    transport_mode: str = "url_reference",
    source_ref: str = "",
    byte_size: int | None = None,
) -> Dict[str, Any]:
    metadata = {
        "mimeType": mime_type,
        "mediaKind": infer_media_kind(mime_type),
        "apiStandard": (api_standard or "openai").lower(),
        "transportMode": (transport_mode or "url_reference").lower(),
    }
    if byte_size is not None:
        metadata["byteSize"] = int(byte_size)
    if source_ref:
        metadata["sourceRef"] = source_ref
    if metadata["transportMode"] == "url_reference" and media_url:
        metadata["mediaUrl"] = media_url
    return metadata


def build_artifact_descriptor(
    *,
    artifact_id: str,
    artifact_kind: Optional[str] = None,
    mime_type: Optional[str] = None,
    title: Optional[str] = None,
    file_path: str | Path | None = None,
    source_path: Optional[str] = None,
    workspace_path: Optional[str] = None,
    external_url: Optional[str] = None,
    preview_url: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    content_url: Optional[str] = None,
) -> Dict[str, Any]:
    path = Path(file_path) if file_path is not None else (Path(source_path) if source_path else None)
    resolved_mime = mime_type or (guess_mime_type(path) if path else "application/octet-stream")
    kind = artifact_kind or infer_media_kind(resolved_mime)
    display_name = title or (path.name if path else external_url or preview_url or artifact_id)
    return {
        "artifactId": artifact_id,
        "kind": kind,
        "mimeType": resolved_mime,
        "title": display_name,
        "sourcePath": str(path) if path else source_path,
        "workspacePath": workspace_path,
        "externalUrl": external_url,
        "previewUrl": preview_url or external_url,
        "contentUrl": content_url,
        "metadata": metadata or {},
    }


def normalize_artifact_record(record: Dict[str, Any]) -> Dict[str, Any]:
    metadata = record.get("metadata")
    if metadata is None and record.get("metadata_json"):
        try:
            import json

            metadata = json.loads(record["metadata_json"])
        except Exception:
            metadata = {}
    metadata = metadata or {}

    artifact_id = record.get("artifactId") or record.get("id")
    kind = record.get("kind") or record.get("artifact_kind") or infer_media_kind(
        record.get("mimeType") or record.get("mime_type") or "application/octet-stream"
    )
    mime_type = record.get("mimeType") or record.get("mime_type") or "application/octet-stream"
    preview_url = record.get("previewUrl") or record.get("preview_url") or record.get("externalUrl") or record.get("external_url")
    content_url = record.get("contentUrl") or record.get("content_url") or preview_url
    external_url = record.get("externalUrl") or record.get("external_url")
    source_path = record.get("sourcePath") or record.get("source_path")
    workspace_path = record.get("workspacePath") or record.get("workspace_path")
    session_id = record.get("sessionId") or record.get("session_id")
    run_id = record.get("runId") or record.get("run_id")
    message_id = record.get("messageId") or record.get("message_id")
    title = record.get("title") or artifact_id
    created_at = record.get("createdAt") or record.get("created_at")
    source_component = record.get("sourceComponent") or record.get("source_component")
    supports_inline_preview = bool(
        record.get("supportsInlinePreview")
        if record.get("supportsInlinePreview") is not None
        else record.get("supports_inline_preview")
    )
    preview_kind = record.get("previewKind") or record.get("preview_kind")

    normalized = dict(record)
    normalized.update(
        {
            "artifactId": artifact_id,
            "id": artifact_id,
            "kind": kind,
            "artifact_kind": kind,
            "mimeType": mime_type,
            "mime_type": mime_type,
            "title": title,
            "sessionId": session_id,
            "session_id": session_id,
            "runId": run_id,
            "run_id": run_id,
            "messageId": message_id,
            "message_id": message_id,
            "sourcePath": source_path,
            "source_path": source_path,
            "workspacePath": workspace_path,
            "workspace_path": workspace_path,
            "externalUrl": external_url,
            "external_url": external_url,
            "previewUrl": preview_url,
            "preview_url": preview_url,
            "contentUrl": content_url,
            "content_url": content_url,
            "metadata": metadata,
            "createdAt": created_at,
            "created_at": created_at,
            "sourceComponent": source_component,
            "source_component": source_component,
            "supportsInlinePreview": supports_inline_preview,
            "supports_inline_preview": supports_inline_preview,
            "previewKind": preview_kind,
            "preview_kind": preview_kind,
            "hasPreview": bool(preview_url),
            "displayLabel": title or artifact_id,
            "displaySubtitle": workspace_path or source_path or preview_url or "暂无路径信息",
        }
    )
    return normalized


def utc_now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
