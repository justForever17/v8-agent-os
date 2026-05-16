from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.delegation_broker import task_brief_route_query_text
from graph.agent_factories import (
    _build_agent_system_content,
    _format_delegated_plan_context,
    build_specialist_agent_components,
)


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
                filtered_native_tools=[
                    _tool("run_system_command"),
                    _tool("command_session_broker"),
                    _tool("web_broker"),
                    _tool("s3_broker"),
                    _tool("delegation_broker"),
                    _tool("read_background_output"),
                    _tool("web_fetch"),
                    _tool("s3_upload_file"),
                    _tool("computer_use_click_target"),
                ],
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
        self.assertIn("command_session_broker", tool_names)
        self.assertIn("web_broker", tool_names)
        self.assertNotIn("s3_broker", tool_names)
        self.assertNotIn("http_request", tool_names)
        self.assertNotIn("ask_user", tool_names)
        self.assertIn("fetch_skill_instructions", tool_names)
        self.assertNotIn("delegation_broker", tool_names)
        self.assertNotIn("read_background_output", tool_names)
        self.assertNotIn("web_fetch", tool_names)
        self.assertNotIn("s3_upload_file", tool_names)
        self.assertNotIn("computer_use_click_target", tool_names)
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

    def test_delegated_plan_context_formats_compact_task_contract(self):
        content = _format_delegated_plan_context(
            {
                "taskBriefId": "task-1",
                "goal": "调研爱因斯坦并产出人物 Skill 草案",
                "writeSet": ["skills/einstein"],
                "behaviorScope": ["research", "synthesis"],
                "requiredCapabilities": ["skill_authoring"],
                "acceptanceContract": "Supervisor verifies the final skill.",
            },
            {
                "planId": "plan-123",
                "executionStrategy": "delegate",
                "planSummary": "Use Nuwa workflow with bounded research and synthesis.",
                "riskFlags": ["network_research"],
                "dependencies": [{"taskBriefId": "task-1", "dependsOn": []}],
                "globalAcceptanceContract": "Supervisor acceptance required.",
                "taskCount": 1,
            },
        )

        self.assertIn("<delegated_task_plan>", content)
        self.assertIn("Plan ID: plan-123", content)
        self.assertIn("Task Brief ID: task-1", content)
        self.assertIn("Required Capabilities: skill_authoring", content)
        self.assertIn("Acceptance Contract: Supervisor verifies the final skill.", content)

    def test_agent_system_content_uses_same_delegated_plan_block(self):
        delegated_plan = _format_delegated_plan_context(
            {"taskBriefId": "task-1", "goal": "Build bounded output"},
            {"planId": "plan-123", "executionStrategy": "delegate"},
        )

        content = _build_agent_system_content(
            agent_name="Agent One",
            agent_system_prompt="Follow the task contract.",
            env_context="<environment>\nOS: Windows\n</environment>\n",
            delegated_plan_context=delegated_plan,
            route_prompt_addition="[Extensions Runtime]\n- Skills 候选：1\n[/Extensions Runtime]",
        )

        self.assertIn("<delegated_task_plan>", content)
        self.assertIn("Plan ID: plan-123", content)
        self.assertIn("Task Brief ID: task-1", content)
        self.assertIn("[Extensions Runtime]", content)
        self.assertIn("command_session_broker(mode=start)", content)
        self.assertNotIn("render_stalled", content)

    def test_task_brief_route_query_omits_acceptance_write_set_and_broad_noise(self):
        route_query = task_brief_route_query_text(
            {
                "goal": "使用 huashu-nuwa 调研爱因斯坦并生成 Einstein 人物 Skill 草案。",
                "context": "这里有很长的上下文，包含 documentation、proposal 与验收说明，但不应进入预筛。",
                "writeSet": ["~/.agents/skills/einstein-perspective"],
                "requiredCapabilities": ["skill_authoring", "research", "documentation"],
                "behaviorScope": ["fetch_skill_instructions", "verification_contract"],
                "acceptanceContract": "Supervisor verifies final documentation and skill structure.",
            }
        )

        self.assertIn("huashu-nuwa", route_query)
        self.assertIn("skill_authoring", route_query)
        self.assertIn("research", route_query)
        self.assertIn("fetch_skill_instructions", route_query)
        self.assertNotIn("Write set", route_query)
        self.assertNotIn("Acceptance contract", route_query)
        self.assertNotIn("documentation", route_query)
        self.assertNotIn("verification_contract", route_query)


if __name__ == "__main__":
    unittest.main()
