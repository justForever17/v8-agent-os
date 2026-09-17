"""Opt-in three isolated Engine processes, signed peer HTTP and real Config Broker IO.

Two targets are used so one can fail while the other commits. Synthetic identity
and peer credentials stay inside the temporary fixture; no user's state is read.
This is loopback integration, not Android/iOS or LAN/VPN physical acceptance.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time

ENGINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ENGINE))


def node(root, name, port):
    root = Path(root)
    home = root / name
    home.mkdir(exist_ok=True)
    os.environ["V8_AGENT_OS_HOME"] = str(home)
    os.environ["V8_AGENT_OS_DISABLE_BYTECODE"] = "true"
    os.environ["ENGINE_INSTALL_PROFILE"] = "minimal"
    os.environ["ENGINE_STARTUP_PROFILE"] = "minimal"
    from core.security import credentials
    # Explicit fixture backend survives a subprocess restart without using the
    # operator's OS credential store. Never installed by product code.
    class FixtureCredentials(credentials.MemoryCredentialBackend):
        def __init__(self):
            super().__init__()
            self.path = home / "fixture-credentials.json"
            if self.path.exists(): self._values = json.loads(self.path.read_text())
        def write(self, target, value):
            super().write(target, value)
            self.path.write_text(json.dumps(self._values))
        def delete(self, target):
            result = super().delete(target)
            self.path.write_text(json.dumps(self._values))
            return result
    credentials.credential_ref_store = credentials.CredentialRefStore(FixtureCredentials())
    from core.storage import storage
    from core.client_identity import get_identity_service
    from core.database import db
    from runtimes.network_supervisor.service import network_supervisor_service as network
    seed = json.loads((root / "seed.json").read_text())
    own = seed[name]
    if not (home / "fixture-initialized").exists():
        config = storage.get_system_base_config()
        config["bridge"]["engineBaseUrl"] = f"http://127.0.0.1:{port}/v1"
        config.setdefault("remoteLink", {})["phoneGateway"] = {"enabled": False, "port": own["gatewayPort"]}
        storage.save_system_base_config(config)
        storage.save_models_config({"governance": {"budgets": {"runMaxTokens": 777 if name == "source" else 100}},
                                    "roleParameters": {"supervisor": {"temperature": 0.7}}})
        config = network.get_config_model()
        config.enabled = True
        config.node.peer_id = name
        config.node.display_name = name
        config.discovery.lan_enabled = False
        config.relay.enabled = False
        from runtimes.network_supervisor.models import TrustedPeerConfig
        peers = [key for key in seed if (name == "source" and key != name) or (name != "source" and key == "source")]
        config.trust.trusted_peers = [TrustedPeerConfig(peerId=key, publicKey=seed[key]["publicKey"], baseUrl=f'http://127.0.0.1:{seed[key]["port"]}') for key in peers]
        network.save_config_model(config)
        network.write_secrets({"privateKey": own["privateKey"], "publicKey": own["publicKey"], "localPeerToken": own["peerToken"],
                               "peerTokens": {key: seed[key]["peerToken"] for key in peers}})
        for peer in peers:
            db.upsert_network_neighbor_link(link_id="link_" + peer, peer_id=peer, local_nickname=name, remote_nickname=peer,
                local_role="primary" if name == "source" else "companion", remote_role="companion" if name == "source" else "primary")
        get_identity_service().create_session(name="Fixture local", surface="cli", hidden=True)
        (home / "fixture-initialized").touch()
    # Production app, routes, client auth boundary, lifespan recovery and worker.
    import main
    import uvicorn
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="error", access_log=False)


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def audit(output):
    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    report = {"live": True, "layer": "three_isolated_production_engine_processes_signed_loopback_http", "cases": []}
    with tempfile.TemporaryDirectory(prefix="v8-config-distribution-live-", ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        seed = {}
        for name in ("source", "target1", "target2"):
            key = Ed25519PrivateKey.generate()
            seed[name] = {"port": free_port(), "gatewayPort": free_port(), "privateKey": base64.b64encode(key.private_bytes_raw()).decode(),
                          "publicKey": base64.b64encode(key.public_key().public_bytes_raw()).decode(), "peerToken": secrets.token_urlsafe(24)}
        (root / "seed.json").write_text(json.dumps(seed))
        processes, logs = {}, {}
        client = httpx.Client(timeout=30, trust_env=False)
        def request(name, method, path, body=None, expected=200):
            config = json.loads((root / name / "config.json").read_text(encoding="utf-8-sig"))
            secret = config["systemBase"]["bridge"]["internalSecret"]
            response = client.request(method, f'http://127.0.0.1:{seed[name]["port"]}/v1/' + path,
                                      headers={"X-V8-Agent-OS-Secret": secret}, json=body)
            assert response.status_code == expected, (path, response.status_code, response.text[:300])
            return response.json()
        def start(name):
            logs[name] = (root / (name + ".log")).open("ab")
            processes[name] = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--node", str(root), name, str(seed[name]["port"])],
                                               cwd=ENGINE, stdout=logs[name], stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                if processes[name].poll() is not None:
                    # Full log remains inside the isolated fixture; never report credential values.
                    raise AssertionError("engine_start_failed:" + name + ":" + (root / (name + ".log")).read_text(errors="replace")[-1800:])
                try:
                    if client.get(f'http://127.0.0.1:{seed[name]["port"]}/v1/health').status_code == 200: return
                except httpx.RequestError: pass
                time.sleep(0.15)
            raise AssertionError("engine_start_timeout:" + name)
        def stop(name):
            process = processes.pop(name, None)
            if process:
                import psutil
                try: children = psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess: children = []
                for child in children:
                    try: child.terminate()
                    except psutil.NoSuchProcess: pass
                process.terminate()
                try: process.wait(timeout=12)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
                logs.pop(name).close()
        def wait_job(job_id, states):
            deadline = time.monotonic() + 65
            while time.monotonic() < deadline:
                result = request("source", "GET", "config-distribution/" + job_id)
                if result["state"] in states: return result
                time.sleep(0.15)
            raise AssertionError("job_timeout:" + result["state"])
        def action(job, kind, **extra):
            return request("source", "POST", f'config-distribution/{job["jobId"]}/{kind}',
                           {"commandId": secrets.token_hex(12), "revision": job["revision"], **extra})
        def tokens(name):
            # Independent readback from the actual persistent config file.
            data = json.loads((root / name / "config.json").read_text(encoding="utf-8-sig"))
            return data["models"]["governance"]["budgets"]["runMaxTokens"]
        try:
            for name in seed: start(name)
            inventory = request("source", "GET", "config-distribution")
            assert len(inventory["peers"]) == 2
            body = {"commandId": "create-main", "templateId": "model-policy", "targets": [{"linkId": "link_target1"}, {"linkId": "link_target2"}]}
            created = request("source", "POST", "config-distribution", body)
            prepared = wait_job(created["jobId"], {"awaiting_confirmation", "partial"})
            assert [row["state"] for row in prepared["targets"]] == ["prepared", "prepared"], prepared
            assert tokens("target1") == tokens("target2") == 100
            report["cases"].append("prepare_real_peer_diff_without_write")
            stop("target2")
            action(prepared, "confirm", planDigest=prepared["planDigest"])
            partial = wait_job(created["jobId"], {"partial"})
            assert [row["state"] for row in partial["targets"]] == ["committed", "offline"], partial
            assert tokens("target1") == 777 and tokens("target2") == 100
            report["cases"].append("one_target_offline_other_committed_and_disk_readback")
            stop("source")
            start("target2")
            start("source")
            restored = request("source", "GET", "config-distribution/" + created["jobId"])
            assert restored["jobId"] == created["jobId"]
            completed = wait_job(created["jobId"], {"completed"})
            assert completed["state"] == "completed", completed
            assert tokens("target1") == tokens("target2") == 777
            assert request("source", "POST", "config-distribution", body)["jobId"] == created["jobId"]
            report["cases"].append("source_and_target_process_restart_resume_same_command")
            action(completed, "withdraw")
            withdrawn = wait_job(created["jobId"], {"withdrawn", "partial"})
            assert withdrawn["state"] == "withdrawn", withdrawn
            assert tokens("target1") == tokens("target2") == 100
            report["cases"].append("exact_withdraw_and_both_disk_readbacks")
            second = request("source", "POST", "config-distribution", {**body, "commandId": "create-revoke"})
            prepared2 = wait_job(second["jobId"], {"awaiting_confirmation"})
            request("target1", "DELETE", "network-supervisor/neighbors/link_source")
            action(prepared2, "confirm", planDigest=prepared2["planDigest"])
            partial2 = wait_job(second["jobId"], {"partial"})
            assert partial2["targets"][0]["state"] == "blocked" and partial2["targets"][1]["state"] == "committed", partial2
            assert tokens("target1") == 100 and tokens("target2") == 777
            report["cases"].append("remote_trust_revocation_rejects_previously_prepared_apply")
            # Cross-owner/device HTTP cannot use the private Config Broker API.
            response = client.post(f'http://127.0.0.1:{seed["source"]["port"]}/v1/config-distribution', json=body)
            assert response.status_code == 401
            report["cases"].append("unauthenticated_control_rejected")
            report["passed"] = True
        finally:
            for name in list(processes): stop(name)
            client.close()
    if output: Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output")
    parser.add_argument("--node", nargs=3)
    args = parser.parse_args()
    if args.node: node(args.node[0], args.node[1], int(args.node[2]))
    elif args.live: audit(args.output)
    else: parser.error("real network audit requires --live")
