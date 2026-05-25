from __future__ import annotations

import urllib.error
import json
from pathlib import Path

from runtimes.rpa.recording import RPARecordingManager
from runtimes.rpa import runtime as rpa_runtime
from runtimes.rpa.runtime import _capture_assistant_error_event, _capture_assistant_last_event
from scripts import rpa_capture_assistant
from scripts.rpa_capture_assistant import _parse_windows_hotkey


class _TraceStoreStub:
    def __init__(self) -> None:
        self.steps = []

    def append_step(self, *, run_id, session_id, goal, runtime_kind, step, metadata):
        self.steps.append(step.as_dict())
        return {"runId": run_id, "stepCount": len(self.steps), "updatedAt": "now"}


def _manager(tmp_path: Path) -> tuple[RPARecordingManager, _TraceStoreStub]:
    store = _TraceStoreStub()
    return RPARecordingManager(trace_store_instance=store, root_dir=tmp_path), store


def test_coordinate_fallback_uses_window_client_relative_anchor(tmp_path: Path) -> None:
    manager, store = _manager(tmp_path)
    session = manager.start({"name": "coordinate fallback", "targetMode": "desktop_window", "appId": "qqmusic"})

    result = manager.append_event(
        session["recordingSessionId"],
        {
            "action": "click",
            "source": "native_inspector",
            "fragileCoordinateFallback": True,
            "coordinate": {"x": 412, "y": 236},
            "targetWindow": {
                "title": "QQMusic",
                "processName": "qqmusic.exe",
                "bounds": {"left": 100, "top": 80, "right": 600, "bottom": 480},
                "clientRect": {"left": 112, "top": 96, "right": 592, "bottom": 456},
                "dpi": 144,
            },
            "screen": {"monitorId": "DISPLAY1"},
            "screenshotAnchor": {"screenshotPatchRef": "raw://patch/1"},
        },
    )

    step = result["step"]
    params = step["params"]
    anchor = params["spatial_anchor"]["coordinateAnchor"]

    assert params["point"] == [0.625, 0.3889]
    assert params["coordinate_source"] == "window_client_relative_capture"
    assert anchor["mode"] == "window_client_relative"
    assert anchor["clientRect"]["left"] == 112.0
    assert params["image_anchor"]["screenshotPatchRef"] == "raw://patch/1"
    assert "visual_locator" not in params
    assert step["recovery"]["fallbackOrder"] == ["image_anchor", "coordinate"]
    assert store.steps[0]["metadata"]["fragileCoordinateFallback"] is True


def test_selector_capture_keeps_coordinate_as_fallback_candidate(tmp_path: Path) -> None:
    manager, _store = _manager(tmp_path)
    session = manager.start({"name": "selector capture", "targetMode": "desktop_window", "appId": "notepad"})

    result = manager.append_event(
        session["recordingSessionId"],
        {
            "action": "click",
            "selectorCandidates": [{"strategy": "uia", "value": "automationId=saveButton"}],
            "selector": {"automationId": "saveButton"},
            "coordinate": {"x": 250, "y": 220},
            "targetWindow": {
                "title": "Notepad",
                "processName": "notepad.exe",
                "bounds": {"left": 100, "top": 100, "right": 500, "bottom": 500},
                "clientRect": {"left": 100, "top": 120, "right": 500, "bottom": 500},
            },
            "screenshotAnchor": {"screenshotPatchRef": "raw://patch/2"},
            "ocrText": "Save",
        },
    )

    step = result["step"]
    params = step["params"]

    assert step["target"]["selector"]["automationId"] == "saveButton"
    assert params["point_candidates"] == [[0.375, 0.2632]]
    assert params["visual_locator"] == "Save"
    assert step["recovery"]["fallbackOrder"] == ["selector", "image_anchor", "coordinate"]


def test_windows_hotkey_parser_accepts_bracket_tokens() -> None:
    parsed = _parse_windows_hotkey("Ctrl+Alt+[", default="Ctrl+Alt+C")

    assert parsed[2] == "Ctrl+Alt+["


def test_capture_post_retries_engine_v1_prefix(monkeypatch) -> None:
    calls: list[str] = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout):  # noqa: ANN001 - mirrors urllib signature for monkeypatch.
        calls.append(request.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)
        return _Response()

    monkeypatch.setattr(rpa_capture_assistant.urllib.request, "urlopen", fake_urlopen)

    rpa_capture_assistant._post_event("http://127.0.0.1:9530", "rec1", {"action": "click"})

    assert calls == [
        "http://127.0.0.1:9530/rpa/recordings/rec1/capture-assistant/capture",
        "http://127.0.0.1:9530/v1/rpa/recordings/rec1/capture-assistant/capture",
    ]


def test_capture_assistant_log_readiness_ignores_warnings(tmp_path: Path) -> None:
    log_path = tmp_path / "assistant.log"
    log_path.write_text(
        "\n".join(
            [
                "plain startup line",
                json.dumps({"ok": False, "warning": "legacy warning only"}),
                json.dumps(
                    {
                        "event": "rpa_capture_assistant.warning",
                        "ok": True,
                        "backend": "windows_register_hotkey",
                        "stage": "cancel_hotkey",
                        "warning": "cancel hotkey unavailable",
                    }
                ),
                json.dumps(
                    {
                        "event": "rpa_capture_assistant.ready",
                        "ok": True,
                        "backend": "windows_register_hotkey",
                        "hotkeyRegistered": True,
                        "mouseHookInstalled": True,
                        "keyboardHookInstalled": True,
                        "overlayReady": True,
                        "targetReady": True,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    ready = _capture_assistant_last_event(log_path, ".ready")

    assert _capture_assistant_error_event(log_path) is None
    assert ready is not None
    assert ready["hotkeyRegistered"] is True
    assert ready["mouseHookInstalled"] is True
    assert ready["keyboardHookInstalled"] is True


def test_windows_native_inspector_requires_published_fla_ui_helper(monkeypatch, tmp_path: Path) -> None:
    missing_helper = tmp_path / "missing" / "V8.Rpa.NativeInspector.exe"

    monkeypatch.setattr(rpa_runtime.sys, "platform", "win32")
    capability = rpa_runtime._native_hotkey_backend_capability({"helperPath": str(missing_helper)})

    assert capability["backend"] == "windows_fla_ui_helper"
    assert capability["available"] is False
    assert capability["state"] == "helper_not_built"
    assert capability["helper"]["publishCommand"][0] == "dotnet"


def test_windows_native_inspector_accepts_published_helper(monkeypatch, tmp_path: Path) -> None:
    helper = tmp_path / "V8.Rpa.NativeInspector.exe"
    helper.write_text("placeholder", encoding="utf-8")

    monkeypatch.setattr(rpa_runtime.sys, "platform", "win32")
    capability = rpa_runtime._native_hotkey_backend_capability({"helperPath": str(helper)})

    assert capability["backend"] == "windows_fla_ui_helper"
    assert capability["available"] is True
    assert capability["state"] == "ready"
    assert capability["helper"]["path"] == str(helper)


def test_capture_assistant_log_error_event_is_detected(tmp_path: Path) -> None:
    log_path = tmp_path / "assistant.log"
    log_path.write_text(
        "\n".join(
            [
                json.dumps({"event": "rpa_capture_assistant.ready", "ok": True}),
                json.dumps(
                    {
                        "event": "rpa_capture_assistant.error",
                        "ok": False,
                        "stage": "overlay",
                        "error": "overlay_bounds_mismatch",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    error = _capture_assistant_error_event(log_path)

    assert error is not None
    assert error["stage"] == "overlay"
