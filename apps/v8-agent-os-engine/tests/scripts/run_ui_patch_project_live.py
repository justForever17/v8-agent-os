from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[1]
sys.path.insert(0, str(ENGINE_ROOT))


PROJECT_SOURCE_SETTLE_SECONDS = 0.9


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _write_project(root: Path, port: int) -> None:
    vite_root = REPO_ROOT / "apps" / "v8-agent-os-desktop-pet" / "node_modules" / "vite"
    react_plugin_root = REPO_ROOT / "apps" / "v8-agent-os-desktop-pet" / "node_modules" / "@vitejs" / "plugin-react"
    react_root = REPO_ROOT / "apps" / "v8-agent-os-web" / "node_modules" / "react"
    react_dom_root = REPO_ROOT / "apps" / "v8-agent-os-web" / "node_modules" / "react-dom"
    required = [vite_root, react_plugin_root, react_root, react_dom_root]
    if not all(path.exists() for path in required):
        raise RuntimeError("Local Vite/React dependencies are unavailable; run workspace dependency install first")

    (root / "src").mkdir(parents=True)
    package = {
        "name": "v8-ui-patch-live-fixture",
        "private": True,
        "scripts": {"dev": f"vite --host 127.0.0.1 --port {port} --strictPort"},
        "dependencies": {"react": "19.2.7", "react-dom": "19.2.7"},
        "devDependencies": {"@vitejs/plugin-react": "5.2.0", "vite": "6.4.3"},
    }
    (root / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    (root / "package-lock.json").write_text("{}\n", encoding="utf-8")
    (root / "index.html").write_text(
        '<!doctype html><html><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>\n',
        encoding="utf-8",
    )
    config = fr'''import {{ defineConfig }} from {json.dumps((vite_root / "dist" / "node" / "index.js").as_uri())};
import react from {json.dumps((react_plugin_root / "dist" / "index.js").as_uri())};

export default defineConfig({{
  plugins: [react()],
  resolve: {{
    alias: [
      {{ find: /^react$/, replacement: {json.dumps(str(react_root / "index.js"))} }},
      {{ find: /^react\/(.+)$/, replacement: {json.dumps(str(react_root).replace('\\', '/') + '/$1.js')} }},
      {{ find: /^react-dom$/, replacement: {json.dumps(str(react_dom_root / "index.js"))} }},
      {{ find: /^react-dom\/(.+)$/, replacement: {json.dumps(str(react_dom_root).replace('\\', '/') + '/$1.js')} }},
    ],
  }},
}});
'''
    (root / "vite.config.mjs").write_text(config, encoding="utf-8")
    (root / "src" / "main.tsx").write_text(
        'import React from "react";\nimport { createRoot } from "react-dom/client";\nimport { App } from "./App";\ncreateRoot(document.getElementById("root")!).render(<App />);\n',
        encoding="utf-8",
    )
    (root / "src" / "App.tsx").write_text(
        'export function App() {\n  return <button className="card" style={{ width: "120px", padding: "8px" }}>Original</button>;\n}\n',
        encoding="utf-8",
    )


def _wait_stopped(terminal_broker, terminal_id: str, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = terminal_broker.read_terminal_session(terminal_id)
        if snapshot.get("isRunning") is False:
            return True
        time.sleep(0.1)
    return False


def run_live() -> dict[str, object]:
    from playwright.sync_api import sync_playwright

    with tempfile.TemporaryDirectory(prefix="v8-ui-patch-live-") as temporary:
        temporary_root = Path(temporary).resolve()
        workspace = temporary_root / "workspace"
        state_root = temporary_root / "state"
        workspace.mkdir()
        state_root.mkdir()
        original_v8_home = os.environ.get("V8_AGENT_OS_HOME")
        os.environ["V8_AGENT_OS_HOME"] = str(state_root)
        from core import client_terminal_broker, ui_patch, workbench_files
        from core.v8_agent_os_paths import V8_AGENT_OS_HOME

        if V8_AGENT_OS_HOME.resolve(strict=False) != state_root:
            raise RuntimeError("Live harness failed to isolate V8_AGENT_OS_HOME before Engine import")

        port = _free_port()
        _write_project(workspace, port)
        authority = SimpleNamespace(
            workspace_root=str(workspace),
            workspace_id="ui-patch-live-workspace",
            project_id="ui-patch-live-project",
            side_effects_allowed=True,
        )
        original_ui_authority = ui_patch.workspace_authority_service.resolve
        original_file_authority = workbench_files.workspace_authority_service.resolve
        original_binding = client_terminal_broker.build_workspace_binding
        original_path = os.environ.get("PATH", "")
        bin_dir = REPO_ROOT / "apps" / "v8-agent-os-desktop-pet" / "node_modules" / ".bin"
        os.environ["PATH"] = str(bin_dir) + os.pathsep + original_path
        ui_patch.workspace_authority_service.resolve = lambda **_: authority
        workbench_files.workspace_authority_service.resolve = lambda **_: authority
        client_terminal_broker.build_workspace_binding = lambda *_args, **_kwargs: SimpleNamespace(
            side_effects_allowed=True,
            active_workspace_root=str(workspace),
        )

        service = ui_patch.UiPatchService()
        service._runtime_root = workspace / ".runtime" / "ui-patch"
        service._transactions_root = service._runtime_root / "transactions"
        preview: dict[str, object] | None = None
        browser = None
        try:
            preview = service.create_preview(
                session_id="ui-patch-live-session",
                parent_origin="http://127.0.0.1:9527",
                project_path=".",
                start_dev_server=True,
            )
            terminal_id = str(preview.get("devSessionId") or "")
            if not terminal_id:
                raise RuntimeError("Project preview did not expose its governed dev terminal")

            with sync_playwright() as playwright:
                edge = Path(os.environ.get("V8_EDGE_PATH") or "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
                browser = playwright.chromium.launch(
                    executable_path=str(edge) if edge.is_file() else None,
                    headless=True,
                    args=["--no-proxy-server"],
                )
                page = browser.new_page()
                page.goto(str(preview["previewUrl"]), wait_until="domcontentloaded", timeout=30_000)
                page.locator(".card").wait_for(state="visible", timeout=20_000)
                assert page.locator(".card").inner_text() == "Original"
                initial_width = page.locator(".card").evaluate("element => getComputedStyle(element).width")

                mapped = service.map_selection(
                    session_id="ui-patch-live-session",
                    patch_session_id=str(preview["patchSessionId"]),
                    selection={
                        "selector": ".card",
                        "tagName": "button",
                        "textContent": "Original",
                        "inlineStyle": {"width": "120px", "padding": "8px"},
                        "computedStyles": {"width": initial_width},
                        "rules": [],
                    },
                )
                style_candidate = next(item for item in mapped["sourceCandidates"] if item["sourceKind"] == "react_inline_style")
                style_commit = service.commit(
                    session_id="ui-patch-live-session",
                    patch_session_id=str(preview["patchSessionId"]),
                    selection_ref=str(mapped["selectionRef"]),
                    candidate_id=str(style_candidate["candidateId"]),
                    changes={"width": "168px"},
                )
                page.wait_for_function("getComputedStyle(document.querySelector('.card')).width === '168px'", timeout=20_000)
                time.sleep(PROJECT_SOURCE_SETTLE_SECONDS)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                page.locator(".card").wait_for(state="visible", timeout=20_000)

                mapped = service.map_selection(
                    session_id="ui-patch-live-session",
                    patch_session_id=str(preview["patchSessionId"]),
                    selection={
                        "selector": ".card",
                        "tagName": "button",
                        "textContent": "Original",
                        "inlineStyle": {"width": "168px", "padding": "8px"},
                        "computedStyles": {"width": "168px"},
                        "rules": [],
                    },
                )
                text_candidate = next(item for item in mapped["sourceCandidates"] if item["sourceKind"] == "component_text")
                text_commit = service.commit(
                    session_id="ui-patch-live-session",
                    patch_session_id=str(preview["patchSessionId"]),
                    selection_ref=str(mapped["selectionRef"]),
                    candidate_id=str(text_candidate["candidateId"]),
                    changes={"__text_content": "Updated"},
                )
                time.sleep(PROJECT_SOURCE_SETTLE_SECONDS)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                assert page.locator(".card").inner_text() == "Updated"

                service.undo(session_id="ui-patch-live-session", transaction_id=str(text_commit["transactionId"]))
                time.sleep(PROJECT_SOURCE_SETTLE_SECONDS)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                assert page.locator(".card").inner_text() == "Original"
                service.undo(session_id="ui-patch-live-session", transaction_id=str(style_commit["transactionId"]))
                time.sleep(PROJECT_SOURCE_SETTLE_SECONDS)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                restored_width = page.locator(".card").evaluate("element => getComputedStyle(element).width")
                assert restored_width == "120px"
                browser.close()
                browser = None

            service.close_preview(
                session_id="ui-patch-live-session",
                patch_session_id=str(preview["patchSessionId"]),
            )
            dev_stopped = _wait_stopped(client_terminal_broker, terminal_id)
            try:
                service.get_preview(
                    session_id="ui-patch-live-session",
                    patch_session_id=str(preview["patchSessionId"]),
                )
                proxy_stopped = False
            except LookupError:
                proxy_stopped = True
            return {
                "ok": True,
                "mode": preview.get("mode"),
                "framework": preview.get("framework"),
                "devCommand": preview.get("devCommand"),
                "initialWidth": initial_width,
                "styleHmrObserved": True,
                "textCommitReloadObserved": True,
                "undoReloadObserved": True,
                "stateRootIsolated": True,
                "devSessionStopped": dev_stopped,
                "proxyStopped": proxy_stopped,
            }
        finally:
            if browser is not None:
                try:
                    browser.close()
                except Exception:
                    pass
            service.shutdown()
            ui_patch.workspace_authority_service.resolve = original_ui_authority
            workbench_files.workspace_authority_service.resolve = original_file_authority
            client_terminal_broker.build_workspace_binding = original_binding
            os.environ["PATH"] = original_path
            if original_v8_home is None:
                os.environ.pop("V8_AGENT_OS_HOME", None)
            else:
                os.environ["V8_AGENT_OS_HOME"] = original_v8_home


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real local Vite/React UI Patch project acceptance harness")
    parser.add_argument("--live", action="store_true", help="Explicitly start a local Vite server and Edge browser")
    args = parser.parse_args()
    if not args.live:
        print("Refusing to start project processes without --live", file=sys.stderr)
        return 2
    result = run_live()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") and result.get("devSessionStopped") and result.get("proxyStopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
