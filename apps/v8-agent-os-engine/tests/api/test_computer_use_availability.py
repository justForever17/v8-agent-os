from __future__ import annotations

import asyncio
import time

from api import computer_use_routes


def _reset_availability_state(monkeypatch):
    monkeypatch.setattr(computer_use_routes, "_availability_cache", None)
    monkeypatch.setattr(computer_use_routes, "_availability_cache_at", 0.0)
    monkeypatch.setattr(computer_use_routes, "_availability_last_error", None)
    monkeypatch.setattr(computer_use_routes, "_availability_last_failure_at", 0.0)
    monkeypatch.setattr(computer_use_routes, "_availability_lock", asyncio.Lock())
    monkeypatch.setattr(computer_use_routes, "_availability_refresh_task", None)


def test_cold_read_returns_unknown_immediately_and_refreshes_once(monkeypatch):
    calls = 0

    class Runtime:
        def availability(self):
            nonlocal calls
            calls += 1
            return {"available": True, "calls": calls}

    def resolve_runtime():
        time.sleep(0.12)
        return Runtime()

    monkeypatch.setattr(computer_use_routes, "_computer_use_runtime", resolve_runtime)
    monkeypatch.setattr(
        computer_use_routes,
        "_configuration_snapshot",
        lambda: {"available": None, "details": {"configuration": {"browserLaneEnabled": True}}},
    )
    _reset_availability_state(monkeypatch)

    async def scenario():
        started = time.perf_counter()
        first_payload = await computer_use_routes.get_computer_use_availability()
        second_payload = await computer_use_routes.get_computer_use_availability()
        assert (time.perf_counter() - started) < 0.08
        assert first_payload["available"] is None
        assert first_payload["environmentProbe"]["state"] == "refreshing"
        assert second_payload["environmentProbe"]["state"] == "refreshing"

        await computer_use_routes._availability_refresh_task
        fresh = await computer_use_routes.get_computer_use_availability()
        assert fresh["available"] is True
        assert fresh["calls"] == 1
        assert fresh["environmentProbe"] == {
            "state": "fresh",
            "stale": False,
            "refreshing": False,
            "error": None,
        }

    asyncio.run(scenario())
    assert calls == 1


def test_explicit_refresh_waits_for_single_probe_and_returns_independent_payloads(monkeypatch):
    calls = 0

    class Runtime:
        def availability(self):
            nonlocal calls
            calls += 1
            return {"details": {"status": "ready"}}

    monkeypatch.setattr(computer_use_routes, "_computer_use_runtime", lambda: Runtime())
    _reset_availability_state(monkeypatch)

    async def scenario():
        first, shared = await asyncio.gather(
            computer_use_routes.get_computer_use_availability(refresh=True),
            computer_use_routes.get_computer_use_availability(refresh=True),
        )
        assert shared["details"]["status"] == "ready"
        first["details"]["status"] = "mutated-by-caller"
        return await computer_use_routes.get_computer_use_availability()

    second = asyncio.run(scenario())

    assert second["details"]["status"] == "ready"
    assert calls == 1


def test_stale_read_returns_immediately_while_background_refreshes(monkeypatch):
    calls = 0

    class Runtime:
        def availability(self):
            nonlocal calls
            calls += 1
            time.sleep(0.12)
            return {"available": True, "revision": 2}

    monkeypatch.setattr(computer_use_routes, "_computer_use_runtime", lambda: Runtime())
    _reset_availability_state(monkeypatch)
    monkeypatch.setattr(computer_use_routes, "_availability_cache", {"available": True, "revision": 1})
    monkeypatch.setattr(computer_use_routes, "_availability_cache_at", -1000.0)

    async def scenario():
        started = time.perf_counter()
        stale = await computer_use_routes.get_computer_use_availability()
        assert (time.perf_counter() - started) < 0.08
        assert stale["revision"] == 1
        assert stale["environmentProbe"]["state"] == "refreshing"
        assert stale["environmentProbe"]["stale"] is True
        await computer_use_routes._availability_refresh_task
        fresh = await computer_use_routes.get_computer_use_availability()
        assert fresh["revision"] == 2

    asyncio.run(scenario())
    assert calls == 1


def test_probe_failure_keeps_last_truth_and_applies_retry_backoff(monkeypatch):
    calls = 0

    class Runtime:
        def availability(self):
            nonlocal calls
            calls += 1
            raise RuntimeError("probe unavailable")

    monkeypatch.setattr(computer_use_routes, "_computer_use_runtime", lambda: Runtime())
    _reset_availability_state(monkeypatch)
    monkeypatch.setattr(computer_use_routes, "_availability_cache", {"available": True, "revision": 1})
    monkeypatch.setattr(computer_use_routes, "_availability_cache_at", -1000.0)

    async def scenario():
        failed = await computer_use_routes.get_computer_use_availability(refresh=True)
        assert failed["available"] is True
        assert failed["environmentProbe"]["state"] == "failed"
        assert failed["environmentProbe"]["stale"] is True
        assert "probe unavailable" in failed["environmentProbe"]["error"]

        retry_blocked = await computer_use_routes.get_computer_use_availability()
        assert retry_blocked["environmentProbe"]["state"] == "failed"
        assert computer_use_routes._availability_refresh_task.done()

    asyncio.run(scenario())
    assert calls == 1


def test_explicit_refresh_timeout_keeps_probe_running_in_background(monkeypatch):
    class Runtime:
        def availability(self):
            time.sleep(0.08)
            return {"available": True, "revision": 2}

    monkeypatch.setattr(computer_use_routes, "_computer_use_runtime", lambda: Runtime())
    monkeypatch.setattr(computer_use_routes, "_AVAILABILITY_EXPLICIT_REFRESH_TIMEOUT_SECONDS", 0.02)
    _reset_availability_state(monkeypatch)
    monkeypatch.setattr(computer_use_routes, "_availability_cache", {"available": True, "revision": 1})
    monkeypatch.setattr(computer_use_routes, "_availability_cache_at", -1000.0)

    async def scenario():
        timed_out = await computer_use_routes.get_computer_use_availability(refresh=True)
        assert timed_out["revision"] == 1
        assert timed_out["environmentProbe"] == {
            "state": "refreshing",
            "stale": True,
            "refreshing": True,
            "error": "refresh_timeout_0.02s",
        }
        assert computer_use_routes._availability_refresh_task.done() is False

        await computer_use_routes._availability_refresh_task
        fresh = await computer_use_routes.get_computer_use_availability()
        assert fresh["revision"] == 2
        assert fresh["environmentProbe"]["state"] == "fresh"

    asyncio.run(scenario())
