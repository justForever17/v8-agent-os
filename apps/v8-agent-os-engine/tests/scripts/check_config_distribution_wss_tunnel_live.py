"""Opt-in isolated WSS echo transport checks; never Phone/native acceptance.

Reuse only the existing fixture's TLS certificate files. Start a loopback echo
origin and a new OS-assigned TLS port; no Engine, adb or existing fixture port
is contacted. Synthetic bearer and payloads remain in memory.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import inspect
import json
from pathlib import Path
import secrets
import ssl
import subprocess
import sys
import time

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

import config_distribution_mobile_fixture as fixture


SUBPROTOCOL = "v8.device-executor.v1"
REQUEST_PATH = "/api/executor/ws?fixture=transport"
IDLE_SECONDS = 31.5


def idle_return_mutant():
    """Compile the former idle-return behavior only in this test process."""
    tree = ast.parse(inspect.getsource(fixture.start_tls_gateway))
    changed = 0
    for node in ast.walk(tree):
        if (isinstance(node, ast.If) and isinstance(node.test, ast.UnaryOp)
                and isinstance(node.test.op, ast.Not) and isinstance(node.test.operand, ast.Name)
                and node.test.operand.id == "ready" and len(node.body) == 1
                and isinstance(node.body[0], ast.Continue)):
            node.body[0] = ast.copy_location(ast.Return(), node.body[0])
            changed += 1
    if changed != 1:
        raise AssertionError("idle_mutant_target_not_unique")
    namespace = dict(vars(fixture))
    exec(compile(ast.fix_missing_locations(tree), "<isolated-idle-return-mutant>", "exec"), namespace)
    return namespace["start_tls_gateway"]


async def check_transport(root: Path, hostname: str, *, mutant: bool = False):
    bearer = secrets.token_urlsafe(24)
    context = ssl.create_default_context(cafile=str(root / "fixture-ca-public.pem"))
    observed = {"bearerHeaderIntact": False, "requestPathIntact": False, "subprotocolIntact": False}
    failures = []

    async def upstream(ws):
        observed.update(
            bearerHeaderIntact=ws.request.headers.get("Authorization") == "Bearer " + bearer,
            requestPathIntact=ws.request.path == REQUEST_PATH,
            subprotocolIntact=ws.subprotocol == SUBPROTOCOL,
        )
        if not all(observed.values()):
            failures.append("handshake_changed")
            await ws.close(4403, "fixture_handshake_changed")
            return
        try:
            async for message in ws:
                if message == "close-revoked":
                    await ws.close(4401, "fixture_revoked")
                    return
                await ws.send(message)
        except ConnectionClosedError:
            # The idle-return mutant intentionally breaks its upstream socket.
            if not mutant:
                failures.append("upstream_abnormal_close")

    gateway_factory = idle_return_mutant() if mutant else fixture.start_tls_gateway
    result = {"scope": "isolated transport echo, not Phone/native acceptance"}
    async with serve(upstream, "127.0.0.1", 0, subprotocols=[SUBPROTOCOL],
                     max_size=2**21, ping_interval=None, compression=None) as origin:
        tunnel = gateway_factory(root, origin.sockets[0].getsockname()[1], 0)
        uri = f"wss://{hostname}:{tunnel.server_port}{REQUEST_PATH}"

        def connection():
            return connect(uri, ssl=context, proxy=None, host="127.0.0.1",
                           subprotocols=[SUBPROTOCOL], additional_headers={"Authorization": "Bearer " + bearer},
                           max_size=2**21, ping_interval=None, compression=None, close_timeout=3)

        try:
            if not mutant:
                async with connection() as ws:
                    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
                    assert ws.transport.get_extra_info("ssl_object").getpeercert()
                    assert ws.subprotocol == SUBPROTOCOL
                    result["tlsVerified"] = True
                    await ws.send("hello 中文")
                    assert await asyncio.wait_for(ws.recv(), 3) == "hello 中文", "unicode_changed"
                    result["unicodeTextIntact"] = True
                    payload = bytes(range(256)) * 4096
                    await ws.send(payload)
                    assert await asyncio.wait_for(ws.recv(), 3) == payload, "binary_changed"
                    result["oneMiBBinaryIntact"] = True
                    pong = await ws.ping()
                    await asyncio.wait_for(pong, 3)
                    result["pingPong"] = True
                    await ws.send("close-revoked")
                    try:
                        await asyncio.wait_for(ws.recv(), 3)
                        raise AssertionError("close_not_forwarded")
                    except ConnectionClosedError as exc:
                        assert exc.rcvd and exc.rcvd.code == 4401 and exc.rcvd.reason == "fixture_revoked"
                        result["closeCodeAndReasonIntact"] = True
                result.update(observed)

            # Both endpoints disable automatic ping. There is no data traffic
            # between the successful upgrade and the post-idle text frame.
            async with connection() as ws:
                assert ws.subprotocol == SUBPROTOCOL and all(observed.values())
                start = time.monotonic()
                await asyncio.sleep(IDLE_SECONDS)
                elapsed = time.monotonic() - start
                try:
                    await ws.send("after-idle 中文")
                    assert await asyncio.wait_for(ws.recv(), 3) == "after-idle 中文", "post_idle_text_changed"
                    await ws.close(1000, "idle-complete")
                    assert ws.close_code == 1000 and ws.close_reason == "idle-complete", "normal_close_changed"
                except ConnectionClosedError as exc:
                    if not mutant:
                        raise
                    assert exc.rcvd is None and ws.close_code == 1006, "mutant_failed_for_other_reason"
                    result.update(idleMutantRejected=True, observedCloseCode=1006,
                                  idleSeconds=round(elapsed, 3), automaticPingDisabled=True)
                else:
                    if mutant:
                        raise AssertionError("idle_return_mutant_survived")
                    result.update(idleOver30sThenText=True, normalClose1000=True,
                                  idleSeconds=round(elapsed, 3), automaticPingDisabled=True)
            assert not failures, ";".join(failures)
        finally:
            await asyncio.to_thread(tunnel.shutdown)
            tunnel.server_close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fixture-root", type=Path, required=True)
    parser.add_argument("--hostname", required=True, help="Existing leaf certificate SAN; connection stays on loopback")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-idle-mutant", action="store_true", help="Also run the former idle-return behavior in a child process")
    parser.add_argument("--idle-mutant-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.live:
        parser.error("explicit --live required")
    if args.check_idle_mutant and args.idle_mutant_child:
        parser.error("mutant child cannot spawn another comparison")
    root = args.fixture_root.resolve()
    for name in ("fixture-ca-public.pem", "fixture-server.pem", "fixture-server-key.private.pem"):
        if not (root / name).is_file():
            parser.error("existing isolated fixture TLS files required")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def bounded_check():
        async with asyncio.timeout(50):
            return await check_transport(root, args.hostname, mutant=args.idle_mutant_child)

    try:
        result = asyncio.run(bounded_check())
        if args.check_idle_mutant:
            # No synthetic credential appears in the child command or report.
            child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--live",
                                    "--fixture-root", str(root), "--hostname", args.hostname,
                                    "--idle-mutant-child"], capture_output=True, text=True, timeout=60)
            if child.returncode:
                raise AssertionError("idle_mutant_child_failed")
            result["formerIdleReturn"] = json.loads(child.stdout)
            assert result["formerIdleReturn"].get("idleMutantRejected"), "mutant_was_not_rejected"
        result["status"] = "passed"
    except Exception as exc:
        # Report the failure class only; request headers and payloads stay private.
        result = {"status": "failed", "errorType": type(exc).__name__,
                  "scope": "isolated transport echo, not Phone/native acceptance"}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
