from __future__ import annotations

import asyncio
import threading

import pytest

from api import config_registry_routes, platform_routes, storage_retention_routes


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
