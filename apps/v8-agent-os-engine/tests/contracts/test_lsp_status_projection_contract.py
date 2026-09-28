"""Contract tests for LSP status transitions and projection."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from runtimes.engineering.lsp.lsp_protocol import LSPStatus
from runtimes.engineering.lsp.lsp_probe_runner import LSPProbeRunner, LSPStatusRegistry


def test_canonical_language_mapping():
    registry = LSPStatusRegistry()
    assert registry.canonical_language("TypeScript") == "ts"
    assert registry.canonical_language("TSX") == "ts"
    assert registry.canonical_language("javascript") == "ts"
    assert registry.canonical_language("python") == "py"
    assert registry.canonical_language("Python") == "py"
    assert registry.canonical_language("rust") == "rust"
    assert registry.canonical_language("rs") == "rust"
    assert registry.canonical_language("golang") == "go"
    assert registry.canonical_language("go") == "go"


def test_status_registry_transitions_and_subscription():
    registry = LSPStatusRegistry()
    events = []

    def on_event(lang, status):
        events.append((lang, status))

    registry.subscribe(on_event)

    assert registry.get_status("ts") == LSPStatus.UNINSTALLED

    registry.set_status("ts", LSPStatus.INITIALIZING)
    assert registry.get_status("ts") == LSPStatus.INITIALIZING
    assert events == [("ts", LSPStatus.INITIALIZING)]

    registry.set_status("typescript", LSPStatus.ACTIVE)
    assert registry.get_status("ts") == LSPStatus.ACTIVE
    assert events[-1] == ("ts", LSPStatus.ACTIVE)

    summary = registry.get_summary()
    assert summary["ts"] == "active"
    assert summary["py"] == "uninstalled"
    assert "rust" in summary
    assert "go" in summary

    # Unsubscribe test
    registry.unsubscribe(on_event)
    registry.set_status("ts", LSPStatus.IDLE)
    assert len(events) == 2  # No new event received


def test_probe_workspace_local_discovery():
    probe = LSPProbeRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir)
        # Create a mock local typescript-language-server
        bin_dir = workspace / "node_modules" / ".bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        is_win = os.name == "nt"
        ts_bin = bin_dir / ("typescript-language-server.cmd" if is_win else "typescript-language-server")
        ts_bin.write_text("mock binary", encoding="utf-8")

        results = probe.probe_workspace(str(workspace))
        assert results["ts"]["available"] is True
        assert results["ts"]["kind"] == "local_workspace"
        assert probe.registry.get_status("ts") == LSPStatus.IDLE

        # Languages with no local toolchain should report uninstalled or host
        with patch("shutil.which", return_value=None):
            results_no_host = probe.probe_workspace(str(workspace))
            assert results_no_host["py"]["available"] is False
            assert results_no_host["py"]["kind"] == "missing"
            assert probe.registry.get_status("py") == LSPStatus.UNINSTALLED


def test_lsp_traffic_light_indicator():
    registry = LSPStatusRegistry()
    assert registry.get_indicator() == "⚪ ts  ⚪ py  ⚪ rust  ⚪ go"
    registry.set_status("ts", LSPStatus.ACTIVE)
    registry.set_status("py", LSPStatus.INITIALIZING)
    registry.set_status("rust", LSPStatus.ERROR)
    assert registry.get_indicator() == "🟢 ts  🟡 py  🔴 rust  ⚪ go"


def test_engineering_lsp_api_routes():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.engineering_routes import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. GET /engineering-lane/lsp/status
    res = client.get("/engineering-lane/lsp/status")
    assert res.status_code == 200
    data = res.json()
    assert "statuses" in data
    assert "indicator" in data
    assert "ts" in data["statuses"]

    # 2. GET /engineering-lane/lsp/probe
    res_probe = client.get("/engineering-lane/lsp/probe")
    assert res_probe.status_code == 200
    probe_data = res_probe.json()
    assert "probe" in probe_data
    assert "indicator" in probe_data
    assert "ts" in probe_data["probe"]

