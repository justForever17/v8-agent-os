"""Isolated Server HTTPS/WSS -> tool/episode -> JPEG -> existing vision dispatch.

Uses only a synthetic device/model boundary, never a phone or provider account.
Run with --live; --require-no-imaging also checks installed distribution absence.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAAYACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDhqKKK+jPDCiiigAooooAKKKKAP//Z")


def include_production_client_routers(app, client_routes, device_executor_routes):
    """Reuse main.py's actual registration statements without starting its lifespan."""
    source = Path(__file__).resolve().parents[2] / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    registrations = []
    for node in tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if (ast.unparse(call.func) == "app.include_router" and call.args
                and ast.unparse(call.args[0]) in {"client_routes.router", "device_executor_routes.router"}):
            registrations.append(node)
    assert len(registrations) == 2, "Update the harness if production router assembly changes"
    exec(compile(ast.Module(body=registrations, type_ignores=[]), str(source), "exec"), {
        "app": app, "client_routes": client_routes, "device_executor_routes": device_executor_routes,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--require-no-imaging", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live is required for local TLS sockets")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    state = root / ("state-" + secrets.token_hex(8))
    state.mkdir()
    os.environ["V8_AGENT_OS_HOME"] = str(state)
    os.environ["ENGINE_INSTALL_PROFILE"] = "server"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    absent = {name: importlib.util.find_spec(name) is None for name in ("PIL", "numpy")}
    if args.require_no_imaging:
        assert all(absent.values()), "This mode needs the real minimal Server environment"
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from fastapi import FastAPI
    import httpx
    import uvicorn
    from websockets.sync.client import connect
    from core import client_identity
    from core.client_identity.service import ClientIdentityService
    from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
    from core.database import db
    from core.native_tools import NATIVE_TOOLS
    from core.runtime_tool_access import runtime_tool_available, runtime_tool_names_for_groups
    from core.runtime.startup_profile import installed_runtime_families
    from core.runtime_episode_runner import RuntimeEpisodeRunner
    from core.tools.native.device import device_broker
    from runtimes.network_supervisor.executors import service as executor_module
    from runtimes.network_supervisor.executors.media import jpeg_dimensions
    from api import device_executor_routes, client_routes, session_workflow_routes
    from core.remote_link.phone_gateway import create_phone_gateway_app
    from erc.runtime_context import bind_runtime_context
    from langchain_core.messages import AIMessage
    from core.tools import vision_media_analyzer as vision

    frame_width, frame_height = jpeg_dimensions(JPEG)

    checks = []
    def passed(name):
        checks.append(name)
        print(name + ": passed", flush=True)
    names = {tool.name for tool in NATIVE_TOOLS if runtime_tool_available(tool.name)}
    assert {"device_broker", "vision_media_analyzer"} <= names
    assert "download_media_for_vision" not in names and "device_broker" in runtime_tool_names_for_groups(["device.control"])
    assert "creative_media" not in installed_runtime_families()
    passed("default_server_actual_tool_surface_without_media_pack")
    identity = ClientIdentityService(state, CredentialRefStore(MemoryCredentialBackend()))
    owner = identity.owners.bootstrap(login="synthetic-executor", name="Synthetic executor bench", now=identity.clock())["id"]
    client_identity._service = identity
    service = executor_module.ExecutorService(identity, runtime_database=db)
    executor_module._service = service
    db.create_or_update_session("bench-session", "Synthetic executor screenshot", user_id=owner)
    db.create_run_record(run_id="bench-run", session_id="bench-session", user_id=owner, run_type="chat", status="running")
    human = identity.create_session(name="Synthetic human", surface="phone")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.client_database = db
    app.state.client_internal_router = session_workflow_routes.router
    include_production_client_routers(app, client_routes, device_executor_routes)
    gateway = create_phone_gateway_app(client_app=app)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "V8 synthetic media loopback")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False).sign(key, hashes.SHA256()))
    ca, keyfile = state / "ca.pem", state / "tls-key.pem"
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(gateway, host="127.0.0.1", port=port, ssl_certfile=str(ca), ssl_keyfile=str(keyfile),
        ws_max_size=16384, ws_max_queue=8, access_log=False, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    context = ssl.create_default_context(cafile=str(ca))
    try:
        until = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > until: raise RuntimeError("fixture_listener_start_timeout")
            time.sleep(.02)
        with httpx.Client(base_url=f"https://localhost:{port}", verify=context, trust_env=False, timeout=10) as client:
            human_headers = {"Authorization": "Bearer " + human["accessToken"]}
            t = client.post("/api/client/executors/tickets", headers=human_headers,
                json={"deviceClass": "android", "name": "Synthetic native", "baseUrl": f"https://localhost:{port}"})
            t.raise_for_status()
            enrolled = client.post("/api/executor/enroll", json={**t.json(), "deviceClass": "android"})
            enrolled.raise_for_status(); device = enrolled.json()
            device_headers = {"Authorization": "Bearer " + device["credential"]}
            package = "com.v8agentos.executorfixture"
            capabilities = [{"capability": c, "resourceId": package} for c in ("android.capture", "android.action")]
            granted = client.put(f"/api/client/executors/{device['deviceId']}/grants", headers=human_headers, json={"expectedRevision": 1, "grants": capabilities})
            granted.raise_for_status()
            with connect(f"wss://localhost:{port}/api/executor/ws", ssl=context, additional_headers=device_headers, proxy=None) as channel:
                channel.send(json.dumps({"type": "hello", "protocolVersion": 1, "deviceId": device["deviceId"], "authorityId": device["authorityId"],
                    "bootId": "synthetic-boot", "controlSessionId": "synthetic-arm", "capabilityRevision": 1, "capabilities": capabilities, "localEnabled": True}))
                session = json.loads(channel.recv(timeout=10)); assert session["type"] == "session"
                runtime_context = {"run_id": "bench-run", "session_id": "bench-session", "runtime_kind": "chat", "agent_id": "supervisor"}
                with bind_runtime_context(**runtime_context):
                    output = device_broker.invoke({"type": "tool_call", "id": "capture-tool", "name": "device_broker",
                        "args": {"mode": "execute", "device_id": device["deviceId"], "capability": "android.capture", "resource_id": package}})
                queued = json.loads(output.content); assert queued["status"] == "queued"
                runner = RuntimeEpisodeRunner()
                claimed = db.claim_runtime_episode(worker_id=runner.worker_id, kinds=["device_action"], lease_seconds=30)
                assert claimed and claimed["episodeId"] == queued["episodeId"]
                episode = threading.Thread(target=lambda: asyncio.run(runner._execute_episode(claimed)), daemon=True)
                episode.start()
                command = json.loads(channel.recv(timeout=10)); assert command["commandId"] == queued["commandId"]
                def receipt(status, seq, **extra):
                    return {"type": "receipt", "protocolVersion": 1, **{k: command[k] for k in (
                        "commandId", "commandDigest", "authorityId", "deviceId", "bootId", "controlSessionId", "leaseEpoch", "grantRevision")},
                        "status": status, "receiptSeq": seq, "deviceMonotonicMs": 100, **extra}
                for status, seq in (("received", 1), ("started", 2)):
                    channel.send(json.dumps(receipt(status, seq))); assert json.loads(channel.recv(timeout=10))["type"] == "receipt_ack"
                passed("human_enroll_grant_to_device_tool_episode_and_authenticated_wss")
                sha = hashlib.sha256(JPEG).hexdigest()
                observation = {**{k: command[k] for k in ("deviceId", "bootId", "controlSessionId", "resourceId")},
                    "observationId": "synthetic-observation", "appId": package, "windowId": "1", "geometryRevision": "1", "rotation": 0,
                    "width": frame_width, "height": frame_height, "viewport": {"left": 0, "top": 0, "width": frame_width, "height": frame_height},
                    "observedUnixMs": int(time.time() * 1000), "captureScope": "window", "availability": "screenshot_only",
                    "frame": {"frameId": "synthetic-frame", "sha256": sha, "mimeType": "image/jpeg", "width": frame_width, "height": frame_height}}
                reserved = client.post("/api/executor/media", headers=device_headers, json={"commandId": command["commandId"],
                    "commandDigest": command["commandDigest"], "observation": observation, "byteLength": len(JPEG), "sha256": sha, "mimeType": "image/jpeg"})
                reserved.raise_for_status(); media = reserved.json()
                uploaded = client.put(media["uploadPath"], content=JPEG, headers={**device_headers, "Content-Type": "image/jpeg"})
                uploaded.raise_for_status()
                observation["frame"]["mediaId"] = media["mediaId"]
                channel.send(json.dumps(receipt("succeeded", 3, observation=observation)))
                assert json.loads(channel.recv(timeout=10))["type"] == "receipt_ack"
                episode.join(timeout=10); assert not episode.is_alive()
                assert db.get_runtime_episode(claimed["episodeId"])["state"] == "completed"
                with bind_runtime_context(**runtime_context):
                    result = json.loads(device_broker.func(mode="status", command_id=command["commandId"]))
                assert result["businessVerification"] == "unverified" and result["mediaStatus"] == "available"
                artifact = result["artifacts"][0]
                path = "/api/client/artifacts/" + artifact["artifactId"] + "/content?sessionId=bench-session"
                assert client.get(path, headers=human_headers).content == JPEG
                assert client.get(path, headers=device_headers).status_code == 401
                passed("bounded_jpeg_receipt_publication_and_owned_human_read")
                calls = []
                class Model:
                    def invoke(self, messages, options):
                        images = [p["image_url"]["url"] for p in messages[0].content if p["type"] == "image_url"]
                        assert len(images) == 1 and base64.b64decode(images[0].split(",", 1)[1]) == JPEG
                        assert options["metadata"]["images"][0]["sourceSha256"] == sha
                        calls.append("model_payload")
                        return AIMessage(content="Synthetic frame received by model boundary.")
                resolution = {"resolvedModelId": "synthetic-vision", "resolvedProviderId": "synthetic", "resolvedModel": {"capabilityClass": "vision_multimodal"}, "resolvedProvider": {"type": "API", "api_standard": "openai"}}
                with patch.object(vision.model_control_plane, "resolve_model_for_role", return_value=resolution), patch.object(vision.llm_factory, "create_for_role", return_value=Model()), bind_runtime_context(**runtime_context):
                    response = vision.vision_media_analyzer.invoke({"type": "tool_call", "id": "vision-tool", "name": "vision_media_analyzer",
                        "args": {"file_path": result["screenshotRef"]["filePath"], "prompt": "Inspect the synthetic frame"}})
                assert calls == ["model_payload"] and "Synthetic frame received" in response.content
                passed("actual_vision_tool_and_budget_to_existing_multimodal_model_boundary")
                imaging_loaded = any(name == "PIL" or name.startswith("PIL.") or name == "numpy" or name.startswith("numpy.") for name in sys.modules)
                if args.require_no_imaging:
                    assert not imaging_loaded
                    passed("no_pillow_or_numpy_installed_or_imported_in_server_capture_chain")
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        keyfile.unlink(missing_ok=True)
    report = {"layer": "real_loopback_https_wss_tool_episode_and_artifact", "device": "synthetic", "model": "synthetic_boundary",
              "physicalDeviceUsed": False, "imagingPackagesAbsent": absent, "imagingModulesLoaded": imaging_loaded, "result": "passed", "checks": checks}
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
