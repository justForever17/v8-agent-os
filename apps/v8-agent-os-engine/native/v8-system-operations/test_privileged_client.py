"""Privileged client protocol/dry-run; no service, password auth or elevation."""
import json
from pathlib import Path
import subprocess
import time
import unittest

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "build/x64/v8-system-operations.exe"
SECRET = "synthetic-credential-never-echo"


class ClientTests(unittest.TestCase):
    def call(self, args, data=b""):
        process = subprocess.run([str(EXE), *args], input=data, capture_output=True,
                                 timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotIn(SECRET.encode(), process.stdout + process.stderr)
        self.assertEqual(process.stderr, b"")
        return process.returncode, json.loads(process.stdout)

    def fixture(self, **changes):
        value = dict(action="run_privileged", command="fixture",
                     argv=["C:/Windows/System32/whoami.exe", "/groups"], cwd="C:/Windows",
                     timeoutSeconds=5, username="fixture-never-authenticated", domain=".", password=SECRET,
                     requestId="24363516-7eaa-4a3d-90a4-093b47abebf4", expiresAt=int(time.time() * 1000) + 30000)
        value.update(changes)
        return json.dumps(value).encode()

    def reject(self, payload, code):
        status, response = self.call(["--execute"], payload)
        self.assertEqual(status, 2)
        self.assertEqual(response["code"], code)
        self.assertFalse(response["executed"])
        self.assertFalse(response["elevated"])
        self.assertFalse(response["verified"])

    def test_expired(self):
        self.reject(self.fixture(expiresAt=1), "expired_request")

    def test_remote_account(self):
        self.reject(self.fixture(domain="\\\\remote"), "local_account_required")

    def test_service_account(self):
        self.reject(self.fixture(username="NT AUTHORITY\\SYSTEM"), "local_account_required")

    def test_bare_executable(self):
        self.reject(self.fixture(argv=["whoami.exe"]), "absolute_local_paths_required")

    def test_relative_cwd(self):
        self.reject(self.fixture(cwd="."), "absolute_local_paths_required")

    def test_no_permissive_json_repair(self):
        self.reject(self.fixture()[:-1], "invalid_request")

    def test_empty_argv(self):
        self.reject(self.fixture(argv=[]), "invalid_argv")

    def test_timeout(self):
        self.reject(self.fixture(timeoutSeconds=601), "invalid_timeout")

    def test_no_secret_arguments(self):
        code, result = self.call(["--execute", SECRET])
        self.assertEqual(code, 2)
        self.assertFalse(result["executed"])

    def test_status_does_not_confuse_build_with_install(self):
        _, result = self.call(["--status"])
        self.assertIsInstance(result["registered"], bool)
        self.assertFalse(result["verified"])
        if not result["registered"]:
            self.assertFalse(result["available"])

    def test_installer_whatif_does_not_install_or_elevate(self):
        before = self.call(["--status"])[1]["registered"]
        for action in ("Install", "Uninstall"):
            process = subprocess.run(["powershell.exe", "-NoProfile", "-File", str(ROOT / "install.ps1"),
                                      "-Action", action, "-Unattended", "-WhatIf"], capture_output=True, timeout=10,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(process.returncode, 0, process.stderr.decode(errors="replace"))
        self.assertEqual(self.call(["--status"])[1]["registered"], before)


if __name__ == "__main__":
    unittest.main()
