from __future__ import annotations

import asyncio
import time

from api import rpa_routes


def test_availability_does_not_block_event_loop_and_coalesces(monkeypatch):
    calls = 0

    class Runtime:
        def availability(self):
            nonlocal calls
            calls += 1
            return {"robotFramework": True, "calls": calls}

    def resolve_runtime():
        time.sleep(0.12)
        return Runtime()

    monkeypatch.setattr(rpa_routes, "_rpa_runtime", resolve_runtime)
    monkeypatch.setattr(rpa_routes, "_availability_cache", None)
    monkeypatch.setattr(rpa_routes, "_availability_cache_at", 0.0)
    monkeypatch.setattr(rpa_routes, "_availability_lock", asyncio.Lock())

    async def scenario():
        first = asyncio.create_task(rpa_routes.get_rpa_availability())
        second = asyncio.create_task(rpa_routes.get_rpa_availability())
        tick_started = time.perf_counter()
        await asyncio.sleep(0.02)
        assert (time.perf_counter() - tick_started) < 0.08

        first_payload, second_payload = await asyncio.gather(first, second)
        assert first_payload == {"robotFramework": True, "calls": 1}
        assert second_payload == first_payload

    asyncio.run(scenario())
    assert calls == 1


def test_availability_cache_returns_independent_payloads(monkeypatch):
    calls = 0

    class Runtime:
        def availability(self):
            nonlocal calls
            calls += 1
            return {"details": {"status": "ready"}}

    monkeypatch.setattr(rpa_routes, "_rpa_runtime", lambda: Runtime())
    monkeypatch.setattr(rpa_routes, "_availability_cache", None)
    monkeypatch.setattr(rpa_routes, "_availability_cache_at", 0.0)
    monkeypatch.setattr(rpa_routes, "_availability_lock", asyncio.Lock())

    async def scenario():
        first = await rpa_routes.get_rpa_availability()
        first["details"]["status"] = "mutated-by-caller"
        return await rpa_routes.get_rpa_availability()

    second = asyncio.run(scenario())

    assert second["details"]["status"] == "ready"
    assert calls == 1
