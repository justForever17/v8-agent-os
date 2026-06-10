from __future__ import annotations

import hashlib
import json
from typing import Any

from core.native_tool_registry import native_tool_family_for_name
from core.runtime_tool_access import runtime_tool_groups_catalog
from core.tool_surface import runtime_kind_for_tool


_VIRTUAL_TOOL_ENTRIES: list[dict[str, Any]] = [
    {
        "canonicalToolName": "ask_user",
        "origin": "governance",
        "runtimeKind": "chat",
        "toolGroup": "governance.ask_user",
        "visibility": "runtime_interrupt",
        "renderKind": "ask_user",
        "safetyClass": "human_input",
        "detailTool": "admin_diagnostics",
    },
    {
        "canonicalToolName": "approval_request",
        "origin": "safety",
        "runtimeKind": "safety",
        "toolGroup": "safety.approval",
        "visibility": "runtime_interrupt",
        "renderKind": "approval",
        "safetyClass": "safety_review",
        "detailTool": "admin_diagnostics",
    },
    {
        "canonicalToolName": "fetch_skill_instructions",
        "origin": "skills_runtime",
        "runtimeKind": "extensions",
        "toolGroup": "registry.skills",
        "visibility": "runtime_granted",
        "renderKind": "skill",
        "safetyClass": "read_only",
        "detailTool": "fetch_skill_instructions(detail_level='section')",
    },
    {
        "canonicalToolName": "external_compat_tool",
        "origin": "network_compat",
        "runtimeKind": "network_supervisor",
        "toolGroup": "external.client",
        "visibility": "client_supplied",
        "renderKind": "external_tool",
        "safetyClass": "external_boundary",
        "detailTool": "network_supervisor_diagnostics",
    },
    {
        "canonicalToolName": "internal_multimodal_llm_analysis",
        "origin": "engine_internal",
        "runtimeKind": "native",
        "toolGroup": "vision.internal",
        "visibility": "internal",
        "renderKind": "vision",
        "safetyClass": "read_only",
        "detailTool": "tool_observation_detail(rawRef)",
    },
]


def _schema_hash(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", getattr(tool, "__name__", "")) or "").strip()


def _tool_description(tool: Any) -> str:
    return str(getattr(tool, "description", getattr(tool, "__doc__", "")) or "").strip()


def _render_kind_for_tool(name: str) -> str:
    lowered = name.lower()
    if "command" in lowered or lowered in {"run_system_command", "execute_system_command"}:
        return "terminal"
    if lowered in {"read_native_file", "write_native_file", "grep_search", "workspace_broker"}:
        return "file"
    if lowered.startswith("web_") or lowered == "research_broker":
        return "search"
    if lowered.startswith("creative_media_"):
        return "creative_media"
    if lowered.startswith("computer_use_") or lowered.startswith("rpa_"):
        return "desktop"
    if lowered in {"runtime_broker", "delegation_broker"}:
        return "runtime"
    if lowered == "vision_media_analyzer":
        return "vision"
    return "generic"


def _safety_class_for_tool(name: str) -> str:
    lowered = name.lower()
    if any(token in lowered for token in ("write", "delete", "command", "cron", "hook", "process", "launch", "click", "input", "paste")):
        return "mutation_or_control"
    if any(token in lowered for token in ("read", "list", "search", "observe", "catalog", "get")):
        return "read_only"
    return "mixed"


def _native_tool_entries() -> list[dict[str, Any]]:
    from core.native_tools import NATIVE_TOOLS

    entries: list[dict[str, Any]] = []
    for tool in NATIVE_TOOLS:
        name = _tool_name(tool)
        if not name:
            continue
        if name == "ask_user":
            # ask_user is implemented as a LangGraph interrupt/governance surface.
            # Keep one canonical virtual record so UI and calibration do not see
            # both a native tool row and the actual interrupt identity.
            continue
        description = _tool_description(tool)
        schema_source = {
            "name": name,
            "description": description,
            "argsSchema": getattr(tool, "args_schema", None),
        }
        entries.append(
            {
                "canonicalToolName": name,
                "origin": "native",
                "runtimeKind": runtime_kind_for_tool(name),
                "toolFamily": native_tool_family_for_name(name),
                "toolGroup": "",
                "visibility": "registered",
                "renderKind": _render_kind_for_tool(name),
                "safetyClass": _safety_class_for_tool(name),
                "schemaHash": _schema_hash(schema_source),
                "detailTool": "tool_observation_detail(rawRef)",
            }
        )
    return entries


def _runtime_group_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for group in runtime_tool_groups_catalog():
        group_name = str(group.get("group") or "").strip()
        runtime_kind = str(group.get("runtimeKind") or "").strip()
        for tool_name in list(group.get("toolNames") or []):
            name = str(tool_name or "").strip()
            if not name:
                continue
            entries.append(
                {
                    "canonicalToolName": name,
                    "origin": "runtime_grant",
                    "runtimeKind": runtime_kind or runtime_kind_for_tool(name),
                    "toolGroup": group_name,
                    "visibility": "grant_required",
                    "renderKind": _render_kind_for_tool(name),
                    "safetyClass": _safety_class_for_tool(name),
                    "schemaHash": _schema_hash({"tool": name, "group": group_name}),
                    "detailTool": "runtime_broker(detail_level='catalog')",
                }
            )
    return entries


def _mcp_tool_entries() -> list[dict[str, Any]]:
    try:
        from core.extensions_runtime import extensions_runtime_service

        tools = extensions_runtime_service.get_mcp_tools()
    except Exception:
        tools = []
    entries: list[dict[str, Any]] = []
    for tool in tools:
        name = _tool_name(tool)
        if not name:
            continue
        metadata = getattr(tool, "metadata", None) if getattr(tool, "metadata", None) else {}
        server_name = str((metadata or {}).get("server_name") or "unknown")
        entries.append(
            {
                "canonicalToolName": name,
                "origin": "mcp",
                "runtimeKind": "extensions",
                "toolGroup": f"mcp.{server_name}",
                "visibility": "extension_registered",
                "renderKind": "mcp",
                "safetyClass": "external_extension",
                "schemaHash": _schema_hash({"name": name, "description": _tool_description(tool), "server": server_name}),
                "detailTool": "extensions_runtime_diagnostics",
            }
        )
    return entries


def build_tool_registry_index() -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    entries.extend(_native_tool_entries())
    entries.extend(_runtime_group_entries())
    entries.extend(_mcp_tool_entries())
    entries.extend(dict(item, schemaHash=_schema_hash(item)) for item in _VIRTUAL_TOOL_ENTRIES)

    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entry in entries:
        key = (
            str(entry.get("canonicalToolName") or ""),
            str(entry.get("origin") or ""),
            str(entry.get("toolGroup") or ""),
        )
        if key[0]:
            deduped[key] = {k: v for k, v in entry.items() if v not in (None, "", [], {})}

    items = sorted(deduped.values(), key=lambda item: (str(item.get("origin") or ""), str(item.get("canonicalToolName") or "")))
    by_origin: dict[str, int] = {}
    by_runtime: dict[str, int] = {}
    for item in items:
        by_origin[str(item.get("origin") or "unknown")] = by_origin.get(str(item.get("origin") or "unknown"), 0) + 1
        by_runtime[str(item.get("runtimeKind") or "unknown")] = by_runtime.get(str(item.get("runtimeKind") or "unknown"), 0) + 1
    return {
        "version": 1,
        "count": len(items),
        "byOrigin": by_origin,
        "byRuntimeKind": by_runtime,
        "items": items,
    }
