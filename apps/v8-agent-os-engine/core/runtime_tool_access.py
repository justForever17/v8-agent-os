from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable


RUNTIME_BROKER_TOOL_NAME = "runtime_broker"

RUNTIME_TOOL_GROUPS: dict[str, dict[str, Any]] = {
    "computer_use.control": {
        "runtimeKind": "computer_use",
        "label": "ComputerUse control",
        "summary": "检查桌面能力、观察当前屏幕、向 ComputerUseRuntime 发布高层任务。",
        "toolNames": [
            "computer_use_desktop_capabilities",
            "computer_use_observe_scene",
            "computer_use_execute_task",
        ],
    },
    "rpa.run": {
        "runtimeKind": "rpa",
        "label": "RPA run",
        "summary": "列出并运行 RPA draft / .robot 流程。",
        "toolNames": [
            "rpa_list_robot_scripts",
            "rpa_run_draft",
            "rpa_run_existing_flow",
        ],
    },
    "automation.ops": {
        "runtimeKind": "automation",
        "label": "Automation ops",
        "summary": "按需观察进程、审计日志，并管理 AutomationRuntime 的 cron/hooks。默认不常驻暴露。",
        "toolNames": [
            "list_processes",
            "read_audit_log",
            "manage_cron",
            "manage_hook",
        ],
    },
    "memory.read": {
        "runtimeKind": "memory",
        "label": "Memory read",
        "summary": "读取长期记忆、日期日志和记忆地图节点。",
        "toolNames": [
            "memory_recall",
            "memory_read_day",
            "memory_map_expand",
        ],
    },
    "memory.maintain": {
        "runtimeKind": "memory",
        "label": "Memory maintain",
        "summary": "维护、更新、删除和汇总长期记忆。",
        "toolNames": [
            "mem_update",
            "mem_delete",
            "memory_map",
            "mem_summary",
        ],
    },
    "research.core": {
        "runtimeKind": "research",
        "label": "Research core",
        "summary": "按需规划和运行只读 web research shards，返回带置信度、来源排序和引用的 evidence bundle。",
        "toolNames": [
            "research_broker",
        ],
    },
    "creative_media.core": {
        "runtimeKind": "creative_media",
        "label": "Creative Media core",
        "summary": "读取媒体目录，编译 recipe，登记资产/角色/关键帧，并创建和查询创意媒体 job。",
        "toolNames": [
            "creative_media_catalog",
            "creative_media_resolutions",
            "creative_media_create_job",
            "creative_media_get_job",
            "creative_media_list_jobs",
            "creative_media_job_artifacts",
            "creative_media_compile_recipe",
            "creative_media_get_recipe",
            "creative_media_list_recipes",
            "creative_media_register_asset",
            "creative_media_list_assets",
            "creative_media_create_character_bible",
            "creative_media_get_character_bible",
            "creative_media_list_character_bibles",
            "creative_media_register_keyframe",
            "creative_media_get_keyframe",
            "creative_media_list_keyframes",
            "creative_media_create_edit_plan",
            "creative_media_get_edit_plan",
            "creative_media_list_edit_plans",
            "creative_media_render_edit_plan",
            "creative_media_get_render",
            "creative_media_list_renders",
            "creative_media_create_quality_job",
            "creative_media_list_quality_jobs",
            "creative_media_get_quality_job",
            "creative_media_retry_job",
            "creative_media_cost_ledger",
            "creative_media_safety_events",
        ],
    },
}

SUBAGENT_ALWAYS_HIDDEN_TOOL_NAMES = {
    RUNTIME_BROKER_TOOL_NAME,
    "delegation_broker",
    "ask_user",
    "write_todos",
    "update_todo",
    "s3_broker",
    "http_request",
    "delegate_network_task",
    "web_fetch",
    "web_read",
    "web_extract",
    "web_search",
}

RUNTIME_MANAGED_TOOL_PREFIXES = ("computer_use_", "rpa_")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def tool_ref_name(tool_ref: Any) -> str:
    return str(getattr(tool_ref, "name", getattr(tool_ref, "__name__", "")) or "").strip()


def all_runtime_group_names() -> set[str]:
    return set(RUNTIME_TOOL_GROUPS)


def all_runtime_group_tool_names() -> set[str]:
    names: set[str] = set()
    for group in RUNTIME_TOOL_GROUPS.values():
        names.update(str(item) for item in list(group.get("toolNames") or []) if str(item).strip())
    return names


def is_runtime_managed_tool_name(tool_name: str) -> bool:
    normalized = str(tool_name or "").strip()
    if not normalized:
        return False
    if normalized in all_runtime_group_tool_names():
        return True
    return any(normalized.startswith(prefix) for prefix in RUNTIME_MANAGED_TOOL_PREFIXES)


def runtime_tool_groups_catalog() -> list[dict[str, Any]]:
    return [
        {
            "group": group_name,
            "runtimeKind": str(group.get("runtimeKind") or ""),
            "label": str(group.get("label") or group_name),
            "summary": str(group.get("summary") or ""),
            "toolNames": list(group.get("toolNames") or []),
        }
        for group_name, group in RUNTIME_TOOL_GROUPS.items()
    ]


def _normalize_group_name(value: Any, *, runtime_kind: str | None = None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if normalized in RUNTIME_TOOL_GROUPS:
        return normalized
    kind = str(runtime_kind or "").strip()
    if kind and "." not in normalized:
        candidate = f"{kind}.{normalized}"
        if candidate in RUNTIME_TOOL_GROUPS:
            return candidate
    return normalized


def normalize_runtime_access(values: Any, *, runtime_kind: str | None = None) -> list[str]:
    raw_values: list[Any]
    if values is None:
        raw_values = []
    elif isinstance(values, str):
        raw_values = [item.strip() for item in values.replace(";", ",").split(",")]
    elif isinstance(values, dict):
        raw_values = [values.get("group") or values.get("toolGroup") or values.get("name")]
    else:
        raw_values = list(values or [])

    groups: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        if isinstance(value, dict):
            group_name = _normalize_group_name(
                value.get("group") or value.get("toolGroup") or value.get("name"),
                runtime_kind=value.get("runtimeKind") or runtime_kind,
            )
        else:
            group_name = _normalize_group_name(value, runtime_kind=runtime_kind)
        if not group_name or group_name not in RUNTIME_TOOL_GROUPS or group_name in seen:
            continue
        seen.add(group_name)
        groups.append(group_name)
    return groups


def runtime_tool_names_for_groups(groups: Iterable[Any]) -> set[str]:
    tool_names: set[str] = set()
    for group_name in normalize_runtime_access(list(groups or [])):
        tool_names.update(str(item) for item in list(RUNTIME_TOOL_GROUPS[group_name].get("toolNames") or []))
    return tool_names


def runtime_access_from_route_context(route_context: dict[str, Any] | None) -> list[str]:
    context = dict(route_context or {})
    raw_grants = context.get("runtimeToolGrants")
    if isinstance(raw_grants, dict):
        raw_items = list(raw_grants.values())
    else:
        raw_items = list(raw_grants or [])
    return normalize_runtime_access(raw_items)


def grant_runtime_tool_groups(
    route_context: dict[str, Any] | None,
    groups: Iterable[Any],
    *,
    reason: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    context = deepcopy(dict(route_context or {}))
    current = {
        group_name: {
            "group": group_name,
            "runtimeKind": RUNTIME_TOOL_GROUPS[group_name]["runtimeKind"],
            "grantedAt": utc_now_iso(),
            "source": RUNTIME_BROKER_TOOL_NAME,
            "reason": "",
        }
        for group_name in runtime_access_from_route_context(context)
        if group_name in RUNTIME_TOOL_GROUPS
    }
    requested = normalize_runtime_access(list(groups or []))
    rejected: list[str] = []
    for item in list(groups or []):
        group_name = _normalize_group_name(item)
        if group_name and group_name not in RUNTIME_TOOL_GROUPS:
            rejected.append(group_name)
    for group_name in requested:
        current[group_name] = {
            "group": group_name,
            "runtimeKind": RUNTIME_TOOL_GROUPS[group_name]["runtimeKind"],
            "grantedAt": utc_now_iso(),
            "source": RUNTIME_BROKER_TOOL_NAME,
            "reason": str(reason or "").strip(),
        }
    context["runtimeToolGrants"] = list(current.values())
    return context, list(current.values()), rejected


def revoke_runtime_tool_groups(
    route_context: dict[str, Any] | None,
    groups: Iterable[Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    context = deepcopy(dict(route_context or {}))
    if groups is None:
        context["runtimeToolGrants"] = []
        return context, []
    revoke_set = set(normalize_runtime_access(list(groups or [])))
    kept = [
        {"group": group_name, "runtimeKind": RUNTIME_TOOL_GROUPS[group_name]["runtimeKind"]}
        for group_name in runtime_access_from_route_context(context)
        if group_name not in revoke_set and group_name in RUNTIME_TOOL_GROUPS
    ]
    context["runtimeToolGrants"] = kept
    return context, kept


def _dedupe_tools(tools: Iterable[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for tool_ref in list(tools or []):
        name = tool_ref_name(tool_ref)
        identity = name or str(id(tool_ref))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(tool_ref)
    return result


def filter_visible_tools_for_actor(
    tools: Iterable[Any],
    *,
    actor: str,
    route_context: dict[str, Any] | None = None,
    runtime_access: Iterable[Any] | None = None,
) -> list[Any]:
    normalized_actor = str(actor or "").strip().lower()
    if normalized_actor == "supervisor":
        granted_groups = runtime_access_from_route_context(route_context)
    else:
        if runtime_access is None:
            context = dict(route_context or {})
            task_brief = context.get("taskBrief") or context.get("task_brief") or {}
            if isinstance(task_brief, dict):
                runtime_access = task_brief.get("runtimeAccess") or task_brief.get("runtime_access")
        granted_groups = normalize_runtime_access(list(runtime_access or []))

    granted_runtime_tools = runtime_tool_names_for_groups(granted_groups)
    visible: list[Any] = []
    for tool_ref in list(tools or []):
        name = tool_ref_name(tool_ref)
        if not name:
            visible.append(tool_ref)
            continue
        if name == RUNTIME_BROKER_TOOL_NAME:
            if normalized_actor == "supervisor":
                visible.append(tool_ref)
            continue
        if normalized_actor != "supervisor" and name in SUBAGENT_ALWAYS_HIDDEN_TOOL_NAMES:
            continue
        if is_runtime_managed_tool_name(name):
            if name in granted_runtime_tools:
                visible.append(tool_ref)
            continue
        visible.append(tool_ref)
    return _dedupe_tools(visible)
