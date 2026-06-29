from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
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


def _audio_format_from_mime(mime_type: str) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized in {"audio/mpeg", "audio/mp3"}:
        return "mp3"
    if normalized in {"audio/mp4", "audio/x-m4a", "audio/m4a"}:
        return "m4a"
    if normalized in {"audio/x-wav", "audio/wave", "audio/wav"}:
        return "wav"
    if normalized == "audio/ogg":
        return "ogg"
    if normalized == "audio/flac":
        return "flac"
    if normalized == "audio/aac":
        return "aac"
    if normalized == "audio/aiff":
        return "aiff"
    if "/" in normalized:
        return normalized.rsplit("/", 1)[-1].replace("x-", "")
    return "mp3"


def _strip_data_url(value: str) -> str:
    text = str(value or "")
    marker = ";base64,"
    if marker in text:
        return text.split(marker, 1)[1]
    return text


def _data_url_for_audio(value: str, mime_type: str) -> str:
    text = str(value or "")
    if text.startswith("data:"):
        return text
    mime = (mime_type or "audio/mpeg").split(";", 1)[0].strip() or "audio/mpeg"
    return f"data:{mime};base64,{_strip_data_url(text)}"


def _audio_payload_profile(*, api_standard: str, provider_id: str = "", model_id: str = "") -> str:
    normalized_api = (api_standard or "openai").lower()
    provider = (provider_id or "").lower()
    model = (model_id or "").lower()
    joined = f"{provider} {model}"
    if normalized_api in {"google", "gemini"}:
        return "gemini_media"
    if "mimo" in joined or "xiaomi" in joined:
        return "mimo_audio_url"
    if "doubao" in joined or "volcengine" in joined or "ark" in joined:
        return "ark_input_audio"
    return "openai_input_audio"


def describe_multimodal_payload_shape(
    *,
    mime_type: str,
    api_standard: str = "openai",
    provider_id: str = "",
    model_id: str = "",
    transport_mode: str = "url_reference",
) -> str:
    kind = infer_media_kind(mime_type)
    if kind != "audio":
        return f"{kind}:{(api_standard or 'openai').lower()}:{(transport_mode or 'url_reference').lower()}"
    return f"audio:{_audio_payload_profile(api_standard=api_standard, provider_id=provider_id, model_id=model_id)}:{(transport_mode or 'url_reference').lower()}"


def build_multimodal_content(
    *,
    prompt: str,
    media_url: str,
    mime_type: str,
    api_standard: str = "openai",
    transport_mode: str = "url_reference",
    provider_id: str = "",
    model_id: str = "",
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

    if kind == "audio":
        payload_profile = _audio_payload_profile(
            api_standard=normalized_api,
            provider_id=provider_id,
            model_id=model_id,
        )
        if normalized_transport == "inline_base64_audio":
            audio_data = _strip_data_url(media_url)
            if payload_profile == "gemini_media":
                content.append(
                    {
                        "type": "media",
                        "data": audio_data,
                        "mime_type": mime_type,
                    }
                )
                return content
            if payload_profile == "mimo_audio_url":
                content.append(
                    {
                        "type": "input_audio",
                        "audio_url": _data_url_for_audio(media_url, mime_type),
                    }
                )
                return content
            content.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": audio_data,
                        "format": _audio_format_from_mime(mime_type),
                    },
                }
            )
            return content

        if normalized_transport == "url_reference":
            if payload_profile == "gemini_media":
                content.append(
                    {
                        "type": "media",
                        "file_uri": media_url,
                        "mime_type": mime_type,
                    }
                )
                return content
            if payload_profile == "mimo_audio_url":
                content.append(
                    {
                        "type": "input_audio",
                        "audio_url": media_url,
                    }
                )
                return content
            content.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "url": media_url,
                        "format": _audio_format_from_mime(mime_type),
                    },
                }
            )
            return content
        raise ValueError(f"不支持的音频传输模式：{transport_mode}")

    if normalized_transport in {"inline_base64_image", "inline_base64_audio"}:
        raise ValueError("只有图片支持 inline_base64_image，只有音频支持 inline_base64_audio。")

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
    provider_id: str = "",
    model_id: str = "",
) -> Dict[str, Any]:
    metadata = {
        "mimeType": mime_type,
        "mediaKind": infer_media_kind(mime_type),
        "apiStandard": (api_standard or "openai").lower(),
        "transportMode": (transport_mode or "url_reference").lower(),
        "payloadShape": describe_multimodal_payload_shape(
            mime_type=mime_type,
            api_standard=api_standard,
            provider_id=provider_id,
            model_id=model_id,
            transport_mode=transport_mode,
        ),
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
        else (
            record.get("supports_inline_preview")
            if record.get("supports_inline_preview") is not None
            else metadata.get("supportsInlinePreview")
            if metadata.get("supportsInlinePreview") is not None
            else metadata.get("supports_inline_preview")
        )
    )
    preview_kind = (
        record.get("previewKind")
        or record.get("preview_kind")
        or metadata.get("previewKind")
        or metadata.get("preview_kind")
    )
    origin = record.get("origin") or record.get("artifactOrigin") or metadata.get("origin") or metadata.get("artifactOrigin")

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
            "origin": origin,
            "artifactOrigin": origin,
            "artifact_origin": origin,
            "hasPreview": bool(preview_url),
            "projectId": metadata.get("projectId") or metadata.get("project_id"),
            "project_id": metadata.get("projectId") or metadata.get("project_id"),
            "workspaceId": metadata.get("workspaceId") or metadata.get("workspace_id"),
            "workspace_id": metadata.get("workspaceId") or metadata.get("workspace_id"),
            "workspaceRoot": metadata.get("workspaceRoot") or metadata.get("workspace_root"),
            "workspace_root": metadata.get("workspaceRoot") or metadata.get("workspace_root"),
            "workspaceRelativePath": metadata.get("workspaceRelativePath") or metadata.get("workspace_relative_path"),
            "workspace_relative_path": metadata.get("workspaceRelativePath") or metadata.get("workspace_relative_path"),
            "storageClass": metadata.get("storageClass") or metadata.get("storage_class"),
            "storage_class": metadata.get("storageClass") or metadata.get("storage_class"),
            "surfaceVisible": metadata.get("surfaceVisible")
                if metadata.get("surfaceVisible") is not None
                else metadata.get("surface_visible"),
            "surface_visible": metadata.get("surfaceVisible")
                if metadata.get("surfaceVisible") is not None
                else metadata.get("surface_visible"),
            "autoAttachToMessage": metadata.get("autoAttachToMessage")
                if metadata.get("autoAttachToMessage") is not None
                else metadata.get("auto_attach_to_message"),
            "auto_attach_to_message": metadata.get("autoAttachToMessage")
                if metadata.get("autoAttachToMessage") is not None
                else metadata.get("auto_attach_to_message"),
            "ephemeral": metadata.get("ephemeral"),
            "artifactSurfacePolicyRuleId": metadata.get("artifactSurfacePolicyRuleId")
                or metadata.get("artifact_surface_policy_rule_id"),
            "artifact_surface_policy_rule_id": metadata.get("artifactSurfacePolicyRuleId")
                or metadata.get("artifact_surface_policy_rule_id"),
            "pathPlane": metadata.get("pathPlane") or metadata.get("path_plane"),
            "path_plane": metadata.get("pathPlane") or metadata.get("path_plane"),
            "canonicalPath": metadata.get("canonicalPath") or metadata.get("canonical_path") or workspace_path,
            "canonical_path": metadata.get("canonicalPath") or metadata.get("canonical_path") or workspace_path,
            "displayLabel": title or artifact_id,
            "displaySubtitle": (
                metadata.get("canonicalPath")
                or metadata.get("canonical_path")
                or metadata.get("workspaceRelativePath")
                or metadata.get("workspace_relative_path")
                or workspace_path
                or source_path
                or preview_url
                or "暂无路径信息"
            ),
        }
    )
    return normalized


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
