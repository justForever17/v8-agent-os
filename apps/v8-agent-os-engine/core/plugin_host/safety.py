from __future__ import annotations

from typing import Any

from erc.safety_guardian import SafetyDecision, safety_guardian


def build_group_guard_summary() -> dict[str, Any]:
    config = safety_guardian.export_config()
    guard = dict(config.get("channelGroupGuard") or {})
    return {
        "enabled": bool(guard.get("enabled", False)),
        "allowlistOnly": bool(guard.get("allowlistOnly", False)),
        "requireMention": bool(guard.get("requireMention", False)),
        "auditOnly": bool(guard.get("auditOnly", False)),
        "allowlistCount": len(list(guard.get("allowlistGroups") or [])),
    }


def assess_channel_inbound_group_risk(
    *,
    source: str,
    chat_type: str,
    remote_id: str,
    text_content: str,
    metadata: dict[str, Any] | None = None,
) -> SafetyDecision:
    config = safety_guardian.export_config()
    guard = dict(config.get("channelGroupGuard") or {})
    if str(chat_type or "").strip().lower() != "group":
        return SafetyDecision(verdict="allow", reason="not_group_chat", risk_code="channel_group_guard")
    if not bool(guard.get("enabled", False)):
        return SafetyDecision(verdict="allow", reason="group_guard_disabled", risk_code="channel_group_guard")

    allowlist = {str(item).strip() for item in list(guard.get("allowlistGroups") or []) if str(item).strip()}
    requires_mention = bool(guard.get("requireMention", False))
    allowlist_only = bool(guard.get("allowlistOnly", False))
    audit_only = bool(guard.get("auditOnly", False))
    metadata = dict(metadata or {})
    mentions = metadata.get("mentions") if isinstance(metadata.get("mentions"), list) else []
    wake_triggered = bool(metadata.get("wake_triggered"))
    explicit_mentioned = bool(metadata.get("mentioned")) or bool(mentions) or wake_triggered

    if allowlist_only and remote_id not in allowlist:
        verdict = "allow" if audit_only else "block"
        return SafetyDecision(
            verdict=verdict,
            reason=f"群聊 {remote_id} 不在自动响应 allowlist 中",
            risk_code="channel_group_not_allowlisted",
            details={
                "source": source,
                "remote_id": remote_id,
                "chat_type": chat_type,
                "text_preview": text_content[:120],
                "audit_only": audit_only,
            },
            allow_override=verdict != "block",
        )

    if requires_mention and not explicit_mentioned:
        verdict = "allow" if audit_only else "block"
        return SafetyDecision(
            verdict=verdict,
            reason="当前群聊未明确 @ 机器人，已按群聊危险会话拦截策略处理",
            risk_code="channel_group_requires_mention",
            details={
                "source": source,
                "remote_id": remote_id,
                "chat_type": chat_type,
                "text_preview": text_content[:120],
                "audit_only": audit_only,
            },
            allow_override=verdict != "block",
        )

    return SafetyDecision(
        verdict="allow",
        reason="group_guard_passed",
        risk_code="channel_group_guard",
        details={
            "source": source,
            "remote_id": remote_id,
            "chat_type": chat_type,
            "explicit_mentioned": explicit_mentioned,
            "allowlist_only": allowlist_only,
        },
    )
