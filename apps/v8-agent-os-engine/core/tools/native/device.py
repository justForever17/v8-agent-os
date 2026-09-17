"""Supervisor's fixed device tools, governed by existing Safety and episodes."""
from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from langchain_core.tools import InjectedToolCallId, tool

from erc.runtime_context import get_runtime_context
from runtimes.network_supervisor.executors.protocol import ExecutorError, MUTATING, canonical, require
from runtimes.network_supervisor.executors.service import get_executor_service


def device_actor(context, service):
    from core.database import db
    from core.client_identity.owner import session_identifier
    run_id = str(context.get("run_id") or "")
    run = db.get_run_record(run_id) if run_id else None
    require(run is not None, "active_runtime_required", 403)
    owner = service.identity.owners.owner()
    require(run.get("user_id") in {owner["id"], session_identifier(owner)}, "runtime_owner_mismatch", 403)
    require(not context.get("subagent_id") and not context.get("delegation_id"), "supervisor_device_tool_required", 403)
    require(run.get("status") not in {"cancelled", "aborted", "failed", "error", "completed"}, "runtime_no_longer_active", 409)
    return owner["id"], run_id


@tool
def device_broker(
    mode: Literal["list", "execute", "status", "cancel"] = "list",
    device_id: str = "",
    capability: str = "",
    resource_id: str = "",
    arguments: dict | None = None,
    precondition: dict | None = None,
    command_id: str = "",
    ttl_ms: int = 15000,
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Use registered fixed-capability Android/ESP32 executors without delegation.

    list exposes exact device/resource grants. execute queues one device.health,
    sensor.read, actuator.set, android.observe, android.capture or android.action episode.
    Observe first: Android node actions require the returned app/window/node map
    and device/boot/control session anchors; actuator.set requires a current
    resourceRevision, boolean level and positive maxHoldMs. No raw pin, shell,
    authorization-page action or LLM runs on the endpoint.
    android.capture takes scope='window' (Android 14+) or explicitly granted
    scope='display'. It returns a screenshot artifact for vision analysis.
    Tap/swipe require a current window screenshot's frameId, geometryRevision,
    width/height, rotation and viewport anchors. Coordinates are frame pixels.
    A tree-only observation cannot authorize coordinates; a display screenshot
    permits observation only. Screenshot-only actions need no fabricated nodeMap.
    Window screenshots exclude overlay pixels. A coordinate_in_obstructed_region
    receipt means the real display path is covered: have the overlay moved/hidden,
    capture again and make a new decision; never blindly repeat the old gesture.
    Use vision_media_analyzer with the returned screenshotRef.filePath;
    a reference alone is not visual inspection. Refresh the capture
    if the target or screenshot expires; never guess a frame ID or coordinates.
    Keep commandId and inspect status/receipt. received/started are progress;
    succeeded proves driver completion only, businessVerification stays unverified.
    Never repeat an unknown_outcome: query status and obtain local reconciliation.
    cancel requests stop; it cannot prove already-started effects were undone.
    Enrollment, grants and reconciliation belong to the human device settings.
    """
    from core.client_identity import IdentityError
    try:
        context, service = get_runtime_context(), get_executor_service()
        owner, run_id = device_actor(context, service)
        if mode == "list":
            return canonical({"items": service.list(owner)})
        if mode == "status":
            return canonical(service.status(owner, command_id))
        if mode == "cancel":
            return canonical(service.cancel(owner, command_id))
        require(mode == "execute" and bool(tool_call_id), "device_tool_call_required", 400)
        # Retries of a tool invocation bind the same immutable command and episode.
        suffix = hashlib.sha256((run_id + ":" + tool_call_id).encode()).hexdigest()[:32]
        command_id, episode_id = "command_" + suffix, "episode_device_" + suffix
        result = service.create(owner=owner, command_id=command_id, device=device_id, capability=capability,
                                resource=resource_id, arguments=arguments or {}, precondition=precondition or {},
                                ttl_ms=ttl_ms, trace={"runId": run_id, "episodeId": episode_id})
        if result["status"] != "authorized":
            return canonical(result)
        if capability in MUTATING:
            from core.tools.native.tool_governance import _enforce_safety_decision
            from erc.safety_guardian import SafetyDecision
            decision = SafetyDecision(verdict="review", risk_code="device_action", governance_target="device_resource",
                reason="执行已授权远程设备上的固定动作。", details={"runtime_context": context,
                    "operationId": command_id, "exactApprovalRequired": True, "target": canonical(result["command"]),
                    "operationArguments": result["command"]})
            allowed, reason = _enforce_safety_decision(decision, tool_call_id=tool_call_id,
                question=f"允许在设备 {device_id} 的 {resource_id} 执行 {capability} 吗？")
            if not allowed:
                service.cancel(owner, command_id)
                return canonical({"ok": False, "code": "device_action_blocked", "reason": reason, "commandId": command_id})
        # Run cancellation and user ownership are rechecked after any approval.
        device_actor(context, service)
        from core.runtime_episodes import build_runtime_episode, enqueue_runtime_episode
        episode = build_runtime_episode(kind="device_action", need={
            "episodeId": episode_id, "reason": f"{capability}: {device_id}/{resource_id}",
            "inputs": {"commandId": command_id, "ownerId": owner},
            "idempotencyKey": command_id, "retryPolicy": {"maxAttempts": 1},
        })
        queued = enqueue_runtime_episode(episode, session_id=str(context.get("session_id") or ""), run_id=run_id)
        return canonical({"commandId": command_id, "episodeId": episode_id, "status": queued["state"],
                          "deviceId": device_id, "resourceId": resource_id, "capability": capability,
                          "businessVerification": "unverified", "next": "等待设备回执；使用 status 查询，不重复发送。"})
    except (ExecutorError, IdentityError) as exc:
        return canonical({"ok": False, "code": exc.code})
