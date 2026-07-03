from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import MethodType
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.database import DatabaseManager  # noqa: E402
from core.v8_agent_os_paths import V8_AGENT_OS_HOME  # noqa: E402
from runtimes.network_supervisor import neighbor as neighbor_module  # noqa: E402
from runtimes.network_supervisor import neighbor_tasks as task_module  # noqa: E402
from runtimes.network_supervisor import neighbor_workspace as neighbor_workspace_module  # noqa: E402
from runtimes.network_supervisor.models import NetworkEnvelope, NetworkPeerMutationPayload, NetworkTraceContext  # noqa: E402
from runtimes.network_supervisor.neighbor import NetworkNeighborService  # noqa: E402
from runtimes.network_supervisor.neighbor_tasks import NetworkNeighborTaskService  # noqa: E402


class DryRunNetworkService:
    def __init__(self, device: "DryRunDevice") -> None:
        self.device = device
        self.state: dict[str, Any] = {}
        self.upserts: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def read_state(self) -> dict[str, Any]:
        return self.state

    def write_state(self, payload: dict[str, Any]) -> None:
        self.state = dict(payload)

    def ensure_local_identity(self) -> dict[str, Any]:
        return dict(self.device.identity)

    def read_secrets(self) -> dict[str, Any]:
        return {"localPeerToken": self.device.local_token}

    def status_payload(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "started": True,
            "node": {"peerId": self.device.peer_id, "displayName": self.device.display_name},
            "discovery": {"lanEnabled": True, "lastAnnounceAt": "dry-run"},
        }

    def list_peers_payload(self) -> dict[str, Any]:
        discovered = []
        for peer in self.device.discovered:
            discovered.append(peer.discovery_payload())
        return {
            "trustedItems": [],
            "discoveredItems": discovered,
            "meshCandidates": [],
        }

    def list_peers(self) -> list[dict[str, Any]]:
        return [peer.discovery_payload() for peer in self.device.discovered]

    def upsert_peer(self, payload: NetworkPeerMutationPayload) -> dict[str, Any]:
        self.upserts.append(payload.model_dump(by_alias=True))
        return {"ok": True, "peerId": payload.peer_id}

    def delete_peer(self, peer_id: str) -> dict[str, Any]:
        self.deleted.append(peer_id)
        return {"ok": True, "peerId": peer_id}

    def verify_envelope(self, envelope: NetworkEnvelope, **_kwargs: Any) -> dict[str, Any]:
        return {"publicKey": envelope.payload.get("publicKey") or f"{envelope.from_peer_id}-public-key", "trustedPeer": None}

    def build_envelope(
        self,
        *,
        message_type: str,
        to_peer_id: str,
        payload: dict[str, Any],
        trace: NetworkTraceContext | None = None,
        expires_in_seconds: int = 60,
    ) -> NetworkEnvelope:
        return NetworkEnvelope.model_validate(
            {
                "version": "1",
                "messageId": f"msg_{self.device.peer_id}_{message_type}",
                "messageType": message_type,
                "sentAt": "2026-07-02T00:00:00Z",
                "expiresAt": "2026-07-02T00:05:00Z",
                "fromPeerId": self.device.peer_id,
                "toPeerId": to_peer_id,
                "nonce": f"nonce_{self.device.peer_id}_{message_type}_{len(self.device.sent)}",
                "signature": "dry-run",
                "trace": (trace or NetworkTraceContext()).model_dump(by_alias=True),
                "payload": payload,
            }
        )

    async def _post_peer(self, peer_id: str, path: str, envelope: NetworkEnvelope) -> dict[str, Any]:
        target = self.device.mesh.get(peer_id)
        if target is None:
            raise RuntimeError(f"Unknown dry-run peer: {peer_id}")
        self.device.sent.append({"to": peer_id, "path": path, "messageType": envelope.message_type})
        previous_service = neighbor_module.network_supervisor_service
        previous_db = neighbor_module.db
        previous_task_service = task_module.network_neighbor_task_service
        try:
            target.activate()
            if path == "peer/neighbors/pairing/consume":
                return target.neighbor.handle_pairing_consume(envelope).model_dump(by_alias=True)
            if path == "peer/neighbors/messages":
                response = await target.neighbor.handle_peer_message(envelope)
                return response.model_dump(by_alias=True)
            if path == "peer/neighbors/tasks":
                response = await target.tasks.handle_task_envelope(envelope)
                return response.model_dump(by_alias=True)
            raise RuntimeError(f"Unsupported dry-run path: {path}")
        finally:
            neighbor_module.network_supervisor_service = previous_service
            neighbor_module.db = previous_db
            task_module.network_supervisor_service = previous_service
            task_module.db = previous_db
            task_module.network_neighbor_task_service = previous_task_service


class DryRunDevice:
    def __init__(self, root: Path, *, peer_id: str, display_name: str) -> None:
        self.root = root
        self.peer_id = peer_id
        self.display_name = display_name
        self.local_token = f"{peer_id}-token"
        self.identity = {
            "peerId": peer_id,
            "displayName": display_name,
            "publicKey": f"{peer_id}-public-key",
            "publicKeyFingerprint": f"{peer_id}-fp",
            "localPeerTokenFingerprint": f"{peer_id}-token-fp",
            "advertisedBaseUrl": f"http://{peer_id}.local:9530",
            "advertisedWsUrl": f"ws://{peer_id}.local:9530/v1/network-supervisor/peer/ws",
            "transportProfileId": "",
            "peerBaseUrl": "",
        }
        self.db = DatabaseManager(root / f"{peer_id}.db")
        self.network = DryRunNetworkService(self)
        self.neighbor = NetworkNeighborService()
        self.tasks = NetworkNeighborTaskService()
        self.discovered: list[DryRunDevice] = []
        self.mesh: dict[str, DryRunDevice] = {}
        self.sent: list[dict[str, Any]] = []
        self.scheduled_runs: list[dict[str, Any]] = []
        self.task_result_wakes: list[dict[str, Any]] = []

    def discovery_payload(self) -> dict[str, Any]:
        return {
            "peerId": self.peer_id,
            "displayName": self.display_name,
            "online": True,
            "lastSeenAt": "2026-07-02T00:00:00Z",
            "source": "lan",
            "baseUrl": self.identity["advertisedBaseUrl"],
            "wsUrl": self.identity["advertisedWsUrl"],
            "publicKey": self.identity["publicKey"],
            "publicKeyFingerprint": self.identity["publicKeyFingerprint"],
        }

    def activate(self) -> None:
        neighbor_module.network_supervisor_service = self.network
        neighbor_module.db = self.db
        task_module.network_supervisor_service = self.network
        task_module.db = self.db
        task_module.network_neighbor_task_service = self.tasks
        neighbor_workspace_module.workspace_resolution_service.get_main_workspace_path = lambda: str(self.root / "workspace")

    def link_summary(self) -> list[dict[str, Any]]:
        return self.db.list_network_neighbor_links()

    def first_link_id(self) -> str:
        links = self.link_summary()
        if not links:
            raise RuntimeError(f"{self.peer_id} has no neighbor link")
        return str(links[0]["linkId"])

    def timeline(self) -> list[dict[str, Any]]:
        return self.db.list_network_neighbor_messages(link_id=self.first_link_id(), limit=100)

    def task_summary(self) -> dict[str, Any]:
        return {
            "tasks": self.db.list_network_neighbor_tasks(limit=50),
            "assignments": [
                assignment
                for task in self.db.list_network_neighbor_tasks(limit=50)
                for assignment in self.db.list_network_neighbor_assignments(task_id=str(task.get("taskId") or ""), limit=50)
            ],
            "results": [
                result
                for task in self.db.list_network_neighbor_tasks(limit=50)
                for result in self.db.list_network_neighbor_task_results(task_id=str(task.get("taskId") or ""), limit=50)
            ],
        }

    def relay_summary(self) -> dict[str, Any]:
        return {
            "outbox": self.db.list_network_relay_outbox(limit=20),
            "inboxCursor": self.db.get_network_relay_cursor(self.peer_id),
            "deadLetters": self.db.list_network_relay_dead_letters(limit=20),
        }


async def run_dry_run(output_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="v8os-network-neighbor-") as temp:
        from runtimes.network_supervisor import relay_runtime as relay_runtime_module

        relay_runtime_module.network_relay_worker_service.relay_available = lambda: False
        root = Path(temp)
        main = DryRunDevice(root / "main", peer_id="peer_main", display_name="主设备")
        companion_a = DryRunDevice(root / "companion-a", peer_id="peer_companion_a", display_name="副设备 A")
        companion_b = DryRunDevice(root / "companion-b", peer_id="peer_companion_b", display_name="副设备 B")
        devices = [main, companion_a, companion_b]
        for device in devices:
            device.discovered = [item for item in devices if item is not device]
            device.mesh = {item.peer_id: item for item in devices if item is not device}

        def fake_supervisor_run_for(device: DryRunDevice):
            async def _fake_supervisor_run(_self: NetworkNeighborService, **kwargs: Any) -> None:
                device.scheduled_runs.append(
                    {
                        "runId": kwargs.get("run_id"),
                        "peerId": kwargs.get("link", {}).get("peerId"),
                        "messageId": kwargs.get("inbound_message", {}).get("messageId"),
                        "workspaceBinding": kwargs.get("workspace_binding"),
                    }
                )
            return _fake_supervisor_run

        def fake_task_run_for(device: DryRunDevice):
            async def _fake_task_run(_self: NetworkNeighborTaskService, **kwargs: Any) -> None:
                task = dict(kwargs.get("task") or {})
                assignment = dict(kwargs.get("assignment") or {})
                body = str(assignment.get("body") or task.get("body") or "")
                device.scheduled_runs.append(
                    {
                        "kind": "neighbor_task",
                        "runId": kwargs.get("run_id"),
                        "taskId": task.get("taskId"),
                        "assignmentId": assignment.get("assignmentId"),
                        "peerId": kwargs.get("link", {}).get("peerId"),
                    }
                )
                if device.peer_id == companion_a.peer_id and "需要 GPU" in body:
                    await device.tasks._send_handoff_request(
                        link=dict(kwargs.get("link") or {}),
                        task=task,
                        assignment=assignment,
                        reason="副设备 A 缺少 GPU 能力，请转交 GPU 设备。",
                        requested_capabilities=["gpu"],
                        workspace_binding=dict(kwargs.get("workspace_binding") or {}),
                        run_id=str(kwargs.get("run_id") or ""),
                    )
                    return
                await device.tasks._send_result(
                    link=dict(kwargs.get("link") or {}),
                    task=task,
                    assignment=assignment,
                    status="completed",
                    body=f"{device.display_name} 已完成：{body[:120]}",
                    workspace_binding=dict(kwargs.get("workspace_binding") or {}),
                    run_id=str(kwargs.get("run_id") or ""),
                )
            return _fake_task_run

        def fake_result_wake_for(device: DryRunDevice):
            def _fake_result_wake(_self: NetworkNeighborTaskService, task: dict[str, Any], assignment: dict[str, Any], result: dict[str, Any]) -> None:
                device.task_result_wakes.append(
                    {
                        "taskId": task.get("taskId"),
                        "assignmentId": assignment.get("assignmentId") or result.get("assignmentId"),
                        "resultId": result.get("resultId"),
                        "originSessionId": task.get("originSessionId"),
                    }
                )
            return _fake_result_wake

        for device in devices:
            device.neighbor._execute_neighbor_supervisor_message = MethodType(fake_supervisor_run_for(device), device.neighbor)
            device.tasks.execute_assignment = MethodType(fake_task_run_for(device), device.tasks)
            device.tasks._schedule_origin_wake = MethodType(fake_result_wake_for(device), device.tasks)

        pairing_results: list[dict[str, Any]] = []
        main.activate()
        invitation_a = main.neighbor.create_pairing_invitation(local_role="primary", local_nickname="主设备")
        companion_a.activate()
        pairing_results.append(
            await companion_a.neighbor.consume_pairing_invitation(
                peer_id=main.peer_id,
                code=invitation_a["code"],
                local_nickname="副设备 A",
            )
        )
        main.activate()
        invitation_b = main.neighbor.create_pairing_invitation(local_role="primary", local_nickname="主设备")
        companion_b.activate()
        pairing_results.append(
            await companion_b.neighbor.consume_pairing_invitation(
                peer_id=main.peer_id,
                code=invitation_b["code"],
                local_nickname="副设备 B",
            )
        )

        main.activate()
        link_a = next(link for link in main.link_summary() if link["peerId"] == companion_a.peer_id)
        link_b = next(link for link in main.link_summary() if link["peerId"] == companion_b.peer_id)
        main.neighbor.update_link(link_a["linkId"], {"capabilityTags": "research, docs", "description": "适合资料调研和文档整理"})
        main.neighbor.update_link(link_b["linkId"], {"capabilityTags": "gpu, vision", "description": "适合 GPU / 视觉类任务"})

        main_message_result = await main.neighbor.send_message(
            link_id=link_a["linkId"],
            body="主设备发送给副设备 A 的邻居消息",
            wake_supervisor=False,
        )

        companion_a.activate()
        wake_result = await companion_a.neighbor.send_message(
            link_id=companion_a.first_link_id(),
            body="请主设备主理人处理这条邻居消息",
            wake_supervisor=True,
        )
        main.activate()
        await main.neighbor.process_wake_queue_once(worker_id="dry-run-main")
        await asyncio.sleep(0)

        task_a = await main.tasks.dispatch_task(
            title="场景 A：能力定向",
            body="请调研一段资料并返回摘要。",
            link_id=link_a["linkId"],
            required_capabilities=["research"],
            wake_policy="inbox",
            origin_session_id="session_neighbor_dry_run",
        )
        companion_a.activate()
        await companion_a.neighbor.process_wake_queue_once(worker_id="dry-run-companion-a-a")
        await asyncio.sleep(0)

        main.activate()
        main.tasks.update_settings({"resultWakePolicy": "per_result"})
        task_b = await main.tasks.dispatch_task(
            title="场景 B：全部设备",
            body="请各自汇报设备状态。",
            target="all",
            wake_policy="per_result",
            origin_session_id="session_neighbor_dry_run",
        )
        companion_a.activate()
        await companion_a.neighbor.process_wake_queue_once(worker_id="dry-run-companion-a-b")
        companion_b.activate()
        await companion_b.neighbor.process_wake_queue_once(worker_id="dry-run-companion-b-b")
        await asyncio.sleep(0)

        main.activate()
        main.tasks.update_settings({"resultWakePolicy": "inbox"})
        task_c = await main.tasks.dispatch_task(
            title="场景 C：一跳转交",
            body="这个任务需要 GPU，请先由副设备 A 判断能否处理；如果不能，请申请协助。",
            link_id=link_a["linkId"],
            required_capabilities=["research"],
            wake_policy="inbox",
            origin_session_id="session_neighbor_dry_run",
        )
        companion_a.activate()
        await companion_a.neighbor.process_wake_queue_once(worker_id="dry-run-companion-a-c")
        main.activate()
        child_assignments = [
            item
            for item in main.db.list_network_neighbor_assignments(task_id=str(task_c["task"]["taskId"]), limit=20)
            if item.get("parentAssignmentId")
        ]
        if child_assignments:
            companion_b.activate()
            await companion_b.neighbor.process_wake_queue_once(worker_id="dry-run-companion-b-c")
            await asyncio.sleep(0)
            main.activate()
            child_assignments = [
                item
                for item in main.db.list_network_neighbor_assignments(task_id=str(task_c["task"]["taskId"]), limit=20)
                if item.get("parentAssignmentId")
            ]

        main.activate()
        report = {
            "ok": True,
            "kind": "network_neighbor_dry_run",
            "mainDevice": {
                "peerId": main.peer_id,
                "links": main.link_summary(),
                "timeline": {link["peerId"]: main.db.list_network_neighbor_messages(link_id=link["linkId"], limit=100) for link in main.link_summary()},
                "tasks": main.task_summary(),
                "trustUpserts": main.network.upserts,
                "wakeQueue": main.db.list_network_neighbor_wake_queue(limit=20),
                "scheduledRuns": main.scheduled_runs,
                "taskResultWakes": main.task_result_wakes,
                "relay": main.relay_summary(),
                "sent": main.sent,
            },
            "companionDevices": {
                companion_a.peer_id: {
                    "links": companion_a.link_summary(),
                    "timeline": companion_a.timeline(),
                    "tasks": companion_a.task_summary(),
                    "trustUpserts": companion_a.network.upserts,
                    "wakeQueue": companion_a.db.list_network_neighbor_wake_queue(limit=20),
                    "scheduledRuns": companion_a.scheduled_runs,
                    "relay": companion_a.relay_summary(),
                    "sent": companion_a.sent,
                },
                companion_b.peer_id: {
                    "links": companion_b.link_summary(),
                    "timeline": companion_b.timeline(),
                    "tasks": companion_b.task_summary(),
                    "trustUpserts": companion_b.network.upserts,
                    "wakeQueue": companion_b.db.list_network_neighbor_wake_queue(limit=20),
                    "scheduledRuns": companion_b.scheduled_runs,
                    "relay": companion_b.relay_summary(),
                    "sent": companion_b.sent,
                },
            },
            "pairing": {
                "inviteIds": [invitation_a["inviteId"], invitation_b["inviteId"]],
                "codeLengths": [len(invitation_a["code"]), len(invitation_b["code"])],
                "resultLinkIds": [item["link"]["linkId"] for item in pairing_results],
            },
            "messages": {
                "mainToCompanion": main_message_result["message"]["messageId"],
                "companionWakeToMain": wake_result["message"]["messageId"],
                "wakeDelivery": wake_result["delivery"].get("payload", {}),
            },
            "taskScenarios": {
                "A_targetedInbox": {
                    "taskId": task_a["task"]["taskId"],
                    "assignments": [item["assignmentId"] for item in task_a["assignments"]],
                    "mainResults": main.db.list_network_neighbor_task_results(task_id=str(task_a["task"]["taskId"]), limit=20),
                },
                "B_fanoutPerResultWake": {
                    "taskId": task_b["task"]["taskId"],
                    "assignments": [item["assignmentId"] for item in task_b["assignments"]],
                    "wakeCount": len(main.task_result_wakes),
                },
                "C_oneHopHandoff": {
                    "taskId": task_c["task"]["taskId"],
                    "childAssignments": child_assignments,
                    "mainResults": main.db.list_network_neighbor_task_results(task_id=str(task_c["task"]["taskId"]), limit=20),
                },
            },
            "workspaceChecks": {
                "remotePathNotReused": all(
                    "E:\\Projects" not in str(link.get("workspaceBinding", {}).get("workspacePath") or "")
                    for link in [*main.link_summary(), *companion_a.link_summary(), *companion_b.link_summary()]
                ),
                "mainWorkspaceSource": main.link_summary()[0]["workspaceBinding"].get("source"),
                "companionWorkspaceSources": [
                    companion_a.link_summary()[0]["workspaceBinding"].get("source"),
                    companion_b.link_summary()[0]["workspaceBinding"].get("source"),
                ],
            },
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a V8OS Network Links neighbor dry-run report.")
    parser.add_argument(
        "--output",
        type=Path,
        default=V8_AGENT_OS_HOME / "reports" / "network_neighbor_dry_run" / "report.json",
        help="Report JSON path. Defaults to ~/.v8-agent-os/reports/network_neighbor_dry_run/report.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = asyncio.run(run_dry_run(args.output))
    print(json.dumps({"ok": True, "output": str(args.output), "summary": report["messages"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
