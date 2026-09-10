from io import BytesIO
import json

import pytest

from core.system_operations import process_client
from core.tools.native import command


@pytest.mark.parametrize("signal", ["pause", "cancel", "interrupt"])
def test_pending_control_signal_never_starts_privileged_helper(monkeypatch, signal):
    monkeypatch.setattr(command, "_peek_command_control_signal", lambda _: {"command": signal})
    monkeypatch.setattr(process_client.subprocess, "Popen", lambda *a, **k: pytest.fail("started after cancellation"))
    result = process_client.call_private_helper(["owned-helper"], {"password": "synthetic"}, timeout=5, context={})
    assert result["executed"] is False and not result["ok"]


def test_fast_completion_cannot_hide_cancel_arriving_during_execution(monkeypatch):
    signals = iter([None, {"command": "cancel"}])
    monkeypatch.setattr(command, "_peek_command_control_signal", lambda _: next(signals))
    class Completed:
        returncode = 0
        stdin = BytesIO()
        stdout = BytesIO()
        def communicate(self, **kwargs):
            return json.dumps({"ok": True, "executed": True, "verified": True}).encode(), None
        def poll(self):
            return 0
    monkeypatch.setattr(process_client.subprocess, "Popen", lambda *a, **k: Completed())
    result = process_client.call_private_helper(["owned-helper"], {}, timeout=5, context={})
    assert not result["ok"] and result["executed"] is True
    assert result["code"] == "system_operation_interrupted"


def test_cmd_tool_uses_typed_argv_until_native_serialization(monkeypatch, tmp_path):
    import importlib
    native = importlib.import_module("core.tools.native.system_operations")
    from core import workspace_capability
    captured = []
    monkeypatch.setattr(native, "get_runtime_context", lambda: {})
    monkeypatch.setattr(command, "_engineering_command_scope_block", lambda *a, **k: None)
    monkeypatch.setattr(workspace_capability, "preflight_command_workspace", lambda *a, **k: {"ok": True, "cwd": str(tmp_path)})
    monkeypatch.setattr(command, "_resolve_shell_dialect", lambda *a: "cmd")
    monkeypatch.setattr(command, "_shell_command_argv", lambda value, dialect: ["cmd.exe", "/d", "/s", "/c", value])
    monkeypatch.setattr(command, "_sandbox_launch", lambda context, argv: (argv, None))
    monkeypatch.setattr(native.shutil, "which", lambda _: str(tmp_path))
    monkeypatch.setattr(native.system_operation_service, "execute", lambda **kwargs: captured.append(kwargs["payload"]) or {"ok": True})
    source = 'echo "two words 中文"'
    native.system_operations.func(action="run_privileged", command=source, shell_dialect="cmd", tool_call_id="fixture")
    assert captured[0]["argv"][1:] == ["/d", "/s", "/c", source]
