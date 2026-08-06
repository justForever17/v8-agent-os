from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core.runtime_episode_runner import RuntimeEpisodeRunner


def test_runner_start_and_stop_report_real_liveness(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RuntimeEpisodeRunner()

    async def idle_loop() -> None:
        assert runner._stop_event is not None
        runner._thread_started.set()
        runner._record_successful_queue_poll()
        while not runner._stop_event.is_set():
            await asyncio.sleep(0.01)

    monkeypatch.setattr(runner, "_run_loop", idle_loop)

    asyncio.run(runner.start())
    assert runner.readiness_status()["ready"] is True

    asyncio.run(runner.stop())
    assert runner.readiness_status()["ready"] is False


def test_runner_start_fails_closed_when_thread_never_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RuntimeEpisodeRunner()

    class FakeEvent:
        def clear(self) -> None:
            return None

        def wait(self, _timeout: float) -> bool:
            return False

        def set(self) -> None:
            return None

        def is_set(self) -> bool:
            return False

    class FakeThread:
        def __init__(self, **_kwargs: object) -> None:
            return None

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return False

    runner._thread_started = FakeEvent()  # type: ignore[assignment]
    runner._thread_factory = FakeThread  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="did not become ready"):
        asyncio.run(runner.start())


def test_runner_empty_queue_poll_is_required_before_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RuntimeEpisodeRunner()
    monkeypatch.setattr("core.runtime_episode_runner.db.claim_runtime_episode", lambda **_kwargs: None)

    asyncio.run(runner.start())
    status = runner.readiness_status()

    assert status["ready"] is True
    assert status["queueReady"] is True
    assert status["lastSuccessfulPollAt"]
    assert status["consecutivePollFailures"] == 0
    asyncio.run(runner.stop())


def test_runner_initial_queue_failure_fails_closed_then_can_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RuntimeEpisodeRunner()

    def fail_claim(**_kwargs: object) -> None:
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr("core.runtime_episode_runner.db.claim_runtime_episode", fail_claim)
    with pytest.raises(RuntimeError, match="did not become ready"):
        asyncio.run(runner.start())

    failed_status = runner.readiness_status()
    assert failed_status["ready"] is False
    assert failed_status["queueReady"] is False
    assert failed_status["failureType"] == "RuntimeError"
    assert failed_status["lastPollFailureType"] == "RuntimeError"
    assert "queue unavailable" not in str(failed_status)

    asyncio.run(runner.stop())
    reset_status = runner.readiness_status()
    assert reset_status["ready"] is False
    assert reset_status["failureType"] == ""
    assert reset_status["lastPollFailureType"] == ""
    assert reset_status["consecutivePollFailures"] == 0

    monkeypatch.setattr("core.runtime_episode_runner.db.claim_runtime_episode", lambda **_kwargs: None)
    asyncio.run(runner.start())
    assert runner.readiness_status()["ready"] is True
    asyncio.run(runner.stop())


def test_runner_degrades_after_bounded_poll_failures_and_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RuntimeEpisodeRunner()
    runner._poll_seconds = 0.01
    runner._poll_error_seconds = 0.01
    recovery_allowed = threading.Event()
    calls = 0

    def claim(**_kwargs: object):
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        if calls <= 1 + runner._poll_failure_threshold:
            raise RuntimeError("transient queue failure")
        recovery_allowed.wait(2.0)
        return None

    monkeypatch.setattr("core.runtime_episode_runner.db.claim_runtime_episode", claim)
    asyncio.run(runner.start())

    deadline = time.monotonic() + 2.0
    while runner.readiness_status()["queueReady"] and time.monotonic() < deadline:
        time.sleep(0.01)
    degraded = runner.readiness_status()
    assert degraded["ready"] is False
    assert degraded["consecutivePollFailures"] == runner._poll_failure_threshold
    assert degraded["lastPollFailureType"] == "RuntimeError"

    recovery_allowed.set()
    deadline = time.monotonic() + 2.0
    while not runner.readiness_status()["ready"] and time.monotonic() < deadline:
        time.sleep(0.01)
    recovered = runner.readiness_status()
    assert recovered["ready"] is True
    assert recovered["consecutivePollFailures"] == 0
    assert recovered["lastPollFailureType"] == ""
    asyncio.run(runner.stop())
