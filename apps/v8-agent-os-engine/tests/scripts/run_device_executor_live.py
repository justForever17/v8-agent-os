"""Isolated Android bench server. Never uses the developer's Engine identity.

Run explicitly with --live --allow-side-effects. Only the separately installed
com.v8agentos.executorfixture package is granted observation/node actions. The
debug APK trusts the generated CA; release trust settings are never weakened.
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import secrets
import sys
import json
from collections import defaultdict, deque


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--allow-side-effects", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9533)
    parser.add_argument("--state-directory", type=Path, help="Resume this harness's synthetic state only")
    args = parser.parse_args()
    if not args.live or not args.allow_side_effects:
        parser.error("Use --live --allow-side-effects for this isolated test application only.")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    state = args.state_directory.resolve() if args.state_directory else root / ("state-" + secrets.token_hex(5))
    if state.parent != root or not state.name.startswith("state-"):
        parser.error("Bench state must be a state-* child of the explicit output directory")
    os.environ["V8_AGENT_OS_HOME"] = str(state)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "V8 isolated executor bench")])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False).sign(key, hashes.SHA256()))
    ca, private_key = root / "ca.pem", root / "server-key.pem"
    # Keep the debug trust anchor stable across harness restarts in this bench
    # directory. It contains synthetic TLS material, never an Engine user key.
    if not ca.exists() and not private_key.exists():
        ca.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    if not ca.exists() or not private_key.exists():
        parser.error("Incomplete bench TLS files; use a fresh output directory")
    from fastapi import FastAPI, Request
    import uvicorn
    from core.client_identity.service import ClientIdentityService
    from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
    from runtimes.network_supervisor.executors.service import ExecutorService
    from runtimes.network_supervisor.executors.protocol import ExecutorError, canonical, digest
    from api import device_executor_routes
    identity = ClientIdentityService(state, CredentialRefStore(MemoryCredentialBackend()))
    existing_owner = identity.owners.owner(required=False)
    owner = (existing_owner or identity.owners.bootstrap(login="isolated-bench", name="Synthetic owner", now=identity.clock()))["id"]
    package = "com.v8agentos.executorfixture"
    capabilities = [{"capability": c, "resourceId": package} for c in ("android.observe", "android.action")]
    injections = defaultdict(deque)
    class BenchService(ExecutorService):
        def enroll(self, payload):
            result = super().enroll(payload)
            self.grant(owner, result["deviceId"], 1, capabilities)
            return {**result, "grantRevision": 2}
        def outbound(self, device, epoch):
            result = super().outbound(device, epoch)
            if injections[device]:
                result.append(injections[device].popleft())
            return result
    service = BenchService(identity)
    device_executor_routes.get_executor_service = lambda: service
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(device_executor_routes.router)

    @app.get("/fixture/ticket")
    def ticket():
        return service.identities.ticket(owner, device_class="android", name="Android bench", base_url=f"https://localhost:{args.port}")

    @app.get("/fixture/state")
    def status():
        devices = service.list(owner)
        with identity.database() as db:
            ids = [r[0] for r in db.execute("SELECT command_id FROM executor_commands ORDER BY created_at")]
        return {"devices": devices, "commands": [service.status(owner, command) for command in ids]}

    @app.post("/fixture/command")
    async def command(request: Request):
        data = await device_executor_routes.body(request)
        device = data.get("deviceId")
        if not device:
            devices = [d for d in service.list(owner) if d["online"]]
            if len(devices) != 1:
                return {"ok": False, "code": "select_exact_online_device"}
            device = devices[0]["deviceId"]
        capability = data.get("capability", "android.observe")
        if capability not in {"android.observe", "android.action"}:
            return {"ok": False, "code": "fixture_capability_only"}
        command_id = "bench_" + secrets.token_hex(8)
        try:
            result = service.create(owner=owner, command_id=command_id, device=device, capability=capability,
                resource=package, arguments=data.get("arguments", {}), precondition=data.get("precondition", {}),
                ttl_ms=data.get("ttlMs", 15000), trace={"runId": "isolated-bench", "episodeId": "bench-" + command_id})
            service.activate(owner, command_id)
            return result
        except ExecutorError as exc:
            return {"ok": False, "code": exc.code}

    @app.post("/fixture/cancel/{command_id}")
    def cancel(command_id: str):
        return service.cancel(owner, command_id)

    @app.post("/fixture/revoke/{device_id}")
    def revoke(device_id: str):
        service.revoke(owner, device_id)
        return {"ok": True}

    @app.post("/fixture/replay/{command_id}")
    def replay(command_id: str):
        result = service.status(owner, command_id)
        injections[result["deviceId"]].append(result["command"])
        return {"ok": True, "commandId": command_id}

    @app.post("/fixture/disconnect/{device_id}")
    def disconnect(device_id: str):
        service.identities.owned(owner, device_id)
        with identity.database() as db:
            row = db.execute("SELECT epoch FROM executor_connections WHERE device_id=?", (device_id,)).fetchone()
        if row:
            service.disconnect(device_id, row["epoch"])
        return {"ok": True}

    @app.post("/fixture/raw-command")
    async def raw_command(request: Request):
        data = await device_executor_routes.body(request)
        base = service.status(owner, data.get("baseCommandId", ""))
        overrides = data.get("overrides", {})
        if not isinstance(overrides, dict) or set(overrides) - {"precondition", "leaseEpoch", "grantRevision", "bootId", "controlSessionId", "issuedUnixMs", "deadlineUnixMs", "ttlMs", "capabilityRevision"}:
            return {"ok": False, "code": "fixture_override_forbidden"}
        command = {**base["command"], **overrides, "commandId": "fault_" + secrets.token_hex(8)}
        if command["resourceId"] != package or command["capability"] not in {"android.observe", "android.action"}:
            return {"ok": False, "code": "fixture_target_required"}
        command["commandDigest"] = digest(command)
        with identity.transaction() as db:
            db.execute("INSERT INTO executor_commands(command_id,device_id,owner_id,request_digest,body,state,created_at,updated_at) VALUES (?,?,?,?,?,'sent',?,?)",
                (command["commandId"], command["deviceId"], owner, command["commandDigest"], canonical(command), service.now(), service.now()))
        injections[command["deviceId"]].append(command)
        return {"commandId": command["commandId"], "deviceId": command["deviceId"], "status": "sent"}

    print(f"Isolated Android fixture ready on https://localhost:{args.port}; public CA: {ca}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, ssl_certfile=str(ca), ssl_keyfile=str(private_key),
                ws_max_size=16384, ws_max_queue=8, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
