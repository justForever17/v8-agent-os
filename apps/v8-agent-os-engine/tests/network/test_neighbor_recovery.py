"""Recovery proofs: failure cannot forge delivery or repeat an executed task."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from tests.network.test_neighbor_delivery_adversarial import signed_peers, _message
from runtimes.network_supervisor import neighbor as neighbors, neighbor_tasks as tasks


def test_intake_crash_rolls_back_task_message_and_wake(signed_peers):
    _local, remote, database, _neighbor, task, _link = signed_peers
    request = _message(remote, "neighbor.task.assign", {"taskId": "task", "assignmentId": "assignment", "body": "Only once", "depth": 0})
    with database.get_connection() as conn:
        conn.execute("CREATE TRIGGER fail_intake BEFORE INSERT ON network_neighbor_wake_queue BEGIN SELECT RAISE(ABORT,'power loss'); END")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        asyncio.run(task.handle_task_envelope(request))
    assert database.get_network_neighbor_task("task") is None
    assert database.get_network_neighbor_assignment("assignment") is None
    assert database.list_network_neighbor_messages(link_id="link") == []
    with database.get_connection() as conn:
        conn.execute("DROP TRIGGER fail_intake")
        conn.commit()
    ack = asyncio.run(task.handle_task_envelope(request))
    assert ack.payload["status"] == "received"
    assert len(database.list_network_neighbor_wake_queue()) == 1


def test_failed_send_keeps_one_message_and_retries_same_business_id(signed_peers, monkeypatch):
    local, _remote, database, neighbor, _task, link = signed_peers
    from runtimes.network_supervisor import relay_runtime
    monkeypatch.setattr(relay_runtime.network_relay_worker_service, "relay_available", lambda: False)
    calls = []
    async def post(peer, path, envelope):
        calls.append(envelope.payload["messageId"])
        if len(calls) == 1:
            raise ConnectionError("ACK lost")
        return {"payload": {"status": "received"}}
    monkeypatch.setattr(local, "_post_peer", post)
    body = "x" * 70000 + " TAIL_PROOF"
    with pytest.raises(ConnectionError):
        asyncio.run(neighbor.send_message(link_id=link["linkId"], body=body))
    stored = database.list_network_neighbor_messages(link_id="link")
    assert len(stored) == 1 and stored[0]["status"] == "failed"
    assert stored[0]["body"] == body
    result = asyncio.run(neighbor.retry_message(link_id="link", message_id=stored[0]["messageId"]))
    assert result["message"]["status"] == "delivered"
    assert calls[0] == calls[1]
    assert len(database.list_network_neighbor_messages(link_id="link")) == 1


def test_approval_resume_delivers_terminal_record_without_reexecuting(signed_peers, monkeypatch):
    _local, remote, database, neighbor, task, link = signed_peers
    request = _message(remote, "neighbor.task.assign", {"taskId": "task", "assignmentId": "assignment", "body": "Perform after approval", "depth": 0})
    ack = asyncio.run(task.handle_task_envelope(request))
    run_id = ack.payload["runId"]
    database.create_or_update_session("session", "Fixture")
    monkeypatch.setattr(neighbor, "_ensure_neighbor_session", lambda *args: "session")
    executions = []
    async def events(*args, **kwargs):
        executions.append(kwargs["run_id"])
        database.create_run_record(kwargs["run_id"], "session", status="waiting_approval")
        yield {"type": "text_chunk", "content": "Awaiting approval"}
        yield {"type": "done", "status": "waiting_approval"}
    monkeypatch.setattr(tasks, "_get_chat_runtime", lambda: SimpleNamespace(stream_legacy_events=events))
    deliveries = []
    async def send(**kwargs):
        deliveries.append(kwargs["transport_envelope"].payload)
        return {"ok": True}
    monkeypatch.setattr(neighbor, "send_message", send)
    asyncio.run(neighbor.process_wake_queue_once(worker_id="worker"))
    assert database.get_network_neighbor_wake_queue_item(ack.payload["queueId"])["state"] == "waiting_approval"
    assert deliveries[-1]["status"] == "waiting_approval"
    # More than one UI page of newer pending runs must not hide this run's
    # eventual completion from the background delivery worker.
    for index in range(55):
        pending = asyncio.run(task.handle_task_envelope(_message(remote, "neighbor.task.assign", {
            "taskId": f"pending-task-{index}", "assignmentId": f"pending-{index}", "body": "Wait", "depth": 0})))
        database.create_run_record(pending.payload["runId"], "session", status="waiting_input")
        with database.get_connection() as conn:
            conn.execute("UPDATE network_neighbor_wake_queue SET state='waiting_input' WHERE id=?", (pending.payload["queueId"],))
            conn.commit()
    # Simulate canonical runtime resumption after the user approves in V8.
    database.update_run_record(run_id, status="completed")
    database.create_chat_canonical_message(message_id="final", session_id="session", run_id=run_id,
        ordinal=1, role="assistant", state="completed", nodes=[], content_text="Verified result after approval")
    assert asyncio.run(neighbor.process_wake_queue_once(worker_id="worker")) is True
    assert deliveries[-1]["status"] == "completed"
    assert deliveries[-1]["body"] == "Verified result after approval"
    assert database.get_network_neighbor_wake_queue_item(ack.payload["queueId"])["state"] == "completed"
    assert executions == [run_id]
    assert asyncio.run(neighbor.process_wake_queue_once(worker_id="worker")) is False


def test_expired_execution_and_late_worker_do_not_repeat_or_commit(signed_peers):
    _local, remote, database, neighbor, _task, _link = signed_peers
    ack = asyncio.run(neighbor.handle_peer_message(_message(remote, "neighbor.message", {"messageId": "one", "body": "once", "wakeSupervisor": True})))
    old = database.claim_next_network_neighbor_wake_item(worker_id="old")
    with database.get_connection() as conn:
        conn.execute("UPDATE network_neighbor_wake_queue SET lease_expires_at='2000-01-01T00:00:00Z' WHERE id=?", (ack.payload["queueId"],))
        conn.commit()
    assert database.claim_next_network_neighbor_wake_item(worker_id="new") is None
    from core.network_neighbor_delivery import settle_wake
    assert settle_wake(database, old, state="completed") is False
    assert database.get_network_neighbor_wake_queue_item(old["queueId"])["state"] == "failed"


def test_disabled_runtime_cannot_be_woken_by_trusted_peer(signed_peers, monkeypatch):
    local, remote, database, neighbor, _task, _link = signed_peers
    monkeypatch.setattr(local, "get_config_model", lambda: SimpleNamespace(enabled=False))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        asyncio.run(neighbor.handle_peer_message(_message(remote, "neighbor.message", {"messageId": "off", "body": "wake", "wakeSupervisor": True})))
    assert failure.value.status_code == 403
    assert database.list_network_neighbor_messages(link_id="link") == []


def test_timeline_shows_new_messages_after_one_hundred_and_pages_back(signed_peers):
    _local, _remote, database, neighbor, _task, _link = signed_peers
    for number in range(105):
        database.add_network_neighbor_message(message_id=f"page-{number}", link_id="link", direction="inbound",
            from_peer_id="remote", from_nickname="Remote", role="companion", body=f"Message {number}", preview=str(number))
    latest = neighbor.timeline("link", limit=100)
    assert latest["items"][-1]["body"] == "Message 104"
    assert latest["items"][0]["seq"] == 6
    older = neighbor.timeline("link", before=latest["previousCursor"], limit=100)
    assert [row["seq"] for row in older["items"]] == [1, 2, 3, 4, 5]
    assert older["previousCursor"] is None
    incremental = neighbor.timeline("link", cursor="104")
    assert [row["seq"] for row in incremental["items"]] == [105]


def test_handoff_parent_does_not_keep_successful_child_task_assigned(signed_peers):
    _local, _remote, database, _neighbor, task, _link = signed_peers
    database.upsert_network_neighbor_task(task_id="handoff", body="GPU task")
    database.upsert_network_neighbor_assignment(assignment_id="parent", task_id="handoff", link_id="link", peer_id="remote", body="GPU task", status="handoff_requested")
    database.upsert_network_neighbor_assignment(assignment_id="child", task_id="handoff", link_id="link", peer_id="remote", body="GPU task", status="completed", parent_assignment_id="parent", depth=1)
    task._recompute_task_status("handoff")
    assert database.get_network_neighbor_task("handoff")["status"] == "completed"


def test_companion_owner_can_select_own_execution_directory(signed_peers):
    _local, _remote, database, neighbor, _task, link = signed_peers
    changed = neighbor.update_link("link", {"localRole": "companion", "workspaceBinding": link["workspaceBinding"]})
    assert changed["link"]["localRole"] == "companion"
    assert changed["link"]["workspaceBinding"]["workspacePath"] == link["workspaceBinding"]["workspacePath"]


@pytest.mark.parametrize("produced", [False, True])
def test_crashed_execution_reports_failure_or_redelivers_existing_result_without_model(signed_peers, monkeypatch, produced):
    _local, remote, database, neighbor, task, _link = signed_peers
    ack = asyncio.run(task.handle_task_envelope(_message(remote, "neighbor.task.assign", {
        "taskId": "task", "assignmentId": "assignment", "body": "Read fixture", "depth": 0})))
    item = database.claim_next_network_neighbor_wake_item(worker_id="crashed")
    from core.network_neighbor_delivery import settle_wake
    settle_wake(database, item, state="failed", error="crash after side effect")
    if produced:
        database.add_network_neighbor_task_result(result_id="produced", task_id="task", assignment_id="assignment",
            link_id="link", peer_id="local", status="completed", summary="verified", body="Verified before power loss",
            metadata={"direction": "outbound"})
    for index in range(55):
        unrelated = asyncio.run(task.handle_task_envelope(_message(remote, "neighbor.task.assign", {
            "taskId": f"done-task-{index}", "assignmentId": f"done-{index}", "body": "Already handled", "depth": 0})))
        database.add_network_neighbor_task_result(result_id=f"delivered-{index}", task_id=f"done-task-{index}",
            assignment_id=f"done-{index}", link_id="link", peer_id="local", status="completed", body="Delivered",
            metadata={"direction": "outbound"})
        database.add_network_neighbor_message(message_id=f"delivered-{index}", link_id="link", direction="outbound",
            from_peer_id="local", from_nickname="Local", role="companion", body="Delivered", preview="Done", status="delivered")
        with database.get_connection() as conn:
            conn.execute("UPDATE network_neighbor_wake_queue SET state='failed' WHERE id=?", (unrelated.payload["queueId"],))
            conn.commit()
    monkeypatch.setattr(tasks, "_get_chat_runtime", lambda: (_ for _ in ()).throw(AssertionError("Do not execute again")))
    deliveries = []
    async def send(**kwargs):
        payload = kwargs["transport_envelope"].payload
        deliveries.append(payload)
        database.add_network_neighbor_message(message_id=kwargs["message_id"], link_id="link", direction="outbound",
            from_peer_id="local", from_nickname="Local", role="companion", body=kwargs["body"], preview="result", status="delivered")
    monkeypatch.setattr(neighbor, "send_message", send)
    assert asyncio.run(neighbor.process_wake_queue_once(worker_id="restarted"))
    assert deliveries[0]["status"] == ("completed" if produced else "failed")
    if produced:
        assert deliveries[0]["body"] == "Verified before power loss"
    assert len(database.list_network_neighbor_wake_queue(limit=100)) == 56
    assert database.list_network_neighbor_wake_queue(recovery_mode="delivery") == []


@pytest.mark.parametrize("waiting_state", ["waiting_input", "waiting_approval"])
@pytest.mark.parametrize("already_terminal", [False, True])
@pytest.mark.parametrize("ack_saved_before_crash", [False, True])
def test_lost_waiting_delivery_restores_original_run(signed_peers, monkeypatch, waiting_state, already_terminal, ack_saved_before_crash):
    local, remote, database, neighbor, task, _link = signed_peers
    from runtimes.network_supervisor import relay_runtime
    monkeypatch.setattr(relay_runtime.network_relay_worker_service, "relay_available", lambda: False)
    request = _message(remote, "neighbor.task.assign", {"taskId": "task", "assignmentId": "assignment", "body": "One governed execution", "depth": 0})
    ack = asyncio.run(task.handle_task_envelope(request))
    run_id, queue_id = ack.payload["runId"], ack.payload["queueId"]
    database.create_or_update_session("waiting-session", "Waiting delivery fixture")
    monkeypatch.setattr(neighbor, "_ensure_neighbor_session", lambda *_args: "waiting-session")
    executions, deliveries = [], []
    disconnected = True

    async def events(*_args, **kwargs):
        executions.append(kwargs["run_id"])
        assert executions == [run_id], "Recovery must not execute the graph again"
        database.create_run_record(run_id, "waiting-session", status=waiting_state)
        yield {"type": "text_chunk", "content": "Wait for the original approval/input card"}
        yield {"type": "done", "status": waiting_state}

    async def post(_peer, _path, envelope):
        deliveries.append(envelope.payload)
        if disconnected:
            raise ConnectionError("Waiting result ACK lost")
        return {"payload": {"status": "received"}}

    monkeypatch.setattr(tasks, "_get_chat_runtime", lambda: SimpleNamespace(stream_legacy_events=events))
    monkeypatch.setattr(local, "_post_peer", post)
    assert asyncio.run(neighbor.process_wake_queue_once(worker_id="worker"))
    assert database.get_network_neighbor_wake_queue_item(queue_id)["state"] == "failed"
    waiting_result = database.list_network_neighbor_task_results(assignment_id="assignment")[-1]
    assert waiting_result["status"] == waiting_state
    disconnected = False
    if ack_saved_before_crash:
        # Power loss after the delivery receipt but before repairing the queue.
        database.update_network_neighbor_message_status(waiting_result["resultId"], "delivered")

    def finish_original_run():
        database.update_run_record(run_id, status="completed")
        database.create_chat_canonical_message(message_id="canonical-final", session_id="waiting-session", run_id=run_id,
            ordinal=1, role="assistant", state="completed", nodes=[], content_text="Verified final answer from original run")

    if already_terminal:
        finish_original_run()
    assert asyncio.run(neighbor.process_wake_queue_once(worker_id="restarted"))
    assert database.get_network_neighbor_wake_queue_item(queue_id)["state"] == waiting_state
    assert executions == [run_id]
    if not already_terminal:
        assert not asyncio.run(neighbor.process_wake_queue_once(worker_id="restarted"))
        finish_original_run()
    assert asyncio.run(neighbor.process_wake_queue_once(worker_id="restarted"))
    assert deliveries[-1]["status"] == "completed"
    assert deliveries[-1]["body"] == "Verified final answer from original run"
    assert database.get_network_neighbor_wake_queue_item(queue_id)["state"] == "completed"
    assert len(database.list_network_neighbor_wake_queue()) == 1
    assert executions == [run_id]
