"""Isolated physical Phone fixture; never invokes adb, installs an APK or reads user state.

Start: python ... --live --root <new-temp-directory> serve
Ticket: python ... --live --root <same-directory> ticket --node source
Readback/control: ... status | edit --node target1 --tokens 999 | stop --node target2
Only synthetic short-lived pairing data is written to the private fixture folder.
The device coordinator owns adb reverse and the separately installed validation APK.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import ipaddress
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import psutil
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import hashes, serialization
from cryptography import x509
from cryptography.x509.oid import NameOID

from run_config_distribution_live import ENGINE, free_port


def tls_fixture(root, hostname):
    """Public CA can be installed only in the scoped non-release validation app."""
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "V8 configuration validation fixture CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(ca_key, hashes.SHA256()))
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    try: san = x509.IPAddress(ipaddress.ip_address(hostname))
    except ValueError: san = x509.DNSName(hostname)
    certificate = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([san]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True).sign(ca_key, hashes.SHA256()))
    (root / "fixture-ca-public.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (root / "fixture-server.pem").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (root / "fixture-server-key.private.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    # CA signing key is never saved. The leaf private key stays in this isolated fixture.
    return ca.fingerprint(hashes.SHA256()).hex()


def start_tls_gateway(root, gateway_port, tls_port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def forward(self):
            headers = {key: value for key, value in self.headers.items() if key.lower() not in {"host", "connection", "content-length", "accept-encoding"}}
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            try:
                with httpx.Client(timeout=httpx.Timeout(60, read=300), trust_env=False) as client:
                    with client.stream(self.command, f"http://127.0.0.1:{gateway_port}" + self.path, content=body, headers=headers) as response:
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in {"connection", "transfer-encoding", "content-encoding", "content-length"}: self.send_header(key, value)
                        self.end_headers()
                        for chunk in response.iter_bytes(): self.wfile.write(chunk); self.wfile.flush()
            except (httpx.RequestError, OSError):
                try: self.send_response(503); self.end_headers()
                except OSError: pass
        do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_OPTIONS = forward
    server = ThreadingHTTPServer(("0.0.0.0", tls_port), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(root / "fixture-server.pem", root / "fixture-server-key.private.pem")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    return server


def call(root, seed, name, method, path, body=None):
    config = json.loads((root / name / "config.json").read_text(encoding="utf-8-sig"))
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.request(method, f'http://127.0.0.1:{seed[name]["port"]}/v1/' + path,
            headers={"X-V8-Agent-OS-Secret": config["systemBase"]["bridge"]["internalSecret"]}, json=body)
    if response.is_error: raise RuntimeError(f"fixture_request_failed:{response.status_code}:{path}")
    return response.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=["serve", "ticket", "status", "stop", "start", "edit"])
    parser.add_argument("--node", choices=["source", "target1", "target2"], default="source")
    parser.add_argument("--tokens", type=int, default=999)
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--hostname", help="Actual LAN IP or DNS name accepted by the standard Phone pairing policy")
    args = parser.parse_args()
    if not args.live: parser.error("explicit --live required")
    root = args.root.resolve()
    if args.command == "serve":
        if root.exists(): parser.error("serve requires a new isolated directory")
        if not args.hostname: parser.error("serve requires --hostname for verified HTTPS")
        root.mkdir(parents=True)
        fingerprint = tls_fixture(root, args.hostname)
        seed = {}
        for name in ("source", "target1", "target2"):
            key = Ed25519PrivateKey.generate()
            seed[name] = {"port": free_port(), "gatewayPort": free_port(), "tlsPort": free_port(), "hostname": args.hostname, "privateKey": base64.b64encode(key.private_bytes_raw()).decode(),
                          "publicKey": base64.b64encode(key.public_key().public_bytes_raw()).decode(), "peerToken": secrets.token_urlsafe(24)}
        (root / "seed.json").write_text(json.dumps(seed), encoding="utf-8")
        processes, logs, gateways = {}, {}, []
        def start(name):
            if name in processes and processes[name].poll() is None: return
            logs[name] = (root / (name + ".log")).open("ab")
            processes[name] = subprocess.Popen([sys.executable, str(ENGINE / "tests/scripts/run_config_distribution_live.py"), "--node", str(root), name, str(seed[name]["port"])],
                cwd=ENGINE, stdout=logs[name], stderr=subprocess.STDOUT)
        def stop(name):
            process = processes.pop(name, None)
            if process and process.poll() is None:
                try: children = psutil.Process(process.pid).children(recursive=True)
                except psutil.NoSuchProcess: children = []
                for child in children:
                    try: child.terminate()
                    except psutil.NoSuchProcess: pass
                process.terminate()
                try: process.wait(10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(5)
            if name in logs: logs.pop(name).close()
        try:
            for name in seed: start(name)
            for row in seed.values(): gateways.append(start_tls_gateway(root, row["gatewayPort"], row["tlsPort"]))
            manifest = {name: {"enginePort": row["port"], "gatewayPort": row["gatewayPort"], "tlsPort": row["tlsPort"], "phoneOrigin": f'https://{row["hostname"]}:{row["tlsPort"]}'} for name, row in seed.items()}
            manifest["trust"] = {"publicCa": str(root / "fixture-ca-public.pem"), "sha256": fingerprint, "hostname": args.hostname}
            (root / "public.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            print(json.dumps({"fixtureRoot": str(root), "publicManifest": str(root / "public.json"), "state": "starting"}), flush=True)
            deadline = time.monotonic() + min(max(args.minutes, 1), 120) * 60
            while time.monotonic() < deadline:
                control = root / "control.json"
                if control.exists():
                    message = json.loads(control.read_text()); control.unlink()
                    if message["node"] in seed:
                        (start if message["action"] == "start" else stop)(message["node"])
                time.sleep(0.3)
        finally:
            for gateway in gateways: gateway.shutdown(); gateway.server_close()
            for name in list(processes): stop(name)
        return
    seed = json.loads((root / "seed.json").read_text())
    if args.command in {"stop", "start"}:
        (root / "control.json").write_text(json.dumps({"action": args.command, "node": args.node}))
        print(json.dumps({"requested": args.command, "node": args.node})); return
    if args.command == "ticket":
        endpoint = f'https://{seed[args.node]["hostname"]}:{seed[args.node]["tlsPort"]}'
        ticket = call(root, seed, args.node, "POST", "client-identity/pairing-ticket", {"baseUrl": endpoint, "ttlMs": 600000})
        target = root / (args.node + "-pairing.private.json")
        target.write_text(json.dumps(ticket), encoding="utf-8")
        print(json.dumps({"privateTicketFile": str(target), "expiresAt": ticket["expiresAt"], "transport": "verified_https", "phoneOrigin": endpoint})); return
    if args.command == "edit":
        plan = call(root, seed, args.node, "POST", "config-broker/model-policy/prepare", {"governance": {"budgets": {"runMaxTokens": args.tokens}}})
        result = call(root, seed, args.node, "POST", f'config-broker/transactions/{plan["transactionId"]}/commit', {"planDigest": plan["planDigest"]})
        print(json.dumps({"node": args.node, "state": result["state"]})); return
    result = {}
    for name, row in seed.items():
        try:
            config = json.loads((root / name / "config.json").read_text(encoding="utf-8-sig"))
            with httpx.Client(timeout=3, trust_env=False) as client:
                health = client.get(f'http://127.0.0.1:{row["gatewayPort"]}/api/client/instance')
            result[name] = {"gatewayReady": health.status_code == 200, "runMaxTokens": config["models"]["governance"]["budgets"]["runMaxTokens"]}
        except (OSError, httpx.RequestError): result[name] = {"gatewayReady": False}
    print(json.dumps(result))


if __name__ == "__main__": main()
