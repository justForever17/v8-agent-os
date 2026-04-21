from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .profiles import callable_tool_defs, support_profile

_LIVE_INVENTORY_SOURCES = {"gateway_rpc", "plugin_source_scan", "durable_cache"}


class PluginHostToolArgs(BaseModel):
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="传给 OpenClaw 原生插件工具的参数对象；若工具支持 action，请在 params.action 中显式传入。",
    )


class PluginHostToolRegistry:
    def describe_plugin(
        self,
        *,
        plugin: dict[str, Any],
        runtime_enabled: bool,
        family_allowed: bool,
        host_surface: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plugin_id = str(plugin.get("pluginId") or "").strip()
        existing_surface = plugin.get("toolSurface")
        if isinstance(existing_surface, dict) and (
            "registrationMode" in existing_surface or "callableTools" in existing_surface
        ):
            return {
                "pluginId": plugin_id,
                "registrationMode": str(existing_surface.get("registrationMode") or "none"),
                "callableTools": [
                    dict(item)
                    for item in list(existing_surface.get("callableTools") or [])
                    if isinstance(item, dict)
                ],
                "callableEnabled": bool(existing_surface.get("callableEnabled")),
                "unavailableReason": str(existing_surface.get("unavailableReason") or "").strip() or None,
            }
        support = support_profile(plugin_id=plugin_id)
        plugin_family = str(plugin.get("pluginType") or "plugin").strip().lower() or "plugin"
        callable_descriptor = callable_tool_defs(plugin_id=plugin_id)
        registration_mode = str(support.get("registrationMode") or "none")
        execution_support = str(support.get("executionSupport") or "execution unsupported")
        family_adapter_ready = bool(support.get("familyAdapterReady"))
        host_surface = dict(host_surface or {})
        inbound_ownership = str(host_surface.get("inboundOwnership") or "delegated").strip() or "delegated"
        handoff_ready = bool(host_surface.get("handoffReady"))

        callable_tools = [dict(item) for item in list(callable_descriptor.get("callableTools") or []) if isinstance(item, dict)]
        if registration_mode == "none":
            registration_mode = str(callable_descriptor.get("registrationMode") or "none")
        enabled = bool(
            runtime_enabled
            and family_allowed
            and family_adapter_ready
            and execution_support == "plugin_tools_proxy"
            and callable_tools
        )
        unavailable_reason: str | None = None
        if not runtime_enabled:
            unavailable_reason = "PluginHostRuntime 当前已关闭。"
        elif not family_allowed:
            unavailable_reason = f"当前宿主未允许 {plugin_family} 家族接管。"
        elif not family_adapter_ready:
            unavailable_reason = "当前插件家族尚未完成 V8 family adapter。"
        elif execution_support != "plugin_tools_proxy":
            unavailable_reason = "当前插件未声明原生工具代理执行支持。"
        elif plugin_family == "channel" and callable_tools and inbound_ownership != "v8_owned" and not handoff_ready:
            unavailable_reason = "当前真实入站 ownership 仍未切到 V8，暂不注册该渠道插件的原生工具。"
        elif not callable_tools:
            unavailable_reason = "当前插件没有可暴露给 Supervisor 的原生工具。"

        return {
            "pluginId": plugin_id,
            "registrationMode": registration_mode,
            "callableTools": callable_tools,
            "callableEnabled": enabled,
            "unavailableReason": unavailable_reason,
        }

    def build_supervisor_tools(self) -> list[Any]:
        from core.plugin_host import plugin_host_service

        tools: list[Any] = []
        try:
            catalog = plugin_host_service.list_bridge_tools(limit=48)
        except Exception:
            return tools
        bridge_ready = bool(catalog.get("bridgeReady"))
        inventory_source = str(catalog.get("toolInventorySource") or "").strip() or None
        inventory_health = str(catalog.get("toolInventoryHealth") or "").strip() or None
        inventory_freshness = str(catalog.get("toolInventoryFreshness") or "").strip() or None
        # Supervisor-callable PluginHost tools must come from a live bridge inventory.
        # Log-inferred OpenClaw tools are useful diagnostics, but they are not safe or
        # reliable enough to enter the callable tool pool.
        if (
            not bridge_ready
            or str(inventory_source or "").strip().lower() not in _LIVE_INVENTORY_SOURCES
            or (str(inventory_health or "").strip().lower() not in {"", "healthy"})
        ):
            return tools
        managed_channels = [
            str(item).strip()
            for item in list(catalog.get("managedChannels") or [])
            if str(item).strip()
        ]
        tool_entries = list(catalog.get("exposure") or catalog.get("inventory") or catalog.get("tools") or [])
        for tool_def in tool_entries:
            if not isinstance(tool_def, dict):
                continue
            if tool_def.get("allowed") is False:
                continue
            canonical_name = str(tool_def.get("canonicalName") or tool_def.get("name") or "").strip()
            if not canonical_name:
                continue
            raw_tool_name = str(tool_def.get("toolName") or canonical_name).strip()
            plugin_id = str(tool_def.get("pluginId") or "").strip() or None

            def _invoke(
                params: dict[str, Any],
                *,
                _canonical_name: str = canonical_name,
                _plugin_id: str | None = plugin_id,
            ):
                return plugin_host_service.invoke_bridge_tool(
                    tool_name=_canonical_name,
                    plugin_id=_plugin_id,
                    params=params,
                )

            metadata: dict[str, Any] = {}
            metadata.update(
                {
                    "pluginHost": True,
                    "pluginId": plugin_id,
                    "canonicalName": canonical_name,
                    "rawName": raw_tool_name,
                    "source": str(tool_def.get("source") or "").strip() or None,
                    "bridgeReady": bridge_ready,
                    "toolInventorySource": inventory_source,
                    "toolInventoryHealth": inventory_health,
                    "toolInventoryFreshness": inventory_freshness,
                    "managedChannels": managed_channels,
                }
            )
            tool = StructuredTool.from_function(
                func=_invoke,
                name=canonical_name,
                description=str(
                    tool_def.get("description")
                    or tool_def.get("label")
                    or raw_tool_name
                    or canonical_name
                ).strip()
                or canonical_name,
                args_schema=PluginHostToolArgs,
                metadata=metadata,
            )
            tools.append(tool)
        return tools


plugin_host_tool_registry = PluginHostToolRegistry()
