from __future__ import annotations

import re
from threading import RLock
from typing import Any

from langchain_core.tools import StructuredTool


_ALIASES: dict[str, dict[str, str]] = {}
_ALIASES_LOCK = RLock()


def _safe_name(value: Any) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value or "").strip())
    return normalized.strip("_").lower()


def resolve_plugin_tool_alias(tool_name: str) -> dict[str, str] | None:
    with _ALIASES_LOCK:
        payload = _ALIASES.get(str(tool_name or "").strip())
        return dict(payload) if payload else None


def build_guarded_mcp_tool(
    original_tool: Any,
    *,
    plugin_id: str,
    component_id: str,
    grant: dict[str, Any],
) -> StructuredTool:
    """Wrap an MCP tool with a fail-closed grant check on every invocation."""

    original_name = str(getattr(original_tool, "name", "") or "").strip()
    if not original_name:
        raise ValueError("plugin MCP tool is missing a name")
    alias = f"plugin__{_safe_name(plugin_id)}__{_safe_name(original_name)}"
    original_metadata = dict(getattr(original_tool, "metadata", None) or {})
    server_name = str(original_metadata.get("server_name") or "").strip()
    alias_payload = {
        "pluginId": str(plugin_id or "").strip().lower(),
        "componentId": str(component_id or "").strip(),
        "grantId": str(grant.get("grantId") or "").strip(),
        "pluginDigest": str(grant.get("manifestDigest") or grant.get("pluginDigest") or "").strip(),
        "originalToolName": original_name,
        "serverName": server_name,
    }
    with _ALIASES_LOCK:
        _ALIASES[alias] = dict(alias_payload)

    async def guarded_call(**kwargs: Any) -> Any:
        from erc.runtime_context import get_runtime_context
        from runtimes.plugin_manager.service import plugin_manager_service

        context = get_runtime_context() or {}
        session_id = str(context.get("session_id") or context.get("sessionId") or "").strip()
        run_id = str(context.get("run_id") or context.get("runId") or "").strip() or None
        agent_id = str(context.get("agent_id") or context.get("agentId") or "supervisor").strip() or "supervisor"
        runtime_kind = str(context.get("runtime_kind") or context.get("runtimeKind") or "chat").strip()
        delegation_id = str(context.get("delegation_id") or context.get("delegationId") or "").strip() or None
        delegation_depth = context.get("delegation_depth") or context.get("delegationDepth")
        grantee_type = "subagent" if runtime_kind == "subagent" and agent_id != "supervisor" else "supervisor"
        plugin_manager_service.validate_grant_for_invocation(
            grant_id=alias_payload["grantId"],
            plugin_id=alias_payload["pluginId"],
            component_id=alias_payload["componentId"],
            session_id=session_id,
            run_id=run_id,
            grantee_type=grantee_type,
            grantee_id=agent_id,
            delegation_id=delegation_id,
            delegation_depth=delegation_depth,
            manifest_digest=alias_payload["pluginDigest"] or None,
        )
        return await original_tool.ainvoke(kwargs)

    description = str(getattr(original_tool, "description", "") or "").strip()
    guarded = StructuredTool.from_function(
        coroutine=guarded_call,
        name=alias,
        description=(
            f"Authorized {plugin_id} plugin tool. Original MCP tool: {original_name}. "
            "The Engine revalidates the active plugin grant on every call. "
            f"{description}"
        ).strip(),
        args_schema=getattr(original_tool, "args_schema", None),
        infer_schema=getattr(original_tool, "args_schema", None) is None,
        response_format=getattr(original_tool, "response_format", "content"),
        metadata={
            **original_metadata,
            "server_name": server_name,
            "plugin_id": alias_payload["pluginId"],
            "plugin_component_id": alias_payload["componentId"],
            "plugin_grant_id": alias_payload["grantId"],
            "plugin_manifest_digest": alias_payload["pluginDigest"],
            "plugin_original_tool_name": original_name,
        },
    )
    return guarded
