import unittest
import types
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from graph.tool_routing import async_tool_call_wrapper


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


if __name__ == "__main__":
    unittest.main()
