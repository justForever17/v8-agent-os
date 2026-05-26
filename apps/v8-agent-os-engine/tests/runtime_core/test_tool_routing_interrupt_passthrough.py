import unittest
import types
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from graph.tool_routing import async_tool_call_wrapper
from erc.runtime_context import bind_runtime_context


class _DummyRequest:
    def __init__(self, tool_name: str, tool_call_id: str = "tool_call_1", state=None) -> None:
        self.tool_call = {
            "name": tool_name,
            "id": tool_call_id,
            "args": {},
        }
        self.state = state


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
            state={"current_route_context": {"engineeringRequired": True}},
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

    async def test_complex_direct_write_enqueues_episode_when_runtime_context_exists(self):
        request = _DummyRequest(
            "write_native_file",
            "tool_call_write",
            state={"current_route_context": {"engineeringRequired": True}},
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
