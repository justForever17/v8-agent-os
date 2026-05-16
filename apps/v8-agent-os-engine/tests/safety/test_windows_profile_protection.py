from __future__ import annotations

import unittest
from pathlib import Path

from erc.safety_guardian import safety_guardian


RUNTIME_CONTEXT = {"workspace_path": r"E:\Projects\v8chat\v8-agent-os", "runtime_kind": "chat"}


class WindowsProfileProtectionTests(unittest.TestCase):
    def setUp(self):
        safety_guardian._recent_downloads = []
        self.home = Path.home()

    def test_reg_profilelist_mutation_is_blocked(self):
        decision = safety_guardian.assess_system_command(
            r'reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\S-1-5-21-demo" /v ProfileImagePath /d C:\Users\TEMP /f',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_registry_mutation")

    def test_reg_profilelist_query_requires_review(self):
        decision = safety_guardian.assess_system_command(
            r'reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        self.assertEqual(decision.risk_code, "windows_profile_sensitive_read")

    def test_icacls_profile_hive_is_blocked_after_env_expansion(self):
        decision = safety_guardian.assess_system_command(
            r'icacls "%USERPROFILE%\NTUSER.DAT" /grant Everyone:F',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_acl_mutation")

    def test_takeown_usrclass_is_blocked(self):
        target = self.home / "AppData" / "Local" / "Microsoft" / "Windows" / "UsrClass.dat"
        decision = safety_guardian.assess_system_command(
            f'takeown /F "{target}"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_acl_mutation")

    def test_attrib_profile_hive_is_blocked(self):
        decision = safety_guardian.assess_system_command(
            r'attrib +h "%USERPROFILE%\NTUSER.DAT"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_acl_mutation")

    def test_reparse_profile_root_is_blocked(self):
        decision = safety_guardian.assess_system_command(
            f'mklink /J "{self.home}" "E:\\ShadowProfile"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_reparse_mutation")

    def test_fsutil_reparse_temp_profile_is_blocked(self):
        decision = safety_guardian.assess_system_command(
            r'fsutil reparsepoint delete "C:\Users\TEMP"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_reparse_mutation")

    def test_remove_profile_hive_is_blocked(self):
        decision = safety_guardian.assess_system_command(
            r'Remove-Item -LiteralPath "$env:USERPROFILE\NTUSER.DAT" -Force',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_hive_mutation")

    def test_profile_hive_backup_copy_requires_review(self):
        decision = safety_guardian.assess_system_command(
            f'copy "{self.home / "NTUSER.DAT"}" "E:\\Backups\\ntuser.dat.bak"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "review")
        self.assertEqual(decision.risk_code, "windows_profile_sensitive_read")

    def test_robocopy_mirror_to_temp_profile_is_blocked(self):
        decision = safety_guardian.assess_system_command(
            r'robocopy "E:\ProfileSeed" "C:\Users\TEMP" /MIR',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_destructive_copy")

    def test_regular_workspace_copy_is_allowed(self):
        decision = safety_guardian.assess_system_command(
            r'copy "E:\Projects\v8chat\v8-agent-os\README.md" "E:\Projects\v8chat\v8-agent-os\README.copy.md"',
            runtime_context=RUNTIME_CONTEXT,
        )
        self.assertEqual(decision.verdict, "allow")
        self.assertEqual(decision.risk_code, "command_allowed")

    def test_file_write_profile_hive_is_blocked(self):
        decision = safety_guardian.assess_file_write(
            str(self.home / "NTUSER.DAT"),
            append=False,
            runtime_context=RUNTIME_CONTEXT,
            content_preview="demo",
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_hive_mutation")

    def test_file_write_startup_script_is_blocked(self):
        target = self.home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "profile.ps1"
        decision = safety_guardian.assess_file_write(
            str(target),
            append=False,
            runtime_context=RUNTIME_CONTEXT,
            content_preview="Write-Host demo",
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_registry_mutation")

    def test_profile_reg_file_content_is_blocked_even_in_workspace(self):
        decision = safety_guardian.assess_file_write(
            r"E:\Projects\v8chat\v8-agent-os\profile.reg",
            append=False,
            runtime_context=RUNTIME_CONTEXT,
            content_preview=r'[HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\S-1-5-21-demo]',
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "windows_profile_registry_mutation")

    def test_regular_workspace_file_write_is_allowed(self):
        decision = safety_guardian.assess_file_write(
            r"E:\Projects\v8chat\v8-agent-os\docs\notes.md",
            append=False,
            runtime_context=RUNTIME_CONTEXT,
            content_preview="# notes",
        )
        self.assertEqual(decision.verdict, "allow")
        self.assertEqual(decision.risk_code, "workspace_file_write_allowed")

    def test_external_bash_profile_mutation_hard_stops(self):
        decision = safety_guardian.assess_external_tool_call(
            tool_name="Bash",
            params={"command": r'reg delete "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\S-1-5-21-demo" /f'},
            tool_kind="shell",
            side_effect="process_or_shell",
            runtime_context={"runtime_kind": "network_supervisor"},
        )
        self.assertEqual(decision.verdict, "block")
        self.assertEqual(decision.risk_code, "external_tool_local_system_hard_stop")

    def test_external_read_profile_hive_requires_review(self):
        decision = safety_guardian.assess_external_tool_call(
            tool_name="Read",
            params={"file_path": str(self.home / "NTUSER.DAT")},
            tool_kind="read",
            side_effect="none",
            runtime_context={"runtime_kind": "network_supervisor"},
        )
        self.assertEqual(decision.verdict, "review")
        self.assertEqual(decision.risk_code, "external_tool_local_system_review")

    def test_external_workspace_read_is_allowed(self):
        decision = safety_guardian.assess_external_tool_call(
            tool_name="Read",
            params={"file_path": r"E:\Projects\v8chat\v8-agent-os\README.md"},
            tool_kind="read",
            side_effect="none",
            runtime_context={"runtime_kind": "network_supervisor"},
        )
        self.assertEqual(decision.verdict, "allow")


if __name__ == "__main__":
    unittest.main()
