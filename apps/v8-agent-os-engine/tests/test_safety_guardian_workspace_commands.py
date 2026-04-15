from __future__ import annotations

import sys
import unittest
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from erc.safety_guardian import safety_guardian


WORKSPACE_PATH = r"C:\Users\sunny\.v8-agent-os\workspace"
RUNTIME_CONTEXT = {"workspace_path": WORKSPACE_PATH, "runtime_kind": "chat"}


class SafetyGuardianWorkspaceCommandTests(unittest.TestCase):
    def test_workspace_dir_search_is_allowed(self):
        decision = safety_guardian.assess_system_command(
            r'dir "C:\Users\sunny\.v8-agent-os\workspace" *.mp4 /s /b',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "allow")
        self.assertEqual(decision.risk_code, "workspace_read_allowed")

    def test_workspace_get_child_item_is_allowed(self):
        decision = safety_guardian.assess_system_command(
            r'Get-ChildItem -Path "C:\Users\sunny\.v8-agent-os\workspace" -Recurse -Filter *.mp4',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "allow")
        self.assertEqual(decision.risk_code, "workspace_read_allowed")

    def test_sensitive_system_directory_read_is_reviewed(self):
        decision = safety_guardian.assess_system_command(
            r'dir "C:\Windows\System32" /b',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        self.assertEqual(decision.risk_code, "sensitive_system_read_command")

    def test_protected_process_is_blocked(self):
        decision = safety_guardian.assess_system_command(
            "taskkill /IM v8-agent-os.exe /F",
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "protected_process_command")

    def test_generic_process_control_is_reviewed(self):
        decision = safety_guardian.assess_system_command(
            "Stop-Process -Name python -Force",
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        self.assertEqual(decision.risk_code, "process_control_command")


if __name__ == "__main__":
    unittest.main()
