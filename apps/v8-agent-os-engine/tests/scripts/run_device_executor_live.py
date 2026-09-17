"""Isolated Android bench server. Never uses the developer's Engine identity.

Run explicitly with --live --allow-side-effects. Only the separately installed
com.v8agentos.executorfixture package is granted observation/capture/actions. The
debug APK trusts the generated CA; release trust settings are never weakened.
"""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import secrets
import sys
import json
import threading
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
    from core.database import db as runtime_database
    from runtimes.network_supervisor.executors.service import ExecutorService
    from runtimes.network_supervisor.executors.protocol import ExecutorError, canonical, digest
    from api import device_executor_routes
    identity = ClientIdentityService(state, CredentialRefStore(MemoryCredentialBackend()))
    existing_owner = identity.owners.owner(required=False)
    owner = (existing_owner or identity.owners.bootstrap(login="isolated-bench", name="Synthetic owner", now=identity.clock()))["id"]
    package = "com.v8agentos.executorfixture"
    capabilities = [{"capability": c, "resourceId": package} for c in ("android.observe", "android.capture", "android.action")]
    runtime_database.create_or_update_session("isolated-bench-session", "Synthetic executor fixture", user_id=owner)
    runtime_database.create_run_record(run_id="isolated-bench", session_id="isolated-bench-session", user_id=owner, run_type="chat", status="running")
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
    service = BenchService(identity, runtime_database=runtime_database)
    media_hold, media_arrived, media_release, media_finished = (threading.Event() for _ in range(4))
    media_barrier_lock = threading.Lock()
    media_tracked = {}
    original_finish = service.media.finish
    def held_finish(principal, media_id):
        with media_barrier_lock:
            held = media_hold.is_set()
            if held:
                media_hold.clear()
                with identity.database() as db:
                    row = db.execute("SELECT command_id FROM executor_media WHERE media_id=?", (media_id,)).fetchone()
                media_tracked.update(mediaId=media_id, commandId=row["command_id"] if row else None)
                media_arrived.set()
        try:
            if held and not media_release.wait(12):
                raise ExecutorError("fixture_media_hold_timeout")
            return original_finish(principal, media_id)
        except ExecutorError as exc:
            if exc.code.startswith("jpeg_"):
                # This bench grants only its no-account fixture. Preserve its
                # rejected bytes for decoder-contract diagnosis, never publish.
                rejected = service.media.path(media_id, temporary=True)
                if rejected.is_file():
                    (root / "rejected-fixture.jpg").write_bytes(rejected.read_bytes())
            raise
        finally:
            if held:
                media_finished.set()
    service.media.finish = held_finish
    device_executor_routes.get_executor_service = lambda: service
    route_errors = deque(maxlen=32)
    original_error = device_executor_routes.error
    def recorded_error(exc):
        route_errors.append({"code": exc.code, "status": exc.status})
        return original_error(exc)
    device_executor_routes.error = recorded_error
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(device_executor_routes.router)

    @app.get("/fixture/errors")
    def errors():
        return {"items": list(route_errors)}

    @app.post("/fixture/media-hold")
    def hold_media():
        with media_barrier_lock:
            if media_hold.is_set() or (media_arrived.is_set() and not media_finished.is_set()):
                return {"ok": False, "code": "fixture_media_barrier_busy"}
            media_tracked.clear()
            media_tracked["barrierId"] = secrets.token_hex(12)
            media_release.clear(); media_arrived.clear(); media_finished.clear(); media_hold.set()
            return {"ok": True, "barrierId": media_tracked["barrierId"]}

    @app.get("/fixture/media-barrier")
    def media_barrier():
        with media_barrier_lock:
            result = {**media_tracked, "arrived": media_arrived.is_set(), "finished": media_finished.is_set()}
        if result.get("mediaId"):
            with identity.database() as db:
                row = db.execute("SELECT state FROM executor_media WHERE media_id=?", (result["mediaId"],)).fetchone()
            result.update(mediaState=row["state"] if row else "gone",
                          bytesRemain=service.media.path(result["mediaId"]).exists()
                          or service.media.path(result["mediaId"], temporary=True).exists())
        return result

    @app.post("/fixture/media-release")
    def release_media():
        with media_barrier_lock:
            media_hold.clear()
            media_release.set()
        return {"ok": True}

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
        if capability not in {"android.observe", "android.capture", "android.action"}:
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

    @app.get("/fixture/agent-view/{command_id}")
    def agent_view(command_id: str):
        from langchain_core.messages import ToolMessage
        from core.tool_surface import apply_tool_surface_budget
        from erc.runtime_context import bind_runtime_context
        result = service.status(owner, command_id)
        with bind_runtime_context(session_id="isolated-bench-session", run_id="isolated-bench", runtime_kind="chat", agent_id="supervisor"):
            visible = apply_tool_surface_budget(ToolMessage(name="device_broker", tool_call_id="fixture-status", content=canonical(result)),
                                                {"agentVisibleBudget": 6000})
        return {"content": visible.content}

    @app.get("/fixture/commands/{command_id}")
    def one_command(command_id: str):
        return service.status(owner, command_id)

    @app.post("/fixture/reconcile/{command_id}")
    def reconcile(command_id: str):
        result = service.status(owner, command_id)
        if result["command"]["resourceId"] != package:
            return {"ok": False, "code": "fixture_target_required"}
        return service.reconcile(owner, command_id, "Synthetic fixture acknowledgement after deadline; outcome remains unknown and no absence of side effects is asserted.")

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
        if command["resourceId"] != package or command["capability"] not in {"android.observe", "android.capture", "android.action"}:
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
