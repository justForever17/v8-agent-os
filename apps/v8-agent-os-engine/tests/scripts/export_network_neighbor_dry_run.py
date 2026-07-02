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
from runtimes.network_supervisor import neighbor_workspace as neighbor_workspace_module  # noqa: E402
from runtimes.network_supervisor.models import NetworkEnvelope, NetworkPeerMutationPayload, NetworkTraceContext  # noqa: E402
from runtimes.network_supervisor.neighbor import NetworkNeighborService  # noqa: E402


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
        try:
            target.activate()
            if path == "peer/neighbors/pairing/consume":
                return target.neighbor.handle_pairing_consume(envelope).model_dump(by_alias=True)
            if path == "peer/neighbors/messages":
                response = await target.neighbor.handle_peer_message(envelope)
                return response.model_dump(by_alias=True)
            raise RuntimeError(f"Unsupported dry-run path: {path}")
        finally:
            neighbor_module.network_supervisor_service = previous_service
            neighbor_module.db = previous_db


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
        self.discovered: list[DryRunDevice] = []
        self.mesh: dict[str, DryRunDevice] = {}
        self.sent: list[dict[str, Any]] = []
        self.scheduled_runs: list[dict[str, Any]] = []

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


async def run_dry_run(output_path: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="v8os-network-neighbor-") as temp:
        root = Path(temp)
        main = DryRunDevice(root / "main", peer_id="peer_main", display_name="主设备")
        companion = DryRunDevice(root / "companion", peer_id="peer_companion", display_name="副设备")
        main.discovered = [companion]
        companion.discovered = [main]
        main.mesh = {companion.peer_id: companion}
        companion.mesh = {main.peer_id: main}

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

        main.neighbor._execute_neighbor_supervisor_message = MethodType(fake_supervisor_run_for(main), main.neighbor)
        companion.neighbor._execute_neighbor_supervisor_message = MethodType(fake_supervisor_run_for(companion), companion.neighbor)

        main.activate()
        invitation = main.neighbor.create_pairing_invitation(local_role="primary", local_nickname="主设备")

        companion.activate()
        pairing_result = await companion.neighbor.consume_pairing_invitation(
            peer_id=main.peer_id,
            code=invitation["code"],
            local_nickname="副设备",
        )

        main.activate()
        main_link_id = main.first_link_id()
        main_message_result = await main.neighbor.send_message(
            link_id=main_link_id,
            body="主设备发送给副设备的邻居消息",
            wake_supervisor=False,
        )

        companion.activate()
        companion_link_id = companion.first_link_id()
        wake_result = await companion.neighbor.send_message(
            link_id=companion_link_id,
            body="请主设备主理人处理这条邻居消息",
            wake_supervisor=True,
        )
        await asyncio.sleep(0)

        report = {
            "ok": True,
            "kind": "network_neighbor_dry_run",
            "mainDevice": {
                "peerId": main.peer_id,
                "links": main.link_summary(),
                "timeline": main.timeline(),
                "trustUpserts": main.network.upserts,
                "scheduledRuns": main.scheduled_runs,
                "sent": main.sent,
            },
            "companionDevice": {
                "peerId": companion.peer_id,
                "links": companion.link_summary(),
                "timeline": companion.timeline(),
                "trustUpserts": companion.network.upserts,
                "scheduledRuns": companion.scheduled_runs,
                "sent": companion.sent,
            },
            "pairing": {
                "inviteId": invitation["inviteId"],
                "codeLength": len(invitation["code"]),
                "expiresAt": invitation["expiresAt"],
                "resultLinkId": pairing_result["link"]["linkId"],
            },
            "messages": {
                "mainToCompanion": main_message_result["message"]["messageId"],
                "companionWakeToMain": wake_result["message"]["messageId"],
                "wakeDelivery": wake_result["delivery"].get("payload", {}),
            },
            "workspaceChecks": {
                "remotePathNotReused": all(
                    "E:\\Projects" not in str(link.get("workspaceBinding", {}).get("workspacePath") or "")
                    for link in [*main.link_summary(), *companion.link_summary()]
                ),
                "mainWorkspaceSource": main.link_summary()[0]["workspaceBinding"].get("source"),
                "companionWorkspaceSource": companion.link_summary()[0]["workspaceBinding"].get("source"),
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
