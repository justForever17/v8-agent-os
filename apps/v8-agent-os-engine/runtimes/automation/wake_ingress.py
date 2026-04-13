from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional


VALID_TRIGGER_KINDS = {"nudge", "wake", "recovery_wake"}
VALID_ATTACH_POLICIES = {"new_session", "attach_session", "attach_run", "resume_run"}


def _clean_str(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _clean_jsonable_dict(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if item is not None}


def normalize_target_binding(raw: Any) -> Optional[Dict[str, Any]]:
    data = _clean_jsonable_dict(raw)
    normalized = {
        "projectId": _clean_str(data.get("projectId") or data.get("project_id")),
        "workspaceId": _clean_str(data.get("workspaceId") or data.get("workspace_id")),
        "workspacePath": _clean_str(data.get("workspacePath") or data.get("workspace_path")),
        "channelType": _clean_str(data.get("channelType") or data.get("channel_type")),
        "channelRemoteId": _clean_str(data.get("channelRemoteId") or data.get("channel_remote_id")),
        "resolvedScope": _clean_str(data.get("resolvedScope") or data.get("resolved_scope")),
    }
    cleaned = {key: value for key, value in normalized.items() if value is not None}
    return cleaned or None


def normalize_recovery_anchor(raw: Any) -> Optional[Dict[str, Any]]:
    data = _clean_jsonable_dict(raw)
    normalized = {
        "sessionId": _clean_str(data.get("sessionId") or data.get("session_id")),
        "runId": _clean_str(data.get("runId") or data.get("run_id")),
        "parentSessionId": _clean_str(data.get("parentSessionId") or data.get("parent_session_id")),
        "checkpointId": _clean_str(data.get("checkpointId") or data.get("checkpoint_id")),
        "blockedReason": _clean_str(data.get("blockedReason") or data.get("blocked_reason")),
    }
    cleaned = {key: value for key, value in normalized.items() if value is not None}
    return cleaned or None


def _extract_envelope_candidates(payload: Dict[str, Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    candidates: Dict[str, Any] = {}
    sources = [
        kwargs.get("wake_ingress_envelope"),
        kwargs.get("wakeIngressEnvelope"),
        payload.get("wakeIngressEnvelope"),
        payload.get("wake_ingress_envelope"),
        payload,
    ]
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in (
            "triggerKind",
            "trigger_kind",
            "targetBinding",
            "target_binding",
            "recoveryAnchor",
            "recovery_anchor",
            "attachPolicy",
            "attach_policy",
            "wakeReason",
            "wake_reason",
            "message",
            "task",
            "sourceMetadata",
            "source_metadata",
        ):
            if key in source and source.get(key) is not None and key not in candidates:
                candidates[key] = source.get(key)
    return candidates


def normalize_wake_ingress_envelope(
    *,
    source_runtime: str,
    trigger_source: Optional[str],
    payload: Dict[str, Any],
    kwargs: Dict[str, Any],
    default_message: Optional[str] = None,
    default_attach_policy: Optional[str] = None,
    allow_nudge_without_target: bool = True,
    source_runtime_enabled: bool = True,
) -> Dict[str, Any]:
    candidates = _extract_envelope_candidates(payload, kwargs)
    requested_trigger_kind = _clean_str(candidates.get("triggerKind") or candidates.get("trigger_kind")) or "nudge"
    trigger_kind = requested_trigger_kind if requested_trigger_kind in VALID_TRIGGER_KINDS else "nudge"

    target_binding = normalize_target_binding(
        candidates.get("targetBinding")
        or candidates.get("target_binding")
        or {
            "projectId": kwargs.get("project_id"),
            "workspaceId": kwargs.get("workspace_id"),
            "workspacePath": kwargs.get("workspace_path"),
            "channelType": kwargs.get("channel_type"),
            "channelRemoteId": kwargs.get("channel_remote_id"),
            "resolvedScope": kwargs.get("resolved_scope"),
        }
    )
    recovery_anchor = normalize_recovery_anchor(
        candidates.get("recoveryAnchor")
        or candidates.get("recovery_anchor")
        or {
            "sessionId": kwargs.get("session_id"),
            "runId": kwargs.get("run_id"),
            "parentSessionId": kwargs.get("parent_session_id"),
        }
    )

    has_target = bool(target_binding)
    has_recovery = bool(recovery_anchor)
    nudge_without_target_blocked = False
    if not source_runtime_enabled:
        trigger_kind = "nudge"
    if trigger_kind == "recovery_wake" and not has_recovery:
        trigger_kind = "wake" if has_target else "nudge"
    elif trigger_kind == "wake" and not (has_target or has_recovery):
        trigger_kind = "nudge"
    elif trigger_kind == "nudge" and not allow_nudge_without_target and not (has_target or has_recovery):
        nudge_without_target_blocked = True

    requested_attach_policy = _clean_str(candidates.get("attachPolicy") or candidates.get("attach_policy"))
    normalized_default_attach = _clean_str(default_attach_policy)
    attach_policy = requested_attach_policy if requested_attach_policy in VALID_ATTACH_POLICIES else None
    if attach_policy is None:
        if recovery_anchor and recovery_anchor.get("runId"):
            attach_policy = "attach_run" if trigger_kind == "recovery_wake" else "attach_session"
        elif recovery_anchor and recovery_anchor.get("sessionId"):
            attach_policy = "attach_session"
        else:
            attach_policy = normalized_default_attach if normalized_default_attach in VALID_ATTACH_POLICIES else "new_session"

    if attach_policy in {"attach_run", "resume_run"} and not (recovery_anchor or {}).get("runId"):
        attach_policy = "attach_session" if (recovery_anchor or {}).get("sessionId") else "new_session"
    if attach_policy == "attach_session" and not ((recovery_anchor or {}).get("sessionId") or has_target):
        attach_policy = "new_session"

    message = (
        _clean_str(candidates.get("message"))
        or _clean_str(candidates.get("task"))
        or _clean_str(default_message)
        or ""
    )
    wake_reason = _clean_str(candidates.get("wakeReason") or candidates.get("wake_reason")) or (
        "recovery_requested" if trigger_kind == "recovery_wake" else "non_human_ingress"
    )
    source_metadata = _clean_jsonable_dict(candidates.get("sourceMetadata") or candidates.get("source_metadata"))
    source_metadata.setdefault("triggerSource", _clean_str(trigger_source) or "manual")
    source_metadata.setdefault("requestedTriggerKind", requested_trigger_kind)
    if not source_runtime_enabled:
        source_metadata["sourceRuntimeDisabled"] = True
    if trigger_kind == "nudge" and nudge_without_target_blocked:
        source_metadata["nudgeWithoutTargetRejected"] = True
        source_metadata["policyDegradedToNudge"] = True

    return {
        "sourceRuntime": source_runtime,
        "triggerKind": trigger_kind,
        "targetBinding": target_binding,
        "recoveryAnchor": recovery_anchor,
        "attachPolicy": attach_policy,
        "wakeReason": wake_reason,
        "message": message,
        "sourceMetadata": source_metadata,
    }


def apply_wake_envelope_to_kwargs(kwargs: Dict[str, Any], envelope: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(kwargs)
    target_binding = dict(envelope.get("targetBinding") or {})
    recovery_anchor = dict(envelope.get("recoveryAnchor") or {})

    merged.setdefault("project_id", target_binding.get("projectId"))
    merged.setdefault("workspace_id", target_binding.get("workspaceId"))
    merged.setdefault("workspace_path", target_binding.get("workspacePath"))
    merged.setdefault("channel_type", target_binding.get("channelType"))
    merged.setdefault("channel_remote_id", target_binding.get("channelRemoteId"))
    merged.setdefault("resolved_scope", target_binding.get("resolvedScope"))

    attach_policy = str(envelope.get("attachPolicy") or "new_session").strip()
    if attach_policy in {"attach_run", "resume_run"} and recovery_anchor.get("runId"):
        merged["run_id"] = recovery_anchor.get("runId")
    if attach_policy == "attach_session" and recovery_anchor.get("sessionId"):
        merged["session_id"] = recovery_anchor.get("sessionId")
    if recovery_anchor.get("parentSessionId"):
        merged.setdefault("parent_session_id", recovery_anchor.get("parentSessionId"))

    merged["wake_ingress_envelope"] = envelope
    return merged


def derive_wake_session_id(envelope: Dict[str, Any]) -> Optional[str]:
    if not isinstance(envelope, dict):
        return None
    if str(envelope.get("triggerKind") or "nudge") == "nudge":
        return None
    recovery_anchor = dict(envelope.get("recoveryAnchor") or {})
    if recovery_anchor.get("sessionId"):
        return str(recovery_anchor["sessionId"])

    canonical = json.dumps(
        {
            "sourceRuntime": envelope.get("sourceRuntime"),
            "triggerKind": envelope.get("triggerKind"),
            "targetBinding": envelope.get("targetBinding") or {},
            "wakeReason": envelope.get("wakeReason"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()[:12]
    runtime = _clean_str(envelope.get("sourceRuntime")) or "automation"
    return f"wake:{runtime}:{digest}"
