from __future__ import annotations

import ctypes
from ctypes import wintypes
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import uuid
from typing import Any


@contextmanager
def measure_native_stages(runtime):
    """Time real methods without replacing results or recording UI contents."""
    from functools import wraps
    from threading import local
    from unittest.mock import patch

    results = {}
    nesting = local()

    def timed(method, label):
        @wraps(method)
        def wrapped(*args, **kwargs):
            stack = getattr(nesting, "stack", None)
            if stack is None:
                stack = nesting.stack = []
            frame = [time.perf_counter(), 0.0]
            stack.append(frame)
            try:
                return method(*args, **kwargs)
            finally:
                elapsed = time.perf_counter() - frame[0]
                stack.pop()
                if stack:
                    stack[-1][1] += elapsed
                entry = results.setdefault(label, {"calls": 0, "totalMs": 0.0, "selfMs": 0.0})
                entry["calls"] += 1
                entry["totalMs"] += elapsed * 1000
                entry["selfMs"] += max(0, elapsed - frame[1]) * 1000
        return wrapped

    methods = {
        "runtime": (runtime, ("focus_window", "_prepare_action_window_context", "_collect_window_candidates",
                              "_wait_for_post_action_stability", "_stop_step_heartbeat")),
        "driver": (runtime.driver, ("focus_window", "_focus_wrapper", "list_windows", "list_windows_batch", "_safe_backend_windows",
                                    "observe_desktop", "_enumerate_elements", "_resolve_target", "capture_screenshot",
                                    "click_element", "type_text", "verify_action")),
    }
    with ExitStack() as scope:
        for owner, (target, names) in methods.items():
            for name in names:
                method = getattr(target, name, None)
                if callable(method):
                    scope.enter_context(patch.object(target, name, timed(method, f"{owner}.{name}")))
        yield results
    for entry in results.values():
        entry["totalMs"] = round(entry["totalMs"], 2)
        entry["selfMs"] = round(entry["selfMs"], 2)


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


def run_owned_window_probe(runtime: Any, *, output_directory: Path, native_actions: bool = False) -> dict[str, Any]:
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
                observation_started = time.perf_counter()
                observation = driver.observe_desktop(window_handle=handle, use_cache=False)
                observation_ms = round((time.perf_counter() - observation_started) * 1000, 2)
                observed_handle = int(observation.metadata.get("windowHandle") or 0)
                assert_owned(observed_handle)
                if observed_handle != handle:
                    raise RuntimeError("observation_handle_mismatch")
                path = output_directory / f"{label}.png"
                capture_started = time.perf_counter()
                screenshot = driver.capture_screenshot(path, window_handle=observed_handle)
                capture_ms = round((time.perf_counter() - capture_started) * 1000, 2)
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
                    "timings": {"observationMs": observation_ms, "captureMs": capture_ms},
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

            if not native_actions:
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
            if native_actions:
                payload["checks"]["nativeActions"] = _run_native_actions(
                    runtime, title=title, handle=main_handle, output_directory=output_directory,
                    process=process, assert_owned=assert_owned, marker=marker,
                )
            else:
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


def _run_native_actions(runtime, *, title, handle, output_directory, process, assert_owned, marker):
    """Real native tools, real isolated runtime, no model or replacement driver."""
    from core.database import db
    from core.tools.native import computer_use as native
    from erc.command_service import command_service
    from erc.runtime_context import bind_runtime_context

    if native._get_computer_use_runtime() is not runtime:
        raise RuntimeError("native_probe_runtime_is_not_the_isolated_instance")
    session = f"owned-native-{uuid.uuid4().hex[:10]}"
    workspace = output_directory / "workspace"
    workspace.mkdir()
    db.create_or_update_session(session, title=session, user_id="owned-native-audit")
    run_id = f"run-{session}"
    context = dict(session_id=session, run_id=run_id, workspace_path=str(workspace),
                   user_id="owned-native-audit", actor_role="supervisor", agent_id="supervisor", runtime_kind="chat",
                   goal=f"Only operate the owned temporary test window {title}; type synthetic text and submit.")
    timings = []

    def invoke(name, arguments):
        assert_owned(handle)
        if not input_desktop_status()["available"]:
            raise RuntimeError("input_desktop_changed_before_native_action")
        started = time.perf_counter()
        with measure_native_stages(runtime) as stages, bind_runtime_context(**context):
            message = getattr(native, name).invoke({"type": "tool_call", "id": f"owned-{uuid.uuid4().hex}",
                "name": name, "args": {"window_title": title, "window_handle": handle, **arguments}})
        result = json.loads(message.content)
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        timings.append({"tool": name, "elapsedMs": elapsed, "ok": result.get("ok"),
                        "status": result.get("status"), "verification": result.get("verification"),
                        "planStep": result.get("planStep"), "summary": result.get("summary"), "stages": stages})
        (output_directory / "native-progress.json").write_text(json.dumps(timings, ensure_ascii=False, indent=2), encoding="utf-8")
        if not result.get("ok"):
            raise RuntimeError(f"native_action_failed:{name}:{result.get('summary')}")
        return result

    # Two continuous UIA rounds: the second must retain the exact selector and
    # use the already-foreground focus evidence rather than a focus cache.
    for value in (marker + "-first", marker):
        invoke("computer_use_input_text", {"automation_id": "OwnedInput", "control_type": "Edit",
               "text": value, "clear_first": True, "submit": False})
        invoke("computer_use_click_target", {"automation_id": "OwnedSubmit", "control_type": "Button"})
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            submitted = _read_json_when_ready(output_directory / "submitted.json", process, timeout=1)
            if submitted.get("submittedText") == value and int(submitted.get("pid") or 0) == process.pid:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("native_tool_submission_not_observed")

    # A real control signal produces a failed focus receipt. No injected fake
    # focus response and no attempt to target another app or an invalid HWND.
    before = (output_directory / "submitted.json").read_bytes()
    command_service.cancel_run(run_id, reason="owned fixture cancellation counterexample")
    with bind_runtime_context(**context):
        denied = native.computer_use_input_text.invoke({"type": "tool_call", "id": "owned-cancelled-input",
            "name": "computer_use_input_text", "args": {"window_title": title, "window_handle": handle,
            "automation_id": "OwnedInput", "control_type": "Edit", "text": "MUST-NOT-BE-TYPED"}})
    denied_payload = json.loads(denied.content)
    actual_input = runtime.driver._resolve_target(window_handle=handle, automation_id="OwnedInput", control_type="Edit")[0]
    input_value = actual_input.get_value()
    failed_focus_safe = not denied_payload.get("ok") and input_value == marker and (output_directory / "submitted.json").read_bytes() == before
    return {"status": "real_host_passed" if failed_focus_safe else "failed", "timings": timings,
            "cancelledFocusZeroSideEffects": failed_focus_safe, "cancelledStatus": denied_payload.get("status")}


def main(argv=None):
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Real native desktop actions against only an owned temporary window.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--isolated-root", required=True)
    args = parser.parse_args(argv)
    if not args.live:
        parser.error("--live is required before reading configuration or creating a window")
    isolated = Path(args.isolated_root).resolve()
    if isolated.exists() or isolated == (Path.home() / ".v8-agent-os").resolve():
        parser.error("isolated root must be a fresh directory")
    isolated.mkdir(parents=True)
    os.environ["V8_AGENT_OS_HOME"] = str(isolated)
    engine = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(engine))
    from core.runtime.startup_profile import get_runtime_registry_state
    get_runtime_registry_state()
    from runtimes.computer_use.runtime import computer_use_runtime
    from core.llm_factory import llm_factory
    from unittest.mock import patch
    model_attempts = []
    focus_probes = []
    original_skip = computer_use_runtime._should_skip_for_already_in_target_state

    def observe_skip(**kwargs):
        if kwargs.get("action_type") != "focus_window":
            return original_skip(**kwargs)
        started = time.perf_counter()
        foreground = computer_use_runtime.driver.foreground_window() or {}
        probe_ms = round((time.perf_counter() - started) * 1000, 2)
        skipped = original_skip(**kwargs)
        payload = kwargs.get("action_payload") or {}
        before = kwargs.get("before_observation") or {}
        focus_probes.append({"targetHandle": payload.get("window_handle"),
            "observedHandle": (before.get("metadata") or {}).get("windowHandle"),
            "foregroundBeforeHandle": foreground.get("handle"), "requireVisualGuard": payload.get("require_visual_guard"),
            "targetPathPresent": bool(payload.get("target_path")), "scene": kwargs.get("scene_assessment"),
            "skipped": skipped, "additionalReadProbeMs": probe_ms})
        return skipped

    def reject_model(*_args, **_kwargs):
        model_attempts.append("unexpected_model_request")
        raise RuntimeError("owned_native_probe_forbids_model_calls")

    # This fixture validates deterministic primitives. A model fallback makes
    # the case fail before provider access; desktop/runtime methods stay real.
    with patch.object(llm_factory, "create_for_role", side_effect=reject_model), patch.object(llm_factory, "create_chat_model", side_effect=reject_model), \
            patch.object(computer_use_runtime, "_should_skip_for_already_in_target_state", side_effect=observe_skip):
        result = run_owned_window_probe(computer_use_runtime, output_directory=isolated / "probe", native_actions=True)
    result["modelAttempts"] = len(model_attempts)
    result["focusProbes"] = focus_probes
    result["focusReuseAudit"] = {"status": "observed" if any(item["skipped"] for item in focus_probes) else "unverified",
        "skipCount": sum(bool(item["skipped"]) for item in focus_probes),
        "note": "Functional input/submit success does not prove that foreground focus reuse was exercised."}
    result["ok"] = bool(result.get("ok")) and not model_attempts
    target = isolated / "result.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": result.get("ok"), "status": result.get("status"), "failedStage": result.get("failedStage"),
                      "path": str(target), "cleanup": result.get("cleanup"), "modelAttempts": len(model_attempts)}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
