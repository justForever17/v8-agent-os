from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from typing import Any

from fastapi import HTTPException

from api.models import ChatRequest
from core.database import db
from core.network_neighbor_delivery import message_text as _message_text
from runtimes.network_supervisor.models import NetworkEnvelope, NetworkTraceContext
from runtimes.network_supervisor.neighbor_workspace import resolve_network_neighbor_workspace_binding
from runtimes.network_supervisor.service import network_supervisor_service


TASK_WAKE_INBOX = "inbox"
TASK_WAKE_PER_RESULT = "per_result"
TASK_WAKE_POLICIES = {TASK_WAKE_INBOX, TASK_WAKE_PER_RESULT}
TASK_HANDOFF_MARKER = "[HANDOFF_REQUEST]"


def _text(value: Any, *, limit: int | None = None) -> str:
    normalized = str(value or "").strip()
    if limit is not None and len(normalized) > limit:
        return normalized[:limit].rstrip()
    return normalized


def _normalize_tags(value: Any, *, limit: int = 12) -> list[str]:
    if isinstance(value, str):
        raw_items = re.split(r"[,，\n;；]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        tag = re.sub(r"\s+", "_", str(item or "").strip().lower())
        tag = re.sub(r"[^\w\u4e00-\u9fff\-]+", "", tag)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        tags.append(tag[:40])
        if len(tags) >= max(1, int(limit or 12)):
            break
    return tags




def _result_id(task_id: str, assignment_id: str, peer_id: str, status: str) -> str:
    digest = hashlib.sha1(f"{task_id}:{assignment_id}:{peer_id}:{status}".encode("utf-8")).hexdigest()[:16]
    return f"ntres_{digest}"


def _assignment_id(task_id: str, link_id: str, depth: int, parent_assignment_id: str | None = None) -> str:
    digest = hashlib.sha1(f"{task_id}:{link_id}:{parent_assignment_id or ''}:{depth}".encode("utf-8")).hexdigest()[:16]
    return f"ntasn_{digest}"


def _link_tags(link: dict[str, Any]) -> list[str]:
    metadata = dict(link.get("metadata") or {})
    return _normalize_tags(metadata.get("capabilityTags") or link.get("capabilityTags") or metadata.get("capability_tags"))


def _link_description(link: dict[str, Any]) -> str:
    metadata = dict(link.get("metadata") or {})
    return _text(metadata.get("description") or link.get("description"), limit=240)


def _wake_policy(value: Any, fallback: str = TASK_WAKE_INBOX) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in TASK_WAKE_POLICIES else fallback


def _handoff_parse(text: str) -> dict[str, Any] | None:
    body = str(text or "").strip()
    if TASK_HANDOFF_MARKER not in body:
        return None
    after_marker = body.split(TASK_HANDOFF_MARKER, 1)[1].strip()
    capabilities: list[str] = []
    reason = after_marker
    for line in after_marker.splitlines():
        normalized = line.strip()
        lowered = normalized.lower()
        if lowered.startswith(("capabilities:", "能力:", "需要能力:")):
            capabilities = _normalize_tags(normalized.split(":", 1)[-1] if ":" in normalized else normalized.split("：", 1)[-1])
        elif lowered.startswith(("reason:", "原因:")):
            reason = normalized.split(":", 1)[-1].strip() if ":" in normalized else normalized.split("：", 1)[-1].strip()
    return {
        "requestedCapabilities": capabilities,
        "reason": reason[:2000],
    }


class NetworkNeighborTaskService:
    def _state(self) -> dict[str, Any]:
        return network_supervisor_service.read_state()

    def _write_state(self, state: dict[str, Any]) -> None:
        network_supervisor_service.write_state(state)

    def settings_payload(self) -> dict[str, Any]:
        state = self._state()
        settings = dict(state.get("neighborTaskSettings") or {})
        return {
            "ok": True,
            "resultWakePolicy": _wake_policy(settings.get("resultWakePolicy"), TASK_WAKE_INBOX),
        }

    def update_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        state = self._state()
        settings = dict(state.get("neighborTaskSettings") or {})
        settings["resultWakePolicy"] = _wake_policy(payload.get("resultWakePolicy") or payload.get("wakePolicy"), TASK_WAKE_INBOX)
        state["neighborTaskSettings"] = settings
        self._write_state(state)
        return self.settings_payload()

    def list_devices(self) -> dict[str, Any]:
        from runtimes.network_supervisor.neighbor import network_neighbor_service

        links = list(network_neighbor_service.list_links().get("items") or [])
        items: list[dict[str, Any]] = []
        for link in links:
            metadata = dict(link.get("metadata") or {})
            items.append(
                {
                    "linkId": link.get("linkId") or link.get("id"),
                    "peerId": link.get("peerId"),
                    "nickname": link.get("remoteNickname") or link.get("displayName") or link.get("peerId"),
                    "localRole": link.get("localRole"),
                    "remoteRole": link.get("remoteRole"),
                    "online": bool(link.get("online")),
                    "lastSeenAt": link.get("lastSeenAt"),
                    "capabilityTags": _link_tags(link),
                    "description": _link_description(link),
                    "workspaceBinding": link.get("workspaceBinding") or {},
                    "recentAvailability": metadata.get("recentAvailability") or ("online" if link.get("online") else "offline"),
                }
            )
        return {"ok": True, "items": items}

    def _select_links(
        self,
        *,
        target: str | None = None,
        link_id: str | None = None,
        link_ids: list[str] | None = None,
        required_capabilities: list[str] | None = None,
        exclude_link_ids: list[str] | None = None,
        max_assignments: int = 1,
    ) -> tuple[list[dict[str, Any]], str]:
        devices = list(self.list_devices().get("items") or [])
        by_id = {str(item.get("linkId") or ""): item for item in devices if str(item.get("linkId") or "").strip()}
        excluded = {str(item or "").strip() for item in list(exclude_link_ids or []) if str(item or "").strip()}
        required = set(_normalize_tags(required_capabilities))

        explicit_ids = [str(item).strip() for item in ([link_id] if link_id else []) + list(link_ids or []) if str(item).strip()]
        if explicit_ids:
            selected = [by_id[item] for item in explicit_ids if item in by_id and item not in excluded]
            if not selected:
                raise HTTPException(status_code=404, detail="No matching neighbor link found for explicit target")
            return selected, "explicit"

        candidates = [item for item in devices if str(item.get("linkId") or "") not in excluded]
        if required:
            candidates = [
                item
                for item in candidates
                if required.issubset(set(_normalize_tags(item.get("capabilityTags"))))
            ] or [
                item
                for item in devices
                if str(item.get("linkId") or "") not in excluded
                and required.intersection(set(_normalize_tags(item.get("capabilityTags"))))
            ]
        if not candidates:
            raise HTTPException(status_code=404, detail="No neighbor devices match the requested capabilities")

        def _score(item: dict[str, Any]) -> tuple[int, int, str]:
            tags = set(_normalize_tags(item.get("capabilityTags")))
            return (
                len(tags.intersection(required)),
                1 if item.get("online") else 0,
                str(item.get("lastSeenAt") or ""),
            )

        candidates.sort(key=_score, reverse=True)
        if str(target or "").strip().lower() == "all":
            return candidates, "all"
        return candidates[: max(1, int(max_assignments or 1))], "auto"

    async def dispatch_task(
        self,
        *,
        body: str,
        title: str | None = None,
        target: str | None = None,
        link_id: str | None = None,
        link_ids: list[str] | None = None,
        required_capabilities: list[str] | str | None = None,
        wake_policy: str | None = None,
        origin_session_id: str | None = None,
        origin_run_id: str | None = None,
        workspace_binding: dict[str, Any] | None = None,
        task_id: str | None = None,
        parent_assignment_id: str | None = None,
        exclude_link_ids: list[str] | None = None,
        max_assignments: int = 1,
        depth: int = 0,
    ) -> dict[str, Any]:
        body_text = _text(body)
        if not body_text:
            raise HTTPException(status_code=400, detail="Task body is required")
        required = _normalize_tags(required_capabilities)
        policy = _wake_policy(wake_policy, _wake_policy(self.settings_payload().get("resultWakePolicy"), TASK_WAKE_INBOX))
        selected, target_mode = self._select_links(
            target=target,
            link_id=link_id,
            link_ids=link_ids,
            required_capabilities=required,
            exclude_link_ids=exclude_link_ids,
            max_assignments=max_assignments,
        )
        resolved_task_id = _text(task_id) or f"ntask_{uuid.uuid4().hex}"
        task = db.get_network_neighbor_task(resolved_task_id)
        if task and (task.get("body") != body_text or task.get("targetMode") == "inbound"):
            raise HTTPException(status_code=409, detail="Task ID already belongs to different content or a remote origin")
        task = task or db.upsert_network_neighbor_task(
            task_id=resolved_task_id,
            title=_text(title, limit=120) or body_text[:80],
            body=body_text,
            status="dispatching",
            target_mode=target_mode,
            origin_session_id=_text(origin_session_id) or None,
            origin_run_id=_text(origin_run_id) or None,
            wake_policy=policy,
            required_capabilities=required,
            workspace_binding=workspace_binding or {},
            metadata={
                "source": "network_neighbor_broker" if origin_session_id or origin_run_id else "admin",
                "target": target or "",
                "parentAssignmentId": parent_assignment_id or "",
            },
        )

        assignments: list[dict[str, Any]] = []
        for link in selected:
            link_id_value = str(link.get("linkId") or link.get("id") or "")
            assignment_id = _assignment_id(resolved_task_id, link_id_value, int(depth or 0), parent_assignment_id)
            prior = db.get_network_neighbor_assignment(assignment_id)
            if prior and prior.get("status") not in {"failed", "queued"}:
                assignments.append({**prior, "delivery": {"status": prior["status"], "duplicate": True}})
                continue
            assignment = prior or db.upsert_network_neighbor_assignment(
                assignment_id=assignment_id,
                task_id=resolved_task_id,
                link_id=link_id_value,
                peer_id=str(link.get("peerId") or ""),
                parent_assignment_id=parent_assignment_id or None,
                depth=int(depth or 0),
                status="queued",
                body=body_text,
                required_capabilities=required,
                wake_policy=policy,
                metadata={"targetMode": target_mode},
            )
            envelope = network_supervisor_service.build_envelope(
                message_type="neighbor.task.assign",
                to_peer_id=str(link.get("peerId") or ""),
                payload={
                    "taskId": resolved_task_id,
                    "assignmentId": assignment_id,
                    "parentAssignmentId": parent_assignment_id or "",
                    "title": task.get("title") or "",
                    "body": body_text,
                    "requiredCapabilities": required,
                    "wakePolicy": policy,
                    "originSessionId": _text(origin_session_id),
                    "originRunId": _text(origin_run_id),
                    "workspaceBinding": workspace_binding or {},
                    "depth": int(depth or 0),
                    "handoffDepthRemaining": max(0, 1 - int(depth or 0)),
                },
                trace=NetworkTraceContext(source_run_id=origin_run_id, source_session_id=origin_session_id, delegation_id=resolved_task_id),
                expires_in_seconds=300,
            )
            from runtimes.network_supervisor.neighbor import network_neighbor_service
            try:
                delivered = await network_neighbor_service.send_message(
                    link_id=link_id_value, message_id=assignment_id,
                    body=f"任务：{task.get('title') or body_text[:80]}\n\n{body_text}",
                    workspace_binding=workspace_binding or {}, source="supervisor", transport_envelope=envelope,
                )
                delivery = delivered["delivery"]
                next_status = "sent" if delivery.get("status") == "delivered" else "queued_via_relay"
                db.update_network_neighbor_assignment_status(assignment_id, status=next_status, only_without_result=True)
            except Exception as exc:
                db.update_network_neighbor_assignment_status(assignment_id, status="failed", error=str(exc), completed=True, only_without_result=True)
                delivery = {"status": "failed", "error": str(exc)}
            assignments.append({**(db.get_network_neighbor_assignment(assignment_id) or assignment), "delivery": delivery})

        self._recompute_task_status(resolved_task_id)
        return {"ok": True, "task": db.get_network_neighbor_task(resolved_task_id), "assignments": assignments}

    def list_tasks(self, *, limit: int = 50) -> dict[str, Any]:
        tasks = db.list_network_neighbor_tasks(limit=limit)
        return {
            "ok": True,
            "items": [
                {
                    **task,
                    "assignments": db.list_network_neighbor_assignments(task_id=str(task.get("taskId") or ""), limit=50),
                    "results": db.list_network_neighbor_task_results(task_id=str(task.get("taskId") or ""), limit=50),
                }
                for task in tasks
            ],
        }

    def read_task(self, task_id: str) -> dict[str, Any]:
        task = db.get_network_neighbor_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Neighbor task not found: {task_id}")
        return {
            "ok": True,
            "task": task,
            "assignments": db.list_network_neighbor_assignments(task_id=task_id, limit=200),
            "results": db.list_network_neighbor_task_results(task_id=task_id, limit=200),
        }

    def read_inbox(self, *, limit: int = 20) -> dict[str, Any]:
        tasks = db.list_network_neighbor_tasks(limit=limit)
        results: list[dict[str, Any]] = []
        for task in tasks:
            results.extend(db.list_network_neighbor_task_results(task_id=str(task.get("taskId") or ""), limit=20))
        results.sort(key=lambda item: str(item.get("createdAt") or ""), reverse=True)
        return {"ok": True, "tasks": tasks, "results": results[: max(1, min(int(limit or 20), 100))]}

    async def handle_task_envelope(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        if not getattr(network_supervisor_service.get_config_model(), "enabled", False):
            raise HTTPException(status_code=403, detail="Neighbor runtime is disabled")
        network_supervisor_service.verify_envelope(envelope, mark_nonce_seen=False)
        message_type = str(envelope.message_type or "").strip()
        if message_type == "neighbor.task.assign":
            return await self._handle_assign(envelope)
        if message_type == "neighbor.task.result":
            return await self._handle_result(envelope)
        if message_type == "neighbor.task.handoff_request":
            return await self._handle_handoff_request(envelope)
        if message_type == "neighbor.task.ack":
            return network_supervisor_service.build_envelope(
                message_type="neighbor.task.ack",
                to_peer_id=envelope.from_peer_id,
                payload={"status": "ack_seen", "requestMessageId": envelope.message_id},
                trace=envelope.trace,
                expires_in_seconds=60,
            )
        raise HTTPException(status_code=400, detail=f"Unsupported neighbor task envelope: {message_type}")

    async def _handle_assign(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        from runtimes.network_supervisor.neighbor import network_neighbor_service
        from core.network_neighbor_delivery import receive_message, _digest

        link = db.get_network_neighbor_link_by_peer(envelope.from_peer_id)
        if not link:
            raise HTTPException(status_code=404, detail="Neighbor link not found")
        payload = dict(envelope.payload or {})
        task_id, assignment_id = _text(payload.get("taskId")), _text(payload.get("assignmentId"))
        body = _text(payload.get("body"))
        depth = int(payload.get("depth") or 0)
        if not task_id or not assignment_id or not body or depth not in (0, 1):
            raise HTTPException(status_code=400, detail="Task requires stable taskId/assignmentId, a body and depth 0 or 1")
        existing_assignment = db.get_network_neighbor_assignment(assignment_id)
        task = db.get_network_neighbor_task(task_id)
        digest = _digest(payload)
        if existing_assignment and (
            existing_assignment.get("peerId") != envelope.from_peer_id
            or existing_assignment.get("taskId") != task_id
            or existing_assignment.get("linkId") != link["linkId"]
            or (existing_assignment.get("metadata") or {}).get("payloadDigest") != digest
        ):
            raise HTTPException(status_code=409, detail="Assignment identity or content conflicts with stored task")
        if task and (task.get("metadata") or {}).get("fromPeerId") != envelope.from_peer_id:
            raise HTTPException(status_code=409, detail="Task identity belongs to another origin")
        if task and task.get("body") != body:
            raise HTTPException(status_code=409, detail="Task identity reused with different content")
        binding_payload = payload.get("workspaceBinding") if isinstance(payload.get("workspaceBinding"), dict) else {}
        workspace_binding = resolve_network_neighbor_workspace_binding(
            peer_id=envelope.from_peer_id, local_role=str(link.get("localRole") or ""),
            remote_project_id=_text(binding_payload.get("projectId") or binding_payload.get("remoteProjectId")) or None,
            remote_workspace_id=_text(binding_payload.get("workspaceId") or binding_payload.get("remoteWorkspaceId")) or None,
            remote_workspace_path=_text(binding_payload.get("workspacePath") or binding_payload.get("remoteWorkspacePath")) or None,
            configured_binding=dict(link.get("workspaceBinding") or {}),
        )
        task_seed = dict(
            task_id=task_id, title=_text(payload.get("title"), limit=120) or body[:80], body=body,
            status="received", target_mode="inbound",
            origin_session_id=_text(payload.get("originSessionId")) or None,
            origin_run_id=_text(payload.get("originRunId")) or None,
            wake_policy=_wake_policy(payload.get("wakePolicy")),
            required_capabilities=_normalize_tags(payload.get("requiredCapabilities")),
            workspace_binding=workspace_binding,
            metadata={"source": "peer", "fromPeerId": envelope.from_peer_id},
        )
        assignment_seed = dict(
            assignment_id=assignment_id, task_id=task_id, link_id=link["linkId"], peer_id=envelope.from_peer_id,
            parent_assignment_id=_text(payload.get("parentAssignmentId")) or None,
            depth=depth, status="received", body=body,
            required_capabilities=_normalize_tags(payload.get("requiredCapabilities")),
            wake_policy=_wake_policy(payload.get("wakePolicy")),
            metadata={"handoffDepthRemaining": max(0, 1 - depth), "payloadDigest": digest},
        )
        message_body, preview, truncated = _message_text(f"任务：{task_seed['title']}\n\n{body}")
        receipt = receive_message(
            db, link=link, envelope=envelope, body=message_body, preview=preview,
            workspace_binding=workspace_binding, business_id=assignment_id,
            metadata={"kind": "neighbor.task.assign", "taskId": task_id, "assignmentId": assignment_id, "bodyTruncated": truncated},
            wake_payload={"kind": "neighbor_task_assignment"}, task_seed=task_seed, assignment_seed=assignment_seed,
        )
        queue = receipt["queue"]
        network_neighbor_service._kick_wake_queue_processing()
        return network_supervisor_service.build_envelope(
            message_type="neighbor.task.ack", to_peer_id=envelope.from_peer_id,
            payload={"requestMessageId": envelope.message_id, "taskId": task_id, "assignmentId": assignment_id,
                     "status": "duplicate" if receipt["duplicate"] else "received",
                     "runScheduled": True, "runId": queue["runId"], "queueId": queue["queueId"]},
            trace=envelope.trace, expires_in_seconds=60,
        )

    async def execute_assignment(
        self,
        *,
        link: dict[str, Any],
        task: dict[str, Any],
        assignment: dict[str, Any],
        inbound_message: dict[str, Any],
        workspace_binding: dict[str, Any],
        run_id: str,
    ) -> None:
        from runtimes.network_supervisor.neighbor import network_neighbor_service

        assignment_id = str(assignment.get("assignmentId") or assignment.get("id") or "")
        task_id = str(task.get("taskId") or task.get("id") or "")
        cached = [row for row in db.list_network_neighbor_task_results(assignment_id=assignment_id)
                  if (row.get("metadata") or {}).get("direction") == "outbound"]
        if cached:
            result = cached[-1]
            if result.get("status") == "handoff_requested":
                await self._send_handoff_request(link=link, task=task, assignment=assignment,
                    reason=result["body"], requested_capabilities=result.get("requestedCapabilities") or [],
                    workspace_binding=workspace_binding, run_id=run_id)
            else:
                await self._send_result(link=link, task=task, assignment=assignment, status=result["status"],
                    body=result["body"], workspace_binding=workspace_binding, run_id=run_id)
            return
        db.update_network_neighbor_assignment_status(assignment_id, status="running")
        session_id = network_neighbor_service._ensure_neighbor_session(link, workspace_binding)
        request = ChatRequest.model_validate(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "你正在处理一条来自邻居设备的任务。\n"
                            "请直接完成任务并给出可回传结果；如果因为缺少另一类设备能力无法完成，"
                            f"请以 {TASK_HANDOFF_MARKER} 开头，并列出 capabilities 与 reason。\n\n"
                            f"任务标题：{task.get('title') or ''}\n"
                            f"需要能力：{', '.join(list(assignment.get('requiredCapabilities') or [])) or '未指定'}\n\n"
                            f"{assignment.get('body') or task.get('body') or inbound_message.get('body') or ''}"
                        ),
                    }
                ],
                "stream": True,
                "sessionId": session_id,
                "conversationId": session_id,
                "userId": f"network-peer:{link.get('peerId')}",
                "projectId": workspace_binding.get("projectId"),
                "workspaceId": workspace_binding.get("workspaceId"),
                "workspacePath": workspace_binding.get("workspacePath"),
                "scopeHint": "network_neighbor_task",
            }
        )
        aggregated = ""
        status = "failed"
        run_bound = False
        try:
            async for event in _get_chat_runtime().stream_legacy_events(request, transport="network_neighbor_task", run_id=run_id):
                if not run_bound and db.get_run_record(run_id):
                    db.update_network_neighbor_assignment_status(assignment_id, status="running", run_id=run_id)
                    run_bound = True
                event_type = str(event.get("type") or "").strip()
                if event_type == "text_chunk":
                    aggregated += str(event.get("content") or "")
                elif event_type == "done":
                    terminal = str(event.get("status") or "finished")
                    status = "completed" if terminal in {"finished", "completed", "success"} else (terminal if terminal in {"waiting_approval", "waiting_input", "cancelled"} else "failed")
                    break
                elif event_type == "error":
                    aggregated = f"邻居任务处理失败：{event.get('error') or 'unknown error'}"
                    status = "failed"
                    break
        except Exception as exc:
            aggregated = f"邻居任务处理失败：{exc}"
            status = "failed"
        aggregated = aggregated.strip()
        if not aggregated and not status.startswith("waiting_"):
            status = "failed"
        handoff = _handoff_parse(aggregated) if status != "failed" else None
        if handoff:
            await self._send_handoff_request(
                link=link,
                task=task,
                assignment=assignment,
                reason=str(handoff.get("reason") or aggregated),
                requested_capabilities=list(handoff.get("requestedCapabilities") or []),
                workspace_binding=workspace_binding,
                run_id=run_id,
            )
            return
        await self._send_result(
            link=link,
            task=task,
            assignment=assignment,
            status=status,
            body=aggregated or "邻居任务没有返回可见内容。",
            workspace_binding=workspace_binding,
            run_id=run_id,
        )
        if status in {"waiting_input", "waiting_approval"}:
            from core.network_neighbor_delivery import NeighborExecutionPaused
            raise NeighborExecutionPaused(status)
        if status != "completed":
            raise RuntimeError(f"Neighbor task ended as {status}")

    async def _send_result(
        self,
        *,
        link: dict[str, Any],
        task: dict[str, Any],
        assignment: dict[str, Any],
        status: str,
        body: str,
        workspace_binding: dict[str, Any],
        run_id: str,
    ) -> None:
        identity = network_supervisor_service.ensure_local_identity()
        task_id = str(task.get("taskId") or task.get("id") or "")
        assignment_id = str(assignment.get("assignmentId") or assignment.get("id") or "")
        result_id = _result_id(task_id, assignment_id, str(identity.get("peerId") or ""), status)
        db.add_network_neighbor_task_result(
            result_id=result_id,
            task_id=task_id,
            assignment_id=assignment_id,
            link_id=str(link.get("linkId") or ""),
            peer_id=str(identity.get("peerId") or ""),
            status=status,
            summary=body[:280],
            body=body,
            metadata={"runId": run_id, "direction": "outbound"},
        )
        db.update_network_neighbor_assignment_status(assignment_id, status=status, result_id=result_id, completed=status in {"completed", "failed", "cancelled"})
        envelope = network_supervisor_service.build_envelope(
            message_type="neighbor.task.result",
            to_peer_id=str(link.get("peerId") or ""),
            payload={
                "taskId": task_id,
                "assignmentId": assignment_id,
                "resultId": result_id,
                "status": status,
                "summary": body[:280],
                "body": body,
                "workspaceBinding": workspace_binding,
            },
            trace=NetworkTraceContext(source_run_id=run_id, delegation_id=task_id),
            expires_in_seconds=300,
        )
        from runtimes.network_supervisor.neighbor import network_neighbor_service
        await network_neighbor_service.send_message(
            link_id=link["linkId"], message_id=result_id, body=body,
            workspace_binding=workspace_binding, source="supervisor", transport_envelope=envelope,
        )

    async def _send_handoff_request(
        self,
        *,
        link: dict[str, Any],
        task: dict[str, Any],
        assignment: dict[str, Any],
        reason: str,
        requested_capabilities: list[str],
        workspace_binding: dict[str, Any],
        run_id: str,
    ) -> None:
        identity = network_supervisor_service.ensure_local_identity()
        task_id = str(task.get("taskId") or task.get("id") or "")
        assignment_id = str(assignment.get("assignmentId") or assignment.get("id") or "")
        result_id = _result_id(task_id, assignment_id, str(identity.get("peerId") or ""), "handoff_requested")
        db.add_network_neighbor_task_result(
            result_id=result_id,
            task_id=task_id,
            assignment_id=assignment_id,
            link_id=str(link.get("linkId") or ""),
            peer_id=str(identity.get("peerId") or ""),
            status="handoff_requested",
            summary=reason[:280],
            body=reason,
            needs_attention=True,
            requested_capabilities=requested_capabilities,
            handoff_reason=reason,
            metadata={"runId": run_id, "direction": "outbound"},
        )
        db.update_network_neighbor_assignment_status(assignment_id, status="handoff_requested", result_id=result_id)
        envelope = network_supervisor_service.build_envelope(
            message_type="neighbor.task.handoff_request",
            to_peer_id=str(link.get("peerId") or ""),
            payload={
                "taskId": task_id,
                "assignmentId": assignment_id,
                "resultId": result_id,
                "reason": reason,
                "requestedCapabilities": requested_capabilities,
                "workspaceBinding": workspace_binding,
            },
            trace=NetworkTraceContext(source_run_id=run_id, delegation_id=task_id),
            expires_in_seconds=300,
        )
        from runtimes.network_supervisor.neighbor import network_neighbor_service
        await network_neighbor_service.send_message(
            link_id=link["linkId"], message_id=result_id, body=reason,
            workspace_binding=workspace_binding, source="supervisor", transport_envelope=envelope,
        )

    async def _handle_result(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        return await self._store_inbound_result(envelope, handoff=False)

    async def _handle_handoff_request(self, envelope: NetworkEnvelope) -> NetworkEnvelope:
        response = await self._store_inbound_result(envelope, handoff=True)
        if response.payload.get("status") == "duplicate":
            # Receipt may have committed immediately before a process crash.
            # Repair the missing handoff, but never dispatch existing children twice.
            parent_id = _text(envelope.payload.get("assignmentId"))
            children = db.list_network_neighbor_assignments(task_id=_text(envelope.payload.get("taskId")), limit=500)
            if any(row.get("parentAssignmentId") == parent_id for row in children):
                return response
        payload = dict(envelope.payload or {})
        assignment = db.get_network_neighbor_assignment(_text(payload.get("assignmentId")))
        link = db.get_network_neighbor_link_by_peer(envelope.from_peer_id)
        if not assignment or not link:
            return response
        if str(link.get("localRole") or "").strip() != "primary":
            db.update_network_neighbor_assignment_status(str(assignment.get("assignmentId") or ""), status="handoff_review_required")
            return response
        if int(assignment.get("depth") or 0) >= 1:
            db.update_network_neighbor_assignment_status(str(assignment.get("assignmentId") or ""), status="handoff_review_required")
            return response
        task = db.get_network_neighbor_task(str(assignment.get("taskId") or ""))
        requested = _normalize_tags(payload.get("requestedCapabilities"))
        if task:
            try:
                await self.dispatch_task(
                    task_id=str(task.get("taskId") or ""),
                    title=str(task.get("title") or ""),
                    body=str(task.get("body") or assignment.get("body") or ""),
                    target="auto",
                    required_capabilities=requested,
                    wake_policy=str(task.get("wakePolicy") or TASK_WAKE_INBOX),
                    origin_session_id=str(task.get("originSessionId") or "") or None,
                    origin_run_id=str(task.get("originRunId") or "") or None,
                    workspace_binding=dict(task.get("workspaceBinding") or {}),
                    parent_assignment_id=str(assignment.get("assignmentId") or ""),
                    exclude_link_ids=[str(link.get("linkId") or "")],
                    depth=1,
                )
            except Exception as exc:
                db.update_network_neighbor_assignment_status(str(assignment.get("assignmentId") or ""), status="handoff_no_candidate", error=str(exc))
        return response

    async def _store_inbound_result(self, envelope: NetworkEnvelope, *, handoff: bool) -> NetworkEnvelope:
        from core.network_neighbor_delivery import receive_message
        from runtimes.network_supervisor.neighbor import network_neighbor_service

        link = db.get_network_neighbor_link_by_peer(envelope.from_peer_id)
        if not link:
            raise HTTPException(status_code=404, detail="Neighbor link not found")
        payload = dict(envelope.payload or {})
        task_id, assignment_id = _text(payload.get("taskId")), _text(payload.get("assignmentId"))
        task = db.get_network_neighbor_task(task_id)
        assignment = db.get_network_neighbor_assignment(assignment_id)
        if not task or not assignment:
            raise HTTPException(status_code=404, detail="Unknown task or assignment")
        if (assignment.get("taskId"), assignment.get("peerId"), assignment.get("linkId")) != (task_id, envelope.from_peer_id, link["linkId"]):
            raise HTTPException(status_code=403, detail="Result sender does not own this assignment")
        if task.get("targetMode") == "inbound":
            raise HTTPException(status_code=403, detail="Peer cannot submit a result for its own inbound request")
        status = "handoff_requested" if handoff else _text(payload.get("status"))
        if status not in {"completed", "failed", "cancelled", "waiting_input", "waiting_approval", "handoff_requested"}:
            raise HTTPException(status_code=400, detail="Task result requires an explicit supported status")
        body = _text(payload.get("body") or payload.get("reason"))
        if status == "completed" and not body:
            raise HTTPException(status_code=400, detail="Completed task has no result")
        result_id = _text(payload.get("resultId")) or _result_id(task_id, assignment_id, envelope.from_peer_id, status)
        existing = db.get_network_neighbor_task_result(result_id)
        if existing and (existing.get("taskId"), existing.get("assignmentId"), existing.get("peerId"), existing.get("status"), existing.get("body")) != (task_id, assignment_id, envelope.from_peer_id, status, body):
            raise HTTPException(status_code=409, detail="Result identity reused with different content or owner")
        # A delayed waiting message cannot turn a terminal result back to running.
        if assignment.get("status") in {"completed", "failed", "cancelled"} and assignment.get("resultId") and not existing:
            raise HTTPException(status_code=409, detail="Assignment already has a terminal result")
        if existing and assignment.get("status") in {"completed", "failed", "cancelled"} and assignment.get("resultId") != result_id:
            return network_supervisor_service.build_envelope(
                message_type="neighbor.task.ack", to_peer_id=envelope.from_peer_id,
                payload={"requestMessageId": envelope.message_id, "taskId": task_id, "assignmentId": assignment_id,
                         "resultId": result_id, "status": "duplicate"}, trace=envelope.trace, expires_in_seconds=60,
            )
        summary = _text(payload.get("summary") or body[:280], limit=500)
        result_seed = dict(
            result_id=result_id, task_id=task_id, assignment_id=assignment_id,
            link_id=link["linkId"], peer_id=envelope.from_peer_id, status=status, summary=summary, body=body,
            needs_attention=handoff or status.startswith("waiting_"),
            requested_capabilities=_normalize_tags(payload.get("requestedCapabilities")),
            handoff_reason=_text(payload.get("reason"), limit=2000) if handoff else None,
            metadata={"direction": "inbound", "remoteMessageId": envelope.message_id},
        )
        policy = _wake_policy(task.get("wakePolicy"), _wake_policy(self.settings_payload().get("resultWakePolicy")))
        should_wake = not handoff and policy == TASK_WAKE_PER_RESULT and bool(task.get("originSessionId"))
        message_body, preview, truncated = _message_text(body or summary)
        receipt = receive_message(
            db, link=link, envelope=envelope, body=message_body, preview=preview,
            workspace_binding=dict(task.get("workspaceBinding") or {}), business_id=result_id,
            metadata={"kind": envelope.message_type, "taskId": task_id, "assignmentId": assignment_id,
                      "resultId": result_id, "bodyTruncated": truncated},
            wake_payload={"kind": "neighbor_task_result", "task": task, "assignment": assignment} if should_wake else None,
            result_seed=result_seed,
        )
        if not receipt["duplicate"]:
            self._recompute_task_status(task_id)
        if receipt["queue"]:
            network_neighbor_service._kick_wake_queue_processing()
        return network_supervisor_service.build_envelope(
            message_type="neighbor.task.ack", to_peer_id=envelope.from_peer_id,
            payload={"requestMessageId": envelope.message_id, "taskId": task_id, "assignmentId": assignment_id,
                     "resultId": result_id, "status": "duplicate" if receipt["duplicate"] else "received"},
            trace=envelope.trace, expires_in_seconds=60,
        )

    def _recompute_task_status(self, task_id: str) -> None:
        assignments = db.list_network_neighbor_assignments(task_id=task_id, limit=500)
        if not assignments:
            return
        superseded = {str(item.get("parentAssignmentId") or "") for item in assignments if item.get("parentAssignmentId")}
        statuses = {str(item.get("status") or "") for item in assignments
                    if str(item.get("assignmentId") or "") not in superseded}
        terminal = {"completed", "failed", "cancelled", "handoff_no_candidate", "handoff_review_required"}
        if statuses and statuses.issubset(terminal):
            if statuses == {"completed"}:
                db.update_network_neighbor_task_status(task_id, status="completed", completed=True)
            elif "completed" in statuses:
                db.update_network_neighbor_task_status(task_id, status="degraded", completed=True)
            else:
                db.update_network_neighbor_task_status(task_id, status="failed", completed=True)
        elif "running" in statuses:
            db.update_network_neighbor_task_status(task_id, status="running")
        else:
            db.update_network_neighbor_task_status(task_id, status="assigned")


    async def _wake_origin_supervisor(self, *, task: dict[str, Any], assignment: dict[str, Any], result: dict[str, Any], run_id: str) -> None:
        origin_session_id = _text(task.get("originSessionId"))
        if not origin_session_id:
            return
        body = (
            "邻居任务结果已返回。\n"
            f"taskId: {task.get('taskId')}\n"
            f"assignmentId: {assignment.get('assignmentId') or result.get('assignmentId')}\n"
            f"resultId: {result.get('resultId')}\n\n"
            f"{result.get('body') or result.get('summary') or ''}"
        )
        request = ChatRequest.model_validate(
            {
                "messages": [{"role": "user", "content": body}],
                "stream": True,
                "sessionId": origin_session_id,
                "conversationId": origin_session_id,
                "userId": "network-neighbor-result",
                "scopeHint": "network_neighbor_result",
                "sessionLanePolicy": "queue",
            }
        )
        async for event in _get_chat_runtime().stream_legacy_events(request, transport="network_neighbor_result", run_id=run_id):
            if str(event.get("type") or "") == "error":
                raise RuntimeError(str(event.get("error") or "Neighbor result continuation failed"))
            if str(event.get("type") or "") == "done":
                state = str(event.get("status") or "finished")
                if state in {"waiting_input", "waiting_approval"}:
                    from core.network_neighbor_delivery import NeighborExecutionPaused
                    raise NeighborExecutionPaused(state)
                if state in {"finished", "completed", "success"}:
                    return
                raise RuntimeError(f"Neighbor result continuation ended as {state}")
        raise RuntimeError("Neighbor result continuation ended without terminal event")


network_neighbor_task_service = NetworkNeighborTaskService()
def _get_chat_runtime():
    from runtimes.chat.runtime import chat_runtime

    return chat_runtime
