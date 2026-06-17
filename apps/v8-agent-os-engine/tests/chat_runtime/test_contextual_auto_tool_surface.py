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
                    _tool("ask_user"),
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
        self.assertIn("ask_user", tool_names)
        self.assertNotIn("s3_broker", tool_names)
        self.assertNotIn("http_request", tool_names)
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

    def test_delegated_plan_context_warns_artifact_workers_to_use_write_tool(self):
        content = _format_delegated_plan_context(
            {
                "taskBriefId": "TASK-010",
                "goal": "写入 ling-perspective/SKILL.md 和 references/research/*.md",
                "deliverableKind": "artifact",
                "writeRequired": True,
                "expectedOutputs": [
                    ".agents/skills/ling-perspective/SKILL.md",
                    ".agents/skills/ling-perspective/references/research/01-writings.md",
                ],
                "context": {
                    "artifactWriteDiscipline": "Skill files must include source markers.",
                },
                "acceptanceContract": "All expected files must contain substantive, source-backed content.",
            },
            {"planId": "plan-artifact", "executionStrategy": "delegate"},
        )

        self.assertIn("Artifact Write Discipline:", content)
        self.assertIn("Use `write_native_file` for content-bearing project files", content)
        self.assertIn("Do NOT use `run_system_command`, shell redirection", content)
        self.assertIn("New-Item", content)
        self.assertIn("Empty placeholder files", content)
        self.assertIn("Skill files must include source markers.", content)

    def test_delegated_plan_context_keeps_research_runtime_contract_distinct(self):
        content = _format_delegated_plan_context(
            {
                "taskBriefId": "TASK-RESEARCH",
                "goal": "完成 Spec 中的六维调研任务。",
                "familyHint": "research",
                "runtimeAccess": ["research.core"],
                "requiredCapabilities": ["source_backed_research", "evidence_pack"],
                "context": {
                    "assignedResearchBrief": (
                        "### TASK-001: 官方设定与系统思考调研\n"
                        "调研官方设定、角色故事、角色档案、版本设定。输出 references/research/01-writings.md。\n"
                        "### TASK-002: 剧情对话与即兴表达调研\n"
                        "调研主线剧情、短信、同行任务、活动剧情中的表达方式。"
                    )
                },
                "engineeringTaskCapsule": {
                    "deliverableKind": "evidence",
                    "runtimeLane": "research",
                    "proofExpectations": ["Report selected sources and gaps."],
                },
            },
            None,
        )

        self.assertIn("Runtime Access: research.core", content)
        self.assertIn("Assigned Research Brief", content)
        self.assertIn("官方设定与系统思考调研", content)
        self.assertIn("Runtime Task Capsule:", content)
        self.assertIn("Runtime Lane: research", content)
        self.assertNotIn("Engineering Role: verification", content)

    def test_delegated_plan_context_does_not_dump_runtime_only_context(self):
        content = _format_delegated_plan_context(
            {
                "taskBriefId": "TASK-001",
                "goal": "执行已批准 Spec 任务。",
                "context": {
                    "specId": "spec_demo",
                    "taskId": "TASK-001",
                    "taskExcerpt": "只需要实现 index.html。",
                    "stageContent": {
                        "requirements": "SHOULD_NOT_DUMP_REQUIREMENTS_FULL_TEXT",
                        "design": "SHOULD_NOT_DUMP_DESIGN_FULL_TEXT",
                    },
                    "specExecutionBundle": {
                        "documents": {"requirements": {"content": "SHOULD_NOT_DUMP_BUNDLE"}}
                    },
                    "engineeringExecutionContract": {
                        "workspacePath": "E:/Projects/test3",
                        "taskId": "TASK-001",
                        "allowedWorkset": ["index.html"],
                    },
                    "handoffContract": {
                        "requiredFields": ["changedFiles", "testResults"],
                    },
                },
            },
            None,
        )

        self.assertIn("Agent-Visible Context:", content)
        self.assertIn("Spec ID: spec_demo", content)
        self.assertIn("Task Excerpt: 只需要实现 index.html。", content)
        self.assertIn("Runtime-only context omitted from prompt", content)
        self.assertNotIn("SHOULD_NOT_DUMP_REQUIREMENTS_FULL_TEXT", content)
        self.assertNotIn("SHOULD_NOT_DUMP_DESIGN_FULL_TEXT", content)
        self.assertNotIn("SHOULD_NOT_DUMP_BUNDLE", content)

    def test_delegated_plan_context_renders_engineering_execution_and_handoff_contract(self):
        content = _format_delegated_plan_context(
            {
                "taskBriefId": "TASK-001",
                "goal": "实现已批准 Spec 的浏览器计数器。",
                "writeSet": ["index.html", "README.md"],
                "context": {
                    "engineeringExecutionContract": {
                        "workspacePath": "E:/Projects/test3",
                        "taskId": "TASK-001",
                        "runtimeFamily": "engineering",
                        "writeRequired": True,
                        "allowedWorkset": ["index.html", "README.md"],
                        "expectedArtifacts": ["index.html", "README.md"],
                        "mustRead": ["Read task excerpt.", "Read detailRefs if needed."],
                        "acceptance": ["Button increments visible count."],
                        "forbiddenScopes": ["Do not edit outside allowedWorkset."],
                        "sourceRefs": {
                            "requirementIds": ["REQ-001"],
                            "designIds": ["DES-001"],
                            "detailRefs": ["spec://spec_demo/tasks#TASK-001"],
                        },
                    },
                    "handoffContract": {
                        "type": "engineering_typed_handoff",
                        "requiredFields": ["changedFiles", "commandsRun", "testResults", "artifacts", "proofRefs"],
                        "mustInclude": ["specId=spec_demo", "taskId=TASK-001"],
                        "completionRule": "A plain done message is not enough.",
                    },
                },
                "engineeringTaskCapsule": {
                    "runtimeLane": "engineering",
                    "writeSet": ["index.html", "README.md"],
                    "proofExpectations": ["Report touched files and tests."],
                },
            },
            None,
        )

        self.assertIn("Engineering Execution Contract:", content)
        self.assertIn("Allowed Workset: index.html, README.md", content)
        self.assertIn("Forbidden Scope: Do not edit outside allowedWorkset.", content)
        self.assertIn("Detail Refs: spec://spec_demo/tasks#TASK-001", content)
        self.assertIn("Required Typed Handoff:", content)
        self.assertIn("Required Fields: changedFiles, commandsRun, testResults, artifacts, proofRefs", content)
        self.assertIn("A plain done message is not enough.", content)

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
        self.assertIn("<delegated_agent_operating_charter>", content)
        self.assertIn("You are a delegated V8OS worker", content)
        self.assertIn("use its approved requirement/design/task refs", content)
        self.assertIn("Child tasks must contain a real goal", content)
        self.assertIn("Approval/ask-user events are handled by the user-facing layer", content)
        self.assertIn("Plan ID: plan-123", content)
        self.assertIn("Task Brief ID: task-1", content)
        self.assertIn("[Extensions Runtime]", content)
        self.assertIn("run_system_command(mode=auto)", content)
        self.assertIn('command_session_broker(mode="input"', content)
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
