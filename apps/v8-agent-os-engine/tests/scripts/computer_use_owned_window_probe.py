from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import uuid
from typing import Any


def fixture_image_proof(path: Path, *, expected_color: tuple[int, int, int]) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        dimensions = list(image.size)
        sample = image.convert("RGB").resize((64, 64))
        pixels = [sample.getpixel((x, y)) for y in range(64) for x in range(64)]
    matching = sum(max(abs(pixel[index] - expected_color[index]) for index in range(3)) <= 12 for pixel in pixels)
    coverage = matching / len(pixels)
    return {"imageSize": dimensions, "fixtureColorCoverage": round(coverage, 4), "fixturePixelsPresent": coverage >= 0.2}


def input_desktop_status() -> dict[str, Any]:
    """Read-only guard: never switch, wake or unlock an input desktop."""
    import psutil

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    desktop = user32.OpenInputDesktop(0, False, 1)
    if not desktop:
        return {"available": False, "reason": "input_desktop_unavailable", "winError": ctypes.get_last_error()}
    name = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    try:
        if not user32.GetUserObjectInformationW(desktop, 2, name, ctypes.sizeof(name), ctypes.byref(needed)):
            return {"available": False, "reason": "input_desktop_identity_unavailable", "winError": ctypes.get_last_error()}
    finally:
        user32.CloseDesktop(desktop)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    foreground_pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(foreground_pid))
    try:
        lock_surface = psutil.Process(foreground_pid.value).name().lower() in {"lockapp.exe", "logonui.exe", "winlogon.exe"}
    except (psutil.Error, ValueError):
        lock_surface = False
    available = name.value.lower() == "default" and not lock_surface
    return {"available": available, "desktopName": name.value, "foregroundIsLockSurface": lock_surface,
            "reason": None if available else "locked_or_secure_desktop"}


def _read_json_when_ready(path: Path, process: subprocess.Popen, timeout: float = 20) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"owned_probe_process_exited:{process.returncode}")
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.1)
    raise TimeoutError(f"owned_probe_file_not_ready:{path.name}")


def run_owned_window_probe(runtime: Any, *, output_directory: Path) -> dict[str, Any]:
    """Exercise production UIA/capture against only a child process we own."""
    if platform.system() != "Windows":
        return {"ok": False, "status": "unsupported", "reason": "owned_window_probe_requires_windows"}
    state_root = Path(os.environ.get("V8_AGENT_OS_HOME") or Path.home() / ".v8-agent-os").resolve()
    if state_root == (Path.home() / ".v8-agent-os").resolve():
        raise RuntimeError("owned_window_probe_requires_isolated_V8_AGENT_OS_HOME")
    output_directory.mkdir(parents=True, exist_ok=True)
    if any((output_directory / name).exists() for name in ("ready.json", "submitted.json", "close.marker")):
        raise RuntimeError("owned_window_probe_requires_a_fresh_output_directory")
    desktop_status = input_desktop_status()
    if not desktop_status["available"]:
        return {"ok": False, "status": "blocked", "evidenceClass": "local_real_gui_owned_fixture", "inputDesktop": desktop_status,
                "cleanup": {"processExited": True, "remainingOwnedHandles": [], "fixtureNeverStarted": True}}
    driver = runtime.driver
    driver.ensure_available()
    import mss

    title = f"V8-CU-Owned-{uuid.uuid4().hex[:12]}"
    fixture = Path(__file__).resolve().parents[1] / "fixtures" / "computer_use" / "owned_probe_window.ps1"
    payload: dict[str, Any] = {"ok": False, "evidenceClass": "local_real_gui_owned_fixture", "title": title, "inputDesktop": desktop_status, "checks": {}}
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    owned_handles: list[int] = []
    process = None
    stage = "start_owned_windows"
    with (output_directory / "fixture.stdout.log").open("w", encoding="utf-8") as stdout, (output_directory / "fixture.stderr.log").open("w", encoding="utf-8") as stderr:
        try:
            process = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(fixture), "-ProbeTitle", title, "-ProbeDirectory", str(output_directory)],
                stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ready = _read_json_when_ready(output_directory / "ready.json", process)
            if int(ready["pid"]) != process.pid or ready["title"] != title:
                raise RuntimeError("owned_probe_identity_mismatch")
            main_handle, dialog_handle = int(ready["mainHandle"]), int(ready["dialogHandle"])
            owned_handles = [main_handle, dialog_handle]

            def assert_owned(handle: int) -> None:
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
                if process.poll() is not None or owner.value != process.pid:
                    raise RuntimeError("owned_window_identity_changed")

            for handle in owned_handles:
                assert_owned(handle)
            payload["pid"] = process.pid
            payload["screens"] = ready["screens"]
            with mss.mss() as capture:
                payload["captureMonitors"] = [{key: item.get(key) for key in ("left", "top", "width", "height", "is_primary")} for item in capture.monitors]

            stage = "observe_and_capture_main"
            focused = driver.focus_window(window_handle=main_handle)
            time.sleep(0.25)
            # Automatic resolution is inspected only. If it chooses any other
            # process, fail before taking a screenshot or sending input.
            automatic = driver._window_dict(driver._resolve_root())
            automatic_handle = int(automatic.get("handle") or 0)
            automatic_owned = automatic_handle in owned_handles and int(automatic.get("processId") or 0) == process.pid
            payload["checks"]["automaticTarget"] = {
                "status": "real_host_passed" if automatic_owned else "failed",
                "focusResultHandle": focused.get("handle"), "automaticHandle": automatic_handle,
                "automaticOwnerMatches": automatic_owned,
                "reason": None if automatic_owned else "automatic_target_is_not_owned_fixture",
            }
            if automatic_owned:
                assert_owned(automatic_handle)
                payload["checks"]["automaticTarget"].update({key: automatic.get(key) for key in ("title", "className", "processId", "bounds")})

            def capture_owned(label: str, handle: int) -> dict[str, Any]:
                assert_owned(handle)
                observation = driver.observe_desktop(window_handle=handle, use_cache=False)
                observed_handle = int(observation.metadata.get("windowHandle") or 0)
                assert_owned(observed_handle)
                if observed_handle != handle:
                    raise RuntimeError("observation_handle_mismatch")
                path = output_directory / f"{label}.png"
                screenshot = driver.capture_screenshot(path, window_handle=observed_handle)
                captured_handle = int((screenshot.get("window") or {}).get("handle") or 0)
                if captured_handle != observed_handle:
                    raise RuntimeError("capture_handle_mismatch")
                proof = fixture_image_proof(path, expected_color=(34, 89, 54) if label == "dialog" else (24, 58, 82))
                dimensions = proof["imageSize"]
                size = screenshot["size"]
                if dimensions != [size["width"], size["height"]]:
                    raise RuntimeError("capture_dimensions_invalid")
                return {
                    "status": "real_host_passed" if proof["fixturePixelsPresent"] else "failed",
                    "reason": None if proof["fixturePixelsPresent"] else "owned_fixture_pixels_missing",
                    **proof, "requestedHandle": handle, "observedHandle": observed_handle,
                    "capturedHandle": captured_handle, "windowTitle": observation.window_title,
                    "windowBounds": observation.metadata.get("windowBounds"), "dpiScale": observation.metadata.get("dpiScale"),
                    "captureBounds": screenshot["bounds"], "imageSize": dimensions, "bytes": path.stat().st_size,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "imagePath": str(path),
                }

            payload["checks"]["mainCapture"] = capture_owned("main", main_handle)
            if payload["checks"]["mainCapture"]["status"] != "real_host_passed":
                raise RuntimeError("owned_fixture_pixels_missing_before_input")
            stage = "observe_and_capture_small_dialog"
            payload["checks"]["smallDialogCapture"] = capture_owned("dialog", dialog_handle)
            if payload["checks"]["smallDialogCapture"]["status"] != "real_host_passed":
                raise RuntimeError("owned_dialog_pixels_missing_before_input")
            if payload["checks"]["smallDialogCapture"]["imageSize"][0] >= payload["checks"]["mainCapture"]["imageSize"][0] / 2:
                raise RuntimeError("small_dialog_fixture_not_small")

            stage = "native_task_route"
            binding = runtime._resolve_app_binding(window_title=title, include_running=True)
            loop = runtime.prepare_task_loop(
                goal="在本地应用输入测试文本并提交，随后截图。", app_id=binding.resolved_app_id, app_name=title,
            )
            if (loop.get("domain") or {}).get("selectedPlaybook") is not None or loop.get("status") != "generic_planner":
                raise RuntimeError("native_input_task_was_routed_to_web_playbook")
            payload["checks"]["nativeTaskRoute"] = {
                "status": "real_host_passed", "appId": binding.resolved_app_id,
                "controlClass": (binding.catalog_entry or {}).get("controlClass"),
                "selectedPlaybook": (loop.get("domain") or {}).get("selectedPlaybook"), "routeStatus": loop.get("status"),
            }

            stage = "owned_native_input_and_submit"
            if not input_desktop_status()["available"]:
                raise RuntimeError("input_desktop_changed_before_input")
            marker = f"owned-input-{uuid.uuid4().hex[:8]}"
            assert_owned(main_handle)
            driver.type_text(window_handle=main_handle, automation_id="OwnedInput", control_type="Edit", text=marker, clear_first=True)
            assert_owned(main_handle)
            driver.click_element(window_handle=main_handle, automation_id="OwnedSubmit", control_type="Button")
            submitted = _read_json_when_ready(output_directory / "submitted.json", process, timeout=5)
            if submitted.get("submittedText") != marker or int(submitted.get("pid") or 0) != process.pid:
                raise RuntimeError("native_input_submission_not_observed")
            payload["checks"]["nativeInput"] = {"status": "real_host_passed", "submittedMarker": marker, "pid": process.pid}
            payload["checks"]["postInputCapture"] = capture_owned("submitted", main_handle)
            payload["ok"] = all(check.get("status") == "real_host_passed" for check in payload["checks"].values())
        except Exception as exc:
            payload["failedStage"] = stage
            payload["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            (output_directory / "close.marker").touch()
            forced = False
            if process is not None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    forced = True
                    process.terminate()
                    process.wait(timeout=5)
            remaining = [handle for handle in owned_handles if user32.IsWindow(handle)]
            payload["cleanup"] = {"processExited": process is None or process.poll() is not None, "forcedOwnedChildTermination": forced, "remainingOwnedHandles": remaining}
            if remaining or (process is not None and process.poll() is None):
                payload["ok"] = False
    return payload
