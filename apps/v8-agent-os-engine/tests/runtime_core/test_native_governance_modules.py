from __future__ import annotations

import json
import sys


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

