from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


import core.graph_stream_watchdog as watchdog_module
import runtimes.chat.runtime as chat_runtime_module
from core.graph_stream_watchdog import (
    GraphStreamDownstreamTimeoutError,
    GraphStreamIdleTimeoutError,
    GraphStreamWatchdogState,
    next_graph_stream_event,
)
from runtimes.chat.runtime import ChatRuntime, ChatStreamState


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
        self.assertEqual(state.active_runtime_episode_ids, {"runtime_episode_1"})

        state.finish_event(
            {
                "event": "on_chain_end",
                "name": "parallel_delegate_task",
                "run_id": "runtime_episode_1",
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
        self.assertEqual(timeout_payloads[0]["activeRuntimeEpisodeIds"], ["runtime_episode_join"])

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

