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

    async def test_direct_replace_tool_remains_supervisor_available_outside_planning(self) -> None:
        request = _DummyRequest(
            "replace_native_file",
            "tool_call_write",
            state={"current_route_context": {"engineeringRequired": True, "workspacePath": r"E:\Projects\test7"}},
            args={"path": "src/App.tsx", "content": "demo"},
        )

        async def execute(_request):
            return ToolMessage(content="replaced focused file", name="replace_native_file", tool_call_id="tool_call_write")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        self.assertIn("replaced focused file", str(result.content))
        self.assertNotIn("[route required]", str(result.content))

    async def test_direct_mutating_tool_does_not_enqueue_without_a_hard_gate(self) -> None:
        request = _DummyRequest(
            "run_system_command",
            "tool_call_cmd",
            state={"current_route_context": {"engineeringRequired": True}},
            args={"command": "npm run build"},
        )

        async def execute(_request):
            return ToolMessage(content="build completed", name="run_system_command", tool_call_id="tool_call_cmd")

        with bind_runtime_context(run_id="run_agent_quality_gate", session_id="session_agent_quality_gate"):
            with patch("graph.tool_routing._enqueue_route_intent_episode") as enqueue_episode:
                result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIn("build completed", str(result.content))
        self.assertNotIn("[route required]", str(result.content))
        enqueue_episode.assert_not_called()

    async def test_planning_mode_allows_bounded_readonly_command_fact_gathering(self) -> None:
        request = _DummyRequest(
            "run_system_command",
            "tool_call_readonly",
            state={
                "taskPlanningMode": True,
                "current_route_context": {"engineeringRequired": True},
            },
            args={"command": "rg \"RuntimeEpisode\" apps/v8-agent-os-engine -n"},
        )

        async def execute(_request):
            return ToolMessage(content="found RuntimeEpisode", name="run_system_command", tool_call_id="tool_call_readonly")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIn("found RuntimeEpisode", str(result.content))
        self.assertNotIn("[route required]", str(result.content))

    async def test_planning_mode_still_blocks_mutating_or_install_commands(self) -> None:
        request = _DummyRequest(
            "run_system_command",
            "tool_call_install",
            state={
                "taskPlanningMode": True,
                "current_route_context": {"engineeringRequired": True},
            },
            args={"command": "npm install left-pad"},
        )

        async def execute(_request):
            raise AssertionError("install command should still be blocked in planning mode")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIsInstance(result, ToolMessage)
        self.assertIn("[route required]", str(result.content))

    async def test_planning_mode_allows_expanded_web_fact_gathering_budget(self) -> None:
        previous_calls = [
            types.SimpleNamespace(tool_calls=[{"name": "web_broker", "id": f"web-{index}"}])
            for index in range(7)
        ]
        request = _DummyRequest(
            "web_broker",
            "tool_call_web_8",
            state={
                "taskPlanningMode": True,
                "current_route_context": {"engineeringRequired": True},
                "messages": previous_calls,
            },
            args={"query": "current docs"},
        )

        async def execute(_request):
            return ToolMessage(content="web evidence", name="web_broker", tool_call_id="tool_call_web_8")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIn("web evidence", str(result.content))
        self.assertNotIn("[route required]", str(result.content))

    async def test_planning_mode_routes_web_after_expanded_budget(self) -> None:
        previous_calls = [
            types.SimpleNamespace(tool_calls=[{"name": "web_broker", "id": f"web-{index}"}])
            for index in range(8)
        ]
        request = _DummyRequest(
            "web_broker",
            "tool_call_web_9",
            state={
                "taskPlanningMode": True,
                "current_route_context": {"engineeringRequired": True},
                "messages": previous_calls,
            },
            args={"query": "current docs"},
        )

        async def execute(_request):
            raise AssertionError("ninth planning web call should route to Research")

        result = await async_tool_call_wrapper(request, execute, tool_node_name="supervisor_tools")

        self.assertIn("[route required]", str(result.content))
        self.assertEqual(result.additional_kwargs["routeIntent"]["kind"], "research")


def test_runtime_broker_route_accepts_json_need_and_returns_waitable_episode() -> None:
    command = runtime_broker.func(
        mode="route",
        need=json.dumps(
            {
                "tool": "write_native_file",
                "reason": "blocked direct implementation",
                "inputs": {
                    "workspacePath": r"E:\Projects\test7",
                    "taskBriefs": [
                        {
                            "taskBriefId": "task-agent-quality-route",
                            "title": "Apply a focused application patch",
                            "goal": "Update src/App.tsx with the requested behavior.",
                            "context": {"source": "current_supervisor_turn"},
                            "expectedOutput": "src/App.tsx",
                            "expectedArtifacts": ["src/App.tsx"],
                            "writeSet": ["src/App.tsx"],
                            "acceptance": "The requested behavior is present and the focused checks pass.",
                            "constraints": ["Write only src/App.tsx."],
                            "detailRefs": ["conversation://current-turn"],
                        }
                    ],
                },
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
    assert episode["inputs"]["tasks"][0]["taskBriefId"] == "task-agent-quality-route"


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


def test_delegation_dispatch_null_task_is_single_diagnostic_not_fake_swarm() -> None:
    command = delegation_broker.func(
        mode="dispatch",
        tasks=[{"taskBriefId": None, "goal": None, "executionLaneHint": None, "preferredAgentId": None}],
        state={"current_route_context": {}},
        tool_call_id="call-delegation-null-task-agent-quality",
    )
    payload = _payload(command)

    assert payload["ok"] is False
    assert payload["error"] == "missing_tasks"
    assert payload["dispatchStatus"] == "missing_tasks"
    assert payload["diagnosticKey"] == "delegation_missing_tasks"
    assert "parallel_invocations" not in command.update
