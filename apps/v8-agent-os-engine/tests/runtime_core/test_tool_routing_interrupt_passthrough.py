import unittest
import types
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from graph.tool_routing import _route_intent_for_blocked_tool, _supervisor_direct_pressure_count, async_tool_call_wrapper
from erc.runtime_context import bind_runtime_context


class _DummyRequest:
    def __init__(self, tool_name: str, tool_call_id: str = "tool_call_1", state=None) -> None:
        self.tool_call = {
            "name": tool_name,
            "id": tool_call_id,
            "args": {},
        }
        self.state = state


def _previous_write_messages(count: int) -> list[types.SimpleNamespace]:
    return [
        types.SimpleNamespace(
            tool_calls=[
                {
                    "name": "write_native_file",
                    "id": f"tool_call_previous_write_{index}",
                    "args": {"path": f"E:\\Projects\\test2\\demo-{index}.md", "content": "demo"},
                }
            ]
        )
        for index in range(count)
    ]


class ToolRoutingInterruptPassthroughTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        fake_native_tools = types.ModuleType("core.native_tools")

        def _raise_runtime_governance_exception_if_needed(exc: Exception) -> None:
            for value in getattr(exc, "args", ()):
                if isinstance(value, dict) and (
                    value.get("approvalKind")
                    or value.get("approval_kind")
                    or value.get("question")
                    or value.get("toolCallId")
                    or value.get("tool_call_id")
                ):
                    raise exc

        fake_native_tools._raise_runtime_governance_exception_if_needed = _raise_runtime_governance_exception_if_needed
        self._native_tools_patch = patch.dict("sys.modules", {"core.native_tools": fake_native_tools})
        self._native_tools_patch.start()

    def tearDown(self) -> None:
        self._native_tools_patch.stop()

    def test_supervisor_direct_pressure_count_ignores_spec_flow_tools(self):
        flow_tools = [
            {"name": "memory_broker"},
            {"name": "fetch_skill_instructions"},
            {"name": "fetch_skill_instructions"},
            {"name": "spec_broker"},
            {"name": "spec_broker"},
            {"name": "spec_broker"},
            {"name": "spec_broker"},
            {"name": "write_todos"},
            {"name": "update_todo"},
            {"name": "research_broker"},
            {"name": "runtime_broker"},
            {"name": "ask_user"},
            {"name": "web_broker"},
            {"name": "run_system_command", "args": {"command": "git status --short"}},
        ]

        self.assertEqual(_supervisor_direct_pressure_count(flow_tools), 1)
        self.assertEqual(_supervisor_direct_pressure_count(["web_broker"] * 11), 11)
        self.assertEqual(_supervisor_direct_pressure_count([{"name": "run_system_command", "args": {"command": "mkdir demo"}}]), 1)

    async def test_regular_tool_exception_returns_error_tool_message(self):
        request = _DummyRequest("demo_tool")

        async def execute(_request):
            raise RuntimeError("normal tool failure")

        result = await async_tool_call_wrapper(request, execute)

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.name, "demo_tool")
        self.assertEqual(result.tool_call_id, "tool_call_1")
        self.assertEqual(getattr(result, "status", None), "error")
        self.assertIn("normal tool failure", str(result.content))

    async def test_interrupt_like_exception_is_re_raised(self):
        request = _DummyRequest("ask_user", "tool_call_ask")

        async def execute(_request):
            raise RuntimeError(
                {
                    "approvalKind": "ask_user",
                    "question": "请补充继续执行所需的信息",
                    "toolCallId": "tool_call_ask",
                }
            )

        with self.assertRaises(RuntimeError):
            await async_tool_call_wrapper(request, execute)

    async def test_complex_direct_write_returns_route_required_not_approval(self):
        request = _DummyRequest(
            "write_native_file",
            "tool_call_write",
            state={
                "messages": _previous_write_messages(3),
                "current_route_context": {"engineeringRequired": True},
            },
        )

        async def execute(_request):
            raise AssertionError("direct write should have been blocked before execution")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(getattr(result, "status", None), "error")
        self.assertIn("[route required]", str(result.content))
        self.assertEqual(result.additional_kwargs["recommendedNextAction"], "runtime_broker(mode='route')")
        self.assertEqual(result.additional_kwargs["allowedNextTools"], ["runtime_broker"])
        self.assertEqual(result.additional_kwargs["routeIntent"]["kind"], "engineering")
        self.assertIn("inputs", result.additional_kwargs["routeIntent"])

    async def test_limited_write_native_file_executes_before_route_gate_limit(self):
        request = _DummyRequest(
            "write_native_file",
            "tool_call_write",
            state={"current_route_context": {"engineeringRequired": True}},
        )

        async def execute(_request):
            return ToolMessage(content="wrote one lightweight file", name="write_native_file", tool_call_id="tool_call_write")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        self.assertNotIn("[route required]", str(result.content))
        self.assertIn("wrote one lightweight file", str(result.content))

    async def test_engineering_route_intent_preserves_original_user_request(self):
        request = _DummyRequest(
            "run_system_command",
            "tool_call_command",
            state={
                "current_route_context": {
                    "engineeringRequired": True,
                    "latestUserContent": "创建一个全屏 Canvas 像素风网页游戏项目，包含 index.html、main.js 和 README。",
                    "workspacePath": "E:\\Projects\\test3",
                }
            },
        )
        request.tool_call["args"] = {
            "command": 'mkdir -p ".v8/live-audit/pixel-run-gun/demo"',
            "cwd": "E:\\Projects\\test3",
        }

        async def execute(_request):
            raise AssertionError("direct command should have been routed before execution")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        route_intent = result.additional_kwargs["routeIntent"]
        self.assertEqual(route_intent["kind"], "engineering")
        inputs = route_intent["inputs"]
        self.assertEqual(inputs["userRequest"], "创建一个全屏 Canvas 像素风网页游戏项目，包含 index.html、main.js 和 README。")
        brief = inputs["taskBriefs"][0]
        self.assertEqual(brief["goal"], inputs["userRequest"])
        self.assertEqual(brief["context"]["blockedCommand"], 'mkdir -p ".v8/live-audit/pixel-run-gun/demo"')

    async def test_spec_mode_blocks_execution_tools_before_runtime_approval(self):
        request = _DummyRequest(
            "run_system_command",
            "tool_call_command",
            state={
                "specMode": True,
                "current_route_context": {
                    "specMode": True,
                    "specId": "spec_gate_test",
                    "engineeringRequired": True,
                    "specBrief": {
                        "pipelineControl": {
                            "runtimeExecutionAllowed": False,
                        }
                    },
                },
            },
        )
        request.tool_call["args"] = {
            "command": 'mkdir -p ".v8/spec-test"',
            "cwd": "E:\\Projects\\test2",
        }

        async def execute(_request):
            raise AssertionError("spec mode execution tool should have been blocked before execution")

        with bind_runtime_context(run_id="run_spec_gate_test", session_id="session_spec_gate_test"):
            with patch("graph.tool_routing._enqueue_route_intent_episode") as enqueue_episode:
                result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(getattr(result, "status", None), "error")
        self.assertIn("[spec gate]", str(result.content))
        self.assertEqual(result.additional_kwargs["riskCode"], "spec_runtime_execution_not_approved")
        self.assertEqual(result.additional_kwargs["recommendedNextAction"], "continue_spec_stage")
        self.assertEqual(result.additional_kwargs["blockedTool"], "run_system_command")
        self.assertFalse(result.additional_kwargs["runtimeExecutionAllowed"])
        self.assertIn("spec_broker", result.additional_kwargs["allowedNextTools"])
        enqueue_episode.assert_not_called()

    async def test_research_route_intent_requires_live_run_not_plan_only(self):
        route_intent = _route_intent_for_blocked_tool(
            tool_name="web_read",
            tool_call={"args": {"query": "核查最新技术文档并返回来源"}},
            state_mapping={
                "current_route_context": {
                    "taskShapeHint": {
                        "boundaryDecision": {"primaryRuntime": "research"},
                    }
                }
            },
            hard_reasons=["task_boundary_route_correction"],
            route_required=True,
        )

        self.assertEqual(route_intent["kind"], "research")
        self.assertEqual(route_intent["inputs"]["mode"], "run")
        self.assertEqual(route_intent["inputs"]["query"], "核查最新技术文档并返回来源")

    async def test_complex_direct_write_enqueues_episode_when_runtime_context_exists(self):
        request = _DummyRequest(
            "write_native_file",
            "tool_call_write",
            state={
                "messages": _previous_write_messages(3),
                "current_route_context": {"engineeringRequired": True},
            },
        )

        async def execute(_request):
            raise AssertionError("direct write should have been blocked before execution")

        with bind_runtime_context(run_id="run_gate_test", session_id="session_gate_test"):
            with patch("graph.tool_routing._enqueue_route_intent_episode") as enqueue_episode:
                enqueue_episode.return_value = {"episodeId": "episode_gate_test", "state": "queued"}
                result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(getattr(result, "status", None), "error")
        self.assertEqual(result.additional_kwargs["recommendedNextAction"], "wait_episode")
        self.assertEqual(result.additional_kwargs["queuedEpisodeId"], "episode_gate_test")
        self.assertTrue(result.additional_kwargs["hasActiveRuntimeEpisode"])
        enqueue_episode.assert_called_once()
        self.assertEqual(enqueue_episode.call_args.kwargs["run_id"], "run_gate_test")
        self.assertEqual(enqueue_episode.call_args.kwargs["session_id"], "session_gate_test")

    async def test_complex_direct_write_waits_when_episode_already_queued(self):
        request = _DummyRequest(
            "write_native_file",
            "tool_call_write",
            state={
                "messages": _previous_write_messages(3),
                "current_route_context": {
                    "engineeringRequired": True,
                    "capabilityEpisodes": [
                        {"episodeId": "episode_engineering", "kind": "engineering", "state": "queued"}
                    ],
                }
            },
        )

        async def execute(_request):
            raise AssertionError("direct write should have been blocked before execution")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(getattr(result, "status", None), "error")
        self.assertEqual(result.additional_kwargs["recommendedNextAction"], "wait_episode")
        self.assertTrue(result.additional_kwargs["hasActiveRuntimeEpisode"])
        self.assertIn("wait for the active Runtime episode", str(result.content))


if __name__ == "__main__":
    unittest.main()
