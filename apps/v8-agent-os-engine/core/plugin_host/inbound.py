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

    mentions = normalized_metadata.get("mentions") if isinstance(normalized_metadata.get("mentions"), list) else []
    wake_triggered = bool(normalized_metadata.get("wake_triggered"))
    mentioned = bool(normalized_metadata.get("mentioned")) or bool(mentions) or wake_triggered
    plugin = _resolve_channel_plugin(normalized_source)

    normalized_metadata.setdefault("source", normalized_source)
    normalized_metadata.setdefault("chat_type", normalized_chat_type)
    normalized_metadata.setdefault("remote_id", normalized_remote_id)
    normalized_metadata.setdefault("mentioned", mentioned)
    normalized_metadata.setdefault("mentions", mentions)
    normalized_metadata.setdefault("wake_triggered", wake_triggered)
    normalized_metadata.setdefault("plugin_id", plugin.get("pluginId") if plugin else None)
    normalized_metadata.setdefault(
        "channel_envelope",
        {
            "source": normalized_source,
            "chatType": normalized_chat_type,
            "remoteId": normalized_remote_id,
            "senderId": sender_id,
            "senderName": sender_name,
            "isGroup": normalized_chat_type == "group",
        },
    )
    if normalized_chat_type == "group":
        normalized_metadata.setdefault(
            "group_marker",
            {
                "groupId": normalized_remote_id,
                "requiresMention": mentioned,
                "source": normalized_source,
            },
        )

    return {
        "source": normalized_source,
        "chatType": normalized_chat_type,
        "remoteId": normalized_remote_id,
        "textContent": normalized_text,
        "metadata": normalized_metadata,
        "plugin": plugin,
    }
