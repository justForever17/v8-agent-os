"""Opt-in signed HTTP peer -> real Supervisor -> fixture file -> signed result.

The callback peer is a small protocol client, not a second LLM runtime. Neither
this loopback integration nor its receipts claim Linux/VPN physical acceptance.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import patch


def audit(root: Path, models: dict, port: int) -> dict:
    import httpx
    import uvicorn
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from core.storage import storage
    from core.tools.native.mcp import config_broker
    from erc.runtime_context import bind_runtime_context
    with patch.object(storage, "get_models_config", side_effect=lambda: deepcopy(models)):
        from runtimes.network_supervisor.service import NetworkSupervisorService, network_supervisor_service as receiver
        from runtimes.network_supervisor.models import NetworkEnvelope, NetworkSupervisorRuntimeConfig, TrustedPeerConfig
        from runtimes.network_supervisor.neighbor import network_neighbor_service
        from core.database import db
        import main
        workspace = root / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "input.txt").write_text("V8-PEER-9271", encoding="utf-8")
        with bind_runtime_context(user_id="peer-audit", agent_id="supervisor", actor_role="supervisor", safety_approval_mode="minimal"):
            plan = json.loads(config_broker.invoke({"mode": "network_prepare", "network_settings": {
                "enabled": True, "discovery": {"lanEnabled": False}, "relay": {"enabled": False},
                "delegation": {"defaultTimeoutSeconds": 120},
            }}))
            assert plan["ok"]
            assert json.loads(config_broker.invoke({"mode": "commit", "transaction_id": plan["transactionId"], "plan_digest": plan["planDigest"]}))["ok"]
        receiver_id = receiver.ensure_local_identity()
        sender = NetworkSupervisorService()
        key = Ed25519PrivateKey.generate()
        public = base64.b64encode(key.public_key().public_bytes_raw()).decode()
        sender_config = NetworkSupervisorRuntimeConfig.model_validate({"enabled": True, "node": {"peerId": "peer_live_sender"}})
        sender.get_config_model = lambda: sender_config
        sender._private_key = lambda: key
        state = {}
        sender.read_state = lambda: deepcopy(state)
        sender.write_state = lambda value: (state.clear(), state.update(value))
        sender.ensure_local_identity = lambda: {"peerId": "peer_live_sender", "displayName": "Live sender", "publicKey": public}
        sender_config.trust.trusted_peers = [TrustedPeerConfig(peerId=receiver_id["peerId"], baseUrl=f"http://127.0.0.1:{port}", publicKey=receiver_id["publicKey"])]
        received, callback_errors = [], []
        callback_token = hashlib.sha256(os.urandom(32)).hexdigest()
        class Callback(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                try:
                    if self.path != "/v1/network-supervisor/peer/neighbors/tasks" or self.headers.get("X-V8-Peer-Token") != callback_token:
                        self.send_error(401); return
                    envelope = NetworkEnvelope.model_validate(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                    sender.verify_envelope(envelope)
                    received.append(envelope.payload)
                    ack = sender.build_envelope(message_type="neighbor.task.ack", to_peer_id=envelope.from_peer_id,
                        payload={"requestMessageId": envelope.message_id, **{name: envelope.payload[name] for name in ("taskId", "assignmentId", "resultId")}, "status": "received"}, trace=envelope.trace)
                    wire = json.dumps(ack.model_dump(by_alias=True)).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(wire)
                except Exception as exc:
                    callback_errors.append(type(exc).__name__)
                    self.send_error(500)
        callback = ThreadingHTTPServer(("127.0.0.1", 0), Callback)
        callback_thread = threading.Thread(target=callback.serve_forever, daemon=True)
        callback_thread.start()
        server = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        report = {"live": True, "layer": "real_provider_signed_http_fixture_peer", "cells": []}
        try:
            deadline = time.monotonic() + 90
            while not server.started and time.monotonic() < deadline:
                time.sleep(0.1)
            assert server.started, "engine_not_ready"
            invite = network_neighbor_service.create_pairing_invitation(local_role="companion")
            consume = sender.build_envelope(message_type="neighbor.pairing.consume", to_peer_id=receiver_id["peerId"], payload={
                "code": invite["code"], "publicKey": public, "peerToken": callback_token,
                "displayName": "Live sender", "baseUrl": f"http://127.0.0.1:{callback.server_port}", "wsUrl": "",
            })
            with httpx.Client(timeout=140, trust_env=False) as client:
                started = time.monotonic()
                paired = client.post(f"http://127.0.0.1:{port}/v1/network-supervisor/peer/neighbors/pairing/consume", json=consume.model_dump(by_alias=True))
                paired.raise_for_status()
                ack = NetworkEnvelope.model_validate(paired.json())
                sender.verify_envelope(ack)
                assert ack.message_type == "neighbor.pairing.accepted"
                report["cells"].append({"case": "signed_http_pairing", "passed": True, "elapsedMs": round((time.monotonic()-started)*1000)})
                token = ack.payload["peerToken"]
                link = db.get_network_neighbor_link_by_peer("peer_live_sender")
                network_neighbor_service.update_link(link["linkId"], {"workspaceBinding": {"workspacePath": str(workspace)}})
                request = sender.build_envelope(message_type="neighbor.task.assign", to_peer_id=receiver_id["peerId"], payload={
                    "taskId": "peer-live-task", "assignmentId": "peer-live-assignment", "depth": 0,
                    "title": "本机邻居读写验收", "body": "这是专用临时工作区的基础读写验收。读取 input.txt，把读取到的完整口令原样写到 output.txt，读回确认后在最终答复只给出口令与验证结论。不要访问网络，不要修改其他文件，不要初始化Git。",
                    "workspaceBinding": {}, "wakePolicy": "inbox",
                })
                started = time.monotonic()
                posted = client.post(f"http://127.0.0.1:{port}/v1/network-supervisor/peer/neighbors/tasks", json=request.model_dump(by_alias=True), headers={"X-V8-Peer-Token": token})
                posted.raise_for_status()
                sender.verify_envelope(NetworkEnvelope.model_validate(posted.json()))
                deadline = time.monotonic() + 130
                while not received and time.monotonic() < deadline:
                    time.sleep(0.3)
                output = workspace / "output.txt"
                result = received[-1] if received else {}
                report["cells"].append({"case": "supervisor_read_write_and_signed_result", "elapsedMs": round((time.monotonic()-started)*1000),
                    "passed": result.get("status") == "completed" and output.is_file() and output.read_text(encoding="utf-8").strip() == "V8-PEER-9271" and "V8-PEER-9271" in result.get("body", ""),
                    "resultStatus": result.get("status"), "outputExists": output.is_file(), "callbackErrors": callback_errors})
                assert report["cells"][-1]["passed"], "peer_read_write_delivery_failed"
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and db.list_network_neighbor_wake_queue()[0]["state"] == "leased":
                    time.sleep(0.05)
                assert db.list_network_neighbor_wake_queue()[0]["state"] == "completed", "signed_result_ack_not_accepted"
                original_mtime = output.stat().st_mtime_ns
                # Business identity survives a retry; the transport nonce and TTL do not.
                retry = sender.build_envelope(message_type=request.message_type, to_peer_id=request.to_peer_id,
                                              payload=request.payload, trace=request.trace)
                retried = client.post(f"http://127.0.0.1:{port}/v1/network-supervisor/peer/neighbors/tasks", json=retry.model_dump(by_alias=True), headers={"X-V8-Peer-Token": token})
                retried.raise_for_status()
                assert retried.json()["payload"]["status"] == "duplicate"
                assert len(db.list_network_neighbor_wake_queue()) == 1 and output.stat().st_mtime_ns == original_mtime
                report["cells"].append({"case": "duplicate_assignment_no_second_write", "passed": True})
        except Exception as exc:
            import traceback
            report["failureType"] = type(exc).__name__
            report["frames"] = [{"file": Path(f.filename).name, "line": f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-5:]]
            if isinstance(exc, httpx.HTTPStatusError):
                report["httpStatus"] = exc.response.status_code
                report["httpError"] = exc.response.json().get("detail")
            report["failureCode"] = str(exc) if isinstance(exc, AssertionError) else "peer_live_failed"
            report["queueStates"] = [{"state": item.get("state"), "error": item.get("lastError", "")[:250]} for item in db.list_network_neighbor_wake_queue()]
        finally:
            server.should_exit = True
            thread.join(20)
            callback.shutdown(); callback.server_close(); callback_thread.join(2)
            report["engineStopped"] = not thread.is_alive()
        report["passed"] = not report.get("failureType") and report["engineStopped"] and all(cell["passed"] for cell in report["cells"])
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--isolated-root", required=True)
    parser.add_argument("--port", type=int, default=19533)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live required")
    root = Path(args.isolated_root).resolve()
    if root.exists():
        parser.error("isolated root must not exist")
    with socket.socket() as probe: probe.bind(("127.0.0.1", args.port))
    models = json.loads((Path(os.environ.get("V8_AGENT_OS_HOME") or Path.home()/".v8-agent-os")/"config.json").read_text(encoding="utf-8"))["models"]
    root.mkdir(parents=True)
    os.environ["V8_AGENT_OS_HOME"] = str(root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            report = audit(root, models, args.port)
        except Exception as exc:
            import traceback
            report = {"passed": False, "errorType": type(exc).__name__, "frames": [{"file": Path(f.filename).name, "line": f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-5:]]}
    (root/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "report": str(root/"report.json"), "cells": report.get("cells"), "failureCode": report.get("failureCode"), "frames": report.get("frames")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
