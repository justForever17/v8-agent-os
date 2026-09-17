"""Verify server dependency/import closure in a disposable state root (no provider calls)."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FORBIDDEN = {"chromadb", "numpy", "pillow", "edge-tts", "yt-dlp", "torch", "onnxruntime", "mediapipe", "robotframework", "rpaframework", "mss", "aiortc", "av", "soundcard", "pymupdf", "python-docx", "openpyxl"}
FAMILIES = {"chat", "memory", "extensions", "automation", "network_supervisor", "engineering", "research", "plugin_manager"}


def verify(bundle: Path, browser: bool) -> dict:
    engine = bundle / "apps/v8-agent-os-engine"
    installed = {d.metadata["Name"].lower().replace("_", "-"): d.version for d in importlib.metadata.distributions()}
    unexpected = FORBIDDEN.intersection(installed)
    if unexpected:
        raise RuntimeError(f"Server production environment contains optional dependencies: {sorted(unexpected)}")
    sys.path.insert(0, str(engine))
    with tempfile.TemporaryDirectory(prefix="v8os-server-verify-") as state:
        os.environ.update(V8_AGENT_OS_HOME=state, ENGINE_INSTALL_PROFILE="server", ENGINE_STARTUP_PROFILE="server", V8_AGENT_OS_DISABLE_BYTECODE="1")
        import main
        from core.native_tools import NATIVE_TOOLS
        from core.runtime.startup_profile import get_runtime_registry_state
        from core.runtime_tool_access import runtime_tool_available
        from core.memory_backend_health import inspect_memory_backend
        from agents.runners.supervisor_runner import supervisor_runner
        registry = get_runtime_registry_state()
        assert set(registry["installedRuntimeFamilies"]) == FAMILIES, registry["installedRuntimeFamilies"]
        assert inspect_memory_backend()["mode"] == "sqlite_fts5"
        names = {tool.name for tool in NATIVE_TOOLS if runtime_tool_available(tool.name)}
        assert {"browser_broker", "web_broker", "delegation_broker", "agent_broker", "run_system_command"} <= names
        assert not any(name.startswith(("computer_use_", "rpa_", "creative_media_")) for name in names)
        assert not {"chromadb", "PIL", "edge_tts", "runtimes.creative_media.runtime", "runtimes.computer_use.runtime", "runtimes.rpa.runtime"}.intersection(sys.modules)
        result = {"profile": registry["installProfile"], "families": sorted(FAMILIES), "packages": len(installed), "tools": len(names), "memory": "sqlite_fts5", "supervisorImport": bool(supervisor_runner)}
        if browser:
            if os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"):
                raise RuntimeError("Browser acceptance requires DISPLAY and WAYLAND_DISPLAY unset")
            from core.agent_browser_automation import agent_browser_automation
            class Page(BaseHTTPRequestHandler):
                def do_GET(self):
                    content = b'<html><body><main id="result">before</main><script>document.querySelector("main").textContent="SERVER_BROWSER_JS_PROOF"</script></body></html>'
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(content)
                def log_message(self, *_args):
                    pass
            http = ThreadingHTTPServer(("127.0.0.1", 0), Page)
            worker = threading.Thread(target=http.serve_forever, daemon=True)
            worker.start()
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            from core.storage import storage
            config = dict(storage.get_computer_use_config() or {})
            config["browserLane"] = {**config.get("browserLane", {}), "proxyPort": port - 100, "userDataDir": str(Path(state) / "browser")}
            agent_browser_automation.configure(config)
            try:
                started = agent_browser_automation.ensure_agent_browser_background(browser_kind="chromium")
                if not started.get("ok"):
                    raise RuntimeError(f"Headless browser unavailable: {started}. Check Chromium system libraries (python -m playwright install-deps chromium), sandbox/user-namespace permission and debug-port ownership; do not disable Chromium's sandbox.")
                page = agent_browser_automation.read_profile_page(url=f"http://127.0.0.1:{http.server_port}/", timeout_seconds=20)
                assert "SERVER_BROWSER_JS_PROOF" in json.dumps(page), page
                result["browser"] = {"headless": started.get("managedHeadless"), "javascript": True, "owner": "core.agent_browser_automation"}
            finally:
                agent_browser_automation.shutdown()
                http.shutdown()
                http.server_close()
                worker.join(timeout=5)
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle.resolve(), args.browser), indent=2))
