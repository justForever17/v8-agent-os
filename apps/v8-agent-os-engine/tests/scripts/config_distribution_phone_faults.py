"""Opt-in finite Phone fault injection around the existing isolated TLS fixture.

Only resume starts services, through the existing fixture owner. Self-check uses
MockTransport; it does not operate a Phone or reproduce historical physical runs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import httpx


CATALOG = "/api/client/config-distribution"
TICKETS = "/api/client/executors/tickets"
DEVICE = re.compile(r"executor_[0-9a-f]{24}\Z")
PHASE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
SAFE_CODES = frozenset({
    "access_token_expired", "fixture_invalid_grant", "phone_gateway_bearer_required",
    "device_session_revoked", "client_route_not_found", "executor_requires_https_origin",
    "executor_identity_invalid", "executor_owner_mismatch", "executor_owner_changed",
    "executor_not_found", "executor_credential_required", "executor_credential_revoked",
    "executor_ticket_invalid", "grant_revision_conflict",
})


def gateway_port(root: Path, node: str) -> int:
    port = json.loads((root / "public.json").read_text(encoding="utf-8"))[node]["gatewayPort"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("invalid_public_gateway_port")
    return port


def flag_path(root: Path, node: str, kind: str) -> Path:
    return root / f"phone-{node}-{kind}-fault.json"


def atomic_json(path: Path, value: dict):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(value, stream)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def arm(root: Path, node: str, kind: str, phase: str, device_id: str | None = None):
    if not PHASE.fullmatch(phase):
        raise ValueError("phase_must_be_a_short_nonsecret_label")
    if kind == "management" and not DEVICE.fullmatch(device_id or ""):
        raise ValueError("exact_fixture_executor_device_id_required")
    path = flag_path(root, node, kind)
    if path.exists():
        raise ValueError("fault_already_armed")
    atomic_json(path, {"gatewayPort": gateway_port(root, node), "phase": phase,
        "method": "GET" if kind == "refresh" else "PUT",
        "path": CATALOG if kind == "refresh" else f"/api/client/executors/{device_id}/grants",
        "remaining": 1 if kind == "refresh" else 2})


def traced_client(root: Path, node: str, output: Path):
    """One shared lock/count owner for all clients in the fixture process."""
    port, lock = gateway_port(root, node), threading.Lock()
    original_client = httpx.Client
    output.parent.mkdir(parents=True, exist_ok=True)

    def record(response: httpx.Response, phase: str | None = None):
        request, path = response.request, response.request.url.path
        allowed = path in {CATALOG, TICKETS, "/api/client/executors", "/api/client/auth/refresh",
                           "/api/executor/enroll", "/api/executor/revoke"} or bool(re.fullmatch(
                               r"/api/client/executors/executor_[0-9a-f]{24}/grants", path))
        if request.url.host != "127.0.0.1" or request.url.port != port or not allowed:
            return
        item = {"at": time.time(), "path": path, "method": request.method, "status": response.status_code}
        if phase:
            item["phase"] = phase
        if response.status_code >= 400:
            response.read()
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    code = payload.get("code") or payload.get("error") or payload.get("detail")
                    if isinstance(code, str) and code in SAFE_CODES:
                        item["code"] = code
            except ValueError:
                pass
        with lock, output.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item) + "\n")

    class TraceClient(original_client):
        def __init__(self, *args, **kwargs):
            hooks = dict(kwargs.pop("event_hooks", {}))
            hooks["response"] = [*hooks.get("response", []), record]
            super().__init__(*args, event_hooks=hooks, **kwargs)

        def send(self, request, *args, **kwargs):
            injected = None
            if request.url.host == "127.0.0.1" and request.url.port == port and not request.url.query:
                with lock:
                    for kind, status, code in (("refresh", 401, "access_token_expired"), ("management", 400, "fixture_invalid_grant")):
                        flag = flag_path(root, node, kind)
                        if not flag.exists():
                            continue
                        state = json.loads(flag.read_text(encoding="utf-8"))
                        if (state.get("gatewayPort"), state.get("method"), state.get("path")) != (port, request.method, request.url.path):
                            continue
                        if state.get("remaining") not in range(1, 2 if kind == "refresh" else 3) or not PHASE.fullmatch(state.get("phase", "")):
                            raise ValueError("invalid_fault_flag")
                        if state["remaining"] == 1:
                            flag.unlink()
                        else:
                            atomic_json(flag, {**state, "remaining": state["remaining"] - 1})
                        injected = (status, code, state["phase"])
                        break
            if injected:
                status, code, phase = injected
                response = httpx.Response(status, request=request, headers={"x-v8-auth-stage": "pre_execution"}, json={"error": code})
                record(response, phase)
                return response
            return super().send(request, *args, **kwargs)

    return TraceClient


def self_check():
    with tempfile.TemporaryDirectory(prefix="v8-phone-faults-") as directory:
        root = Path(directory)
        (root / "public.json").write_text(json.dumps({"source": {"gatewayPort": 24001}}), encoding="utf-8")
        trace = root / "trace.jsonl"
        client_type = traced_client(root, "source", trace)
        dispatched = []
        secret = "synthetic_do_not_log_0123456789"
        device_id = "executor_" + "a" * 24
        grants = f"/api/client/executors/{device_id}/grants"

        def dispatch(request):
            dispatched.append((request.method, request.url.port, request.url.path))
            return httpx.Response(400 if request.url.path == TICKETS else 200,
                                  json={"error": secret, "credential": secret}, headers={"X-Secret": secret})

        with client_type(transport=httpx.MockTransport(dispatch), headers={"Authorization": "Bearer " + secret}) as client:
            arm(root, "source", "refresh", "check-first")
            try:
                arm(root, "source", "refresh", "must-not-overwrite")
                raise AssertionError("pending_flag_overwritten")
            except ValueError as exc:
                assert str(exc) == "fault_already_armed"
            for method, url in (("POST", f"http://127.0.0.1:24001{CATALOG}"),
                                ("GET", f"http://127.0.0.1:24002{CATALOG}"),
                                ("GET", f"http://other.invalid:24001{CATALOG}"),
                                ("GET", f"http://127.0.0.1:24001{CATALOG}/other"),
                                ("GET", f"http://127.0.0.1:24001{CATALOG}?token={secret}")):
                assert client.request(method, url).status_code == 200
            assert flag_path(root, "source", "refresh").exists()
            response = client.get(f"http://127.0.0.1:24001{CATALOG}")
            assert response.status_code == 401 and response.headers["x-v8-auth-stage"] == "pre_execution"
            assert not flag_path(root, "source", "refresh").exists()
            assert client.get(f"http://127.0.0.1:24001{CATALOG}").status_code == 200
            arm(root, "source", "management", "check-management", device_id)
            assert client.put(f"http://127.0.0.1:24001/api/client/executors/executor_{'b' * 24}/grants").status_code == 200
            responses = [client.put(f"http://127.0.0.1:24001{grants}", json={"secret": secret}) for _ in range(3)]
            assert [response.status_code for response in responses] == [400, 400, 200]
            assert all(response.headers["x-v8-auth-stage"] == "pre_execution" for response in responses[:2])
            assert not flag_path(root, "source", "management").exists()
            client.post(f"http://127.0.0.1:24001{TICKETS}", json={"secret": secret})
        text = trace.read_text(encoding="utf-8")
        rows = [json.loads(line) for line in text.splitlines()]
        assert len(dispatched) == 9 and secret not in text
        assert all(set(row) <= {"at", "path", "method", "status", "code", "phase"} for row in rows)
        assert sum(row.get("phase") == "check-first" for row in rows) == 1
        assert sum(row.get("phase") == "check-management" for row in rows) == 2
    return {"status": "passed", "layer": "httpx_mock_transport_only", "exactTargetAndCounts": True,
            "subsequentDispatch": True, "secretFreeTrace": True, "physicalDeviceUsed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("command", choices=("resume", "arm-refresh", "arm-management", "self-check"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--node", choices=("source", "target1", "target2"), default="source")
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--phase")
    parser.add_argument("--device-id", help="Exact synthetic executor ID enrolled in the selected isolated fixture")
    args = parser.parse_args()
    if not args.live:
        parser.error("explicit --live required")
    if args.command == "self-check":
        print(json.dumps(self_check())); return
    if not args.root:
        parser.error("--root required")
    root = args.root.resolve()
    if args.command.startswith("arm-"):
        kind = args.command.removeprefix("arm-")
        try:
            arm(root, args.node, kind, args.phase or kind, args.device_id)
        except ValueError as exc:
            parser.error(str(exc))
        print(json.dumps({"armed": kind, "node": args.node, "count": 1 if kind == "refresh" else 2})); return
    import config_distribution_mobile_fixture as fixture
    client_type = traced_client(root, args.node, args.trace or root / "phone-fault-trace.jsonl")
    # The existing fixture retains CA, ports, state and the bounded process
    # lifetime. No additional thread, daemon, network owner or seed export.
    argv = [str(Path(fixture.__file__)), "--live", "--root", str(root), "--minutes", str(args.minutes), "resume"]
    with patch.object(fixture.httpx, "Client", client_type), patch.object(sys, "argv", argv):
        fixture.main()


if __name__ == "__main__":
    main()
