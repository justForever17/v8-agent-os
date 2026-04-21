from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from graph.agent_factories import build_specialist_agent_components


def _tool(name: str, **metadata):
    return SimpleNamespace(name=name, metadata=metadata)


class ContextualAutoToolSurfaceTests(unittest.TestCase):
    def _build_components(self, *, tool_mode: str):
        with patch("graph.agent_factories.create_routed_tool_node", return_value=lambda state: state):
            return build_specialist_agent_components(
                loaded_agents=[
                    {
                        "id": "agent-one",
                        "name": "Agent One",
                        "description": "Test agent",
                        "system_prompt": "You are a test agent.",
                        "tool_mode": tool_mode,
                        "tools": ["docs_server.query", "plugin-a.generate"],
                    }
                ],
                all_mcp_tools=[_tool("docs_server.query")],
                all_plugin_host_tools=[
                    _tool(
                        "gateway.generate",
                        pluginId="plugin-a",
                        rawName="generate",
                        canonicalName="plugin-a.generate",
                    )
                ],
                filtered_native_tools=[_tool("run_system_command"), _tool("delegation_broker")],
                default_agent_llm=object(),
                supervisor_model_id="supervisor",
                robust_invoke=lambda *args, **kwargs: None,
                build_failure_command=lambda *args, **kwargs: None,
                extract_task_context=lambda *args, **kwargs: None,
                resolve_todos=lambda *args, **kwargs: [],
                sanitize_message_chain=lambda messages, **kwargs: messages,
                sanitize_response_tool_calls=lambda message, **kwargs: message,
                fetch_skill_instructions=_tool("fetch_skill_instructions"),
            )

    def test_contextual_auto_static_tool_surface_excludes_full_external_tree(self):
        agent_nodes = self._build_components(tool_mode="contextual_auto")

        tool_names = {getattr(tool, "name", "") for tool in agent_nodes["agent-one"]["tools"]}

        self.assertEqual(agent_nodes["agent-one"]["tool_mode"], "contextual_auto")
        self.assertIn("run_system_command", tool_names)
        self.assertIn("fetch_skill_instructions", tool_names)
        self.assertNotIn("delegation_broker", tool_names)
        self.assertNotIn("docs_server.query", tool_names)
        self.assertNotIn("gateway.generate", tool_names)
        self.assertIsNotNone(agent_nodes["agent-one"]["tool_node_func"])

    def test_explicit_static_tool_surface_still_respects_selected_external_tools(self):
        agent_nodes = self._build_components(tool_mode="explicit")

        tool_names = {getattr(tool, "name", "") for tool in agent_nodes["agent-one"]["tools"]}

        self.assertEqual(agent_nodes["agent-one"]["tool_mode"], "explicit")
        self.assertIn("run_system_command", tool_names)
        self.assertIn("fetch_skill_instructions", tool_names)
        self.assertIn("docs_server.query", tool_names)
        self.assertIn("gateway.generate", tool_names)


if __name__ == "__main__":
    unittest.main()
