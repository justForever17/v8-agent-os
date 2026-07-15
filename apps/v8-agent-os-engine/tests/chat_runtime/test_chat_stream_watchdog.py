from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


import core.graph_stream_watchdog as watchdog_module
import runtimes.chat.runtime as chat_runtime_module
from agents.runners.supervisor_runner import SupervisorExecutionBundle
from core.graph_stream_watchdog import (
    GraphStreamDownstreamTimeoutError,
    GraphStreamIdleTimeoutError,
    GraphStreamWatchdogState,
    next_graph_stream_event,
)
from langchain_core.messages import AIMessage, ToolMessage
from runtimes.chat.runtime import ChatExecutionBundle, ChatRuntime, ChatStreamState


class DownstreamTimeoutIterator:
    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(0)
        raise asyncio.TimeoutError("provider timeout")


class NeverYieldIterator:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self._event.wait()
        return {"event": "never"}


class ControlledIterator:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.value: dict | None = None

    def __aiter__(self):
        return self

    async def __anext__(self):
        await self.release.wait()
        if self.value is None:
            raise StopAsyncIteration
        value = self.value
        self.value = None
        self.release.clear()
        return value


class FakeChatRun:
    def __init__(self) -> None:
        self.active_run_id = "run_test"
        self.session_id = "session_test"
        self.request = SimpleNamespace(config=SimpleNamespace(provider="test-provider", model_name="test-model"))
        self.events: list[dict] = []

    def emit_runtime_event(self, topic: str, payload: dict, **kwargs):
        event = {"topic": topic, "payload": payload, **kwargs}
        self.events.append(event)
        return event


class ChatStreamWatchdogTests(unittest.IsolatedAsyncioTestCase):
    async def test_engineering_projection_uses_execution_contract_without_global_plan(self):
        runtime = ChatRuntime()
        chat_run = FakeChatRun()
        chat_run.prepared = SimpleNamespace(
            engineering_trigger_decision={"active": True},
            engineering_context_pack={
                "contextPack": {
                    "codingExecutionContractPreview": {
                        "enabled": True,
                        "writeSet": [],
                        "riskFlags": ["write_set_missing"],
                        "proofExpectations": ["Return verification evidence."],
                    }
                }
            },
            engineering_mode="force",
        )
        bundle = ChatExecutionBundle(
            run_handle=object(),
            runner_bundle=SupervisorExecutionBundle(graph=None, payload=None, graph_config={}, diagnostics={}),
        )

        with mock.patch.object(
            chat_runtime_module.supervisor_runner,
            "get_state_snapshot",
            new=mock.AsyncMock(return_value={"parallel_results": [], "current_route_context": {}}),
        ):
            await runtime.emit_engineering_lane_projection(chat_run, bundle)

        event = chat_run.events[-1]
        self.assertEqual(event["topic"], "engineering.plan.projected")
        self.assertEqual(event["payload"]["summary"], "工程执行合同已投影 · 0 个工程任务")
        self.assertEqual(event["payload"]["riskFlags"], ["write_set_missing"])
        self.assertEqual(event["payload"]["traceRef"], {"runId": "run_test"})

    def test_runtime_episode_chain_uses_long_timeout_window(self):
        state = GraphStreamWatchdogState()

        state.observe_event(
            {
                "event": "on_chain_start",
                "name": "parallel_delegate_task",
                "run_id": "runtime_episode_1",
            }
        )

        self.assertEqual(state.idle_phase(), "runtime_episode_wait")
        self.assertEqual(
            state.idle_timeout_seconds(),
            watchdog_module.ACTIVE_RUNTIME_EPISODE_IDLE_TIMEOUT_SECONDS,
        )
        self.assertEqual(state.active_runtime_episode_ids, {"chain:parallel_delegate_task"})

        state.finish_event(
            {
                "event": "on_chain_end",
                "name": "parallel_delegate_task",
                "run_id": "runtime_episode_1",
            }
        )

        self.assertEqual(state.active_runtime_episode_ids, set())
        self.assertEqual(state.idle_phase(), "stream_progress")

    def test_runtime_episode_chain_finish_ignores_unstable_langgraph_span_id(self):
        state = GraphStreamWatchdogState()

        state.observe_event(
            {
                "event": "on_chain_start",
                "name": "runtime_episode",
                "run_id": "langgraph-span-start",
            }
        )
        self.assertEqual(state.active_runtime_episode_ids, {"chain:runtime_episode"})

        state.finish_event(
            {
                "event": "on_chain_end",
                "name": "runtime_episode",
                "run_id": "langgraph-span-end",
            }
        )

        self.assertEqual(state.active_runtime_episode_ids, set())
        self.assertEqual(state.idle_phase(), "stream_progress")

    async def test_runtime_episode_timeout_payload_includes_active_episode(self):
        state = GraphStreamWatchdogState()
        state.observe_event(
            {
                "event": "on_chain_start",
                "name": "parallel_delegate_join",
                "run_id": "runtime_episode_join",
            }
        )
        timeout_payloads: list[dict] = []

        with mock.patch.object(watchdog_module, "ACTIVE_RUNTIME_EPISODE_IDLE_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(GraphStreamIdleTimeoutError):
                await next_graph_stream_event(
                    NeverYieldIterator(),
                    state=state,
                    session_id="session_test",
                    run_id="run_test",
                    on_timeout=timeout_payloads.append,
                )

        self.assertEqual(len(timeout_payloads), 1)
        self.assertEqual(timeout_payloads[0]["phase"], "runtime_episode_wait")
        self.assertEqual(timeout_payloads[0]["activeRuntimeEpisodeCount"], 1)
        self.assertEqual(timeout_payloads[0]["activeRuntimeEpisodeIds"], ["chain:parallel_delegate_join"])

    async def test_shared_watchdog_distinguishes_downstream_timeout(self):
        state = GraphStreamWatchdogState(has_productive_stream_activity=True)
        timeout_payloads: list[dict] = []

        with self.assertRaises(GraphStreamDownstreamTimeoutError):
            await next_graph_stream_event(
                DownstreamTimeoutIterator(),
                state=state,
                session_id="session_test",
                run_id="run_test",
                on_timeout=timeout_payloads.append,
            )

        self.assertEqual(timeout_payloads, [])

    async def test_shared_watchdog_only_fires_for_real_idle(self):
        state = GraphStreamWatchdogState(has_productive_stream_activity=True)
        timeout_payloads: list[dict] = []

        with mock.patch.object(watchdog_module, "ACTIVE_MODEL_STREAM_IDLE_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(GraphStreamIdleTimeoutError):
                await next_graph_stream_event(
                    NeverYieldIterator(),
                    state=state,
                    session_id="session_test",
                    run_id="run_test",
                    on_timeout=timeout_payloads.append,
                )

        self.assertEqual(len(timeout_payloads), 1)
        self.assertEqual(timeout_payloads[0]["phase"], "stream_progress")

    async def test_chat_runtime_emits_downstream_timeout_instead_of_watchdog(self):
        runtime = ChatRuntime()
        chat_run = FakeChatRun()
        stream_state = ChatStreamState()
        stream_state.watchdog.has_productive_stream_activity = True

        with self.assertRaises(GraphStreamDownstreamTimeoutError):
            await runtime._wait_for_stream_signal(
                stream_iter=DownstreamTimeoutIterator(),
                chat_run=chat_run,
                stream_state=stream_state,
            )

        topics = [event["topic"] for event in chat_run.events]
        self.assertIn("run.stream.downstream_timeout", topics)
        self.assertNotIn("run.watchdog.stream_idle_timeout", topics)

    async def test_chat_runtime_runtime_episode_idle_reports_episode_stalled(self):
        runtime = ChatRuntime()
        chat_run = FakeChatRun()
        stream_state = ChatStreamState()
        stream_state.watchdog.observe_event(
            {
                "event": "on_chain_start",
                "name": "parallel_delegate_task",
                "run_id": "runtime_episode_child",
            }
        )

        with mock.patch.object(watchdog_module, "ACTIVE_RUNTIME_EPISODE_IDLE_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(GraphStreamIdleTimeoutError):
                await runtime._wait_for_stream_signal(
                    stream_iter=NeverYieldIterator(),
                    chat_run=chat_run,
                    stream_state=stream_state,
                )

        topics = [event["topic"] for event in chat_run.events]
        self.assertIn("run.watchdog.runtime_episode_stalled", topics)
        self.assertNotIn("run.watchdog.stream_idle_timeout", topics)
        stalled_payload = next(event["payload"] for event in chat_run.events if event["topic"] == "run.watchdog.runtime_episode_stalled")
        self.assertEqual(stalled_payload["phase"], "runtime_episode_wait")
        self.assertEqual(stalled_payload["failureClass"], "episode_stalled")

    def test_tool_watchdog_timeout_builds_matching_error_tool_message(self):
        runtime = ChatRuntime()
        stream_state = ChatStreamState()
        stream_state.tool_calls_buffer.append({"id": "call_slow", "name": "slow_tool", "args": {"q": "x"}})
        stream_state.watchdog.note_tool_start("call_slow")
        exc = GraphStreamIdleTimeoutError(
            run_id="run_test",
            session_id="session_test",
            idle_seconds=360,
            phase="tool_wait",
            last_event="on_tool_start:slow_tool",
        )

        messages = runtime._build_tool_watchdog_timeout_messages(stream_state=stream_state, exc=exc)

        self.assertEqual(len(messages), 1)
        self.assertIsInstance(messages[0], ToolMessage)
        self.assertEqual(messages[0].tool_call_id, "call_slow")
        self.assertEqual(messages[0].name, "slow_tool")
        self.assertEqual(getattr(messages[0], "status", None), "error")
        self.assertIn("tool_watchdog_timeout", str(messages[0].content))

    async def test_tool_watchdog_continuation_injects_tool_error_observation(self):
        runtime = ChatRuntime()
        stream_state = ChatStreamState()
        stream_state.tool_calls_buffer.append({"id": "call_slow", "name": "slow_tool", "args": {}})
        stream_state.watchdog.note_tool_start("call_slow")
        exc = GraphStreamIdleTimeoutError(
            run_id="run_test",
            session_id="session_test",
            idle_seconds=360,
            phase="tool_wait",
            last_event="on_tool_start:slow_tool",
        )
        previous_bundle = ChatExecutionBundle(
            run_handle=object(),
            runner_bundle=SupervisorExecutionBundle(graph=None, payload=None, graph_config={}, diagnostics={}),
        )
        binding = SimpleNamespace(
            project_id="project_test",
            workspace_id="workspace_test",
            workspace_path="E:/workspace",
            resolved_scope="workspace:main",
        )
        chat_run = SimpleNamespace(
            request=SimpleNamespace(config=SimpleNamespace()),
            session_id="session_test",
            run_handle=object(),
            scope_result=SimpleNamespace(binding=binding),
            prepared=SimpleNamespace(
                engineering_context_pack={},
                task_shape_hint={},
                explicit_subagent_families=[],
                context_mentions=[],
            ),
            transport="test",
        )
        captured: dict[str, object] = {}

        async def fake_get_state_snapshot(_runner_bundle):
            return {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[{"id": "call_slow", "name": "slow_tool", "args": {}}],
                    )
                ],
                "todos": [],
                "current_route_context": {},
            }

        async def fake_create_execution_bundle(**kwargs):
            captured["messages"] = kwargs["messages"]
            return SupervisorExecutionBundle(graph=None, payload={"messages": kwargs["messages"]}, graph_config={}, diagnostics={})

        with mock.patch.object(chat_runtime_module.supervisor_runner, "get_state_snapshot", side_effect=fake_get_state_snapshot):
            with mock.patch.object(chat_runtime_module.supervisor_runner, "create_execution_bundle", side_effect=fake_create_execution_bundle):
                bundle = await runtime.create_tool_watchdog_continuation_bundle(
                    chat_run=chat_run,
                    previous_bundle=previous_bundle,
                    stream_state=stream_state,
                    exc=exc,
                    continuation_count=1,
                )

        self.assertIsNotNone(bundle)
        messages = list(captured["messages"])
        self.assertIsInstance(messages[-1], ToolMessage)
        self.assertEqual(messages[-1].tool_call_id, "call_slow")
        self.assertIn("tool_watchdog_timeout", str(messages[-1].content))
        self.assertEqual(bundle.runner_bundle.diagnostics["continuationReason"], "tool_watchdog_timeout")

    async def test_text_flush_keeps_pending_stream_task_alive(self):
        runtime = ChatRuntime()
        chat_run = FakeChatRun()
        stream_state = ChatStreamState()
        stream_state.text_aggregator.push("hello")
        iterator = ControlledIterator()

        original_flush = runtime.TEXT_FLUSH_INTERVAL_SECONDS
        runtime.TEXT_FLUSH_INTERVAL_SECONDS = 0.01
        try:
            runtime._schedule_text_flush_deadline(stream_state)
            await asyncio.sleep(0.02)
            signal_kind, event = await runtime._wait_for_stream_signal(
                stream_iter=iterator,
                chat_run=chat_run,
                stream_state=stream_state,
            )
            self.assertEqual(signal_kind, "text_flush")
            self.assertIsNone(event)
            self.assertIsNotNone(stream_state.pending_stream_event_task)
            self.assertFalse(stream_state.pending_stream_event_task.done())

            iterator.value = {"event": "on_chat_model_stream", "name": "V8ChatModelAdapter"}
            iterator.release.set()
            signal_kind, event = await runtime._wait_for_stream_signal(
                stream_iter=iterator,
                chat_run=chat_run,
                stream_state=stream_state,
            )
            self.assertEqual(signal_kind, "graph_event")
            self.assertEqual(event["event"], "on_chat_model_stream")
            self.assertIsNone(stream_state.pending_stream_event_task)
        finally:
            runtime.TEXT_FLUSH_INTERVAL_SECONDS = original_flush
            await runtime._cancel_pending_stream_event_task(stream_state)

    async def test_text_flush_deadline_kind_wins_before_clock_crosses_deadline(self):
        runtime = ChatRuntime()
        chat_run = FakeChatRun()
        stream_state = ChatStreamState()
        iterator = ControlledIterator()
        stream_state.text_aggregator.push("hello")
        stream_state.pending_stream_event_task = asyncio.create_task(iterator.__anext__())
        stream_state.text_flush_deadline = asyncio.get_running_loop().time() + 1.0

        async def fake_wait(tasks, timeout=None):
            return set(), set(tasks)

        try:
            with mock.patch.object(chat_runtime_module.asyncio, "wait", new=fake_wait):
                signal_kind, event = await runtime._wait_for_stream_signal(
                    stream_iter=iterator,
                    chat_run=chat_run,
                    stream_state=stream_state,
                )
        finally:
            await runtime._cancel_pending_stream_event_task(stream_state)

        self.assertEqual(signal_kind, "text_flush")
        self.assertIsNone(event)
        topics = [runtime_event["topic"] for runtime_event in chat_run.events]
        self.assertNotIn("run.watchdog.stream_idle_timeout", topics)


if __name__ == "__main__":
    unittest.main()

