"""Session-unlock client acceptance; synthetic rejected input, no authentication."""
from __future__ import annotations

import json
import pathlib
import subprocess
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent
CLIENT = ROOT / "build" / "x64" / "v8-session-unlock.exe"
MARKER = "synthetic-secret-must-not-appear"


class ClientProtocolTests(unittest.TestCase):
    def invoke(self, args, data=b""):
        result = subprocess.run(
            [str(CLIENT), *args], input=data, capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self.assertNotIn(MARKER.encode(), result.stdout + result.stderr)
        self.assertEqual(result.stderr, b"")
        payload = json.loads(result.stdout)
        self.assertIsInstance(payload["sessionId"], int)
        self.assertIsInstance(payload["registered"], bool)
        self.assertIsInstance(payload["verified"], bool)
        return result.returncode, payload

    def fixture(self, **changes):
        value = dict(username="fixture-user", domain="fixture-domain", password=MARKER,
                     requestId="24363516-7eaa-4a3d-90a4-093b47abebf4",
                     expiresAt=int(time.time() * 1000) + 30000, sessionId=0xFFFFFFFF)
        value.update(changes)
        return json.dumps(value).encode()

    def assert_rejected(self, data, status):
        code, result = self.invoke(["--unlock"], data)
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], status)
        self.assertFalse(result["ok"])
        self.assertFalse(result["verified"])
        self.assertFalse(result["submitted"])

    def test_partial_json_cannot_execute(self):
        self.assert_rejected(self.fixture()[:-1], "invalid_request")

    def test_wrong_session_cannot_execute(self):
        self.assert_rejected(self.fixture(), "wrong_session")

    def test_expired_request_cannot_execute(self):
        self.assert_rejected(self.fixture(expiresAt=1), "expired_request")

    def test_distant_expiry_cannot_execute(self):
        self.assert_rejected(self.fixture(expiresAt=int(time.time() * 1000) + 120000), "expired_request")

    def test_unknown_fields_cannot_execute(self):
        self.assert_rejected(self.fixture(command="whoami"), "invalid_request")

    def test_bad_utf8_cannot_execute(self):
        self.assert_rejected(self.fixture().replace(b"fixture-user", b"\xff"), "invalid_request")

    def test_password_in_argv_is_rejected_without_echo(self):
        code, result = self.invoke(["--unlock", MARKER])
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "invalid_request")

    def test_status_is_observation_and_build_is_not_installation(self):
        _, result = self.invoke(["--status"])
        self.assertFalse(result["submitted"])
        self.assertEqual(result["providerInstalled"], result["registered"])
        if not result["registered"]:
            self.assertFalse(result["available"])
        if result["status"] == "already_unlocked":
            self.assertIs(result["locked"], False)
            self.assertTrue(result["verified"])
        elif result["status"] == "locked":
            self.assertIs(result["locked"], True)
            self.assertFalse(result["verified"])

    def test_installer_whatif_never_registers(self):
        before = self.invoke(["--status"])[1]["registered"]
        for action in ("Install", "Uninstall"):
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", str(ROOT / "install.ps1"),
                 "-Action", action, "-Unattended", "-WhatIf"], capture_output=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(self.invoke(["--status"])[1]["registered"], before)


if __name__ == "__main__":
    unittest.main()
