from __future__ import annotations

import json
import types
import unittest
from unittest.mock import patch

from langchain_core.messages import ToolMessage

from core.native_tools import delegation_broker, runtime_broker
from erc.runtime_context import bind_runtime_context
from graph.tool_routing import async_tool_call_wrapper


class _DummyRequest:
    def __init__(self, tool_name: str, tool_call_id: str = "tool_call_1", state=None, args=None) -> None:
        self.tool_call = {
            "name": tool_name,
            "id": tool_call_id,
            "args": dict(args or {}),
        }
        self.state = state


def _payload(command) -> dict:
    return json.loads(command.update["messages"][0].content)


class AgentQualityToolCallValidationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Keep this focused on routing behavior. The wrapper imports this helper
        # lazily; the fixture mirrors runtime_core tests and avoids unrelated
        # governance exception behavior.
        fake_native_tools = types.ModuleType("core.native_tools")

        def _raise_runtime_governance_exception_if_needed(exc: Exception) -> None:
            for value in getattr(exc, "args", ()):
                if isinstance(value, dict) and (value.get("approvalKind") or value.get("toolCallId")):
                    raise exc

        fake_native_tools._raise_runtime_governance_exception_if_needed = _raise_runtime_governance_exception_if_needed
        self._native_tools_patch = patch.dict("sys.modules", {"core.native_tools": fake_native_tools})
        self._native_tools_patch.start()

    def tearDown(self) -> None:
        self._native_tools_patch.stop()

    async def test_direct_mutating_tool_is_forced_to_runtime_route(self) -> None:
        request = _DummyRequest(
            "write_native_file",
            "tool_call_write",
            state={"current_route_context": {"engineeringRequired": True, "workspacePath": r"E:\Projects\test7"}},
            args={"path": "src/App.tsx", "content": "demo"},
        )

        async def execute(_request):
            raise AssertionError("direct mutation should be blocked before execution")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(getattr(result, "status", None), "error")
        self.assertIn("[route required]", str(result.content))
        self.assertEqual(result.additional_kwargs["allowedNextTools"], ["runtime_broker"])
        self.assertEqual(result.additional_kwargs["recommendedNextAction"], "runtime_broker(mode='route')")
        self.assertEqual(result.additional_kwargs["routeIntent"]["kind"], "engineering")
        self.assertEqual(result.additional_kwargs["routeIntent"]["inputs"]["workspacePath"], r"E:\Projects\test7")

    async def test_direct_mutating_tool_enqueues_episode_when_runtime_context_exists(self) -> None:
        request = _DummyRequest(
            "run_system_command",
            "tool_call_cmd",
            state={"current_route_context": {"engineeringRequired": True}},
            args={"command": "npm run build"},
        )

        async def execute(_request):
            raise AssertionError("direct command should be blocked before execution")

        with bind_runtime_context(run_id="run_agent_quality_gate", session_id="session_agent_quality_gate"):
            with patch("graph.tool_routing._enqueue_route_intent_episode") as enqueue_episode:
                enqueue_episode.return_value = {"episodeId": "episode_agent_quality_gate", "state": "queued"}
                result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertEqual(result.additional_kwargs["recommendedNextAction"], "wait_episode")
        self.assertEqual(result.additional_kwargs["queuedEpisodeId"], "episode_agent_quality_gate")
        self.assertTrue(result.additional_kwargs["hasActiveRuntimeEpisode"])
        enqueue_episode.assert_called_once()
        self.assertEqual(enqueue_episode.call_args.kwargs["run_id"], "run_agent_quality_gate")
        self.assertEqual(enqueue_episode.call_args.kwargs["session_id"], "session_agent_quality_gate")


def test_runtime_broker_route_accepts_json_need_and_returns_waitable_episode() -> None:
    command = runtime_broker.func(
        mode="route",
        need=json.dumps(
            {
                "tool": "write_native_file",
                "reason": "blocked direct implementation",
                "inputs": {"workspacePath": r"E:\Projects\test7"},
            }
        ),
        state={"current_route_context": {}},
        tool_call_id="call-runtime-route-agent-quality",
    )
    payload = _payload(command)
    episode = command.update["current_route_context"]["capabilityEpisodes"][-1]

    assert payload["episodeKind"] == "engineering"
    assert payload["queuedEpisodeId"] == episode["episodeId"]
    assert payload["nextAction"] == "wait_episode"
    assert episode["state"] == "queued"
    assert episode["inputs"]["workspacePath"] == r"E:\Projects\test7"
    assert episode["inputs"]["tasks"], "route must synthesize an executable task instead of an empty episode"


def test_delegation_dispatch_without_tasks_is_single_diagnostic_not_fake_swarm() -> None:
    command = delegation_broker.func(
        mode="dispatch",
        state={"current_route_context": {}},
        tool_call_id="call-delegation-missing-agent-quality",
    )
    payload = _payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "missing_tasks"
    assert payload["dispatchStatus"] == "missing_tasks"
    assert payload["diagnosticKey"] == "delegation_missing_tasks"
    assert payload["missingResult"] is True
    assert payload["exampleTasks"]
    assert "parallel_invocations" not in command.update

