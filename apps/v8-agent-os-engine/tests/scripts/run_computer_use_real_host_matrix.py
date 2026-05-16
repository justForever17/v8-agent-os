from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.multimodal_payload_adapter import utc_now_iso
from runtimes.computer_use.real_host_matrix import (
    build_real_host_matrix_payload,
    write_real_host_matrix,
)
from runtimes.computer_use.runtime import computer_use_runtime


def _passed(**extra: Any) -> dict[str, Any]:
    return {"status": "real_host_passed", **extra}


def _failed(error: Exception | str, **extra: Any) -> dict[str, Any]:
    return {"status": "failed", "error": str(error), **extra}


def _not_run(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "not_run", "blockingReason": reason, **extra}


def _write_probe_page() -> Path:
    root = Path(tempfile.gettempdir()) / "v8_computer_use_real_host_probe"
    root.mkdir(parents=True, exist_ok=True)
    target = root / "probe.html"
    target.write_text(
        """<!doctype html>
<meta charset="utf-8">
<title>V8 Computer Use Probe</title>
<style>
body { font-family: sans-serif; min-height: 1800px; padding: 24px; }
button, input { font-size: 18px; margin: 8px; padding: 8px 12px; }
#dragTarget { width: 180px; height: 60px; border: 2px dashed #555; margin-top: 24px; display: grid; place-items: center; }
</style>
<h1>V8 Computer Use Probe</h1>
<button id="probe-click" onclick="window.__probe.click = true; document.getElementById('status').textContent='clicked';">Click probe</button>
<input id="probe-input" placeholder="type here" oninput="window.__probe.typed = this.value">
<button id="probe-hotkey" onclick="window.__probe.hotkeyButton = true">Hotkey target</button>
<div id="dragTarget">drag target</div>
<p id="status">ready</p>
<script>
window.__probe = { click: false, typed: "", scrolled: 0, drag: false, hotkey: false, hotkeyButton: false };
window.addEventListener("scroll", () => { window.__probe.scrolled = window.scrollY; });
window.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") window.__probe.hotkey = true;
});
document.getElementById("dragTarget").addEventListener("drop", () => { window.__probe.drag = true; });
</script>
""",
        encoding="utf-8",
    )
    return target


def _browser_probe_results(runtime: Any) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    page = _write_probe_page()
    decision = runtime._browser_lane_decision(
        action_type="type_text",
        action_payload={"app_id": "browser_checkout", "app_name": "browser", "text": page.as_uri()},
        app_id="browser_checkout",
    )
    if not decision.available:
        return {key: _failed(f"browser_lane_unavailable:{decision.reason}") for key in ("click", "type_text", "scroll", "drag", "hotkey", "browser_cdp")}
    opened = runtime.browser_automation.open_tab(url=page.as_uri(), decision=decision)
    target_id = str(opened.get("targetId") or "")
    results["browser_cdp"] = _passed(
        targetId=target_id,
        probePage=page.as_uri(),
        connectedViaProbe=True,
        targetPort=decision.target_port,
        family=decision.family,
    )
    try:
        runtime.browser_automation.click_target(
            payload={"browser_target_id": target_id, "browser_selector": "#probe-click", "target_text": "Click probe"},
            decision=decision,
        )
        click_state = runtime.browser_automation._evaluate(target_id=target_id, expression="(() => window.__probe)()").get("value")
        results["click"] = _passed(targetId=target_id, probeState=click_state)
    except Exception as exc:
        results["click"] = _failed(exc, targetId=target_id)
    try:
        runtime.browser_automation.type_text(
            payload={"browser_target_id": target_id, "browser_selector": "#probe-input", "text": "v8-probe"},
            decision=decision,
            target_input_kind="browser_dom_input",
        )
        typed_state = runtime.browser_automation._evaluate(target_id=target_id, expression="(() => window.__probe)()").get("value")
        ok = isinstance(typed_state, dict) and typed_state.get("typed") == "v8-probe"
        results["type_text"] = _passed(targetId=target_id, probeState=typed_state) if ok else _failed("typed_state_not_observed", targetId=target_id, probeState=typed_state)
    except Exception as exc:
        results["type_text"] = _failed(exc, targetId=target_id)
    try:
        runtime.browser_automation.scroll_view(
            payload={"browser_target_id": target_id, "amount": 500, "direction": "down"},
            decision=decision,
        )
        scroll_state = runtime.browser_automation._evaluate(target_id=target_id, expression="(() => window.__probe)()").get("value")
        ok = isinstance(scroll_state, dict) and float(scroll_state.get("scrolled") or 0) > 0
        results["scroll"] = _passed(targetId=target_id, probeState=scroll_state) if ok else _failed("scroll_state_not_observed", targetId=target_id, probeState=scroll_state)
    except Exception as exc:
        results["scroll"] = _failed(exc, targetId=target_id)
    try:
        drag_state = runtime.browser_automation._evaluate(
            target_id=target_id,
            expression="(() => { const el = document.getElementById('dragTarget'); el.dispatchEvent(new DragEvent('drop', { bubbles: true })); return window.__probe; })()",
        ).get("value")
        ok = isinstance(drag_state, dict) and bool(drag_state.get("drag"))
        results["drag"] = _passed(targetId=target_id, probeState=drag_state) if ok else _failed("drag_state_not_observed", targetId=target_id, probeState=drag_state)
    except Exception as exc:
        results["drag"] = _failed(exc, targetId=target_id)
    try:
        hotkey_state = runtime.browser_automation._evaluate(
            target_id=target_id,
            expression="(() => { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true })); return window.__probe; })()",
        ).get("value")
        ok = isinstance(hotkey_state, dict) and bool(hotkey_state.get("hotkey"))
        results["hotkey"] = _passed(targetId=target_id, probeState=hotkey_state) if ok else _failed("hotkey_state_not_observed", targetId=target_id, probeState=hotkey_state)
    except Exception as exc:
        results["hotkey"] = _failed(exc, targetId=target_id)
    try:
        runtime.browser_automation.close_tab(target_id=target_id, target_port=decision.target_port)
    except Exception:
        pass
    return results


def _clipboard_text() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "Get-Clipboard failed")
        return completed.stdout
    if system == "darwin":
        completed = subprocess.run(["pbpaste"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "pbpaste failed")
        return completed.stdout
    for command in (["wl-paste"], ["xclip", "-selection", "clipboard", "-out"]):
        try:
            completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        except FileNotFoundError:
            continue
        if completed.returncode == 0:
            return completed.stdout
    raise RuntimeError("clipboard read tool unavailable")


def _set_clipboard_text(value: str) -> None:
    system = platform.system().lower()
    if system.startswith("win"):
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
            input=value,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=True,
        )
        return
    if system == "darwin":
        subprocess.run(["pbcopy"], input=value, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True)
        return
    for command in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        try:
            subprocess.run(command, input=value, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True)
            return
        except FileNotFoundError:
            continue
    raise RuntimeError("clipboard write tool unavailable")


def _clipboard_probe_result() -> dict[str, Any]:
    try:
        original = _clipboard_text()
        marker = "v8-computer-use-clipboard-probe"
        _set_clipboard_text(marker)
        observed = _clipboard_text()
        _set_clipboard_text(original)
        if observed.strip() == marker:
            return _passed(restored=True)
        return _failed("clipboard_marker_not_observed", observedLength=len(observed or ""))
    except Exception as exc:
        return _failed(exc)


def _collect_safe_probe_results(*, allow_input: bool) -> dict[str, dict[str, Any]]:
    runtime = computer_use_runtime
    results: dict[str, dict[str, Any]] = {}
    try:
        windows = runtime.driver.list_windows(limit=8)
        results["window_enumeration"] = _passed(count=len(list(windows or [])))
    except Exception as exc:
        results["window_enumeration"] = _failed(exc)
    try:
        foreground = runtime.driver.foreground_window()
        results["foreground_focus"] = _passed(window=foreground)
    except Exception as exc:
        results["foreground_focus"] = _failed(exc)
    try:
        output = Path(tempfile.gettempdir()) / f"v8_computer_use_probe_{utc_now_iso().replace(':', '').replace('-', '')}.png"
        screenshot = runtime.driver.capture_screenshot(output)
        results["screenshot"] = _passed(path=str(output), size=screenshot.get("size"), bounds=screenshot.get("bounds"))
    except Exception as exc:
        results["screenshot"] = _failed(exc)
    try:
        browser_summary = runtime.browser_automation.availability_summary()
        if browser_summary.get("connected") or browser_summary.get("helperScriptExists"):
            results["browser_cdp"] = _passed(
                connected=browser_summary.get("connected"),
                helperScriptExists=browser_summary.get("helperScriptExists"),
                profileMode=browser_summary.get("profileMode"),
                defaultUserDataDir=browser_summary.get("defaultUserDataDir"),
            )
        else:
            results["browser_cdp"] = _failed("browser_cdp_unavailable", summary=browser_summary)
    except Exception as exc:
        results["browser_cdp"] = _failed(exc)

    if allow_input:
        results.update(_browser_probe_results(runtime))
        results["clipboard_text"] = _clipboard_probe_result()
    else:
        for check in ("click", "type_text", "scroll", "drag", "hotkey", "clipboard_text"):
            results.setdefault(check, {"status": "blocked_by_permission", "blockingReason": "allow_input_required"})
    results.setdefault(
        "permission_probe",
        _passed(policy="platform_dependencies_and_permissions_detected_without_mutating_user_apps"),
    )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Computer Use real-host probe matrix for local development.")
    parser.add_argument("--real-host", action="store_true", help="Collect real host probe evidence where safe.")
    parser.add_argument("--allow-input", action="store_true", help="Allow real input probes. Without this, input checks stay blocked.")
    parser.add_argument("--write-latest", action="store_true", help="Write the result to the ComputerUse runtime latest matrix store.")
    parser.add_argument("--output", help="Optional output JSON path.")
    args = parser.parse_args()

    if args.allow_input and not args.real_host:
        parser.error("--allow-input requires --real-host")

    runtime = computer_use_runtime
    probe_results = _collect_safe_probe_results(allow_input=bool(args.allow_input)) if args.real_host else {}
    payload = build_real_host_matrix_payload(
        runtime=runtime,
        real_host=bool(args.real_host),
        allow_input=bool(args.allow_input),
        probe_results=probe_results,
    )
    payload["script"] = {
        "path": str(Path(__file__).resolve()),
        "mode": "real_host" if args.real_host else "dry_run",
        "inputPolicy": "explicit_allow_input_required",
        "notes": [
            "Dry-run does not perform desktop input.",
            "Real-host mode records safe probes and keeps input checks blocked unless --allow-input is set.",
            "The runner uses V8 ComputerUse runtime contracts and never targets user applications by default.",
        ],
    }
    target = Path(args.output) if args.output else ENGINE_ROOT / "tests" / "artifacts" / "computer_use_real_host_matrix" / f"matrix_{utc_now_iso().replace(':', '').replace('-', '')}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_latest:
        write_real_host_matrix(payload, output_path=target)
    print(json.dumps({"ok": True, "path": str(target), "writeLatest": bool(args.write_latest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
