from __future__ import annotations

import json
import sys

import pytest


def test_command_governance_detects_interactive_and_session_preferred_commands() -> None:
    from core.tools.native.command_governance import (
        _detect_interactive_command,
        _detect_session_preferred_command,
        _windows_shell_syntax_violation_payload,
    )

    assert _detect_interactive_command("python")
    assert _detect_interactive_command("python -c \"print(1)\"") is None
    assert _detect_interactive_command("python src/sandbox_live.py") is None
    assert _detect_interactive_command("python -u src/sandbox_live.py") is None
    assert _detect_interactive_command("py src/sandbox_live.py") is None
    assert _detect_session_preferred_command("npm install")
    assert _detect_session_preferred_command("python -m pip install pytest")
    assert _detect_session_preferred_command("uv add fastapi")
    assert _detect_session_preferred_command("poetry install")
    assert _detect_session_preferred_command("cargo build")
    assert _detect_session_preferred_command("go mod tidy")
    assert _detect_session_preferred_command("mvn package")
    assert _detect_session_preferred_command(".\\gradlew installDebug")

    payload = _windows_shell_syntax_violation_payload("mkdir -p foo")
    if sys.platform == "win32":
        assert payload
        assert payload["kind"] == "cross_shell_syntax_violation"
        assert "mkdir_-p" in payload["violations"]
        assert any("PowerShell" in item for item in payload["suggestedAlternatives"])
    else:
        assert payload is None


def test_powershell_format_placeholders_are_not_posix_brace_expansion(monkeypatch) -> None:
    import core.tools.native.command_governance as command_governance

    monkeypatch.setattr(command_governance.sys, "platform", "win32")
    payload = command_governance._windows_shell_syntax_violation_payload(
        'Write-Host ("{0,-32} {1,-10} {2}" -f "NAME", "VISIBILITY", "URL")',
        shell_dialect="powershell",
    )

    assert payload is None


@pytest.mark.parametrize("command", [
    '$data | ForEach-Object { "{0}={1}" -f $_.Name,$_.Count }',
    "$data | Where-Object {$_.category -notin @('work','study','life')}",
    "$data | ForEach-Object {$_.Name,$_.Count}",
    "& {1,2}",
    "Write-Output '{\"first\":1,\"second\":2}'",
])
def test_powershell_statistics_scriptblocks_are_not_posix_paths(monkeypatch, command):
    import core.tools.native.command_governance as governance

    monkeypatch.setattr(governance.sys, "platform", "win32")
    assert governance._windows_shell_syntax_violation_payload(command, shell_dialect="powershell") is None


@pytest.mark.parametrize("command", [
    r"mkdir -p src\{components,pages}",
    "Get-ChildItem src/{components,pages}/*.tsx",
    "cat {src,test}/index.js",
    "cat file{a,b}.txt",
    "cat src/{1,2}",
])
def test_unquoted_posix_brace_paths_still_require_matching_shell(monkeypatch, command):
    import core.tools.native.command_governance as governance

    monkeypatch.setattr(governance.sys, "platform", "win32")
    payload = governance._windows_shell_syntax_violation_payload(command, shell_dialect="powershell")
    assert payload and "brace_expansion" in payload["violations"]
    assert governance._windows_shell_syntax_violation_payload(command, shell_dialect="bash") is None


@pytest.mark.parametrize("command", [
    '$code = @"\npublic bool Check(bool a, bool b) { return a && b || false; }\n"@\n$code.Length',
    "$code = @'\nC# data with \"quotes\", && and ||\n'@\n$code.Length",
    "$data = @'\n$(Get-Item one && Get-Item two)\n'@\n$data.Length",
    'Write-Output "a && b || c"',
])
def test_powershell_data_strings_are_not_pipeline_chain_operators(monkeypatch, command):
    import core.tools.native.command_governance as governance

    monkeypatch.setattr(governance.sys, "platform", "win32")
    assert governance._windows_shell_syntax_violation_payload(command, shell_dialect="powershell") is None


@pytest.mark.parametrize("command", [
    '$data = @"\nliteral && here\n"@\nGet-Item one && Get-Item two',
    '$data = @"\n$(Get-Item one && Get-Item two)\n"@',
    'Write-Output "$(Get-Item one || Get-Item two)"',
])
def test_powershell_51_executable_chain_operators_remain_rejected(monkeypatch, command):
    import core.tools.native.command_governance as governance

    monkeypatch.setattr(governance.sys, "platform", "win32")
    payload = governance._windows_shell_syntax_violation_payload(command, shell_dialect="powershell")
    assert payload and "powershell_5_chain_operator" in payload["violations"]
    assert governance._windows_shell_syntax_violation_payload(command, shell_dialect="pwsh") is None


def test_workspace_governance_scoped_patch_line_range_and_anchor() -> None:
    from core.tools.native.workspace_governance import _apply_scoped_text_patch

    line_result = _apply_scoped_text_patch(original="a\nb\nc\n", replacement="B", line_start=2, line_end=2)
    assert line_result["ok"] is True
    assert line_result["newText"] == "a\nB\nc\n"
    assert line_result["proof"]["mode"] == "line_range"

    anchor_result = _apply_scoped_text_patch(original="alpha\nbeta\n", replacement="BETA\n", expected_old_text="beta\n")
    assert anchor_result["ok"] is True
    assert anchor_result["newText"] == "alpha\nBETA\n"
    assert anchor_result["proof"]["mode"] == "text_anchor"

    missing = _apply_scoped_text_patch(original="alpha\n", replacement="x", expected_old_text="missing")
    assert missing["ok"] is False
    assert missing["error"] == "patch_anchor_missing"


def test_desktop_governance_route_gate_required_and_runtime_mismatch() -> None:
    from core.tools.native.desktop_governance import _desktop_route_gate

    allowed, failure, route = _desktop_route_gate(state={"current_route_context": {}}, tool_name="computer_use_click_target")
    assert allowed is False
    assert route is None
    assert failure is not None
    assert json.loads(failure)["gateErrorCode"] == "ROUTE_GATE_REQUIRED"

    allowed, failure, route = _desktop_route_gate(
        state={
            "current_route_context": {
                "desktopRoute": {
                    "executionReadyMode": "reuse_mode",
                    "recommendedTool": "rpa_run_draft",
                }
            }
        },
        tool_name="computer_use_click_target",
    )
    assert allowed is False
    assert route is not None
    assert failure is not None
    assert json.loads(failure)["gateErrorCode"] == "RUNTIME_MISMATCH"

