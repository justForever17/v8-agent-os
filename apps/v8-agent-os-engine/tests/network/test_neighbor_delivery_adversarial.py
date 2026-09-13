"""Independent real-signature/SQLite faults at the neighbor delivery boundary."""
from __future__ import annotations

import asyncio
import base64
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.database import DatabaseManager
from runtimes.network_supervisor import neighbor as neighbors
from runtimes.network_supervisor import neighbor_tasks as tasks
from runtimes.network_supervisor.models import NetworkTraceContext, TrustedPeerConfig
from runtimes.network_supervisor.service import NetworkSupervisorService


@pytest.fixture()
def signed_peers(monkeypatch, tmp_path):
    def make(peer_id):
        service = NetworkSupervisorService()
        key = Ed25519PrivateKey.generate()
        public = base64.b64encode(key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )).decode()
        state = {}
        monkeypatch.setattr(service, "ensure_local_identity", lambda: {"peerId": peer_id, "displayName": peer_id})
        monkeypatch.setattr(service, "get_config_model", lambda: SimpleNamespace(enabled=True, node=SimpleNamespace(peer_id=peer_id)))
        monkeypatch.setattr(service, "_private_key", lambda: key)
        monkeypatch.setattr(service, "read_state", lambda: state)
        monkeypatch.setattr(service, "write_state", lambda value: state.update(value))
        return service, public

    local, local_public = make("local")
    remote, remote_public = make("remote")
    monkeypatch.setattr(local, "_trusted_peer_map", lambda: {"remote": TrustedPeerConfig(
        peerId="remote", baseUrl="http://127.0.0.1:19999", publicKey=remote_public,
    )})
    monkeypatch.setattr(remote, "_trusted_peer_map", lambda: {"local": TrustedPeerConfig(
        peerId="local", baseUrl="http://127.0.0.1:19998", publicKey=local_public,
    )})
    database = DatabaseManager(tmp_path / "neighbor-fault.db")
    neighbor = neighbors.NetworkNeighborService()
    task = tasks.NetworkNeighborTaskService()
    for module in (neighbors, tasks):
        monkeypatch.setattr(module, "db", database)
        monkeypatch.setattr(module, "network_supervisor_service", local)
    monkeypatch.setattr(neighbors, "network_neighbor_service", neighbor)
    monkeypatch.setattr(tasks, "network_neighbor_task_service", task)
    monkeypatch.setattr(neighbor, "_kick_wake_queue_processing", lambda: None)
    binding = {"workspacePath": str(tmp_path / "workspace")}
    monkeypatch.setattr(tasks, "resolve_network_neighbor_workspace_binding", lambda **_: binding)
    monkeypatch.setattr(neighbors, "resolve_network_neighbor_workspace_binding", lambda **_: binding)
    link = database.upsert_network_neighbor_link(
        link_id="link", peer_id="remote", local_nickname="Local", remote_nickname="Remote",
        local_role="primary", remote_role="companion", workspace_binding=binding,
    )
    return local, remote, database, neighbor, task, link


def _message(remote, kind, payload):
    # Explicit complete trace isolates delivery faults from the independent
    # optional-trace wire canonicalization test below.
    return remote.build_envelope(
        message_type=kind, to_peer_id="local", payload=payload,
        trace=NetworkTraceContext(sourceRunId="run", sourceSessionId="session", workflowId="flow", delegationId="task"),
    )


def _seed_assignment(database):
    task = database.upsert_network_neighbor_task(task_id="task", body="Read the fixture", status="assigned")
    assignment = database.upsert_network_neighbor_assignment(
        assignment_id="assignment", task_id="task", link_id="link", peer_id="remote", body="Read the fixture", status="sent",
    )
    return task, assignment


@pytest.mark.parametrize("trace", [None, NetworkTraceContext(delegationId="task")])
def test_real_signature_roundtrip_preserves_optional_trace(signed_peers, trace):
    local, remote, *_ = signed_peers
    envelope = remote.build_envelope(message_type="neighbor.message", to_peer_id="local", payload={"body": "Hello"}, trace=trace)
    verified = local.verify_envelope(envelope, mark_nonce_seen=False)
    assert verified["trustedPeer"]["peerId"] == "remote"


def test_real_signature_replay_creates_one_receipt_and_one_wake(signed_peers):
    local, remote, database, neighbor, *_ = signed_peers
    envelope = _message(remote, "neighbor.message", {"messageId": "business-message", "body": "Read only", "wakeSupervisor": True})
    first = asyncio.run(neighbor.handle_peer_message(envelope))
    # Identical signed envelope and then a freshly signed transport retry both
    # resolve to the same durable business receipt.
    duplicate = asyncio.run(neighbor.handle_peer_message(envelope))
    fresh_retry = asyncio.run(neighbor.handle_peer_message(_message(remote, "neighbor.message", envelope.payload)))
    assert first.payload["queueId"] == duplicate.payload["queueId"] == fresh_retry.payload["queueId"]
    assert duplicate.payload["status"] == fresh_retry.payload["status"] == "duplicate"
    assert len(database.list_network_neighbor_messages(link_id="link")) == 1
    assert len(database.list_network_neighbor_wake_queue()) == 1


def test_duplicate_waiting_result_cannot_regress_terminal_assignment(signed_peers):
    _local, remote, database, _neighbor, task, _link = signed_peers
    _seed_assignment(database)
    waiting = _message(remote, "neighbor.task.result", {
        "taskId": "task", "assignmentId": "assignment", "resultId": "waiting-result", "status": "waiting_input", "body": "Need a choice",
    })
    finished = _message(remote, "neighbor.task.result", {
        "taskId": "task", "assignmentId": "assignment", "resultId": "final-result", "status": "completed", "body": "Delivered after the choice",
    })
    asyncio.run(task.handle_task_envelope(waiting))
    asyncio.run(task.handle_task_envelope(finished))
    asyncio.run(task.handle_task_envelope(waiting))
    assert database.get_network_neighbor_assignment("assignment")["status"] == "completed"
    assert database.get_network_neighbor_assignment("assignment")["resultId"] == "final-result"
    assert database.get_network_neighbor_task("task")["status"] == "completed"


def test_handoff_receipt_retry_repairs_dispatch_after_crash(signed_peers, monkeypatch):
    _local, remote, database, _neighbor, task, _link = signed_peers
    _seed_assignment(database)
    handoff = _message(remote, "neighbor.task.handoff_request", {
        "taskId": "task", "assignmentId": "assignment", "resultId": "handoff-result", "reason": "Need GPU", "requestedCapabilities": ["gpu"],
    })
    async def crash_before_dispatch(**kwargs):
        raise asyncio.CancelledError()
    monkeypatch.setattr(task, "dispatch_task", crash_before_dispatch)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(task.handle_task_envelope(handoff))
    dispatched = []
    async def dispatch(**kwargs):
        dispatched.append(kwargs)
        return {"ok": True}
    monkeypatch.setattr(task, "dispatch_task", dispatch)
    asyncio.run(task.handle_task_envelope(handoff))
    assert len(dispatched) == 1, "durable receipt must not swallow the missing handoff dispatch"


def test_task_identity_conflict_has_no_partial_projection(signed_peers, monkeypatch):
    _local, remote, database, _neighbor, task, _link = signed_peers
    first = _message(remote, "neighbor.task.assign", {"taskId": "task", "assignmentId": "assignment", "body": "First body", "depth": 0})
    asyncio.run(task.handle_task_envelope(first))
    conflicting = _message(remote, "neighbor.task.assign", {"taskId": "task", "assignmentId": "other-assignment", "body": "Different body", "depth": 0})
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(task.handle_task_envelope(conflicting))
    assert database.get_network_neighbor_task("task")["body"] == "First body"
    assert database.get_network_neighbor_assignment("other-assignment") is None


@pytest.mark.parametrize("status", ["failed", "cancelled", "waiting_input", "waiting_approval"])
def test_origin_continuation_does_not_ack_non_success_as_complete(signed_peers, monkeypatch, status):
    *_prefix, task, _link = signed_peers
    async def events(*args, **kwargs):
        yield {"type": "done", "status": status}
    monkeypatch.setattr(tasks, "_get_chat_runtime", lambda: SimpleNamespace(stream_legacy_events=events))
    from core.network_neighbor_delivery import NeighborExecutionPaused
    expected = NeighborExecutionPaused if status.startswith("waiting_") else RuntimeError
    with pytest.raises(expected):
        asyncio.run(task._wake_origin_supervisor(
            task={"taskId": "task", "originSessionId": "session"},
            assignment={"assignmentId": "assignment"}, result={"body": "A result"}, run_id="run",
        ))


@pytest.mark.parametrize("status", ["waiting_input", "waiting_approval"])
def test_assignment_pause_is_not_a_completed_wake(signed_peers, monkeypatch, status):
    _local, _remote, database, neighbor, task, link = signed_peers
    task_row, assignment = _seed_assignment(database)
    database.create_or_update_session("session", "Fixture")
    database.create_run_record("run", "session")
    monkeypatch.setattr(neighbor, "_ensure_neighbor_session", lambda *args: "session")
    async def events(*args, **kwargs):
        yield {"type": "text_chunk", "content": "Waiting for your decision"}
        yield {"type": "done", "status": status}
    monkeypatch.setattr(tasks, "_get_chat_runtime", lambda: SimpleNamespace(stream_legacy_events=events))
    deliveries = []
    async def send(**kwargs):
        deliveries.append(kwargs)
    monkeypatch.setattr(task, "_send_result", send)
    from core.network_neighbor_delivery import NeighborExecutionPaused
    with pytest.raises(NeighborExecutionPaused):
        asyncio.run(task.execute_assignment(
            link=link, task=task_row, assignment=assignment,
            inbound_message={"body": "Read fixture"}, workspace_binding={}, run_id="run",
        ))
    assert not any(item["status"] == "completed" for item in deliveries)


def test_fresh_assignment_reaches_runtime_before_binding_nonexistent_run(signed_peers, monkeypatch):
    _local, _remote, database, neighbor, task, link = signed_peers
    task_row, assignment = _seed_assignment(database)
    database.create_or_update_session("session", "Fixture")
    monkeypatch.setattr(neighbor, "_ensure_neighbor_session", lambda *args: "session")
    invoked = []
    async def events(request, **kwargs):
        invoked.append(kwargs["run_id"])
        # Model the real runtime's durable run creation at stream entry.
        database.create_run_record(kwargs["run_id"], "session")
        yield {"type": "text_chunk", "content": "Read-only fixture complete"}
        yield {"type": "done", "status": "finished"}
    monkeypatch.setattr(tasks, "_get_chat_runtime", lambda: SimpleNamespace(stream_legacy_events=events))
    async def send(**kwargs):
        assert kwargs["status"] == "completed"
    monkeypatch.setattr(task, "_send_result", send)
    asyncio.run(task.execute_assignment(
        link=link, task=task_row, assignment=assignment,
        inbound_message={"body": "Read fixture"}, workspace_binding={}, run_id="new-run",
    ))
    assert invoked == ["new-run"]
    assert database.get_network_neighbor_assignment("assignment")["runId"] == "new-run"


def test_rejected_concurrent_assignment_cannot_overwrite_accepted_projection(signed_peers, monkeypatch):
    _local, remote, database, _neighbor, task, _link = signed_peers
    first = _message(remote, "neighbor.task.assign", {"taskId": "task", "assignmentId": "assignment", "body": "First body", "depth": 0})
    conflict = _message(remote, "neighbor.task.assign", {"taskId": "task", "assignmentId": "assignment", "body": "Different body", "depth": 0})
    both_read_absent = threading.Barrier(2)
    first_receipt_committed = threading.Event()
    read_task = database.get_network_neighbor_task
    upsert_task = database.upsert_network_neighbor_task
    def synchronized_read(task_id):
        value = read_task(task_id)
        if task_id == "task" and value is None:
            both_read_absent.wait(timeout=5)
        return value
    def delayed_conflict_write(**kwargs):
        if kwargs["body"] == "Different body":
            assert first_receipt_committed.wait(timeout=5)
        return upsert_task(**kwargs)
    monkeypatch.setattr(database, "get_network_neighbor_task", synchronized_read)
    monkeypatch.setattr(database, "upsert_network_neighbor_task", delayed_conflict_write)
    def submit(envelope):
        try:
            return asyncio.run(task.handle_task_envelope(envelope))
        finally:
            if envelope is first:
                first_receipt_committed.set()
    from fastapi import HTTPException
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [(request, pool.submit(submit, request)) for request in (first, conflict)]
        accepted_bodies, rejected_count = [], 0
        for request, future in outcomes:
            try:
                assert future.result(timeout=8).payload["status"] == "received"
                accepted_bodies.append(request.payload["body"])
            except HTTPException as exc:
                assert exc.status_code == 409
                rejected_count += 1
    # The transaction winner is unspecified. All canonical projections must
    # match that winner; rejecting the other request must leave no partial write.
    assert len(accepted_bodies) == rejected_count == 1
    assert read_task("task")["body"] == accepted_bodies[0]
    assert database.get_network_neighbor_assignment("assignment")["body"] == accepted_bodies[0]
    queued = database.list_network_neighbor_wake_queue()
    assert len(queued) == 1
    assert queued[0]["payload"]["assignment"]["body"] == accepted_bodies[0]
