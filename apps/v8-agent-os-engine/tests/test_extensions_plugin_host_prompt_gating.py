from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.plugin_host.tool_registry import plugin_host_tool_registry
from runtimes.extensions.runtime import _should_expose_plugin_host_tools


def _tool(*, bridge_ready: bool, inventory_source: str, plugin_id: str = "openclaw-lark", managed_channels: list[str] | None = None):
    return SimpleNamespace(
        name=f"{plugin_id}.feishu_doc",
        description="dynamic plugin host tool",
        metadata={
            "pluginHost": True,
            "pluginId": plugin_id,
            "canonicalName": f"{plugin_id}.feishu_doc",
            "rawName": "feishu_doc",
            "bridgeReady": bridge_ready,
            "toolInventorySource": inventory_source,
            "toolInventoryHealth": "healthy",
            "managedChannels": managed_channels or ["feishu"],
        },
    )


class ExtensionsPluginHostPromptGatingTests(unittest.TestCase):
    def test_hides_plugin_host_tools_when_inventory_is_log_inferred(self):
        tools = [_tool(bridge_ready=False, inventory_source="openclaw_log_registered_tools")]
        self.assertFalse(
            _should_expose_plugin_host_tools(
                user_query="帮我发一条飞书消息",
                plugin_host_tools=tools,
                context_payload={"runtime_kind": "chat"},
            )
        )

    def test_exposes_plugin_host_tools_when_bridge_is_ready_and_query_mentions_channel(self):
        tools = [_tool(bridge_ready=True, inventory_source="gateway_rpc")]
        self.assertTrue(
            _should_expose_plugin_host_tools(
                user_query="请用 OpenClaw 的 feishu channel 发文档",
                plugin_host_tools=tools,
                context_payload={"runtime_kind": "chat"},
            )
        )

    def test_exposes_plugin_host_tools_for_channel_runtime_context(self):
        tools = [_tool(bridge_ready=True, inventory_source="durable_cache")]
        self.assertTrue(
            _should_expose_plugin_host_tools(
                user_query="继续处理这个任务",
                plugin_host_tools=tools,
                context_payload={"runtime_kind": "channel"},
            )
        )

    def test_registry_does_not_build_log_inferred_supervisor_tools(self):
        with patch(
            "core.plugin_host.plugin_host_service.list_bridge_tools",
            return_value={
                "bridgeReady": False,
                "toolInventorySource": "openclaw_log_registered_tools",
                "toolInventoryHealth": "healthy",
                "inventory": [
                    {
                        "canonicalName": "openclaw-lark.feishu_doc",
                        "toolName": "feishu_doc",
                        "pluginId": "openclaw-lark",
                        "description": "从 OpenClaw 运行日志推断的动态工具：feishu_doc",
                    }
                ],
            },
        ):
            self.assertEqual(plugin_host_tool_registry.build_supervisor_tools(), [])

    def test_registry_builds_tools_from_live_healthy_inventory(self):
        with patch(
            "core.plugin_host.plugin_host_service.list_bridge_tools",
            return_value={
                "bridgeReady": True,
                "toolInventorySource": "gateway_rpc",
                "toolInventoryHealth": "healthy",
                "managedChannels": ["feishu"],
                "inventory": [
                    {
                        "canonicalName": "openclaw-lark.feishu_doc",
                        "toolName": "feishu_doc",
                        "pluginId": "openclaw-lark",
                        "description": "Read or write Feishu documents.",
                    }
                ],
            },
        ):
            tools = plugin_host_tool_registry.build_supervisor_tools()

        self.assertEqual([tool.name for tool in tools], ["openclaw-lark.feishu_doc"])
        self.assertEqual(tools[0].metadata.get("toolInventorySource"), "gateway_rpc")


if __name__ == "__main__":
    unittest.main()
