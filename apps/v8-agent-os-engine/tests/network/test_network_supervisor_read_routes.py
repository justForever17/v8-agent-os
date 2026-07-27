from __future__ import annotations

import asyncio
import threading

import pytest

from api import network_supervisor_routes as routes


@pytest.mark.parametrize(
    ("route", "service", "method_name"),
    [
        (routes.get_network_supervisor_neighbors_status, routes.network_neighbor_service, "status_payload"),
        (routes.get_network_supervisor_neighbors_candidates, routes.network_neighbor_service, "list_candidates"),
        (routes.get_network_supervisor_peers, routes.network_supervisor_service, "list_peers_payload"),
    ],
)
def test_blocking_network_reads_run_outside_the_event_loop(monkeypatch, route, service, method_name):
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []

    def read_payload():
        worker_threads.append(threading.get_ident())
        return {"ok": True}

    monkeypatch.setattr(service, method_name, read_payload)

    assert asyncio.run(route()) == {"ok": True}
    assert worker_threads
    assert worker_threads[0] != event_loop_thread
