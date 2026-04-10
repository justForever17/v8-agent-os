from __future__ import annotations

from typing import Any

from .registry import default_plugin_registry


def _resolve_channel_plugin(channel_type: str) -> dict[str, Any] | None:
    normalized = str(channel_type or "").strip().lower()
    if not normalized:
        return None
    registry = default_plugin_registry()
    for plugin in (registry.get("plugins") or {}).values():
        if not isinstance(plugin, dict):
            continue
        channels = list((plugin.get("capabilities") or {}).get("channels") or (plugin.get("manifestSummary") or {}).get("channels") or [])
        channel_set = {str(item).strip().lower() for item in channels if str(item).strip()}
        if normalized in channel_set:
            return dict(plugin)
    return None


def _normalize_inbound_mentions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append({"name": text})
            continue
        if not isinstance(item, dict):
            continue
        current: dict[str, Any] = {}
        mention_id = str(
            item.get("id")
            or item.get("userId")
            or item.get("user_id")
            or item.get("openId")
            or item.get("open_id")
            or ""
        ).strip()
        if mention_id:
            current["id"] = mention_id
        name = str(
            item.get("name")
            or item.get("displayName")
            or item.get("display_name")
            or item.get("text")
            or ""
        ).strip()
        if name:
            current["name"] = name
        mention_type = str(
            item.get("type")
            or item.get("mentionType")
            or item.get("mention_type")
            or item.get("entityType")
            or item.get("entity_type")
            or ""
        ).strip()
        if mention_type:
            current["type"] = mention_type
        if current:
            normalized.append(current)
    return normalized


def _normalize_inbound_attachments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                normalized.append({"name": text})
            continue
        if not isinstance(item, dict):
            continue
        current: dict[str, Any] = {}
        for field, aliases in {
            "name": ("name", "fileName", "file_name", "title"),
            "url": ("url", "downloadUrl", "download_url", "href"),
            "path": ("path", "filePath", "file_path", "localPath", "local_path"),
            "mimeType": ("mimeType", "mime_type", "contentType", "content_type"),
            "kind": ("kind", "type", "assetKind", "asset_kind"),
        }.items():
            value_text = str(next((item.get(alias) for alias in aliases if item.get(alias) not in (None, "")), "") or "").strip()
            if value_text:
                current[field] = value_text
        size_value = item.get("size", item.get("fileSize", item.get("file_size", item.get("bytes"))))
        if isinstance(size_value, (int, float)) or (isinstance(size_value, str) and size_value.strip().isdigit()):
            current["size"] = int(size_value)
        if current:
            normalized.append(current)
    return normalized


def normalize_inbound_message(
    *,
    source: str,
    chat_type: str,
    remote_id: str,
    text_content: str,
    sender_id: str | None = None,
    sender_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_source = str(source or "").strip().lower() or "channel"
    normalized_chat_type = "group" if str(chat_type or "").strip().lower() == "group" else "p2p"
    normalized_remote_id = str(remote_id or "").strip() or "unknown"
    normalized_text = str(text_content or "").strip()
    normalized_metadata = dict(metadata or {})

    mentions = _normalize_inbound_mentions(normalized_metadata.get("mentions"))
    attachments = _normalize_inbound_attachments(normalized_metadata.get("attachments"))
    thread_id = str(
        normalized_metadata.get("thread_id")
        or normalized_metadata.get("threadId")
        or ""
    ).strip() or None
    account_id = str(
        normalized_metadata.get("account_id")
        or normalized_metadata.get("accountId")
        or ""
    ).strip() or None
    account_scope = str(
        normalized_metadata.get("account_scope")
        or normalized_metadata.get("accountScope")
        or ""
    ).strip() or None
    event_kind = str(
        normalized_metadata.get("event_kind")
        or normalized_metadata.get("eventKind")
        or ""
    ).strip() or None
    event_subtype = str(
        normalized_metadata.get("event_subtype")
        or normalized_metadata.get("eventSubtype")
        or ""
    ).strip() or None
    raw_action_payload = normalized_metadata.get("action_payload")
    if raw_action_payload is None:
        raw_action_payload = normalized_metadata.get("actionPayload")
    action_payload = dict(raw_action_payload) if isinstance(raw_action_payload, dict) else {}
    raw_payload_ref = dict(normalized_metadata.get("raw_payload_ref") or normalized_metadata.get("rawPayloadRef") or {})
    channel_envelope = dict(normalized_metadata.get("channel_envelope") or normalized_metadata.get("channelEnvelope") or {})
    wake_triggered = bool(normalized_metadata.get("wake_triggered"))
    mentioned = bool(normalized_metadata.get("mentioned")) or bool(mentions) or wake_triggered
    plugin = _resolve_channel_plugin(normalized_source)

    normalized_metadata.setdefault("source", normalized_source)
    normalized_metadata.setdefault("chat_type", normalized_chat_type)
    normalized_metadata.setdefault("remote_id", normalized_remote_id)
    normalized_metadata.setdefault("channel_type", normalized_source)
    normalized_metadata.setdefault(
        "channel_name",
        str(
            normalized_metadata.get("channel_name")
            or normalized_metadata.get("channelName")
            or normalized_source
        ).strip() or normalized_source,
    )
    channel_domain = str(
        normalized_metadata.get("channel_domain")
        or normalized_metadata.get("channelDomain")
        or ""
    ).strip() or None
    if channel_domain:
        normalized_metadata.setdefault("channel_domain", channel_domain)
    if account_id:
        normalized_metadata.setdefault("account_id", account_id)
    if account_scope:
        normalized_metadata.setdefault("account_scope", account_scope)
    default_account = str(
        normalized_metadata.get("default_account")
        or normalized_metadata.get("defaultAccount")
        or ""
    ).strip() or None
    if default_account:
        normalized_metadata.setdefault("default_account", default_account)
    normalized_metadata.setdefault("mentioned", mentioned)
    normalized_metadata.setdefault("mentions", mentions)
    normalized_metadata.setdefault("attachments", attachments)
    if thread_id:
        normalized_metadata.setdefault("thread_id", thread_id)
    if event_kind:
        normalized_metadata.setdefault("event_kind", event_kind)
    if event_subtype:
        normalized_metadata.setdefault("event_subtype", event_subtype)
    if action_payload:
        normalized_metadata.setdefault("action_payload", action_payload)
    if raw_payload_ref:
        normalized_metadata.setdefault("raw_payload_ref", raw_payload_ref)
    normalized_metadata.setdefault("wake_triggered", wake_triggered)
    normalized_metadata.setdefault("plugin_id", plugin.get("pluginId") if plugin else None)
    channel_envelope = {
        **channel_envelope,
        "source": normalized_source,
        "channelId": str(channel_envelope.get("channelId") or normalized_source).strip() or normalized_source,
        "chatType": normalized_chat_type,
        "remoteId": normalized_remote_id,
        "conversationId": str(channel_envelope.get("conversationId") or normalized_remote_id).strip() or normalized_remote_id,
        "messageId": str(channel_envelope.get("messageId") or normalized_metadata.get("message_id") or "").strip() or None,
        "accountId": account_id,
        "accountScope": account_scope,
        "threadId": thread_id,
        "senderId": sender_id,
        "senderName": sender_name,
        "mentions": mentions,
        "attachments": attachments,
        "eventKind": event_kind,
        "eventSubtype": event_subtype,
        "actionPayload": action_payload or None,
        "rawPayloadRef": raw_payload_ref or None,
        "isGroup": normalized_chat_type == "group",
    }
    normalized_metadata["channel_envelope"] = channel_envelope
    if normalized_chat_type == "group":
        normalized_metadata.setdefault(
            "group_marker",
            {
                "groupId": normalized_remote_id,
                "requiresMention": mentioned,
                "source": normalized_source,
                "threadId": thread_id,
            },
        )

    return {
        "source": normalized_source,
        "chatType": normalized_chat_type,
        "remoteId": normalized_remote_id,
        "accountId": account_id,
        "accountScope": account_scope,
        "threadId": thread_id,
        "mentions": mentions,
        "attachments": attachments,
        "eventKind": event_kind,
        "eventSubtype": event_subtype,
        "actionPayload": action_payload or None,
        "channelEnvelope": channel_envelope,
        "textContent": normalized_text,
        "metadata": normalized_metadata,
        "plugin": plugin,
    }
