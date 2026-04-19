from __future__ import annotations

import unittest
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
