"""Engine-owned Phone listener and private Linux local management socket."""
from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import stat
import sys
from contextlib import nullcontext
from pathlib import Path

import uvicorn


def _local_management_directory(home: Path) -> Path:
    home = home.resolve()
    directory = home / "runtime" / "client-control"
    # Linux sockaddr_un.sun_path has 108 bytes including the terminating NUL.
    # Keep the normal path; long state roots use a stable, per-user private path.
    # TMPDIR/XDG_RUNTIME_DIR can themselves be too long, so they are not a fallback.
    if len(os.fsencode(directory / "engine.sock")) <= 107:
        return directory
    identity = hashlib.sha256(os.fsencode(home)).hexdigest()[:32]
    return Path("/tmp") / f"v8os-{os.geteuid()}-{identity}"


class _OwnedServer(uvicorn.Server):
    def capture_signals(self):
        # The parent Engine owns SIGINT/SIGTERM and the shutdown sequence.
        return nullcontext()


class LocalManagementApp:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in {"http", "websocket"}:
            scope = {**scope, "state": {**scope.get("state", {}), "local_management": True}}
        await self.app(scope, receive, send)


class ClientListeners:
    def __init__(self, app, home: Path, config: dict):
        self.app, self.home, self.config = app, home, config
        self.phone = None
        self.local_server = None
        self.local_task = None
        self.local_socket = None
        self.local_path = None
        self.local_identity = None

    async def start(self):
        remote = self.config.get("remoteLink") or {}
        gateway = remote.get("phoneGateway") or {}
        if remote.get("enabled", True) and gateway.get("enabled", True):
            from core.remote_link.phone_gateway import PhoneGatewayConfig, PhoneGatewayServer
            config = PhoneGatewayConfig(listen_port=int(gateway.get("port") or 9532))
            self.phone = PhoneGatewayServer(config, app=self.app)
            await self.phone.start()
        if sys.platform == "linux":
            try:
                await self._start_local()
            except BaseException:
                await self.stop()
                raise
        return self.status()

    async def _start_local(self):
        directory = _local_management_directory(self.home)
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        details = directory.lstat()
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o700:
            raise RuntimeError("local_management_directory_permissions_invalid")
        target = directory / "engine.sock"
        if target.exists() or target.is_symlink():
            details = target.lstat()
            if not stat.S_ISSOCK(details.st_mode) or details.st_uid != os.geteuid():
                raise RuntimeError("local_management_socket_path_conflict")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.settimeout(0.2)
                probe.connect(str(target))
            except ConnectionRefusedError:
                current = target.lstat()
                if (current.st_dev, current.st_ino) != (details.st_dev, details.st_ino):
                    raise RuntimeError("local_management_socket_path_changed")
                target.unlink()  # Owned, provably stale socket only.
            else:
                raise RuntimeError("local_management_socket_already_running")
            finally:
                probe.close()
        local_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            local_socket.bind(str(target))
            bound = target.lstat()
            self.local_socket, self.local_path = local_socket, target
            self.local_identity = (bound.st_dev, bound.st_ino)
            # Parent was private before bind; no other uid can race the chmod.
            target.chmod(0o600)
            local_socket.listen(128)
            local_socket.setblocking(False)
            self.local_server = _OwnedServer(uvicorn.Config(LocalManagementApp(self.app), lifespan="off", access_log=False, log_level="warning"))
            self.local_task = asyncio.create_task(self.local_server.serve(sockets=[local_socket]), name="client-local-socket")
            deadline = asyncio.get_running_loop().time() + 5
            while not self.local_server.started:
                if self.local_task.done():
                    await self.local_task
                    raise RuntimeError("local_management_start_failed")
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("local_management_start_timeout")
                await asyncio.sleep(.01)
        except BaseException:
            local_socket.close()
            self._unlink_owned_socket()
            raise

    def _unlink_owned_socket(self):
        if not self.local_path or self.local_identity is None:
            return
        try:
            current = self.local_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(current.st_mode) and (current.st_dev, current.st_ino) == self.local_identity:
            self.local_path.unlink()

    async def stop(self):
        if self.local_server:
            self.local_server.should_exit = True
        if self.local_task:
            try:
                await asyncio.wait_for(asyncio.shield(self.local_task), timeout=5)
            except TimeoutError:
                self.local_task.cancel()
                await asyncio.gather(self.local_task, return_exceptions=True)
        if self.local_socket:
            self.local_socket.close()
        self._unlink_owned_socket()
        if self.phone:
            await self.phone.stop()
        self.local_server = self.local_task = self.local_socket = self.local_path = self.phone = None
        self.local_identity = None

    def status(self):
        return {"phone": self.phone.status() if self.phone else {"state": "disabled", "upstreamKind": "engine_in_process"},
                "localManagement": {"transport": "unix" if sys.platform == "linux" else "loopback_service_credential",
                                    "ready": bool(self.local_server and self.local_server.started) if sys.platform == "linux" else True}}
