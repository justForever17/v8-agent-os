from __future__ import annotations

import asyncio
import threading

import pytest

from api import config_registry_routes, platform_routes, storage_retention_routes, system_doctor_routes


@pytest.mark.parametrize(
    ("route", "dependency_name", "kwargs"),
    [
        (platform_routes.get_research_runtime_ledger, "research_ledger_summary", {}),
        (platform_routes.get_research_runtime_evidence, "list_evidence_bundles", {}),
        (platform_routes.get_research_runtime_experience, "search_experience_packs_with_options", {}),
        (platform_routes.get_research_runtime_source_providers, "_build_research_runtime_source_providers_payload", {}),
    ],
)
def test_research_reads_run_outside_the_event_loop(monkeypatch, route, dependency_name, kwargs):
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def read_payload(*args, **call_kwargs):
        worker_threads.append(threading.get_ident())
        return [] if dependency_name in {"list_evidence_bundles", "search_experience_packs_with_options"} else {"ok": True}

    monkeypatch.setattr(platform_routes, dependency_name, read_payload)

    result = asyncio.run(route(**kwargs))
    assert result is not None
    assert worker_threads
    assert worker_threads[0] != event_loop_thread


def test_storage_stats_run_outside_the_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def build_stats():
        worker_threads.append(threading.get_ident())
        return {"ok": True}

    monkeypatch.setattr(storage_retention_routes.storage_retention_service, "build_stats", build_stats)

    assert asyncio.run(storage_retention_routes.get_storage_retention_stats()) == {"ok": True}
    assert worker_threads
    assert worker_threads[0] != event_loop_thread


def test_system_doctor_runs_outside_the_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def run_doctor():
        worker_threads.append(threading.get_ident())
        return {"ok": True}

    monkeypatch.setattr(system_doctor_routes.system_doctor_service, "run", run_doctor)

    assert asyncio.run(system_doctor_routes.get_system_doctor()) == {"ok": True}
    assert worker_threads
    assert worker_threads[0] != event_loop_thread


@pytest.mark.parametrize(
    ("payload", "expected_checks"),
    [
        (None, None),
        ({"checks": [{"id": "node"}]}, [{"id": "node"}]),
    ],
)
def test_system_doctor_repair_plan_runs_outside_the_event_loop(monkeypatch, payload, expected_checks):
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    received_checks: list[object] = []

    def build_repair_plan(checks):
        worker_threads.append(threading.get_ident())
        received_checks.append(checks)
        return {"ok": True}

    monkeypatch.setattr(system_doctor_routes.system_doctor_service, "build_repair_plan", build_repair_plan)

    assert asyncio.run(system_doctor_routes.build_system_doctor_repair_plan(payload)) == {"ok": True}
    assert received_checks == [expected_checks]
    assert worker_threads
    assert worker_threads[0] != event_loop_thread


def test_config_registry_reads_run_outside_the_event_loop(monkeypatch):
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def build_domain():
        worker_threads.append(threading.get_ident())
        return {"domain": "test", "data": {}}

    monkeypatch.setitem(
        config_registry_routes.DOMAIN_REGISTRY,
        "test",
        (build_domain, lambda _payload: {}),
    )

    result = asyncio.run(config_registry_routes.get_config_registry_domain("test"))
    assert result["domain"] == "test"
    assert worker_threads
    assert worker_threads[0] != event_loop_thread
