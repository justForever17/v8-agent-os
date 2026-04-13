from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtimes.computer_use.browser_automation import BrowserAutomationProvider


def _is_packaged_electron_executable(path: str | None) -> bool:
    token = str(path or "").strip()
    if not token:
        return False
    executable = Path(token)
    if not executable.exists():
        return False
    resources = executable.parent / "resources"
    return (resources / "app.asar").exists() or (resources / "app" / "package.json").exists()


def _resolve_existing_executable(candidates: List[str | None]) -> str | None:
    for candidate in candidates:
        token = str(candidate or "").strip()
        if not token:
            continue
        path = Path(token)
        if path.exists():
            return str(path)
        resolved = shutil.which(token)
        if resolved:
            return str(Path(resolved))
    return None


def _resolve_chromium_command(explicit: str | None) -> List[str]:
    executable = _resolve_existing_executable(
        [
            explicit,
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            "chrome.exe",
            "msedge.exe",
        ]
    )
    if not executable:
        raise RuntimeError("未找到可用的 Chromium 浏览器可执行文件，请通过 --executable 指定。")
    return [executable]


def _resolve_electron_command(explicit: str | None, main_js: Path) -> List[str] | None:
    if _is_packaged_electron_executable(explicit):
        return [str(Path(str(explicit)).resolve())]
    executable = _resolve_existing_executable([explicit, "electron", "electron.cmd", "electron.exe"])
    if executable:
        return [executable, str(main_js)]
    npx = _resolve_existing_executable(["npx.cmd", "npx"])
    if npx:
        return [npx, "electron", str(main_js)]
    return None


def _build_temp_page(temp_dir: str) -> Path:
    html = """<!doctype html><html><head><meta charset="utf-8"><title>V8 Browser Lane Smoke</title></head>
<body>
  <input id="name" placeholder="Your name">
  <button id="go" onclick="document.getElementById('status').textContent='clicked:' + document.getElementById('name').value;">Go</button>
  <input id="upload" type="file">
  <div id="status">idle</div>
  <div style="height:3200px"></div>
</body></html>"""
    page = Path(temp_dir) / "smoke.html"
    page.write_text(html, encoding="utf-8")
    return page


def _build_electron_main(temp_dir: str, page: Path) -> Path:
    main_js = Path(temp_dir) / "electron-main.js"
    main_js.write_text(
        f"""
const {{ app, BrowserWindow }} = require('electron');
const path = require('path');

function createWindow() {{
  const win = new BrowserWindow({{
    width: 1200,
    height: 900,
    webPreferences: {{
      nodeIntegration: false,
      contextIsolation: true,
    }},
  }});
  win.loadFile({json.dumps(str(page))});
}}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());
""".strip(),
        encoding="utf-8",
    )
    return main_js


def _prepare_provider(proxy_port: int) -> BrowserAutomationProvider:
    provider = BrowserAutomationProvider()
    provider.configure(
        {
            "browserLane": {
                "enabled": True,
                "mode": "auto_if_available",
                "provider": "engine_managed_cdp",
                "proxyPort": proxy_port,
                "connectTimeoutMs": 3000,
                "targetFamilies": ["chromium", "electron", "webview2"],
                "allowManagedLaunch": True,
            }
        }
    )
    return provider


def _launch_host(
    *,
    family: str,
    provider: BrowserAutomationProvider,
    page: Path,
    temp_dir: str,
    executable: str | None,
    debug_port: int,
    isolated_profile: bool,
    skip_if_unavailable: bool,
) -> Tuple[subprocess.Popen[str] | None, Dict[str, Any] | None]:
    if family == "chromium":
        decision = provider.decide_lane(
            action_type="observe",
            action_payload={"app_id": "chrome", "window_title": "V8 Browser Lane Smoke"},
            app_id="chrome",
        )
        if not isolated_profile and decision.available:
            return None, {"decision": decision, "launch": None}
        browser_command = _resolve_chromium_command(executable)
        launch_command = [*browser_command, page.as_uri()]
        if isolated_profile:
            launch_command.insert(1, f"--user-data-dir={temp_dir}")
        launch_command.insert(1 if len(launch_command) > 1 else len(launch_command), f"--remote-debugging-port={debug_port}")
        proc = subprocess.Popen(launch_command)
        time.sleep(4.0)
        decision = provider.decide_lane(
            action_type="observe",
            action_payload={"app_id": "chrome", "window_title": "V8 Browser Lane Smoke"},
            app_id="chrome",
        )
        if not decision.available and skip_if_unavailable:
            return proc, {"skipped": True, "reason": decision.reason, "family": family}
        return proc, {"decision": decision, "launch": {"command": launch_command}}

    if family == "electron":
        main_js = _build_electron_main(temp_dir, page)
        command = _resolve_electron_command(executable, main_js)
        if not command:
            if skip_if_unavailable:
                return None, {"skipped": True, "reason": "未找到 electron / npx，可跳过 Electron smoke。", "family": family}
            raise RuntimeError("未找到可用的 Electron 启动命令。")
        updated_command, updated_env, launch_meta = provider.prepare_launch(
            app_id="electron",
            launch_command=command,
            environment=os.environ.copy(),
        )
        proc = subprocess.Popen(updated_command if isinstance(updated_command, list) else [updated_command], env=updated_env)
        time.sleep(6.0)
        decision = provider.decide_lane(
            action_type="observe",
            action_payload={"app_id": "electron", "window_title": "V8 Browser Lane Smoke"},
            app_id="electron",
        )
        if not decision.available and skip_if_unavailable:
            return proc, {"skipped": True, "reason": decision.reason, "family": family, "launch": launch_meta}
        return proc, {"decision": decision, "launch": launch_meta}

    if family == "webview2":
        if not executable:
            return None, {"skipped": True, "reason": "WebView2 smoke 需要通过 --executable 指定宿主程序路径。", "family": family}
        host_path = Path(executable)
        if not host_path.exists():
            if skip_if_unavailable:
                return None, {"skipped": True, "reason": f"WebView2 宿主不存在: {host_path}", "family": family}
            raise RuntimeError(f"WebView2 宿主不存在: {host_path}")
        command = [str(host_path), page.as_uri()]
        updated_command, updated_env, launch_meta = provider.prepare_launch(
            app_id="webview2",
            launch_command=command,
            environment=os.environ.copy(),
        )
        proc = subprocess.Popen(updated_command if isinstance(updated_command, list) else [updated_command], env=updated_env)
        time.sleep(5.0)
        decision = provider.decide_lane(
            action_type="observe",
            action_payload={"app_id": "webview2", "window_title": "V8 Browser Lane Smoke"},
            app_id="webview2",
        )
        if not decision.available and skip_if_unavailable:
            return proc, {"skipped": True, "reason": decision.reason, "family": family, "launch": launch_meta}
        return proc, {"decision": decision, "launch": launch_meta}

    raise RuntimeError(f"不支持的 family: {family}")


def _run_browser_actions(provider: BrowserAutomationProvider, decision, page: Path) -> Dict[str, Any]:
    opened = provider.open_tab(url=page.as_uri(), decision=decision)
    target_id = str(opened.get("targetId") or "")
    provider._evaluate(
        target_id=target_id,
        expression="""
(() => {
  const existing = document.getElementById('v8-smoke-root');
  if (existing) return 'already-present';
  const root = document.createElement('div');
  root.id = 'v8-smoke-root';
  root.style.cssText = 'position:fixed;top:20px;right:20px;z-index:2147483647;background:#ffffff;border:1px solid #999;padding:12px;border-radius:8px;font:14px sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.18)';
  root.innerHTML = '<input id="name" placeholder="Your name" style="width:220px;margin-right:8px;"><button id="go">Go</button><input id="upload" type="file" style="display:block;margin-top:8px;"><div id="status" style="margin-top:8px;">idle</div>';
  document.body.appendChild(root);
  document.getElementById('go').addEventListener('click', () => {
    document.getElementById('status').textContent = 'clicked:' + document.getElementById('name').value;
  });
  return 'injected';
})()
""".strip(),
    )
    observed = provider.observe(
        window_title="V8 Browser Lane Smoke",
        decision=decision,
        target_id=target_id,
    )
    typed = provider.type_text(
        payload={
            "window_title": "V8 Browser Lane Smoke",
            "browser_target_id": target_id,
            "browser_selector": "#name",
            "text": "hello-browser",
        },
        decision=decision,
        target_input_kind="browser_dom_input",
    )
    clicked = provider.click_target(
        payload={
            "window_title": "V8 Browser Lane Smoke",
            "browser_target_id": target_id,
            "browser_selector": "#go",
        },
        decision=decision,
    )
    status_value = provider._evaluate(
        target_id=target_id,
        expression="document.getElementById('status').textContent",
    )
    scrolled = provider.scroll_view(
        payload={
            "window_title": "V8 Browser Lane Smoke",
            "browser_target_id": target_id,
            "amount": 800,
        },
        decision=decision,
    )
    upload_file = page.parent / "upload.txt"
    upload_file.write_text("upload-ok", encoding="utf-8")
    uploaded = provider.set_files(
        payload={
            "window_title": "V8 Browser Lane Smoke",
            "browser_target_id": target_id,
            "browser_selector": "#upload",
            "file_paths": [str(upload_file)],
        },
        decision=decision,
    )
    uploaded_name = provider._evaluate(
        target_id=target_id,
        expression="document.getElementById('upload').files[0] ? document.getElementById('upload').files[0].name : ''",
    )
    return {
        "observed": observed,
        "typed": typed.get("metadata"),
        "clicked": clicked.get("metadata"),
        "pageStatus": status_value,
        "scrolled": scrolled.get("metadata"),
        "uploaded": uploaded.get("metadata"),
        "uploadedName": uploaded_name,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="computer_use browser lane smoke")
    parser.add_argument("--family", choices=["chromium", "electron", "webview2"], default="chromium")
    parser.add_argument("--executable", help="浏览器/Electron/WebView2 宿主可执行文件路径")
    parser.add_argument("--proxy-port", type=int, default=3462)
    parser.add_argument("--debug-port", type=int, default=9222)
    parser.add_argument("--isolated-profile", action="store_true", help="Chromium 强制使用隔离 profile 烟测。")
    parser.add_argument("--skip-if-unavailable", action="store_true", help="当当前 host 不可用时返回 skipped，而不是抛错。")
    args = parser.parse_args()

    temp_dir = tempfile.mkdtemp(prefix=f"v8-browser-lane-{args.family}-")
    host_proc = None
    provider = _prepare_provider(args.proxy_port)
    try:
        page = _build_temp_page(temp_dir)
        host_proc, launch_payload = _launch_host(
            family=args.family,
            provider=provider,
            page=page,
            temp_dir=temp_dir,
            executable=args.executable,
            debug_port=args.debug_port,
            isolated_profile=bool(args.isolated_profile),
            skip_if_unavailable=bool(args.skip_if_unavailable),
        )
        if launch_payload and launch_payload.get("skipped"):
            print(json.dumps({"status": "skipped", **launch_payload}, ensure_ascii=False, indent=2))
            return 0
        decision = launch_payload["decision"]
        if not decision.available:
            raise RuntimeError(f"browser lane 当前不可用：{decision.as_dict()}")
        try:
            actions = _run_browser_actions(provider, decision, page)
        except Exception as exc:
            if args.skip_if_unavailable:
                print(
                    json.dumps(
                        {
                            "status": "skipped",
                            "reason": str(exc),
                            "family": args.family,
                            "decision": decision.as_dict(),
                            "launch": launch_payload.get("launch"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 0
            raise
        print(
            json.dumps(
                {
                    "status": "ok",
                    "family": args.family,
                    "decision": decision.as_dict(),
                    "launch": launch_payload.get("launch"),
                    **actions,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        provider.shutdown()
        if host_proc is not None:
            try:
                host_proc.terminate()
                host_proc.wait(timeout=5)
            except Exception:
                try:
                    host_proc.kill()
                except Exception:
                    pass
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
