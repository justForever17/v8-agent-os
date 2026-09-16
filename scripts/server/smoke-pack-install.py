"""Explicit local integration: real Engine + existing pack installer, isolated state."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def free_port():
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
        return stream.getsockname()[1]


def audit(bundle: Path) -> dict:
    engine = bundle / "apps/v8-agent-os-engine"
    sys.path.insert(0, str(engine))
    with tempfile.TemporaryDirectory(prefix="v8-server-packs-") as temp:
        state = Path(temp) / "state"
        keys = Path(temp) / "keys"
        keys.mkdir(mode=0o700)
        port, phone_port = free_port(), free_port()
        os.environ.update(V8_AGENT_OS_HOME=str(state), V8_AGENT_OS_CREDENTIAL_KEY_FILE=str(keys / "key"), ENGINE_INSTALL_PROFILE="server", ENGINE_PORT=str(port), ENGINE_HOST="127.0.0.1", V8_AGENT_OS_DISABLE_BYTECODE="1")
        from core.security.server_credentials import LinuxServerCredentialBackend
        LinuxServerCredentialBackend.initialize_key(state_root=state)
        from core.storage import storage
        config = storage.get_system_base_config()
        config["bridge"]["engineBaseUrl"] = f"http://127.0.0.1:{port}"
        config["remoteLink"]["phoneGateway"] = {"enabled": True, "port": phone_port}
        storage.save_system_base_config(config)
        log = open(Path(temp) / "engine.log", "w+")
        child = None
        def start():
            process = subprocess.Popen([str(engine / ".venv/bin/python3"), "main.py"], cwd=engine, stdout=log, stderr=log)
            deadline = time.monotonic() + 100
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Engine exited before ready ({process.returncode})")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                        health = json.load(response)
                    if health.get("ready") is True or health.get("status") == "ok":
                        return process, health
                except (OSError, ValueError):
                    time.sleep(0.2)
            process.terminate()
            process.wait(timeout=45)
            raise TimeoutError("Engine readiness timed out")
        def cli(*args, check=True):
            result = subprocess.run([str(bundle / "v8os"), *args], capture_output=True, text=True, timeout=240)
            if check and result.returncode:
                raise RuntimeError(f"CLI {' '.join(args[:3])} failed: {result.stderr[-1200:]}")
            return result
        try:
            child, _ = start()
            listing = json.loads(cli("packs", "list").stdout)
            assert listing["engineAvailable"]
            assert next(p for p in listing["packs"] if p["id"] == "cloud_voice")["status"] == "not_installed"
            refused = cli("packs", "install", "computer_use_desktop", "--dry-run", check=False)
            assert refused.returncode != 0
            installed = json.loads(cli("packs", "install", "cloud_voice").stdout)
            assert installed["status"] == "installed"
            listing = json.loads(cli("packs", "list").stdout)
            voice = next(p for p in listing["packs"] if p["id"] == "cloud_voice")
            assert voice["installed"] and voice["restartRequired"]
            child.terminate()
            child.wait(timeout=45)
            child, health = start()
            listing = json.loads(cli("packs", "list").stdout)
            voice = next(p for p in listing["packs"] if p["id"] == "cloud_voice")
            assert voice["installed"] and not voice["restartRequired"], voice["status"]
            assert health["startupBundle"]["audio"] is True
            requirements = engine / "requirements/feature-packs/cloud-voice.txt"
            original = requirements.read_bytes()
            try:
                requirements.write_bytes(b"invalid requirement @@@\n")
                failed_upgrade = cli("packs", "install", "cloud_voice", check=False)
                assert failed_upgrade.returncode != 0, "Failed upgrade must not report the previous installed pack as success"
            finally:
                requirements.write_bytes(original)
            return {"layer": "real_engine_local_install_no_provider", "install": True, "restartActivation": True, "failedUpgradeNonzero": True, "desktopRejected": True, "profile": health["installProfile"], "portsIsolated": True}
        finally:
            if child and child.poll() is None:
                child.terminate()
                child.wait(timeout=45)
            log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required: starts an isolated Engine and downloads cloud_voice dependencies")
    print(json.dumps(audit(args.bundle.resolve()), indent=2))
