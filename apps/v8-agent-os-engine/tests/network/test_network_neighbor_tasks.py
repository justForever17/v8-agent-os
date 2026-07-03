from __future__ import annotations

import asyncio

import pytest

from core.database import DatabaseManager
from runtimes.network_supervisor import neighbor as neighbor_module
from runtimes.network_supervisor import neighbor_tasks as task_module
from runtimes.network_supervisor import neighbor_workspace as neighbor_workspace_module
from runtimes.network_supervisor.models import NetworkEnvelope
from runtimes.network_supervisor.neighbor import NetworkNeighborService
from runtimes.network_supervisor.neighbor_tasks import NetworkNeighborTaskService
from tests.network.test_network_neighbor import FakeNetworkSupervisorService


@pytest.fixture()
def task_services(monkeypatch, tmp_path):
    fake_network = FakeNetworkSupervisorService()
    temp_db = DatabaseManager(tmp_path / "tasks.db")
    monkeypatch.setattr(neighbor_module, "network_supervisor_service", fake_network)
    monkeypatch.setattr(neighbor_module, "db", temp_db)
    monkeypatch.setattr(task_module, "network_supervisor_service", fake_network)
    monkeypatch.setattr(task_module, "db", temp_db)
    monkeypatch.setattr(
        neighbor_workspace_module.workspace_resolution_service,
        "get_main_workspace_path",
        lambda: str(tmp_path / "workspace"),
    )

    import runtimes.network_supervisor.relay_runtime as relay_runtime_module

    monkeypatch.setattr(relay_runtime_module.network_relay_worker_service, "relay_available", lambda: False)
    return NetworkNeighborService(), NetworkNeighborTaskService(), fake_network, temp_db


def _link(temp_db: DatabaseManager, peer_id: str, *, link_id: str | None = None, metadata: dict | None = None):
    return temp_db.upsert_network_neighbor_link(
        link_id=link_id or f"nlink_{peer_id}",
        peer_id=peer_id,
        local_nickname="Main",
        remote_nickname=peer_id.replace("peer_", "").title(),
        local_role="primary",
        remote_role="companion",
        workspace_binding={},
        metadata=metadata or {},
    )


def _envelope(message_type: str, *, from_peer_id: str, to_peer_id: str = "peer_local", payload: dict):
    return NetworkEnvelope.model_validate(
        {
            "version": "1",
            "messageId": f"msg_{message_type}_{from_peer_id}",
            "messageType": message_type,
            "sentAt": "2026-07-02T00:00:00Z",
            "expiresAt": "2026-07-02T00:05:00Z",
            "fromPeerId": from_peer_id,
            "toPeerId": to_peer_id,
            "nonce": f"nonce_{message_type}_{from_peer_id}",
            "signature": "sig",
            "trace": {},
            "payload": payload,
        }
    )


def test_capability_tags_are_saved_and_used_for_auto_dispatch(task_services):
    neighbor_svc, task_svc, fake_network, temp_db = task_services
    _link(temp_db, "peer_research", metadata={"capabilityTags": ["research"], "description": "资料调研"})
    _link(temp_db, "peer_gpu", metadata={"capabilityTags": ["gpu", "vision"], "description": "GPU 视觉任务"})
    fake_network.peers_payload["discoveredItems"] = [
        {"peerId": "peer_research", "displayName": "Research", "online": True, "lastSeenAt": "2026-07-02T00:00:00Z"},
        {"peerId": "peer_gpu", "displayName": "GPU", "online": True, "lastSeenAt": "2026-07-02T00:00:00Z"},
    ]

    updated = neighbor_svc.update_link("nlink_peer_research", {"capabilityTags": "research, docs", "description": "官方资料调研"})
    result = asyncio.run(task_svc.dispatch_task(body="分析一张图片", required_capabilities=["vision"]))

    assert updated["link"]["metadata"]["capabilityTags"] == ["research", "docs"]
    assert result["assignments"][0]["peerId"] == "peer_gpu"
    assert fake_network.posted[0][0] == "peer_gpu"
    assert fake_network.posted[0][2].message_type == "neighbor.task.assign"


def test_dispatch_target_all_fans_out_only_when_requested(task_services):
    _neighbor_svc, task_svc, fake_network, temp_db = task_services
    _link(temp_db, "peer_a", metadata={"capabilityTags": ["research"]})
    _link(temp_db, "peer_b", metadata={"capabilityTags": ["research"]})
    fake_network.peers_payload["discoveredItems"] = [
        {"peerId": "peer_a", "displayName": "A", "online": True, "lastSeenAt": "2026-07-02T00:00:00Z"},
        {"peerId": "peer_b", "displayName": "B", "online": True, "lastSeenAt": "2026-07-02T00:00:00Z"},
    ]

    one = asyncio.run(task_svc.dispatch_task(body="查资料", required_capabilities=["research"]))
    all_result = asyncio.run(task_svc.dispatch_task(body="分头查资料", target="all", required_capabilities=["research"]))

    assert len(one["assignments"]) == 1
    assert len(all_result["assignments"]) == 2


def test_inbound_assignment_enters_wake_queue_and_executes_supervisor(task_services, monkeypatch):
    neighbor_svc, task_svc, _fake_network, temp_db = task_services
    _link(temp_db, "peer_primary", link_id="nlink_primary")
    executed: list[dict] = []

    async def fake_execute(**kwargs):
        executed.append(kwargs)

    monkeypatch.setattr(neighbor_module.network_neighbor_service, "_kick_wake_queue_processing", lambda: None)
    monkeypatch.setattr(task_module.network_neighbor_task_service, "execute_assignment", fake_execute)
    envelope = _envelope(
        "neighbor.task.assign",
        from_peer_id="peer_primary",
        payload={
            "taskId": "ntask_inbound",
            "assignmentId": "ntasn_inbound",
            "title": "处理任务",
            "body": "请副设备执行",
            "requiredCapabilities": ["research"],
            "wakePolicy": "inbox",
            "workspaceBinding": {},
            "depth": 0,
        },
    )

    ack = asyncio.run(task_svc.handle_task_envelope(envelope))
    processed = asyncio.run(neighbor_svc.process_wake_queue_once(worker_id="test-task-worker"))

    assert ack.message_type == "neighbor.task.ack"
    assert ack.payload["runScheduled"] is True
    assert processed is True
    assert executed[0]["assignment"]["assignmentId"] == "ntasn_inbound"
    assert temp_db.list_network_neighbor_wake_queue(states=["completed"])


def test_inbound_child_assignment_accepts_remote_parent_assignment_id(task_services, monkeypatch):
    _neighbor_svc, task_svc, _fake_network, temp_db = task_services
    _link(temp_db, "peer_primary", link_id="nlink_primary")
    monkeypatch.setattr(neighbor_module.network_neighbor_service, "_kick_wake_queue_processing", lambda: None)
    envelope = _envelope(
        "neighbor.task.assign",
        from_peer_id="peer_primary",
        payload={
            "taskId": "ntask_child",
            "assignmentId": "ntasn_child",
            "parentAssignmentId": "ntasn_remote_parent",
            "title": "一跳子任务",
            "body": "请接力执行",
            "requiredCapabilities": ["gpu"],
            "wakePolicy": "inbox",
            "workspaceBinding": {},
            "depth": 1,
        },
    )

    ack = asyncio.run(task_svc.handle_task_envelope(envelope))
    assignment = temp_db.get_network_neighbor_assignment("ntasn_child")

    assert ack.payload["status"] == "received"
    assert assignment["parentAssignmentId"] == "ntasn_remote_parent"
    assert assignment["depth"] == 1


def test_result_policy_inbox_vs_per_result_wake_is_idempotent(task_services, monkeypatch):
    _neighbor_svc, task_svc, _fake_network, temp_db = task_services
    _link(temp_db, "peer_companion", link_id="nlink_companion")
    wakes: list[tuple[dict, dict, dict]] = []
    monkeypatch.setattr(task_svc, "_schedule_origin_wake", lambda task, assignment, result: wakes.append((task, assignment, result)))
    temp_db.upsert_network_neighbor_task(
        task_id="ntask_inbox",
        title="只入池",
        body="任务",
        wake_policy="inbox",
        origin_session_id="session_main",
    )
    temp_db.upsert_network_neighbor_assignment(
        assignment_id="ntasn_inbox",
        task_id="ntask_inbox",
        link_id="nlink_companion",
        peer_id="peer_companion",
        body="任务",
        wake_policy="inbox",
    )

    inbox = _envelope(
        "neighbor.task.result",
        from_peer_id="peer_companion",
        payload={"taskId": "ntask_inbox", "assignmentId": "ntasn_inbox", "resultId": "ntres_inbox", "status": "completed", "body": "完成"},
    )
    asyncio.run(task_svc.handle_task_envelope(inbox))
    assert wakes == []

    temp_db.upsert_network_neighbor_task(
        task_id="ntask_wake",
        title="每条唤醒",
        body="任务",
        wake_policy="per_result",
        origin_session_id="session_main",
    )
    temp_db.upsert_network_neighbor_assignment(
        assignment_id="ntasn_wake",
        task_id="ntask_wake",
        link_id="nlink_companion",
        peer_id="peer_companion",
        body="任务",
        wake_policy="per_result",
    )
    wake = _envelope(
        "neighbor.task.result",
        from_peer_id="peer_companion",
        payload={"taskId": "ntask_wake", "assignmentId": "ntasn_wake", "resultId": "ntres_wake", "status": "completed", "body": "完成"},
    )
    asyncio.run(task_svc.handle_task_envelope(wake))
    asyncio.run(task_svc.handle_task_envelope(wake))

    assert len(wakes) == 1
    assert wakes[0][2]["resultId"] == "ntres_wake"


def test_handoff_request_creates_one_hop_child_and_stops_second_handoff(task_services):
    _neighbor_svc, task_svc, fake_network, temp_db = task_services
    _link(temp_db, "peer_a", link_id="nlink_a", metadata={"capabilityTags": ["research"]})
    _link(temp_db, "peer_b", link_id="nlink_b", metadata={"capabilityTags": ["gpu"]})
    fake_network.peers_payload["discoveredItems"] = [
        {"peerId": "peer_a", "displayName": "A", "online": True, "lastSeenAt": "2026-07-02T00:00:00Z"},
        {"peerId": "peer_b", "displayName": "B", "online": True, "lastSeenAt": "2026-07-02T00:00:00Z"},
    ]
    temp_db.upsert_network_neighbor_task(task_id="ntask_parent", title="总任务", body="需要协助", wake_policy="inbox")
    temp_db.upsert_network_neighbor_assignment(
        assignment_id="ntasn_parent",
        task_id="ntask_parent",
        link_id="nlink_a",
        peer_id="peer_a",
        body="需要协助",
        depth=0,
    )

    request = _envelope(
        "neighbor.task.handoff_request",
        from_peer_id="peer_a",
        payload={
            "taskId": "ntask_parent",
            "assignmentId": "ntasn_parent",
            "resultId": "ntres_handoff",
            "reason": "缺 GPU",
            "requestedCapabilities": ["gpu"],
        },
    )
    asyncio.run(task_svc.handle_task_envelope(request))
    child = [item for item in temp_db.list_network_neighbor_assignments(task_id="ntask_parent") if item.get("parentAssignmentId") == "ntasn_parent"]

    assert len(child) == 1
    assert child[0]["peerId"] == "peer_b"
    assert child[0]["depth"] == 1

    second = _envelope(
        "neighbor.task.handoff_request",
        from_peer_id="peer_b",
        payload={
            "taskId": "ntask_parent",
            "assignmentId": child[0]["assignmentId"],
            "resultId": "ntres_handoff_2",
            "reason": "仍需其它设备",
            "requestedCapabilities": ["mac"],
        },
    )
    before = len(temp_db.list_network_neighbor_assignments(task_id="ntask_parent"))
    asyncio.run(task_svc.handle_task_envelope(second))
    after = len(temp_db.list_network_neighbor_assignments(task_id="ntask_parent"))

    assert after == before
    assert temp_db.get_network_neighbor_assignment(child[0]["assignmentId"])["status"] == "handoff_review_required"


def test_companion_device_does_not_auto_forward_handoff_request(task_services):
    _neighbor_svc, task_svc, _fake_network, temp_db = task_services
    temp_db.upsert_network_neighbor_link(
        link_id="nlink_primary",
        peer_id="peer_primary",
        local_nickname="Companion",
        remote_nickname="Primary",
        local_role="companion",
        remote_role="primary",
        workspace_binding={},
        metadata={"capabilityTags": ["research"]},
    )
    temp_db.upsert_network_neighbor_task(task_id="ntask_companion_handoff", title="任务", body="需要协助", wake_policy="inbox")
    temp_db.upsert_network_neighbor_assignment(
        assignment_id="ntasn_companion_handoff",
        task_id="ntask_companion_handoff",
        link_id="nlink_primary",
        peer_id="peer_primary",
        body="需要协助",
        depth=0,
    )
    request = _envelope(
        "neighbor.task.handoff_request",
        from_peer_id="peer_primary",
        payload={
            "taskId": "ntask_companion_handoff",
            "assignmentId": "ntasn_companion_handoff",
            "resultId": "ntres_companion_handoff",
            "reason": "需要其它设备",
            "requestedCapabilities": ["gpu"],
        },
    )

    asyncio.run(task_svc.handle_task_envelope(request))
    assignments = temp_db.list_network_neighbor_assignments(task_id="ntask_companion_handoff")

    assert len(assignments) == 1
    assert assignments[0]["status"] == "handoff_review_required"
