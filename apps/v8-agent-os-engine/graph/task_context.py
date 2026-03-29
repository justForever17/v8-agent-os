import hashlib
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage, SystemMessage


def extract_task_context(messages, max_context=6):
    """Extract only task-relevant non-system messages for delegated execution."""
    non_system_messages = [message for message in messages if not isinstance(message, SystemMessage)]

    if len(non_system_messages) <= max_context:
        return non_system_messages

    task_message = None
    task_index = -1
    for index in range(len(non_system_messages) - 1, -1, -1):
        if isinstance(non_system_messages[index], HumanMessage):
            task_message = non_system_messages[index]
            task_index = index
            break

    if task_message is None:
        return non_system_messages[-max_context:]

    delegation_messages = non_system_messages[task_index:]
    if len(delegation_messages) > max_context:
        delegation_messages = delegation_messages[-max_context:]

    return delegation_messages


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fallback_task_id(name: str, plan: str) -> str:
    digest = hashlib.sha1(f"{name}|{plan}".encode("utf-8")).hexdigest()[:12]
    return f"legacy-{digest}"


def _normalize_todo_item(item: dict, *, index: int, created_at: str, task_id: str) -> dict:
    normalized = dict(item or {})
    normalized.setdefault("id", f"{task_id}-item-{index}")
    normalized["text"] = str(normalized.get("text") or "").strip()
    normalized["status"] = str(normalized.get("status") or "pending").strip() or "pending"
    normalized.setdefault("order", index)
    normalized.setdefault("createdAt", created_at)
    normalized.setdefault("updatedAt", created_at)
    return normalized


def resolve_todos(raw_todos):
    """Resolve todo init/update markers into a stable task plan snapshot."""
    resolved: list[dict] = []
    task_info: dict = {}
    latest_update_at: str | None = None

    for item in raw_todos:
        if item.get("_task_init"):
            created_at = str(item.get("createdAt") or _now_iso())
            task_info = {
                "taskId": str(item.get("taskId") or _fallback_task_id(item.get("name", ""), item.get("plan", ""))),
                "name": item.get("name", ""),
                "plan": item.get("plan", ""),
                "runId": item.get("runId"),
                "sessionId": item.get("sessionId"),
                "createdAt": created_at,
                "updatedAt": str(item.get("updatedAt") or created_at),
            }
            latest_update_at = task_info["updatedAt"]
        elif item.get("_update"):
            index = item.get("index", -1)
            if 0 <= index < len(resolved):
                update_at = str(item.get("updatedAt") or _now_iso())
                if item["status"] == "in_progress":
                    for sibling_index, sibling in enumerate(resolved):
                        if sibling_index != index and sibling.get("status") == "in_progress":
                            sibling["status"] = "pending"
                            sibling["updatedAt"] = update_at
                resolved[index]["status"] = item["status"]
                resolved[index]["updatedAt"] = update_at
                latest_update_at = update_at
        else:
            task_id = str(task_info.get("taskId") or _fallback_task_id(task_info.get("name", ""), task_info.get("plan", "")))
            created_at = str(task_info.get("createdAt") or _now_iso())
            resolved.append(_normalize_todo_item(dict(item), index=len(resolved), created_at=created_at, task_id=task_id))
            latest_update_at = resolved[-1].get("updatedAt") or latest_update_at

    task_id = str(task_info.get("taskId") or _fallback_task_id(task_info.get("name", ""), task_info.get("plan", "")))
    task_info.setdefault("taskId", task_id)
    task_info.setdefault("createdAt", latest_update_at or _now_iso())
    task_info["updatedAt"] = str(latest_update_at or task_info.get("updatedAt") or task_info["createdAt"])

    deduped: list[dict] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(resolved):
        normalized = _normalize_todo_item(item, index=index, created_at=str(task_info["createdAt"]), task_id=task_id)
        identity = str(normalized.get("id") or "").strip()
        if not identity or identity in seen_ids:
            normalized["id"] = f"{task_id}-item-{index}"
            identity = normalized["id"]
        seen_ids.add(identity)
        deduped.append(normalized)

    is_active = any(item.get("status") in ("pending", "in_progress") for item in deduped)
    updated_at = _parse_iso(task_info.get("updatedAt"))
    stale = False
    if is_active and updated_at is not None:
        stale = (datetime.now(timezone.utc) - updated_at).total_seconds() >= 600

    task_info["isActive"] = is_active
    task_info["isStale"] = stale
    task_info["itemCount"] = len(deduped)
    return {"task_info": task_info, "items": deduped}
