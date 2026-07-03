from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.tools import tool

from erc.runtime_context import get_runtime_context


def _compact_devices(items: list[dict[str, Any]]) -> str:
    if not items:
        return "结果：没有已连接的邻居设备。"
    lines = ["结果：可用邻居设备"]
    for item in items[:20]:
        tags = ", ".join(list(item.get("capabilityTags") or [])) or "未标注"
        online = "在线" if item.get("online") else "离线"
        lines.append(
            f"- {item.get('nickname') or item.get('peerId')} "
            f"(linkId={item.get('linkId')}, {online}, 角色={item.get('localRole')})：{tags}"
        )
        description = str(item.get("description") or "").strip()
        if description:
            lines.append(f"  说明：{description[:160]}")
    return "\n".join(lines)


def _compact_task(payload: dict[str, Any]) -> str:
    task = dict(payload.get("task") or {})
    assignments = list(payload.get("assignments") or [])
    results = list(payload.get("results") or [])
    lines = [
        f"结果：邻居任务 {task.get('taskId') or 'unknown'}",
        f"- 状态：{task.get('status') or 'unknown'}",
        f"- 唤醒策略：{task.get('wakePolicy') or 'inbox'}",
    ]
    if assignments:
        lines.append("- 派发：")
        for item in assignments[:20]:
            delivery = dict(item.get("delivery") or {})
            delivery_status = delivery.get("status") or item.get("status")
            lines.append(f"  - {item.get('assignmentId')} -> {item.get('peerId')}：{delivery_status}")
    if results:
        lines.append("- 结果：")
        for item in results[:20]:
            summary = str(item.get("summary") or item.get("body") or "").strip().replace("\n", " ")
            lines.append(f"  - {item.get('resultId')} / {item.get('status')}：{summary[:180]}")
    return "\n".join(lines)


@tool
async def network_neighbor_broker(
    mode: str,
    task_brief: Optional[str] = None,
    target: Optional[str] = None,
    link_id: Optional[str] = None,
    link_ids: Optional[list[str]] = None,
    required_capabilities: Optional[list[str]] = None,
    wake_policy: Optional[str] = None,
    task_id: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Coordinate trusted Neighbor devices. Modes: list_devices, dispatch_task, read_task, read_inbox.

    Use this when the user explicitly wants cross-device collaboration. Prefer a specific linkId when the
    target is known; otherwise provide required_capabilities. Multiple devices are used only when target='all'.
    Results default to the neighbor task inbox unless wake_policy='per_result' is explicitly needed.
    """
    from runtimes.network_supervisor.neighbor_tasks import network_neighbor_task_service

    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode == "list_devices":
        return _compact_devices(list(network_neighbor_task_service.list_devices().get("items") or []))
    if normalized_mode == "read_inbox":
        payload = network_neighbor_task_service.read_inbox(limit=limit)
        return "结果：邻居任务收件箱\n" + json.dumps(payload, ensure_ascii=False, indent=2)
    if normalized_mode == "read_task":
        if not str(task_id or "").strip():
            return "结果：读取失败\n原因：缺少 task_id。"
        return _compact_task(network_neighbor_task_service.read_task(str(task_id).strip()))
    if normalized_mode == "dispatch_task":
        brief = str(task_brief or "").strip()
        if not brief:
            return "结果：派发失败\n原因：缺少 task_brief。"
        runtime_context = get_runtime_context() or {}
        result = await network_neighbor_task_service.dispatch_task(
            body=brief,
            title=brief[:80],
            target=target,
            link_id=link_id,
            link_ids=link_ids,
            required_capabilities=required_capabilities,
            wake_policy=wake_policy,
            origin_session_id=str(runtime_context.get("session_id") or "").strip() or None,
            origin_run_id=str(runtime_context.get("run_id") or "").strip() or None,
            workspace_binding=dict(runtime_context.get("workspace_binding") or {}),
            max_assignments=50 if str(target or "").strip().lower() == "all" else 1,
        )
        return _compact_task(result)
    return "结果：不支持的模式\n可用模式：list_devices、dispatch_task、read_task、read_inbox。"
