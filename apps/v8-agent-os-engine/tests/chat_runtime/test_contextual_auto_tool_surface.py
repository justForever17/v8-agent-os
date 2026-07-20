from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.delegation_broker import normalize_task_brief, task_brief_route_query_text
from graph.agent_factories import (
    _apply_task_tool_policy,
    _bounded_delegated_task_messages,
    _build_agent_system_content,
    _delegated_result_text,
    _delegated_tool_names,
    _delegated_visible_result_text,
    _format_delegated_task_contract,
    _resolve_inherited_route_context,
    build_contextual_auto_tool_node,
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
                        "tools": ["docs_server.query"],
                    }
                ],
                all_mcp_tools=[_tool("docs_server.query")],
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

    def test_contextual_auto_static_tool_surface_keeps_common_package_and_excludes_external_tree(self):
        agent_nodes = self._build_components(tool_mode="contextual_auto")

        tool_names = {getattr(tool, "name", "") for tool in agent_nodes["agent-one"]["tools"]}

        self.assertEqual(agent_nodes["agent-one"]["tool_mode"], "contextual_auto")
        self.assertIn("run_system_command", tool_names)
        self.assertIn("command_session_broker", tool_names)
        self.assertIn("web_broker", tool_names)
        self.assertNotIn("ask_user", tool_names)
        self.assertNotIn("s3_broker", tool_names)
        self.assertNotIn("http_request", tool_names)
        self.assertIn("fetch_skill_instructions", tool_names)
        self.assertIn("delegation_broker", tool_names)
        self.assertIn("read_background_output", tool_names)
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
        self.assertIn("delegation_broker", tool_names)
        self.assertIn("fetch_skill_instructions", tool_names)
        self.assertIn("docs_server.query", tool_names)
        self.assertNotIn("gateway.generate", tool_names)

    def test_delegated_task_contract_formats_compact_task_contract(self):
        content = _format_delegated_task_contract(
            {
                "taskBriefId": "task-1",
                "goal": "调研爱因斯坦并产出人物 Skill 草案",
                "writeSet": ["skills/einstein"],
                "behaviorScope": ["research", "synthesis"],
                "requiredCapabilities": ["skill_authoring"],
                "acceptanceContract": "Supervisor verifies the final skill.",
            },
        )

        self.assertIn("<delegated_task_plan>", content)
        self.assertIn("Task Brief ID: task-1", content)
        self.assertIn("Required Capabilities: skill_authoring", content)
        self.assertIn("Acceptance Contract: Supervisor verifies the final skill.", content)

    def test_task_tool_policy_can_disable_or_allowlist_worker_tools(self):
        tools = [_tool("read_native_file"), _tool("web_broker"), _tool("creative_media_jobs")]

        self.assertEqual(
            _apply_task_tool_policy(tools, {"toolPolicy": {"mode": "none"}}),
            [],
        )
        allowlisted = _apply_task_tool_policy(
            tools,
            {"toolPolicy": {"mode": "allowlist", "allowedTools": ["read_native_file"]}},
        )
        self.assertEqual([tool.name for tool in allowlisted], ["read_native_file"])

    def test_subagent_tool_receipt_excludes_supervisor_tool_calls(self):
        from langchain_core.messages import AIMessage

        supervisor = AIMessage(
            content="",
            tool_calls=[{"id": "sup-1", "name": "delegation_broker", "args": {}}],
        )
        child = AIMessage(
            content="",
            tool_calls=[{"id": "child-1", "name": "read_native_file", "args": {}}],
            additional_kwargs={
                "v8_owner_agent_kind": "subagent",
                "v8_owner_agent_id": "worker-one",
            },
        )

        self.assertEqual(
            _delegated_tool_names([supervisor, child], agent_id="worker-one"),
            ["read_native_file"],
        )

    def test_subagent_exact_result_is_preserved_separately_from_handoff_copy(self):
        from langchain_core.messages import HumanMessage

        message = HumanMessage(
            content="[Worker 执行完毕]\n结果: OWNER_ISOLATION_OK",
            additional_kwargs={
                "v8_owner_agent_kind": "subagent",
                "v8_owner_agent_id": "worker-one",
                "v8_subagent_result_text": "OWNER_ISOLATION_OK",
            },
        )

        self.assertEqual(
            _delegated_result_text([message], agent_id="worker-one"),
            "OWNER_ISOLATION_OK",
        )

    def test_subagent_exact_result_excludes_inline_provider_reasoning(self):
        from langchain_core.messages import AIMessage

        response = AIMessage(content="<think>internal reasoning</think>OWNER_ISOLATION_OK")

        self.assertEqual(_delegated_visible_result_text(response), "OWNER_ISOLATION_OK")

    def test_delegated_task_messages_do_not_replay_parent_user_instruction(self):
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        parent = HumanMessage(content="最终只回复 ACCEPT 或 RETRY。")
        delegated = HumanMessage(
            content="[Supervisor Delegated Task]\n只输出 AUTHORITY_OK",
            additional_kwargs={"v8_governance_type": "delegated_task_instruction"},
        )
        tool_call = AIMessage(
            content="",
            tool_calls=[{"id": "write-1", "name": "write_native_file", "args": {"path": "result.txt"}}],
            additional_kwargs={"v8_owner_agent_id": "worker-one"},
        )
        tool_result = ToolMessage(
            content='{"ok":true,"path":"result.txt"}',
            tool_call_id="write-1",
            name="write_native_file",
        )

        selected = _bounded_delegated_task_messages(
            [parent, delegated, tool_call, tool_result],
            {"taskBriefId": "task-1", "goal": "只输出 AUTHORITY_OK"},
        )

        self.assertEqual(selected, [delegated, tool_call, tool_result])
        self.assertNotIn("ACCEPT", str(selected[0].content))

    def test_delegated_task_messages_anchor_at_latest_instruction(self):
        from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

        stale_instruction = HumanMessage(
            content="[Supervisor Delegated Task]\n旧任务",
            additional_kwargs={"v8_governance_type": "delegated_task_instruction"},
        )
        stale_result = AIMessage(content="旧结果")
        current_instruction = HumanMessage(
            content="[Supervisor Delegated Task]\n当前任务",
            additional_kwargs={"v8_governance_type": "delegated_task_instruction"},
        )
        current_call = AIMessage(
            content="",
            tool_calls=[{"id": "read-1", "name": "read_native_file", "args": {"path": "result.txt"}}],
        )
        current_result = ToolMessage(
            content="ENGINEERING_KERNEL_LIVE_OK",
            tool_call_id="read-1",
            name="read_native_file",
        )

        selected = _bounded_delegated_task_messages(
            [stale_instruction, stale_result, current_instruction, current_call, current_result],
            {"taskBriefId": "task-2", "goal": "当前任务"},
        )

        self.assertEqual(selected, [current_instruction, current_call, current_result])

    def test_task_brief_normalizes_explicit_tool_policy_without_guessing_from_prose(self):
        normalized = normalize_task_brief(
            {
                "taskBriefId": "task-tool-policy",
                "goal": "Inspect one file and report.",
                "expectedOutput": "A concise verdict.",
                "constraints": ["Do not modify files."],
                "evidenceRefs": ["artifact://evidence-1"],
                "detailRefs": ["detail://task-1"],
                "toolPolicy": {
                    "mode": "allowlist",
                    "allowedTools": ["read_native_file"],
                    "forbiddenTools": ["run_system_command"],
                },
            }
        )

        self.assertEqual(normalized["toolPolicy"]["mode"], "allowlist")
        self.assertEqual(normalized["allowedTools"], ["read_native_file"])
        self.assertEqual(normalized["forbiddenTools"], ["run_system_command"])
        self.assertEqual(normalized["expectedOutputs"], ["A concise verdict."])
        self.assertEqual(normalized["behaviorScope"], ["Do not modify files."])
        self.assertEqual(normalized["evidenceRefs"], ["artifact://evidence-1"])
        self.assertEqual(normalized["detailRefs"], ["detail://task-1"])

    def test_task_brief_normalization_preserves_delegation_depth(self):
        normalized = normalize_task_brief(
            {
                "taskBriefId": "terminal-verifier",
                "goal": "Read and execute one file.",
                "delegationDepth": 2,
                "toolPolicy": {
                    "mode": "allowlist",
                    "allowedTools": ["read_native_file", "run_system_command"],
                },
            }
        )

        self.assertEqual(normalized["delegationDepth"], 2)

    def test_parallel_branch_is_authoritative_over_stale_same_agent_context(self):
        branch_task = normalize_task_brief(
            {
                "taskBriefId": "terminal-verifier",
                "goal": "Read and execute src/check.py.",
                "delegationDepth": 2,
                "readOnly": True,
                "readSet": ["src/check.py"],
                "allowChildDelegation": False,
                "childDelegationPolicyExplicit": True,
                "toolPolicy": {
                    "mode": "allowlist",
                    "allowedTools": ["read_native_file", "run_system_command"],
                },
            }
        )
        state = {
            "delegation_contexts": [
                {
                    "agentId": "implementation-engineer",
                    "query": "stale parent task",
                    "mode": "serial",
                    "selectedSkillIds": ["skill:stale"],
                    "selectedSkillNames": ["stale-skill"],
                    "taskBrief": {
                        "taskBriefId": "parent",
                        "goal": "Parent implementation task.",
                        "toolPolicy": {"mode": "default"},
                    },
                }
            ],
            "parallel_branch": {
                "agentId": "implementation-engineer",
                "delegationId": "subagent::child",
                "parentDelegationId": "subagent::parent",
                "delegationDepth": 2,
                "taskBrief": branch_task,
            },
        }

        resolved = _resolve_inherited_route_context(
            state,
            [],
            agent_id="implementation-engineer",
        )

        self.assertEqual(resolved["delegationDepth"], 2)
        self.assertEqual(resolved["delegationId"], "subagent::child")
        self.assertEqual(resolved["taskBrief"]["taskBriefId"], "terminal-verifier")
        self.assertEqual(resolved["taskBrief"]["delegationDepth"], 2)
        self.assertEqual(resolved["taskBrief"]["runtimeAccess"], [])
        self.assertIn(
            "TERMINAL VERIFICATION IS CLOSED-WORLD",
            _format_delegated_task_contract(resolved["taskBrief"]),
        )
        filtered = _apply_task_tool_policy(
            [
                _tool("read_native_file"),
                _tool("run_system_command"),
                _tool("fetch_skill_instructions"),
                _tool("delegation_broker"),
            ],
            resolved["taskBrief"],
        )
        self.assertEqual(
            [tool.name for tool in filtered],
            ["read_native_file", "run_system_command"],
        )

    def test_delegation_tool_schema_exposes_authority_and_acceptance_fields(self):
        from core.tools.native.delegation import delegation_broker

        schema = delegation_broker.args_schema.model_json_schema()
        task_definition = schema["$defs"]["DelegationTaskInput"]
        task_schema = task_definition["properties"]

        self.assertIn("toolPolicy", task_schema)
        self.assertIn("targetAgentName", task_schema)
        self.assertIn("acceptanceContract", task_schema)
        self.assertIn("expectedOutput", task_schema)
        self.assertIn("constraints", task_schema)
        self.assertEqual(
            {item.get("type") for item in task_schema["writeSet"]["anyOf"]},
            {"array", "string"},
        )
        self.assertEqual(
            set(task_definition["required"]),
            {"taskBriefId", "goal", "expectedOutputs", "acceptanceContract"},
        )

    def test_delegated_task_contract_explains_exact_tool_authority(self):
        content = _format_delegated_task_contract(
            {
                "taskBriefId": "task-no-tools",
                "goal": "Return a bounded textual self-check.",
                "toolPolicy": {"mode": "none", "allowedTools": [], "forbiddenTools": []},
            },
        )

        self.assertIn("Tool Policy:", content)
        self.assertIn("this task has no tool authority", content)
        self.assertIn("absence of `delegation_broker`", content)

    def test_normalized_default_child_policy_does_not_disable_direct_subagent_delegation(self):
        content = _format_delegated_task_contract(
            {
                "taskBriefId": "task-default-grandchild",
                "goal": "Implement and request one independent verification.",
                "delegationDepth": 1,
                "allowChildDelegation": False,
                "childDelegationPolicyExplicit": False,
            },
        )

        self.assertIn("delegation_broker(mode='dispatch')", content)
        self.assertNotIn("absence of `delegation_broker`", content)

    def test_explicit_child_delegation_opt_out_remains_terminal(self):
        content = _format_delegated_task_contract(
            {
                "taskBriefId": "task-no-grandchild",
                "goal": "Complete this slice without another worker.",
                "delegationDepth": 1,
                "allowChildDelegation": False,
                "childDelegationPolicyExplicit": True,
            },
        )

        self.assertIn("absence of `delegation_broker`", content)

    def test_delegated_task_contract_uses_supervisor_runtime_origin(self):
        content = _format_delegated_task_contract(
            {
                "taskBriefId": "TASK-SUPERVISOR-FIRST",
                "goal": "根据已批准 Spec 片段实现浏览器计数器。",
                "context": {
                    "specId": "spec_counter_latest",
                    "taskId": "TASK-001",
                    "specDocumentPaths": {
                        "requirements": ".v8/specs/counter/requirements.md",
                        "design": ".v8/specs/counter/design.md",
                        "tasks": ".v8/specs/counter/tasks.md",
                    },
                    "approvedRequirementSlice": "REQ-001: 页面必须包含 SPEC_DRY_RUN_COUNTER 标记。",
                    "approvedDesignSlice": "DES-001: 使用单文件 index.html 与内联 JavaScript。",
                    "specExecutionSummary": "TASK-001 绑定 REQ-001 与 DES-001；输出 index.html。",
                },
            },
        )

        self.assertIn("supervisor's delegation/runtime pipeline", content)
        self.assertNotIn("supervisor's planner/delegation pipeline", content)
        self.assertIn("Spec ID: spec_counter_latest", content)
        self.assertIn(".v8/specs/counter/requirements.md", content)
        self.assertIn("REQ-001: 页面必须包含 SPEC_DRY_RUN_COUNTER 标记。", content)
        self.assertIn("DES-001: 使用单文件 index.html 与内联 JavaScript。", content)

    def test_delegated_task_contract_warns_artifact_workers_to_use_write_tool(self):
        content = _format_delegated_task_contract(
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
        )

        self.assertIn("Artifact Write Discipline:", content)
        self.assertIn("Use `write_native_file` for assigned final project artifacts", content)
        self.assertIn("Spec contract documents", content)
        self.assertIn("belong exclusively to `spec_broker`", content)
        self.assertIn("Do NOT use `run_system_command`, shell redirection", content)
        self.assertIn("New-Item", content)
        self.assertIn("Empty placeholder files", content)
        self.assertIn("Skill files must include source markers.", content)

    def test_delegated_task_contract_keeps_research_runtime_contract_distinct(self):
        content = _format_delegated_task_contract(
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
        )

        self.assertIn("Runtime Access: research.core", content)
        self.assertIn("Assigned Research Brief", content)
        self.assertIn("官方设定与系统思考调研", content)
        self.assertIn("Runtime Task Capsule:", content)
        self.assertIn("Runtime Lane: research", content)
        self.assertNotIn("Engineering Role: verification", content)

    def test_delegated_task_contract_does_not_dump_runtime_only_context(self):
        content = _format_delegated_task_contract(
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
        )

        self.assertIn("Agent-Visible Context:", content)
        self.assertIn("Spec ID: spec_demo", content)
        self.assertIn("Task Excerpt: 只需要实现 index.html。", content)
        self.assertIn("Runtime-only metadata was omitted from this prompt", content)
        self.assertNotIn("SHOULD_NOT_DUMP_REQUIREMENTS_FULL_TEXT", content)
        self.assertNotIn("SHOULD_NOT_DUMP_DESIGN_FULL_TEXT", content)
        self.assertNotIn("SHOULD_NOT_DUMP_BUNDLE", content)

    def test_delegated_task_contract_renders_engineering_execution_and_handoff_contract(self):
        content = _format_delegated_task_contract(
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
        )

        self.assertIn("Engineering Execution Contract:", content)
        self.assertIn("Allowed Workset: index.html, README.md", content)
        self.assertIn("Forbidden Scope: Do not edit outside allowedWorkset.", content)
        self.assertIn("Detail Refs: spec://spec_demo/tasks#TASK-001", content)
        self.assertIn("Required Typed Handoff:", content)
        self.assertIn("Required Fields: changedFiles, commandsRun, testResults, artifacts, proofRefs", content)
        self.assertIn("A plain done message is not enough.", content)

    def test_agent_system_content_uses_same_delegated_task_contract(self):
        delegated_plan = _format_delegated_task_contract(
            {"taskBriefId": "task-1", "goal": "Build bounded output"},
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
        self.assertIn("read_native_file` is the default way to read a known text", content)
        self.assertIn("Do not use `run_system_command`, Python one-liners, `type`, `Get-Content`, `cat`", content)
        self.assertIn("existing files require `read_native_file` first", content)
        self.assertIn("same purpose fails twice", content)
        self.assertIn("User decision gates are handled outside delegated worker control", content)
        self.assertIn("return `waiting_for_user`", content)
        self.assertIn("Task Brief ID: task-1", content)
        self.assertIn("[Extensions Runtime]", content)
        self.assertIn("run_system_command(mode=auto)", content)
        self.assertIn('command_session_broker(mode="input"', content)
        self.assertNotIn("render_stalled", content)

    def test_contextual_auto_real_native_tools_keep_actionable_descriptions(self):
        from core.native_tools import NATIVE_TOOLS
        from core.system_tools.baseline import select_baseline_system_tools
        from runtimes.extensions.skills.loader import fetch_skill_instructions

        real_baseline_tools = select_baseline_system_tools(NATIVE_TOOLS)
        agent_nodes = self._build_components_with_tools(
            tool_mode="contextual_auto",
            filtered_native_tools=real_baseline_tools,
            fetch_skill_instructions=fetch_skill_instructions,
        )

        tools_by_name = {getattr(tool, "name", ""): tool for tool in agent_nodes["agent-one"]["tools"]}
        for name in ("read_native_file", "fetch_skill_instructions"):
            self.assertIn(name, tools_by_name)
            description = str(getattr(tools_by_name[name], "description", "") or "").strip()
            self.assertGreater(len(description), 60, f"{name} should expose an actionable tool description to subagents")
        for name in ("write_native_file", "run_system_command", "command_session_broker"):
            self.assertIn(name, tools_by_name)
            description = str(getattr(tools_by_name[name], "description", "") or "").strip()
            self.assertGreater(len(description), 60, f"{name} should expose an actionable tool description to subagents")
        skill_description = str(getattr(tools_by_name["fetch_skill_instructions"], "description", "") or "")
        self.assertIn("exact skill name/path", skill_description)
        self.assertIn("complete SKILL.md", skill_description)
        self.assertIn("relative_path", skill_description)

    def test_contextual_tool_node_projects_write_capsule_tools(self):
        captured: dict[str, list[str]] = {}

        def fake_create_routed_tool_node(tools, **_kwargs):
            captured["names"] = [str(getattr(tool, "name", "") or "") for tool in tools]

            async def routed(state, **_runtime_kwargs):
                return state

            return routed

        task_brief = normalize_task_brief(
            {
                "taskBriefId": "write-task",
                "goal": "Create result.txt.",
                "writeRequired": True,
                "writeSet": ["result.txt"],
                "expectedOutputs": ["result.txt"],
                "acceptanceContract": "result.txt exists with exact content.",
            }
        )
        tools = [
            _tool("read_native_file"),
            _tool("write_native_file"),
            _tool("run_system_command"),
            _tool("command_session_broker"),
        ]
        with patch("graph.agent_factories.create_routed_tool_node", side_effect=fake_create_routed_tool_node):
            node = build_contextual_auto_tool_node(
                base_tools=tools,
                all_native_tools=tools,
                all_mcp_tools=[],
                name="writer_tools",
                fallback_goto="writer",
            )
            asyncio.run(node({"current_route_context": {"taskBrief": task_brief}}))

        self.assertIn("write_native_file", captured["names"])
        self.assertIn("run_system_command", captured["names"])
        self.assertIn("command_session_broker", captured["names"])

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

    def _build_components_with_tools(self, *, tool_mode: str, filtered_native_tools: list, fetch_skill_instructions):
        with patch("graph.agent_factories.create_routed_tool_node", return_value=lambda state: state):
            return build_specialist_agent_components(
                loaded_agents=[
                    {
                        "id": "agent-one",
                        "name": "Agent One",
                        "description": "Test agent",
                        "system_prompt": "You are a test agent.",
                        "tool_mode": tool_mode,
                        "tools": [],
                    }
                ],
                all_mcp_tools=[],
                filtered_native_tools=filtered_native_tools,
                default_agent_llm=object(),
                supervisor_model_id="supervisor",
                robust_invoke=lambda *args, **kwargs: None,
                build_failure_command=lambda *args, **kwargs: None,
                extract_task_context=lambda *args, **kwargs: None,
                resolve_todos=lambda *args, **kwargs: [],
                sanitize_message_chain=lambda messages, **kwargs: messages,
                sanitize_response_tool_calls=lambda message, **kwargs: message,
                fetch_skill_instructions=fetch_skill_instructions,
            )


if __name__ == "__main__":
    unittest.main()
