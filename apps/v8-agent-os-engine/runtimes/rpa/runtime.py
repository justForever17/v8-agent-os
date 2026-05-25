from __future__ import annotations

import hashlib
import ctypes
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional

from core.audit_logger import audit_logger
from core.database import db
from core.realtime_protocol import utc_now_iso
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME
from erc.kernel import erc_kernel
from erc.runtime_context import bind_runtime_context
from erc.runtime_control import apply_control_signal, consume_stop_signal
from erc.runtime_registry import runtime_registry
from erc.run_service import run_service
from erc.safety_guardian import SafetyDecision, safety_guardian
from erc.side_effect_idempotency import side_effect_idempotency_service
from erc.workflow_ledger import workflow_ledger_service
from runtimes.computer_use.runtime import computer_use_runtime
from runtimes.rpa.compiler import RPATraceCompiler, rpa_trace_compiler
from runtimes.rpa.default_templates import ensure_system_rpa_seed_templates
from runtimes.rpa.execution_semantics import normalize_script_assessment_status, outcome_family_for_execution_state
from runtimes.rpa.recording import RPARecordingManager
from runtimes.rpa.robot_adapter import RobotFrameworkAdapter, robot_framework_adapter
from runtimes.rpa.store import RPAScriptStore, rpa_script_store
from runtimes.rpa.template_service import RPATemplateService, rpa_template_service


def _slug(value: str, *, fallback: str = "rpa") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._:-]+", "-", str(value or "").strip()).strip("-").lower()
    return normalized or fallback


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _text_blob(*values: Any) -> str:
    parts: list[str] = []
    for value in values:
        if isinstance(value, dict):
            parts.append(_text_blob(*value.values()))
        elif isinstance(value, (list, tuple, set)):
            parts.append(_text_blob(*value))
        elif value not in (None, ""):
            parts.append(str(value))
    return " ".join(parts).strip().lower()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _rpa_maximize_capture_window(handle: Any) -> bool:
    window_handle = _as_int(handle)
    if not window_handle or not sys.platform.startswith("win"):
        return False
    try:
        user32 = ctypes.windll.user32
        hwnd = wintypes.HWND(window_handle)
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def _rpa_surface_identity(item: Dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    return _text_blob(
        item.get("title"),
        item.get("windowTitle"),
        item.get("name"),
        item.get("className"),
        item.get("controlType"),
        item.get("role"),
        item.get("processName"),
        item.get("appId"),
        item.get("source"),
    )


def _rpa_is_admin_surface(item: Dict[str, Any] | None) -> bool:
    text = _rpa_surface_identity(item)
    return any(
        marker in text
        for marker in (
            "v8 agent os",
            "v8 os",
            "localhost:9528",
            "127.0.0.1:9528",
            "apps/v8-agent-os-admin",
            "/admin/rpa",
            "rpa runtime - microsoft edge",
        )
    )


def _rpa_is_system_shell_surface(item: Dict[str, Any] | None) -> bool:
    text = _rpa_surface_identity(item)
    return any(
        marker in text
        for marker in (
            "systemtrayicon",
            "taskbarframe",
            "shell_traywnd",
            "traynotifywnd",
            "syslistview32",
            "任务栏",
            "系统托盘",
            "托盘",
            "显示隐藏的图标",
            "音量",
            "网络 ",
            "输入指示器",
            "start menu",
            "windows powershell",
        )
    )


def _rpa_bounds(item: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    bounds = item.get("bounds") if isinstance(item.get("bounds"), dict) else item
    return dict(bounds or {})


def _rpa_window_relative_coordinate(coordinate: Dict[str, Any], target_window: Dict[str, Any] | None) -> Dict[str, Any]:
    if not coordinate or not isinstance(target_window, dict):
        return {}
    x = coordinate.get("x")
    y = coordinate.get("y")
    try:
        x_float = float(x)
        y_float = float(y)
    except Exception:
        return {}
    bounds = _rpa_bounds(target_window)
    left = bounds.get("x", bounds.get("left", bounds.get("screenX", 0)))
    top = bounds.get("y", bounds.get("top", bounds.get("screenY", 0)))
    try:
        left_float = float(left or 0)
        top_float = float(top or 0)
    except Exception:
        left_float = 0
        top_float = 0
    return {
        "x": round(x_float - left_float, 2),
        "y": round(y_float - top_float, 2),
        "absoluteX": x_float,
        "absoluteY": y_float,
    }


def _rpa_normalized_rect(value: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    rect = dict(value.get("bounds") or value)
    left = rect.get("left", rect.get("x", rect.get("screenX", 0)))
    top = rect.get("top", rect.get("y", rect.get("screenY", 0)))
    right = rect.get("right")
    bottom = rect.get("bottom")
    width = rect.get("width")
    height = rect.get("height")
    try:
        left_f = float(left or 0)
        top_f = float(top or 0)
        if right in (None, ""):
            right_f = left_f + float(width or 0)
        else:
            right_f = float(right)
        if bottom in (None, ""):
            bottom_f = top_f + float(height or 0)
        else:
            bottom_f = float(bottom)
    except Exception:
        return {}
    return {
        "left": round(left_f, 2),
        "top": round(top_f, 2),
        "right": round(right_f, 2),
        "bottom": round(bottom_f, 2),
        "width": round(max(0.0, right_f - left_f), 2),
        "height": round(max(0.0, bottom_f - top_f), 2),
    }


def _rpa_coordinate_anchor(
    *,
    coordinate: Dict[str, Any] | None,
    target_window: Dict[str, Any] | None,
    screen: Dict[str, Any] | None = None,
    existing: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    existing = dict(existing or {})
    if existing.get("mode") == "window_client_relative" and existing.get("ratioX") not in (None, ""):
        return existing
    coordinate = dict(coordinate or {})
    target_window = dict(target_window or {})
    if not coordinate or not target_window:
        return existing
    try:
        absolute_x = float(coordinate.get("x"))
        absolute_y = float(coordinate.get("y"))
    except Exception:
        return existing
    window_rect = _rpa_normalized_rect(target_window)
    client_rect = _rpa_normalized_rect(target_window.get("clientRect") if isinstance(target_window.get("clientRect"), dict) else None) or window_rect
    if not client_rect:
        return existing
    left = float(client_rect.get("left") or 0)
    top = float(client_rect.get("top") or 0)
    width = max(1.0, float(client_rect.get("width") or 1))
    height = max(1.0, float(client_rect.get("height") or 1))
    rel_x = absolute_x - left
    rel_y = absolute_y - top
    ratio_x = max(0.0, min(1.0, rel_x / width))
    ratio_y = max(0.0, min(1.0, rel_y / height))
    screen = dict(screen or {})
    return {
        "mode": "window_client_relative",
        "x": round(rel_x, 2),
        "y": round(rel_y, 2),
        "ratioX": round(ratio_x, 4),
        "ratioY": round(ratio_y, 4),
        "windowRect": window_rect,
        "clientRect": client_rect,
        "dpi": target_window.get("dpi") or existing.get("dpi") or 96,
        "monitorId": screen.get("monitorId") or existing.get("monitorId") or "primary",
        "absoluteX": round(absolute_x, 2),
        "absoluteY": round(absolute_y, 2),
    }


def _rpa_image_anchor(
    *,
    event: Dict[str, Any],
    target_window: Dict[str, Any] | None,
    highlight_bounds: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    existing = event.get("imageAnchor")
    if isinstance(existing, dict) and existing:
        return dict(existing)
    screenshot_anchor = event.get("screenshotAnchor")
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    if not isinstance(screenshot_anchor, dict):
        screenshot_anchor = metadata.get("screenshotAnchor") if isinstance(metadata.get("screenshotAnchor"), dict) else {}
    hover = event.get("hoverSample") if isinstance(event.get("hoverSample"), dict) else metadata.get("hoverSample")
    hover = hover if isinstance(hover, dict) else {}
    element = hover.get("element") if isinstance(hover.get("element"), dict) else {}
    bounds = dict(highlight_bounds or event.get("highlightBounds") or metadata.get("highlightBounds") or element.get("bounds") or {})
    ocr_text = str(
        event.get("ocrText")
        or element.get("name")
        or ""
    ).strip()
    patch_ref = (
        screenshot_anchor.get("screenshotPatchRef")
        or screenshot_anchor.get("patchRef")
        or screenshot_anchor.get("ref")
        or None
    )
    return {
        "screenshotPatchRef": patch_ref,
        "ocrText": ocr_text[:240],
        "matchThreshold": float(event.get("matchThreshold") or screenshot_anchor.get("matchThreshold") or 0.82),
        "bounds": bounds,
        "status": "ready" if patch_ref else "deferred_patch",
    }


def _rpa_target_criteria(
    *,
    target_window: Dict[str, Any] | None = None,
    target_process: Dict[str, Any] | None = None,
    target_lock: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    target_window = dict(target_window or {})
    target_process = dict(target_process or {})
    target_lock = dict(target_lock or {})
    handles = {
        item
        for item in (
            _as_int(target_window.get("handle")),
            _as_int(target_window.get("windowHandle")),
            _as_int(target_lock.get("windowHandle")),
        )
        if item is not None
    }
    process_ids = {
        item
        for item in (
            _as_int(target_window.get("processId")),
            _as_int(target_process.get("processId")),
            _as_int(target_lock.get("processId")),
        )
        if item is not None
    }
    process_names: set[str] = set()
    for value in (
        target_window.get("processName"),
        target_process.get("processName"),
        target_lock.get("processName"),
        target_window.get("processNames"),
        target_process.get("processNames"),
        target_lock.get("processNames"),
    ):
        if isinstance(value, (list, tuple, set)):
            process_names.update(str(item).strip().lower() for item in value if str(item).strip())
        elif str(value or "").strip():
            process_names.add(str(value).strip().lower())
    title_fragments = {
        str(item).strip().lower()
        for item in (
            target_window.get("title"),
            target_window.get("windowTitle"),
            target_lock.get("windowTitle"),
            target_lock.get("label"),
            target_lock.get("appId"),
        )
        if str(item or "").strip() and str(item).strip().lower() not in {"desktop", "manual desktop", "agent_browser"}
    }
    return {
        "handles": handles,
        "processIds": process_ids,
        "processNames": process_names,
        "titleFragments": title_fragments,
        "hasTarget": bool(handles or process_ids or process_names or title_fragments),
    }


def _rpa_item_matches_target(item: Dict[str, Any], criteria: Dict[str, Any]) -> bool:
    if not criteria.get("hasTarget"):
        return False
    item_handle = _as_int(item.get("handle") or item.get("windowHandle"))
    if item_handle is not None and item_handle in criteria.get("handles", set()):
        return True
    item_process_id = _as_int(item.get("processId"))
    if item_process_id is not None and item_process_id in criteria.get("processIds", set()):
        return True
    item_process_name = str(item.get("processName") or "").strip().lower()
    if item_process_name and item_process_name in criteria.get("processNames", set()):
        return True
    identity = _rpa_surface_identity(item)
    return any(fragment and fragment in identity for fragment in criteria.get("titleFragments", set()))


_DEFAULT_NATIVE_INSPECTOR_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "backend": "auto",
    "captureGesture": "LeftClick",
    "alternateCaptureGesture": "Ctrl+LeftClick",
    "cancelGesture": "Esc",
    "hotkey": "Ctrl+Alt+C",
    "cancelHotkey": "Ctrl+Alt+X",
    "relockHotkey": "Ctrl+Alt+R",
    "hoverSampleHz": 12,
    "highlightOverlay": True,
    "ignoreAdminSurface": True,
}


def _native_inspector_helper_project_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "native" / "V8.Rpa.NativeInspector"


def _native_inspector_helper_publish_command() -> list[str]:
    project = _native_inspector_helper_project_dir() / "V8.Rpa.NativeInspector.csproj"
    return [
        "dotnet",
        "publish",
        str(project),
        "-c",
        "Release",
        "-r",
        "win-x64",
        "--self-contained",
        "false",
    ]


def _native_inspector_helper_install_command() -> list[str]:
    script = Path(__file__).resolve().parents[2] / "scripts" / "ensure_rpa_native_inspector.py"
    return [sys.executable or "python", str(script)]


def _native_inspector_helper_candidates(config: Dict[str, Any] | None = None) -> list[Path]:
    native_config = dict(config or {})
    explicit_candidates: list[Path] = []
    for value in (
        os.environ.get("V8_RPA_NATIVE_INSPECTOR_HELPER"),
        native_config.get("helperPath"),
    ):
        if value:
            explicit_candidates.append(Path(str(value)).expanduser())
    if explicit_candidates:
        candidates = explicit_candidates
    else:
        project_dir = _native_inspector_helper_project_dir()
        candidates = [
            project_dir / "bin" / "Release" / "net8.0-windows" / "win-x64" / "publish" / "V8.Rpa.NativeInspector.exe",
            project_dir / "bin" / "Release" / "net8.0-windows" / "win-x64" / "V8.Rpa.NativeInspector.exe",
            project_dir / "bin" / "Debug" / "net8.0-windows" / "win-x64" / "V8.Rpa.NativeInspector.exe",
            V8_AGENT_OS_HOME / "bin" / "V8.Rpa.NativeInspector" / "V8.Rpa.NativeInspector.exe",
        ]
    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _native_inspector_helper_status(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    candidates = _native_inspector_helper_candidates(config)
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return {
                    "available": True,
                    "state": "ready",
                    "path": str(candidate),
                    "projectPath": str(_native_inspector_helper_project_dir()),
                    "runtime": "net8.0-windows",
                    "package": "FlaUI",
                    "installCommand": _native_inspector_helper_install_command(),
                    "publishCommand": _native_inspector_helper_publish_command(),
                }
        except Exception:
            continue
    return {
        "available": False,
        "state": "helper_not_built",
        "reason": "V8.Rpa.NativeInspector .NET/FlaUI helper has not been published.",
        "projectPath": str(_native_inspector_helper_project_dir()),
        "candidates": [str(candidate) for candidate in candidates],
        "runtime": "net8.0-windows",
        "package": "FlaUI",
        "installCommand": _native_inspector_helper_install_command(),
        "publishCommand": _native_inspector_helper_publish_command(),
    }


def _native_inspector_config_from_disk() -> Dict[str, Any]:
    try:
        raw_config = storage.read_json("config.json")
    except Exception:
        raw_config = {}
    rpa_config = raw_config.get("rpa") if isinstance(raw_config, dict) else {}
    native_config = rpa_config.get("nativeInspector") if isinstance(rpa_config, dict) else {}
    merged = dict(_DEFAULT_NATIVE_INSPECTOR_CONFIG)
    if isinstance(native_config, dict):
        merged.update({key: value for key, value in native_config.items() if value is not None})
    merged["captureGesture"] = str(merged.get("captureGesture") or _DEFAULT_NATIVE_INSPECTOR_CONFIG["captureGesture"])
    merged["cancelGesture"] = str(merged.get("cancelGesture") or _DEFAULT_NATIVE_INSPECTOR_CONFIG["cancelGesture"])
    merged["hotkey"] = str(merged.get("hotkey") or _DEFAULT_NATIVE_INSPECTOR_CONFIG["hotkey"])
    merged["cancelHotkey"] = str(merged.get("cancelHotkey") or _DEFAULT_NATIVE_INSPECTOR_CONFIG["cancelHotkey"])
    merged["relockHotkey"] = str(merged.get("relockHotkey") or _DEFAULT_NATIVE_INSPECTOR_CONFIG["relockHotkey"])
    if merged.get("helperPath"):
        merged["helperPath"] = str(merged.get("helperPath"))
    try:
        merged["hoverSampleHz"] = max(2, min(30, int(float(merged.get("hoverSampleHz") or 12))))
    except Exception:
        merged["hoverSampleHz"] = 12
    merged["highlightOverlay"] = bool(merged.get("highlightOverlay", True))
    merged["ignoreAdminSurface"] = bool(merged.get("ignoreAdminSurface", True))
    return merged


def _native_hotkey_backend_capability(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    native_config = dict(config or _native_inspector_config_from_disk())
    if sys.platform.startswith("win"):
        helper = _native_inspector_helper_status(native_config)
        return {
            "backend": "windows_fla_ui_helper",
            "state": helper.get("state") or ("ready" if helper.get("available") else "helper_not_built"),
            "available": bool(helper.get("available")),
            "nativeInspector": True,
            "captureGesture": native_config.get("captureGesture") or "LeftClick",
            "alternateCaptureGesture": native_config.get("alternateCaptureGesture") or "Ctrl+LeftClick",
            "cancelGesture": native_config.get("cancelGesture") or "Esc",
            "hotkey": native_config.get("hotkey") or "Ctrl+Alt+C",
            "cancelHotkey": native_config.get("cancelHotkey") or "Ctrl+Alt+X",
            "relockHotkey": native_config.get("relockHotkey") or "Ctrl+Alt+R",
            "hoverSampleHz": native_config.get("hoverSampleHz") or 12,
            "highlightOverlay": bool(native_config.get("highlightOverlay", True)),
            "helper": helper,
            "fallback": "fallback_overlay",
        }
    if sys.platform == "darwin":
        return {
            "backend": "mac_ax",
            "state": "requires_accessibility_permission",
            "available": False,
            "nativeInspector": True,
            "captureGesture": native_config.get("captureGesture") or "LeftClick",
            "alternateCaptureGesture": native_config.get("alternateCaptureGesture") or "Ctrl+LeftClick",
            "cancelGesture": native_config.get("cancelGesture") or "Esc",
            "hotkey": native_config.get("hotkey") or "Ctrl+Alt+C",
            "cancelHotkey": native_config.get("cancelHotkey") or "Ctrl+Alt+X",
            "reason": "macOS native inspector helper is not bundled yet; it must use AXUIElement + Event Tap + NSPanel with Accessibility permission.",
            "fallback": "fallback_overlay",
        }
    return {
        "backend": "linux_atspi_or_portal",
        "state": "requires_portal_or_display_backend",
        "available": False,
        "nativeInspector": True,
        "captureGesture": native_config.get("captureGesture") or "LeftClick",
        "alternateCaptureGesture": native_config.get("alternateCaptureGesture") or "Ctrl+LeftClick",
        "cancelGesture": native_config.get("cancelGesture") or "Esc",
        "hotkey": native_config.get("hotkey") or "Ctrl+Alt+C",
        "cancelHotkey": native_config.get("cancelHotkey") or "Ctrl+Alt+X",
        "reason": "Linux native inspector helper is not bundled yet; X11 needs AT-SPI + overlay, Wayland needs portal capability checks.",
        "fallback": "fallback_overlay",
    }


def _capture_assistant_log_path(recording_id: str) -> Path:
    safe_id = _slug(recording_id, fallback="recording")
    log_dir = V8_AGENT_OS_HOME / "logs" / "rpa_capture_assistant"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{safe_id}-{uuid.uuid4().hex[:8]}.log"


def _tail_text_file(path: str | Path | None, *, max_chars: int = 2400) -> str:
    if not path:
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-max_chars:].strip()


def _capture_assistant_events_from_log(path: str | Path | None) -> list[Dict[str, Any]]:
    if not path:
        return []
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    events: list[Dict[str, Any]] = []
    for line in lines[-200:]:
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if isinstance(payload, dict) and str(payload.get("event") or "").startswith("rpa_capture_assistant."):
            events.append(payload)
        elif isinstance(payload, dict) and payload.get("ok") is False and (payload.get("error") or payload.get("warning")):
            events.append(payload)
    return events


def _capture_assistant_last_event(path: str | Path | None, event_suffix: str) -> Dict[str, Any] | None:
    for event in reversed(_capture_assistant_events_from_log(path)):
        event_name = str(event.get("event") or "")
        if event_name.endswith(event_suffix):
            return event
    return None


def _capture_assistant_error_event(path: str | Path | None) -> Dict[str, Any] | None:
    for event in reversed(_capture_assistant_events_from_log(path)):
        event_name = str(event.get("event") or "")
        if event_name.endswith(".error") or (not event_name and event.get("ok") is False and event.get("error")):
            return event
    return None


def _is_process_running(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            output = (result.stdout or "").strip()
            return result.returncode == 0 and str(pid) in output and "INFO:" not in output.upper()
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_process(pid: int) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0
        except Exception:
            return False
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


def _render_template_value(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", value.strip())
        if match:
            return variables.get(match.group(1), value)
        return value
    if isinstance(value, dict):
        return {str(key): _render_template_value(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_template_value(item, variables) for item in value]
    return value


def _is_unresolved_template_placeholder(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"\{\{\s*[a-zA-Z0-9_]+\s*\}\}", value.strip()))


class RPARuntime:
    kind = "rpa"

    def runtime_descriptor(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "displayName": "RPARuntime",
            "summary": "负责 trace 编译、.robot 导出、RPA 执行与失败后回退，不承担自由式桌面探索。",
            "responsibilities": [
                "把 ComputerUse trace 编译成 draft 和 .robot",
                "运行 Robot Framework / RPA Framework 流程",
                "在失败时回退 ComputerUse 并尝试局部修补",
            ],
            "routingKeywords": ["RPA", "复现流程", "Robot Framework", "自动化脚本", "流程模板"],
            "acceptedInputs": ["trace run ids", "draft id", "robot script", "variables"],
            "producedOutputs": ["draft", "robot script", "execution result", "repair metadata"],
            "ownedSteps": ["rpa.compile", "rpa.execute_draft", "rpa.local_repair", "rpa.export_robot"],
            "supportsPause": True,
            "supportsResume": False,
            "supportsApproval": False,
            "supportsRepair": True,
            "visibility": "specialized",
            "promptHints": [
                "已经存在稳定流程、希望快速复现或导出脚本时，优先交给 RPARuntime。",
                "当 RPA 失败时，允许它局部回退到 ComputerUse，而不是让 Supervisor 自己重跑整段流程。",
            ],
            "capabilities": [
                {
                    "key": "rpa.compile_execute",
                    "label": "RPA 编译与执行",
                    "summary": "管理 draft、.robot 导出、运行与失败修补闭环。",
                    "accepts": ["trace", "draft", "variables"],
                    "outputs": ["robot flow", "trust assessment", "fallback result"],
                    "examples": ["将成功探索固化成可复用流程", "运行已有 RPA 并在失败时局部修补"],
                    "risk_level": "high",
                }
            ],
            "metadata": {
                "managedToolPrefixes": ["rpa_"],
                "managedToolGroups": ["rpa.run"],
            },
        }

    def __init__(
        self,
        *,
        compiler: RPATraceCompiler = rpa_trace_compiler,
        adapter: RobotFrameworkAdapter = robot_framework_adapter,
        script_store: RPAScriptStore = rpa_script_store,
        template_service: RPATemplateService = rpa_template_service,
    ) -> None:
        self.compiler = compiler
        self.adapter = adapter
        self.script_store = script_store
        self.template_service = template_service
        self.recording_manager = RPARecordingManager(trace_store_instance=self.compiler.trace_store)
        ensure_system_rpa_seed_templates(self.script_store)

    def list_drafts(self, *, limit: int = 100, include_archived: bool = False) -> list[Dict[str, Any]]:
        return self.script_store.list_drafts(limit=limit, include_archived=include_archived)

    def list_robot_scripts(self, *, limit: int = 100) -> list[Dict[str, Any]]:
        return self.script_store.list_robot_scripts(limit=limit)

    def availability(self) -> Dict[str, Any]:
        return self.adapter.availability()

    def compile_trace_to_draft(self, run_id: str, *, save: bool = True) -> Dict[str, Any]:
        draft = self.compiler.compile_run_to_draft(run_id, save=save)
        self._log_audit(
            action=f"Compile trace to RPA draft: {run_id}",
            status="SUCCESS",
            details=json.dumps({"runId": run_id, "scriptId": draft.get("id")}, ensure_ascii=False),
        )
        return draft

    def compile_traces_to_draft(self, run_ids: list[str], *, save: bool = True) -> Dict[str, Any]:
        draft = self.compiler.compile_runs_to_draft(run_ids, save=save)
        self._log_audit(
            action="Compile traces to merged RPA draft",
            status="SUCCESS",
            details=json.dumps({"runIds": run_ids, "scriptId": draft.get("id")}, ensure_ascii=False),
        )
        return draft

    def get_draft(self, script_id: str) -> Optional[Dict[str, Any]]:
        return self.script_store.get_draft(script_id)

    def create_draft(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name") or payload.get("goal") or "Untitled RPA flow").strip() or "Untitled RPA flow"
        goal = str(payload.get("goal") or name).strip() or name
        app_id = str(payload.get("appId") or payload.get("app_id") or "desktop").strip() or "desktop"
        steps = list(payload.get("steps") or [])
        variables = list(payload.get("variables") or [])
        object_library = list(payload.get("objectLibrary") or payload.get("object_library") or [])
        metadata = dict(payload.get("metadata") or {})
        metadata.update(
            {
                "source": metadata.get("source") or "manual_canvas",
                "createdBy": metadata.get("createdBy") or "admin_rpa_studio",
                "createdFrom": metadata.get("createdFrom") or "manual_canvas",
                "savedAt": utc_now_iso(),
            }
        )
        script_id = str(payload.get("id") or f"draft_{_slug(name, fallback='manual-canvas')[:48]}_{uuid.uuid4().hex[:8]}")
        draft = {
            "id": script_id,
            "name": name,
            "goal": goal,
            "appId": app_id,
            "steps": steps,
            "variables": variables,
            "objectLibrary": object_library,
            "source": {
                "kind": "manual_canvas",
                "createdBy": "admin_rpa_studio",
            },
            "metadata": metadata,
        }
        saved = self.script_store.save_draft(draft)
        self._log_audit(
            action=f"Create RPA draft: {script_id}",
            status="SUCCESS",
            details=json.dumps(
                {
                    "scriptId": script_id,
                    "appId": app_id,
                    "stepCount": len(steps),
                    "source": metadata.get("source"),
                },
                ensure_ascii=False,
            ),
        )
        return saved

    def patch_draft(self, script_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        draft = self.get_draft(script_id)
        if not draft:
            raise ValueError(f"未找到 draft: {script_id}")
        allowed_top_level = {"name", "goal", "appId", "steps", "variables", "objectLibrary"}
        for key in allowed_top_level:
            if key in patch:
                draft[key] = patch[key]
        metadata_patch = patch.get("metadataPatch")
        if isinstance(metadata_patch, dict):
            metadata = dict(draft.get("metadata") or {})
            metadata.update(metadata_patch)
            draft["metadata"] = metadata
        saved = self.script_store.save_draft(draft)
        self._log_audit(
            action=f"Patch RPA draft: {script_id}",
            status="SUCCESS",
            details=json.dumps({"scriptId": script_id, "patchedKeys": sorted(set(patch.keys()) & (allowed_top_level | {"metadataPatch"}))}, ensure_ascii=False),
        )
        return saved

    def validate_draft_step(
        self,
        script_id: str,
        *,
        step: Dict[str, Any],
        index: int | None = None,
        mode: str = "dry_run",
        variables: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        draft = self.get_draft(script_id)
        if not draft:
            raise ValueError(f"未找到 draft: {script_id}")
        normalized_step = dict(step or {})
        normalized_mode = str(mode or "dry_run").strip().lower()
        params = dict(normalized_step.get("params") or {})
        target = dict(normalized_step.get("target") or {})
        selector = dict(target.get("selector") or {})
        target_window = dict(target.get("window") or {})
        coordinate = dict(normalized_step.get("coordinate") or {})
        verification = normalized_step.get("verification") if isinstance(normalized_step.get("verification"), dict) else {}
        use = str(normalized_step.get("use") or normalized_step.get("action") or "").strip().lower()
        checks: list[Dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []

        def add_check(name: str, ok: bool, message: str, **extra: Any) -> None:
            payload = {"name": name, "ok": bool(ok), "message": message}
            payload.update(extra)
            checks.append(payload)
            if not ok:
                errors.append(message)

        add_check("step_action", bool(use), "步骤动作已填写。" if use else "步骤动作不能为空。", use=use or None)

        selector_value = str(
            selector.get("css")
            or selector.get("xpath")
            or selector.get("role")
            or selector.get("selector")
            or params.get("selector")
            or ""
        ).strip()
        has_coordinate = coordinate.get("x") not in (None, "") and coordinate.get("y") not in (None, "")
        needs_target = use in {
            "click",
            "desktop_click",
            "browser_click",
            "double_click",
            "type_text",
            "find_and_type",
            "wait_for_element",
            "assert_element",
        }
        if needs_target:
            target_ok = bool(selector_value or has_coordinate)
            add_check(
                "target",
                target_ok,
                "目标定位可用。" if target_ok else "该步骤需要 selector、XPath、role 或坐标目标。",
                selector=selector_value or None,
                coordinateFallback=bool(has_coordinate and not selector_value),
            )
            if has_coordinate and not selector_value:
                warnings.append("当前步骤使用纯坐标 fallback，建议补充 DOM selector、XPath、UIA 元素或截图锚点。")

        if selector_value:
            selector_kind = "css"
            if selector_value.startswith(("/", ".//")):
                selector_kind = "xpath"
            elif selector_value.startswith("role:"):
                selector_kind = "role"
            elif selector_value.startswith(("elementId:", "automationId:", "name:")):
                selector_kind = "accessibility"
            add_check(
                "selector",
                True,
                f"{selector_kind} selector 格式已识别。",
                selectorKind=selector_kind,
                selector=selector_value,
            )
            if normalized_mode in {"selector", "dry_run"}:
                try:
                    live_selector = self._validate_live_selector(
                        selector_value=selector_value,
                        selector_kind=selector_kind,
                        target_window=target_window,
                        params=params,
                    )
                    if live_selector.get("checked"):
                        add_check(
                            "live_selector",
                            bool(live_selector.get("found")),
                            "实时目标已命中。" if live_selector.get("found") else "实时目标没有命中。",
                            details=live_selector,
                        )
                    else:
                        warnings.append(str(live_selector.get("reason") or "当前没有可用的实时 selector 验证后端。"))
                except Exception as exc:
                    warnings.append(f"实时 selector 验证失败：{exc}")

        text_value = params.get("text") or params.get("value") or normalized_step.get("text")
        variable_refs = re.findall(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", json.dumps(normalized_step, ensure_ascii=False))
        available_variables = set((variables or {}).keys())
        if use in {"type_text", "find_and_type"}:
            add_check(
                "input_text",
                text_value not in (None, "") or bool(variable_refs),
                "输入内容或变量占位符已填写。" if text_value not in (None, "") or variable_refs else "输入步骤需要 text/value 或变量占位符。",
            )
        missing_variables = sorted({item for item in variable_refs if item not in available_variables})
        if missing_variables:
            warnings.append(f"变量尚未在本次验证输入中提供：{', '.join(missing_variables)}")

        if normalized_mode in {"assertion", "verify_assertion"} or str(verification.get("type") or "").strip():
            assertion_type = str(verification.get("type") or params.get("assertion") or "").strip()
            expected = verification.get("expected") or verification.get("text") or params.get("expected")
            add_check(
                "assertion",
                bool(assertion_type and expected not in (None, "")),
                "断言条件已填写。" if assertion_type and expected not in (None, "") else "断言需要 type 与 expected/text。",
                assertionType=assertion_type or None,
            )
            if assertion_type and expected not in (None, ""):
                try:
                    live_assertion = self._validate_live_assertion(
                        assertion_type=assertion_type,
                        expected=expected,
                        selector_value=selector_value,
                        target_window=target_window,
                        params=params,
                    )
                    if live_assertion.get("checked"):
                        add_check(
                            "live_assertion",
                            bool(live_assertion.get("passed")),
                            "实时断言已通过。" if live_assertion.get("passed") else "实时断言未通过。",
                            details=live_assertion,
                        )
                    else:
                        warnings.append(str(live_assertion.get("reason") or "当前没有可用的实时断言验证后端。"))
                except Exception as exc:
                    warnings.append(f"实时断言验证失败：{exc}")

        normalized_step.setdefault("metadata", {})
        if isinstance(normalized_step["metadata"], dict):
            normalized_step["metadata"]["lastValidatedAt"] = utc_now_iso()
            normalized_step["metadata"]["lastValidationMode"] = normalized_mode
            normalized_step["metadata"]["lastValidationOk"] = not errors

        return {
            "ok": not errors,
            "mode": normalized_mode,
            "scriptId": script_id,
            "index": index,
            "checks": checks,
            "warnings": warnings,
            "errors": errors,
            "normalizedStep": _jsonable(normalized_step),
            "summary": "步骤可作为草稿保存。" if not errors else "步骤仍需补齐后再保存或试跑。",
        }

    def _validate_live_selector(
        self,
        *,
        selector_value: str,
        selector_kind: str,
        target_window: Dict[str, Any],
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        app_id = str(target_window.get("appId") or target_window.get("app_id") or params.get("appId") or params.get("app_id") or "").strip()
        window_title = str(target_window.get("title") or target_window.get("windowTitle") or params.get("windowTitle") or "").strip()
        target_url = str(target_window.get("url") or params.get("targetUrl") or params.get("browserTargetUrl") or "").strip()

        if selector_kind in {"css", "xpath"}:
            from runtimes.computer_use.runtime import computer_use_runtime

            decision = computer_use_runtime._browser_lane_decision(
                action_type="observe",
                action_payload={"app_id": app_id or None, "window_title": window_title or None, "browser_target_url": target_url or None},
                app_id=app_id or None,
            )
            if not bool(getattr(decision, "available", False)):
                return {"checked": False, "reason": "Agent Browser / browser lane 当前不可用。"}
            provider = computer_use_runtime.browser_automation
            provider._ensure_proxy(target_port=decision.target_port)
            targets = provider._list_targets()
            if not targets:
                return {"checked": False, "reason": "Agent Browser 没有可验证的打开页面。"}
            target_id = self._select_browser_target_id(targets, window_title=window_title, target_url=target_url)
            if not target_id:
                return {"checked": False, "reason": "未找到匹配的浏览器 tab。"}
            expression = self._browser_selector_probe_script(selector_value=selector_value, selector_kind=selector_kind)
            evaluated = provider._evaluate(target_id=target_id, expression=expression)
            value = dict(evaluated.get("value") or {})
            return {
                "checked": True,
                "backend": "agent_browser_dom",
                "targetId": target_id,
                "found": bool(value.get("found")),
                "tag": value.get("tag"),
                "textPreview": value.get("textPreview"),
                "visible": value.get("visible"),
            }

        if selector_kind == "accessibility" or selector_value.startswith(("role:", "name:", "automationId:", "elementId:")):
            from runtimes.computer_use.runtime import computer_use_runtime

            query: Dict[str, Any] = {"limit": 5}
            if selector_value.startswith("role:"):
                query["control_type"] = selector_value.split(":", 1)[1].strip()
            elif selector_value.startswith("automationId:"):
                query["automation_id"] = selector_value.split(":", 1)[1].strip()
            elif selector_value.startswith("elementId:"):
                query["element_id"] = selector_value.split(":", 1)[1].strip()
            elif selector_value.startswith("name:"):
                query["name_contains"] = selector_value.split(":", 1)[1].strip()
            if window_title:
                query["window_title"] = window_title
            result = computer_use_runtime.find_elements(**query)
            count = int(result.get("count") or len(result.get("elements") or []))
            return {
                "checked": True,
                "backend": "desktop_accessibility",
                "found": count > 0,
                "count": count,
                "elements": list(result.get("elements") or [])[:3],
            }

        return {"checked": False, "reason": "该 selector 类型目前只做静态格式验证。"}

    def _validate_live_assertion(
        self,
        *,
        assertion_type: str,
        expected: Any,
        selector_value: str,
        target_window: Dict[str, Any],
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        expected_text = str(expected or "")
        normalized_type = str(assertion_type or "").strip().lower()
        if normalized_type in {"text_exists", "url_matches", "element_visible"}:
            from runtimes.computer_use.runtime import computer_use_runtime

            decision = computer_use_runtime._browser_lane_decision(
                action_type="observe",
                action_payload={"window_title": target_window.get("title") or target_window.get("windowTitle")},
                app_id=str(target_window.get("appId") or target_window.get("app_id") or params.get("appId") or "").strip() or None,
            )
            if not bool(getattr(decision, "available", False)):
                return {"checked": False, "reason": "Agent Browser / browser lane 当前不可用。"}
            provider = computer_use_runtime.browser_automation
            provider._ensure_proxy(target_port=decision.target_port)
            targets = provider._list_targets()
            if not targets:
                return {"checked": False, "reason": "Agent Browser 没有可验证的打开页面。"}
            target_id = self._select_browser_target_id(
                targets,
                window_title=str(target_window.get("title") or target_window.get("windowTitle") or "").strip(),
                target_url=str(target_window.get("url") or params.get("targetUrl") or params.get("browserTargetUrl") or "").strip(),
            )
            if not target_id:
                return {"checked": False, "reason": "未找到匹配的浏览器 tab。"}
            expression = self._browser_assertion_probe_script(
                assertion_type=normalized_type,
                expected=expected_text,
                selector_value=selector_value,
            )
            evaluated = provider._evaluate(target_id=target_id, expression=expression)
            value = dict(evaluated.get("value") or {})
            return {
                "checked": True,
                "backend": "agent_browser_dom",
                "targetId": target_id,
                "passed": bool(value.get("passed")),
                "actualPreview": value.get("actualPreview"),
            }

        if normalized_type == "window_exists":
            from runtimes.computer_use.runtime import computer_use_runtime

            result = computer_use_runtime.list_windows(title_filter=expected_text, limit=5)
            items = list(result.get("windows") or result.get("items") or [])
            return {
                "checked": True,
                "backend": "desktop_window",
                "passed": bool(items),
                "matches": items[:3],
            }

        return {"checked": False, "reason": "该断言类型目前只做静态格式验证。"}

    @staticmethod
    def _select_browser_target_id(
        targets: list[Dict[str, Any]],
        *,
        window_title: str | None = None,
        target_url: str | None = None,
    ) -> str | None:
        title_hint = str(window_title or "").strip().lower()
        url_hint = str(target_url or "").strip().lower()
        if url_hint:
            for item in targets:
                url = str(item.get("url") or "").strip().lower()
                if url and (url == url_hint or url_hint in url):
                    return str(item.get("targetId") or item.get("id") or "").strip() or None
        if title_hint:
            for item in targets:
                title = str(item.get("title") or "").strip().lower()
                if title_hint in title:
                    return str(item.get("targetId") or item.get("id") or "").strip() or None
        for item in targets:
            url = str(item.get("url") or "").strip().lower()
            if url.startswith(("http://", "https://", "file://")):
                return str(item.get("targetId") or item.get("id") or "").strip() or None
        if targets:
            return str(targets[0].get("targetId") or targets[0].get("id") or "").strip() or None
        return None

    @staticmethod
    def _browser_selector_probe_script(*, selector_value: str, selector_kind: str) -> str:
        selector_json = json.dumps(selector_value, ensure_ascii=False)
        kind_json = json.dumps(selector_kind, ensure_ascii=False)
        return (
            "(() => {\n"
            f"  const selector = {selector_json};\n"
            f"  const kind = {kind_json};\n"
            "  let target = null;\n"
            "  if (kind === 'xpath') {\n"
            "    target = document.evaluate(selector, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue;\n"
            "  } else {\n"
            "    target = document.querySelector(selector);\n"
            "  }\n"
            "  if (!target) return { found: false };\n"
            "  const rect = target.getBoundingClientRect();\n"
            "  const style = window.getComputedStyle(target);\n"
            "  const visible = !!(rect.width || rect.height) && style.visibility !== 'hidden' && style.display !== 'none';\n"
            "  return { found: true, tag: target.tagName, visible, textPreview: String(target.textContent || target.value || '').slice(0, 160) };\n"
            "})()"
        )

    @staticmethod
    def _browser_assertion_probe_script(*, assertion_type: str, expected: str, selector_value: str) -> str:
        assertion_json = json.dumps(assertion_type, ensure_ascii=False)
        expected_json = json.dumps(expected, ensure_ascii=False)
        selector_json = json.dumps(selector_value, ensure_ascii=False)
        return (
            "(() => {\n"
            f"  const assertionType = {assertion_json};\n"
            f"  const expected = {expected_json};\n"
            f"  const selector = {selector_json};\n"
            "  const normalize = (value) => String(value || '').toLowerCase();\n"
            "  if (assertionType === 'url_matches') {\n"
            "    const actual = location.href;\n"
            "    return { passed: actual.includes(expected), actualPreview: actual };\n"
            "  }\n"
            "  if (assertionType === 'element_visible') {\n"
            "    const target = selector ? document.querySelector(selector) : null;\n"
            "    if (!target) return { passed: false, actualPreview: 'element_not_found' };\n"
            "    const rect = target.getBoundingClientRect();\n"
            "    const style = window.getComputedStyle(target);\n"
            "    const visible = !!(rect.width || rect.height) && style.visibility !== 'hidden' && style.display !== 'none';\n"
            "    return { passed: visible, actualPreview: target.tagName };\n"
            "  }\n"
            "  const text = document.body ? document.body.innerText || document.body.textContent || '' : '';\n"
            "  return { passed: normalize(text).includes(normalize(expected)), actualPreview: String(text).slice(0, 200) };\n"
            "})()"
        )

    def _resolve_browser_capture_target(self, payload: Dict[str, Any]) -> tuple[str | None, str | None]:
        explicit_target = str(payload.get("targetId") or payload.get("target_id") or "").strip()
        if explicit_target:
            return explicit_target, None
        app_id = str(payload.get("appId") or payload.get("app_id") or "").strip()
        window_title = str(payload.get("windowTitle") or payload.get("window_title") or "").strip()
        target_url = str(payload.get("targetUrl") or payload.get("target_url") or "").strip()
        decision = computer_use_runtime._browser_lane_decision(
            action_type="observe",
            action_payload={"app_id": app_id or None, "window_title": window_title or None, "browser_target_url": target_url or None},
            app_id=app_id or None,
        )
        if not bool(getattr(decision, "available", False)):
            return None, "Agent Browser / browser lane 当前不可用。"
        provider = computer_use_runtime.browser_automation
        provider._ensure_proxy(target_port=decision.target_port)
        targets = provider._list_targets()
        if not targets:
            return None, "Agent Browser 没有打开页面。"
        target_id = self._select_browser_target_id(targets, window_title=window_title, target_url=target_url)
        return target_id, None if target_id else "未找到匹配的浏览器 tab。"

    def _forward_recording_action(
        self,
        *,
        recording: Dict[str, Any],
        event: Dict[str, Any],
        coordinate: Dict[str, Any],
        params: Dict[str, Any],
        target: Dict[str, Any],
        window_title: str | None,
        window_handle: Any,
    ) -> Dict[str, Any]:
        action = str(event.get("action") or "").strip().lower()
        payload: Dict[str, Any] = {
            "session_id": str(recording.get("sessionId") or f"rpa:recording:{recording.get('recordingSessionId')}"),
            "user_id": "admin_rpa_recorder",
            "goal": f"rpa_recording_forward:{action or 'action'}",
            "window_title": window_title,
            "window_handle": window_handle,
            "prefer_sendinput_click": True,
        }
        selector = dict((target or {}).get("selector") or {})
        for source_key, target_key in {
            "elementId": "element_id",
            "name": "name",
            "nameContains": "name_contains",
            "automationId": "automation_id",
            "controlType": "control_type",
            "className": "class_name",
        }.items():
            value = selector.get(source_key) or target.get(source_key)
            if value not in (None, "", [], {}):
                payload[target_key] = value
        viewport_mapping = dict(event.get("viewportMapping") or event.get("viewport") or {})
        if coordinate:
            try:
                width = float(viewport_mapping.get("naturalWidth") or viewport_mapping.get("screenWidth") or 0)
                height = float(viewport_mapping.get("naturalHeight") or viewport_mapping.get("screenHeight") or 0)
                x = float(coordinate.get("x"))
                y = float(coordinate.get("y"))
                if width > 0 and height > 0:
                    payload["point"] = [max(0.0, min(1.0, x / width)), max(0.0, min(1.0, y / height))]
                    payload["spatial_anchor"] = {
                        "screenRelativePoint": payload["point"],
                        "displayBounds": [0, 0, int(width), int(height)],
                        "source": "rpa_desktop_live_overlay",
                    }
                else:
                    payload["point"] = [x, y]
            except Exception:
                pass
        try:
            if action in {"click", "double_click"}:
                payload["double"] = action == "double_click"
                return {"ok": True, "action": action, "result": _jsonable(computer_use_runtime.click(**payload))}
            if action == "right_click":
                return {"ok": True, "action": action, "result": _jsonable(computer_use_runtime.right_click(**payload))}
            if action == "type_text":
                payload["text"] = str(params.get("text") or event.get("text") or "")
                payload["clear_first"] = bool(params.get("clearFirst") or params.get("clear_first"))
                payload["press_enter"] = bool(params.get("pressEnter") or params.get("press_enter"))
                return {"ok": True, "action": action, "result": _jsonable(computer_use_runtime.type_text(**payload))}
            if action == "scroll":
                payload["amount"] = int(params.get("amount") or params.get("deltaY") or event.get("deltaY") or 0)
                return {"ok": True, "action": action, "result": _jsonable(computer_use_runtime.scroll(**payload))}
            if action == "hotkey":
                payload["sequence"] = str(params.get("sequence") or event.get("sequence") or "")
                if not payload["sequence"]:
                    return {"ok": False, "action": action, "error": "missing_hotkey_sequence"}
                return {"ok": True, "action": action, "result": _jsonable(computer_use_runtime.hotkey(**payload))}
            if action == "drag":
                start_point = params.get("startPoint") or params.get("start_point")
                end_point = params.get("endPoint") or params.get("end_point")
                if start_point:
                    payload["start_point"] = start_point
                if end_point:
                    payload["end_point"] = end_point
                return {"ok": True, "action": action, "result": _jsonable(computer_use_runtime.drag(**payload))}
            return {"ok": False, "action": action, "error": "unsupported_forward_action"}
        except Exception as exc:
            return {"ok": False, "action": action, "error": str(exc)}

    @staticmethod
    def _browser_recorder_install_script() -> str:
        return r"""
(() => {
  if (window.__v8RpaRecorder && window.__v8RpaRecorder.installed) {
    window.__v8RpaRecorder.enabled = true;
    return { installed: true, reused: true };
  }
  const recorder = window.__v8RpaRecorder = { installed: true, enabled: true, events: [] };
  const safeText = (value, max = 160) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, max);
  const isSensitive = (el) => {
    const text = `${el.type || ''} ${el.name || ''} ${el.id || ''} ${el.autocomplete || ''}`.toLowerCase();
    return /password|passwd|token|secret|apikey|api-key|credential/.test(text);
  };
  const cssEscape = (value) => {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, (ch) => `\\${ch}`);
  };
  const cssPath = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return `#${cssEscape(el.id)}`;
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let part = node.tagName.toLowerCase();
      if (node.getAttribute('data-testid')) part += `[data-testid="${node.getAttribute('data-testid')}"]`;
      else if (node.getAttribute('name')) part += `[name="${node.getAttribute('name')}"]`;
      else {
        let index = 1;
        let sib = node;
        while ((sib = sib.previousElementSibling)) {
          if (sib.tagName === node.tagName) index += 1;
        }
        part += `:nth-of-type(${index})`;
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(' > ');
  };
  const xpath = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      let index = 1;
      let sib = node;
      while ((sib = sib.previousElementSibling)) {
        if (sib.tagName === node.tagName) index += 1;
      }
      parts.unshift(`${node.tagName.toLowerCase()}[${index}]`);
      node = node.parentElement;
    }
    return '/' + parts.join('/');
  };
  const roleCandidate = (el) => {
    const role = el.getAttribute && (el.getAttribute('role') || el.getAttribute('aria-role'));
    const label = el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'));
    const text = safeText(label || el.innerText || el.textContent || el.value, 80);
    if (!role && !text) return null;
    return { kind: 'role', role: role || el.tagName.toLowerCase(), name: text };
  };
  const selectors = (el) => {
    const result = [];
    const css = cssPath(el);
    const xp = xpath(el);
    if (css) result.push({ kind: 'css', css });
    if (xp) result.push({ kind: 'xpath', xpath: xp });
    const role = roleCandidate(el);
    if (role) result.push(role);
    return result;
  };
  const baseEvent = (type, nativeEvent) => {
    const el = nativeEvent.target && nativeEvent.target.nodeType === 1 ? nativeEvent.target : document.activeElement;
    const rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : { left: 0, top: 0, width: 0, height: 0 };
    return {
      source: 'agent_browser_dom',
      action: type,
      url: location.href,
      title: document.title,
      selectorCandidates: selectors(el),
      target: {
        selector: selectors(el)[0] || {},
        window: { title: document.title, url: location.href }
      },
      coordinate: { x: Math.round((nativeEvent.clientX || rect.left + rect.width / 2)), y: Math.round((nativeEvent.clientY || rect.top + rect.height / 2)) },
      viewport: { width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio || 1 },
      params: {},
      metadata: {
        tag: el ? el.tagName : '',
        id: el && el.id || '',
        name: el && el.getAttribute && el.getAttribute('name') || '',
        textPreview: safeText(el && (el.innerText || el.textContent || el.value), 120),
        rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height }
      },
      mergeGroupId: ''
    };
  };
  const push = (event) => {
    if (!recorder.enabled) return;
    recorder.events.push({ ...event, eventId: `dom_${Date.now()}_${Math.random().toString(16).slice(2, 8)}`, recordedAt: new Date().toISOString() });
    if (recorder.events.length > 300) recorder.events.splice(0, recorder.events.length - 300);
  };
  document.addEventListener('click', (ev) => push(baseEvent('click', ev)), true);
  document.addEventListener('dblclick', (ev) => push(baseEvent('double_click', ev)), true);
  document.addEventListener('contextmenu', (ev) => push(baseEvent('right_click', ev)), true);
  document.addEventListener('input', (ev) => {
    const item = baseEvent('type_text', ev);
    const sensitive = isSensitive(ev.target);
    item.sensitiveInput = sensitive;
    item.params.text = sensitive ? '' : String(ev.target && ev.target.value || '');
    item.mergeGroupId = `input:${item.selectorCandidates[0]?.css || item.selectorCandidates[0]?.xpath || item.metadata.name || item.metadata.id || 'active'}`;
    push(item);
  }, true);
  document.addEventListener('change', (ev) => {
    const item = baseEvent('type_text', ev);
    const sensitive = isSensitive(ev.target);
    item.sensitiveInput = sensitive;
    item.params.text = sensitive ? '' : String(ev.target && ev.target.value || '');
    item.mergeGroupId = `input:${item.selectorCandidates[0]?.css || item.selectorCandidates[0]?.xpath || item.metadata.name || item.metadata.id || 'active'}`;
    push(item);
  }, true);
  document.addEventListener('wheel', (ev) => {
    const item = baseEvent('scroll', ev);
    item.params.amount = Math.round(ev.deltaY || 0);
    item.mergeGroupId = `scroll:${item.selectorCandidates[0]?.css || 'viewport'}`;
    push(item);
  }, true);
  document.addEventListener('keydown', (ev) => {
    if (!(ev.ctrlKey || ev.metaKey || ev.altKey)) return;
    const item = baseEvent('hotkey', ev);
    const parts = [];
    if (ev.ctrlKey) parts.push('Ctrl');
    if (ev.metaKey) parts.push('Meta');
    if (ev.altKey) parts.push('Alt');
    if (ev.shiftKey) parts.push('Shift');
    parts.push(ev.key);
    item.params.sequence = parts.join('+');
    push(item);
  }, true);
  document.addEventListener('submit', (ev) => push(baseEvent('submit', ev)), true);
  return { installed: true, reused: false };
})()
"""

    @staticmethod
    def _browser_recorder_drain_script(*, stop: bool = False) -> str:
        stop_literal = "true" if stop else "false"
        return (
            "(() => {\n"
            "  const recorder = window.__v8RpaRecorder;\n"
            "  if (!recorder) return { installed: false, events: [] };\n"
            "  const events = Array.isArray(recorder.events) ? recorder.events.splice(0, recorder.events.length) : [];\n"
            f"  if ({stop_literal}) recorder.enabled = false;\n"
            f"  return {{ installed: true, enabled: recorder.enabled, stopped: {stop_literal}, events }};\n"
            "})()"
        )

    @staticmethod
    def _coalesce_browser_events(events: list[Any]) -> list[Dict[str, Any]]:
        coalesced: list[Dict[str, Any]] = []
        for item in events:
            if not isinstance(item, dict):
                continue
            current = dict(item)
            merge_id = str(current.get("mergeGroupId") or "").strip()
            action = str(current.get("action") or "").strip()
            if merge_id and coalesced and str(coalesced[-1].get("mergeGroupId") or "") == merge_id and action in {"type_text", "scroll"}:
                if action == "scroll":
                    prev_params = dict(coalesced[-1].get("params") or {})
                    cur_params = dict(current.get("params") or {})
                    prev_params["amount"] = int(prev_params.get("amount") or 0) + int(cur_params.get("amount") or 0)
                    coalesced[-1]["params"] = prev_params
                    coalesced[-1]["recordedAt"] = current.get("recordedAt") or coalesced[-1].get("recordedAt")
                else:
                    coalesced[-1] = current
                continue
            coalesced.append(current)
        return coalesced

    @staticmethod
    def _browser_event_to_recording_event(raw_event: Dict[str, Any], *, target_id: str) -> Dict[str, Any]:
        event = dict(raw_event)
        metadata = dict(event.get("metadata") or {})
        metadata.update(
            {
                "source": "agent_browser_dom",
                "browserTargetId": target_id,
                "fragileCoordinateFallback": not bool(event.get("selectorCandidates")),
            }
        )
        event["metadata"] = metadata
        event["source"] = "agent_browser_dom"
        if event.get("action") == "submit":
            event["action"] = "click"
            event.setdefault("intent", "submit_form")
        return event

    def approve_draft_as_template(
        self,
        script_id: str,
        *,
        reviewer: str = "admin_ui",
        notes: str | None = None,
        metadata_patch: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        draft = self.get_draft(script_id)
        if not draft:
            raise ValueError(f"未找到 draft: {script_id}")
        if metadata_patch:
            draft_metadata = dict(draft.get("metadata") or {})
            draft_metadata.update(dict(metadata_patch or {}))
            draft["metadata"] = draft_metadata
            draft = self.script_store.save_draft(draft)
        synced_draft = self.template_service.sync_candidate_for_script(draft, save=True)
        template_id = str(
            (synced_draft.get("source") or {}).get("templateId")
            or (synced_draft.get("metadata") or {}).get("templateCandidateId")
            or ""
        ).strip()
        if not template_id:
            raise ValueError(f"Draft '{script_id}' 未能生成模板候选。")
        template = self.approve_template(template_id, reviewer=reviewer, notes=notes)
        self._log_audit(
            action=f"Approve RPA draft as template: {script_id}",
            status="SUCCESS",
            details=json.dumps({"scriptId": script_id, "templateId": template_id, "reviewer": reviewer}, ensure_ascii=False),
        )
        return {
            "draft": synced_draft,
            "template": template,
            "templateId": template_id,
            "status": "approved",
        }

    def archive_draft(self, script_id: str, *, actor: str = "system", reason: str | None = None) -> Dict[str, Any]:
        payload = self.script_store.archive_draft(script_id, actor=actor, reason=reason)
        self._log_audit(
            action=f"Archive RPA draft: {script_id}",
            status="SUCCESS",
            details=json.dumps({"scriptId": script_id, "actor": actor}, ensure_ascii=False),
        )
        return payload

    def restore_draft(self, script_id: str, *, actor: str = "system") -> Dict[str, Any]:
        payload = self.script_store.restore_draft(script_id, actor=actor)
        self._log_audit(
            action=f"Restore RPA draft: {script_id}",
            status="SUCCESS",
            details=json.dumps({"scriptId": script_id, "actor": actor}, ensure_ascii=False),
        )
        return payload

    def delete_draft(self, script_id: str, *, confirm: bool = False) -> Dict[str, Any]:
        payload = self.script_store.delete_draft(script_id, confirm=confirm)
        self._log_audit(
            action=f"Hard delete RPA draft: {script_id}",
            status="SUCCESS",
            details=json.dumps({"scriptId": script_id}, ensure_ascii=False),
        )
        return payload

    def list_recordings(self, *, limit: int = 20) -> list[Dict[str, Any]]:
        return self.recording_manager.list(limit=limit)

    def get_recording(self, recording_id: str) -> Optional[Dict[str, Any]]:
        return self.recording_manager.get(recording_id)

    def start_recording(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.start(payload)
        self._log_audit(
            action=f"Start RPA recording: {recording.get('recordingSessionId')}",
            status="INFO",
            details=json.dumps(
                {
                    "recordingSessionId": recording.get("recordingSessionId"),
                    "targetMode": recording.get("targetMode"),
                    "traceRunId": recording.get("traceRunId"),
                },
                ensure_ascii=False,
            ),
        )
        return recording

    def pause_recording(self, recording_id: str) -> Dict[str, Any]:
        return self.recording_manager.pause(recording_id)

    def resume_recording(self, recording_id: str) -> Dict[str, Any]:
        return self.recording_manager.resume(recording_id)

    def cancel_recording(self, recording_id: str) -> Dict[str, Any]:
        return self.recording_manager.cancel(recording_id)

    def append_recording_event(self, recording_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        return self.recording_manager.append_event(recording_id, event)

    def capture_assistant_event(self, recording_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.get(recording_id)
        if not recording:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        normalized_event = dict(event or {})
        normalized_event["source"] = normalized_event.get("source") or "rpa_capture_assistant"
        metadata = dict(normalized_event.get("metadata") or {})
        metadata.update(
            {
                "captureAssistant": True,
                "recordingSource": "host_capture_assistant",
            }
        )
        normalized_event["metadata"] = metadata
        assistant = dict(recording.get("captureAssistant") or {})
        target_from_event = normalized_event.get("target") if isinstance(normalized_event.get("target"), dict) else {}
        prepared_target = assistant.get("preparedTarget") if isinstance(assistant.get("preparedTarget"), dict) else {}
        sample = self.sample_recording_desktop(
            recording_id,
            {
                "event": normalized_event,
                "stepId": normalized_event.get("stepId") or recording.get("stepId"),
                "coordinate": normalized_event.get("coordinate"),
                "target": target_from_event,
                "targetWindow": normalized_event.get("targetWindow")
                or normalized_event.get("window")
                or target_from_event.get("window")
                or prepared_target,
                "targetProcess": target_from_event.get("process"),
                "params": normalized_event.get("params"),
                "forwardAction": False,
            },
        )
        selector_candidates: list[Dict[str, Any]] = []
        seen_selectors: set[str] = set()
        for source_candidate in [
            *(normalized_event.get("selectorCandidates") or []),
            *(sample.get("selectorCandidates") or []),
        ]:
            if not isinstance(source_candidate, dict):
                continue
            key = json.dumps(source_candidate, sort_keys=True, default=str)
            if key in seen_selectors:
                continue
            seen_selectors.add(key)
            selector_candidates.append(dict(source_candidate))
        temp_element_id = str(normalized_event.get("tempElementId") or f"temp_el_{hashlib.sha1(json.dumps(normalized_event, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:12]}")
        normalized_event["tempElementId"] = temp_element_id
        normalized_event["captureMode"] = metadata.get("captureMode") or metadata.get("capture_mode") or normalized_event.get("captureMode") or "capture_only"
        active_window = sample.get("activeWindow")
        if active_window:
            target = dict(normalized_event.get("target") or {})
            target.setdefault("window", active_window)
            normalized_event["target"] = target
        target = dict(normalized_event.get("target") or {})
        target_window = dict(
            normalized_event.get("targetWindow")
            or target.get("window")
            or active_window
            or prepared_target
            or {}
        )
        coordinate_anchor = _rpa_coordinate_anchor(
            coordinate=dict(normalized_event.get("coordinate") or {}),
            target_window=target_window,
            screen=dict(normalized_event.get("screen") or {}),
            existing=dict(normalized_event.get("coordinateAnchor") or metadata.get("coordinateAnchor") or {}),
        )
        image_anchor = _rpa_image_anchor(
            event=normalized_event,
            target_window=target_window,
            highlight_bounds=dict(normalized_event.get("highlightBounds") or metadata.get("highlightBounds") or {}),
        )
        if coordinate_anchor:
            normalized_event["coordinateAnchor"] = coordinate_anchor
            normalized_event["windowRelativeCoordinate"] = {
                "x": coordinate_anchor.get("x"),
                "y": coordinate_anchor.get("y"),
            }
        if image_anchor:
            normalized_event["imageAnchor"] = image_anchor
        spatial_anchor = dict(target.get("spatialAnchor") or {})
        if coordinate_anchor:
            spatial_anchor.update(
                {
                    "coordinateAnchor": coordinate_anchor,
                    "windowRelativeCoordinate": {
                        "x": coordinate_anchor.get("x"),
                        "y": coordinate_anchor.get("y"),
                    },
                    "windowRelativePoint": [coordinate_anchor.get("ratioX"), coordinate_anchor.get("ratioY")],
                    "windowBounds": [
                        int(float((coordinate_anchor.get("clientRect") or {}).get("left") or 0)),
                        int(float((coordinate_anchor.get("clientRect") or {}).get("top") or 0)),
                        int(float((coordinate_anchor.get("clientRect") or {}).get("right") or 0)),
                        int(float((coordinate_anchor.get("clientRect") or {}).get("bottom") or 0)),
                    ],
                    "source": normalized_event.get("captureBackend") or "rpa_capture_assistant",
                }
            )
        if image_anchor:
            spatial_anchor["imageAnchor"] = image_anchor
        if selector_candidates:
            normalized_event["selectorCandidates"] = selector_candidates
            normalized_event["fragileCoordinateFallback"] = False
            target.setdefault("selector", selector_candidates[0])
        else:
            normalized_event["fragileCoordinateFallback"] = True
        if spatial_anchor:
            spatial_anchor["fallback"] = not bool(selector_candidates)
            target["spatialAnchor"] = spatial_anchor
        if target_window:
            target["window"] = target_window
        if target:
            normalized_event["target"] = target
        metadata.update(
            {
                "selectorCandidates": selector_candidates,
                "coordinateAnchor": coordinate_anchor,
                "imageAnchor": image_anchor,
                "windowRelativeCoordinate": normalized_event.get("windowRelativeCoordinate"),
                "targetWindow": target_window,
            }
        )
        normalized_event["metadata"] = metadata
        normalized_event["accessibilitySample"] = {
            key: value
            for key, value in {
                "backend": sample.get("backend"),
                "activeWindow": sample.get("activeWindow"),
                "error": (sample.get("sample") or {}).get("error") if isinstance(sample.get("sample"), dict) else None,
            }.items()
            if value not in (None, "", [], {})
        }
        appended = self.recording_manager.append_event(recording_id, normalized_event)
        pool_item = self._capture_pool_item_from_event(
            recording_id=recording_id,
            event=normalized_event,
            sample=sample,
            selector_candidates=selector_candidates,
            temp_element_id=temp_element_id,
        )
        pool_recording = self.recording_manager.add_capture_pool_item(recording_id, pool_item)
        assistant = dict((appended.get("recording") or recording).get("captureAssistant") or {})
        if assistant:
            assistant.update(
                {
                    "state": "captured",
                    "lastCapturedAt": utc_now_iso(),
                    "lastSelectorCount": len(selector_candidates),
                }
            )
            appended["recording"] = self.recording_manager.update_capture_assistant(recording_id, assistant)
        else:
            appended["recording"] = pool_recording
        return {
            "ok": True,
            "status": "captured",
            "recordingId": recording_id,
            "tempElementId": temp_element_id,
            "selectorCandidates": selector_candidates,
            "capturePoolItem": pool_item,
            "sample": sample,
            "recording": appended.get("recording"),
            "step": appended.get("step"),
        }

    def _capture_pool_item_from_event(
        self,
        *,
        recording_id: str,
        event: Dict[str, Any],
        sample: Dict[str, Any],
        selector_candidates: list[Dict[str, Any]],
        temp_element_id: str,
    ) -> Dict[str, Any]:
        coordinate = dict(event.get("coordinate") or {})
        target = dict(event.get("target") or {})
        window = dict(target.get("window") or sample.get("activeWindow") or {})
        params = dict(event.get("params") or {})
        coordinate_anchor = dict(event.get("coordinateAnchor") or (event.get("metadata") or {}).get("coordinateAnchor") or {})
        if not coordinate_anchor and coordinate:
            coordinate_anchor = _rpa_coordinate_anchor(
                coordinate=coordinate,
                target_window=window,
                screen=dict(event.get("screen") or {}),
            )
        image_anchor = dict(event.get("imageAnchor") or (event.get("metadata") or {}).get("imageAnchor") or {})
        if not image_anchor:
            image_anchor = _rpa_image_anchor(event=event, target_window=window, highlight_bounds=dict(event.get("highlightBounds") or {}))
        label = (
            str(params.get("label") or params.get("text") or "").strip()
            or str(window.get("title") or "").strip()
            or f"{event.get('action') or 'capture'}@{coordinate.get('x', '?')},{coordinate.get('y', '?')}"
        )
        best_selector = selector_candidates[0] if selector_candidates else {}
        return {
            "tempElementId": temp_element_id,
            "label": label[:160],
            "source": event.get("source") or "rpa_capture_assistant",
            "action": event.get("action"),
            "sourceStepId": event.get("sourceStepId") or event.get("stepId"),
            "stepId": event.get("stepId"),
            "targetStepId": event.get("targetStepId"),
            "targetWindow": window,
            "selectorCandidates": selector_candidates,
            "selector": best_selector,
            "coordinate": coordinate,
            "coordinateAnchor": coordinate_anchor,
            "imageAnchor": image_anchor,
            "anchorBundle": {
                "selectorCandidates": selector_candidates,
                "imageAnchor": image_anchor,
                "coordinateAnchor": coordinate_anchor,
            },
            "windowRelativeCoordinate": event.get("windowRelativeCoordinate"),
            "confidence": best_selector.get("confidence") if isinstance(best_selector, dict) else None,
            "screenshotAnchorRefs": list(event.get("screenshotAnchorRefs") or []),
            "fragileCoordinateFallback": bool(event.get("fragileCoordinateFallback")),
            "coordinateFallback": bool(event.get("coordinateFallback") or event.get("fragileCoordinateFallback")),
            "targetMatch": event.get("targetMatch"),
            "filteredReason": event.get("filteredReason"),
            "nativeInspectorSessionId": event.get("nativeInspectorSessionId") or (event.get("metadata") or {}).get("nativeInspectorSessionId"),
            "captureBackend": event.get("captureBackend") or (event.get("metadata") or {}).get("captureBackend"),
            "highlightBounds": event.get("highlightBounds") or (event.get("metadata") or {}).get("highlightBounds"),
            "hoverSample": event.get("hoverSample") or (event.get("metadata") or {}).get("hoverSample"),
            "screenshotAnchor": event.get("screenshotAnchor") or (event.get("metadata") or {}).get("screenshotAnchor"),
            "captureMode": event.get("captureMode"),
            "recordingId": recording_id,
            "capturedAt": utc_now_iso(),
        }

    def sample_recording_desktop(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.get(recording_id)
        if not recording:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        event = dict(payload.get("event") or {})
        step_id = str(payload.get("stepId") or event.get("stepId") or recording.get("stepId") or "").strip() or None
        coordinate = dict(event.get("coordinate") or payload.get("coordinate") or {})
        target = dict(event.get("target") or payload.get("target") or {})
        target_lock = dict(recording.get("targetLock") or {})
        target_lock.update(dict(target or {}))
        target_lock.update(dict(payload.get("targetLock") or {}))
        params = dict(event.get("params") or payload.get("params") or {})
        assistant = dict(recording.get("captureAssistant") or {})
        prepared_target = dict(assistant.get("preparedTarget") or {}) if isinstance(assistant.get("preparedTarget"), dict) else {}
        window = dict(
            payload.get("targetWindow")
            or target.get("window")
            or target_lock.get("window")
            or prepared_target
            or {}
        )
        target_process = dict(payload.get("targetProcess") or target.get("process") or {})
        manual_desktop_mode = bool(payload.get("manualDesktopMode") or payload.get("manual_desktop_mode"))
        criteria = _rpa_target_criteria(
            target_window=window,
            target_process=target_process,
            target_lock=target_lock,
        )
        window_title = str(
            window.get("title")
            or window.get("windowTitle")
            or event.get("windowTitle")
            or (recording.get("activeApp") or {}).get("windowTitle")
            or ""
        ).strip() or None
        window_handle = (
            window.get("handle")
            or window.get("windowHandle")
            or event.get("windowHandle")
            or recording.get("windowHandle")
        )
        if not criteria.get("hasTarget") and not manual_desktop_mode:
            return {
                "ok": True,
                "status": "target_context_required",
                "recordingId": recording_id,
                "backend": "desktop_accessibility",
                "reason": "当前步骤没有目标窗口/进程上下文，已跳过全桌面采样，避免把 Admin、任务栏或托盘写入临时池。",
                "sample": {
                    "observation": None,
                    "windows": [],
                    "elements": [],
                    "error": None,
                },
                "selectorCandidates": [],
                "activeWindow": None,
                "capturePoolItems": [],
                "capturePoolAdded": 0,
                "recording": recording,
            }
        observation: Dict[str, Any] | None = None
        windows: list[Dict[str, Any]] = []
        elements: list[Dict[str, Any]] = []
        selector_candidates: list[Dict[str, Any]] = []
        for candidate in list(event.get("selectorCandidates") or []):
            if isinstance(candidate, dict):
                selector_candidates.append({**candidate, "targetMatch": True})
        backend = "desktop_accessibility"
        sample_error: str | None = None
        query_constrained = bool(window_title or window_handle)
        try:
            observation = computer_use_runtime.observe(
                session_id=str(recording.get("sessionId") or f"rpa:recording:{recording_id}"),
                user_id="admin_rpa_recorder",
                goal="rpa_recording_desktop_sample",
                window_title=window_title,
                window_handle=window_handle,
                depth_limit=4,
                element_limit=30,
                include_screenshot=False,
            )
        except Exception as exc:
            sample_error = str(exc)
        try:
            window_result = computer_use_runtime.list_windows(title_filter=window_title, limit=12)
            raw_windows = list(window_result.get("windows") or window_result.get("items") or [])
            windows = [
                item
                for item in raw_windows
                if isinstance(item, dict)
                and not _rpa_is_admin_surface(item)
                and not _rpa_is_system_shell_surface(item)
                and (manual_desktop_mode or _rpa_item_matches_target(item, criteria))
            ][:8]
        except Exception:
            windows = []
        try:
            element_result = computer_use_runtime.find_elements(
                window_title=window_title,
                window_handle=window_handle,
                limit=8,
                depth_limit=6,
            )
            raw_elements = list(element_result.get("elements") or [])
            elements = []
            for item in raw_elements:
                if not isinstance(item, dict):
                    continue
                if _rpa_is_admin_surface(item) or _rpa_is_system_shell_surface(item):
                    continue
                if not manual_desktop_mode and not query_constrained and not _rpa_item_matches_target(item, criteria):
                    continue
                elements.append(item)
                if len(elements) >= 8:
                    break
        except Exception:
            elements = []
        for item in elements[:8]:
            if not isinstance(item, dict):
                continue
            candidate = {
                key: value
                for key, value in {
                    "kind": "accessibility",
                    "elementId": item.get("elementId") or item.get("id"),
                    "name": item.get("name"),
                    "automationId": item.get("automationId") or item.get("automation_id"),
                    "controlType": item.get("controlType") or item.get("role"),
                    "className": item.get("className"),
                    "bounds": item.get("bounds"),
                }.items()
                if value not in (None, "", [], {})
            }
            if candidate:
                candidate["targetMatch"] = bool(manual_desktop_mode or query_constrained or _rpa_item_matches_target(item, criteria))
                selector_candidates.append(candidate)
        capture_pool_items: list[Dict[str, Any]] = []
        capture_pool_recording: Dict[str, Any] | None = None
        if bool(payload.get("writeToCapturePool") or payload.get("write_to_capture_pool")):
            try:
                max_pool_items = int(payload.get("maxPoolItems") or payload.get("max_pool_items") or 8)
            except Exception:
                max_pool_items = 8
            max_pool_items = max(1, min(max_pool_items, 20))
            pool_candidates = selector_candidates[:max_pool_items]
            if not pool_candidates and coordinate:
                pool_candidates = [{}]
            for index, candidate in enumerate(pool_candidates):
                candidate_payload = candidate if isinstance(candidate, dict) else {}
                target_match = bool(candidate_payload.get("targetMatch") or manual_desktop_mode or query_constrained)
                if not target_match and candidate_payload:
                    continue
                event_for_pool = dict(event)
                event_for_pool.setdefault("source", "desktop_accessibility")
                event_for_pool.setdefault("action", "sample_elements")
                if step_id:
                    event_for_pool["stepId"] = step_id
                    event_for_pool["sourceStepId"] = step_id
                event_for_pool["selectorCandidates"] = [candidate_payload] if candidate_payload else []
                event_for_pool["fragileCoordinateFallback"] = not bool(candidate_payload)
                event_for_pool["coordinate"] = coordinate
                event_for_pool["target"] = {
                    **target,
                    "window": window or (windows[0] if windows else None),
                    "process": target_process,
                }
                event_for_pool["targetMatch"] = target_match
                pool_window = window or (windows[0] if windows else None)
                coordinate_anchor = _rpa_coordinate_anchor(
                    coordinate=coordinate,
                    target_window=pool_window,
                    screen=dict(event.get("screen") or payload.get("screen") or {}),
                    existing=dict(event.get("coordinateAnchor") or {}),
                )
                image_anchor = _rpa_image_anchor(
                    event=event_for_pool,
                    target_window=pool_window,
                    highlight_bounds=dict(candidate_payload.get("bounds") or event.get("highlightBounds") or {}),
                )
                event_for_pool["coordinateAnchor"] = coordinate_anchor
                event_for_pool["imageAnchor"] = image_anchor
                event_for_pool["windowRelativeCoordinate"] = (
                    {"x": coordinate_anchor.get("x"), "y": coordinate_anchor.get("y")}
                    if coordinate_anchor
                    else _rpa_window_relative_coordinate(coordinate, pool_window)
                )
                spatial_anchor = dict((event_for_pool.get("target") or {}).get("spatialAnchor") or {})
                if coordinate_anchor:
                    spatial_anchor.update(
                        {
                            "coordinateAnchor": coordinate_anchor,
                            "imageAnchor": image_anchor,
                            "windowRelativeCoordinate": event_for_pool["windowRelativeCoordinate"],
                            "windowRelativePoint": [coordinate_anchor.get("ratioX"), coordinate_anchor.get("ratioY")],
                            "fallback": not bool(candidate_payload),
                        }
                    )
                    event_for_pool["target"]["spatialAnchor"] = spatial_anchor
                event_for_pool["captureMode"] = event_for_pool.get("captureMode") or "sample_to_pool"
                stable_key = {
                    "recordingId": recording_id,
                    "stepId": step_id,
                    "candidate": candidate_payload,
                    "coordinate": coordinate,
                    "index": index,
                }
                temp_element_id = str(event_for_pool.get("tempElementId") or f"temp_el_{hashlib.sha1(json.dumps(stable_key, sort_keys=True, default=str).encode('utf-8')).hexdigest()[:12]}")
                event_for_pool["tempElementId"] = temp_element_id
                pool_item = self._capture_pool_item_from_event(
                    recording_id=recording_id,
                    event=event_for_pool,
                    sample={
                        "activeWindow": window or (windows[0] if windows else None),
                        "backend": backend,
                    },
                    selector_candidates=[candidate_payload] if candidate_payload else [],
                    temp_element_id=temp_element_id,
                )
                candidate_label = str(
                    candidate_payload.get("name")
                    or candidate_payload.get("automationId")
                    or candidate_payload.get("elementId")
                    or candidate_payload.get("controlType")
                    or ""
                ).strip()
                if candidate_label:
                    pool_item["label"] = candidate_label[:160]
                pool_item["sourceStepId"] = step_id
                pool_item["targetMatch"] = target_match
                pool_item["windowRelativeCoordinate"] = event_for_pool.get("windowRelativeCoordinate")
                capture_pool_recording = self.recording_manager.add_capture_pool_item(recording_id, pool_item)
                capture_pool_items.append(pool_item)
        forwarded_result: Dict[str, Any] | None = None
        if bool(payload.get("forwardAction")):
            forwarded_result = self._forward_recording_action(
                recording=recording,
                event=event,
                coordinate=coordinate,
                params=params,
                target=target,
                window_title=window_title,
                window_handle=window_handle,
            )
        return {
            "ok": True,
            "status": "sampled" if capture_pool_items or selector_candidates else "empty",
            "recordingId": recording_id,
            "backend": backend,
            "sample": {
                "observation": _jsonable(observation) if observation else None,
                "windows": _jsonable(windows),
                "elements": _jsonable(elements),
                "error": sample_error,
            },
            "selectorCandidates": selector_candidates,
            "activeWindow": window or (windows[0] if windows else None),
            "forwardedActionResult": forwarded_result,
            "capturePoolItems": capture_pool_items,
            "capturePoolAdded": len(capture_pool_items),
            "recording": capture_pool_recording or recording,
            "reason": None if capture_pool_items or selector_candidates else "目标范围内没有可写入临时池的元素。",
        }

    def prepare_capture_target(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.get(recording_id)
        if not recording:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        target_lock = dict(recording.get("targetLock") or {})
        target_lock.update(dict(payload.get("targetLock") or {}))
        top_level_app_id = str(
            payload.get("appId")
            or payload.get("app")
            or payload.get("applicationId")
            or ""
        ).strip()
        if top_level_app_id:
            target_lock["appId"] = top_level_app_id
        top_level_label = str(payload.get("label") or payload.get("appName") or "").strip()
        if top_level_label:
            target_lock["label"] = top_level_label
        top_level_mode = str(payload.get("mode") or payload.get("targetMode") or "").strip()
        if top_level_mode:
            target_lock["mode"] = top_level_mode
        if "consoleTargetBlocked" in payload:
            target_lock["consoleTargetBlocked"] = bool(payload.get("consoleTargetBlocked"))
        if "ignoreAdminSurface" in payload:
            target_lock["ignoreAdminSurface"] = bool(payload.get("ignoreAdminSurface"))
        window = dict(target_lock.get("window") or {})
        if not window:
            top_window_title = str(payload.get("windowTitle") or "").strip()
            top_window_handle = payload.get("windowHandle")
            if top_window_title:
                window["title"] = top_window_title
            if isinstance(top_window_handle, int):
                window["handle"] = top_window_handle
            elif isinstance(top_window_handle, str) and top_window_handle.strip().isdigit():
                window["handle"] = int(top_window_handle.strip())
            if window:
                target_lock["window"] = window
        app_id = str(target_lock.get("appId") or recording.get("appId") or "").strip()
        label = str(target_lock.get("label") or app_id or "desktop").strip()
        window = dict(target_lock.get("window") or {})
        try:
            prepare_wait_ms = int(payload.get("waitTimeoutMs") or payload.get("wait_timeout_ms") or 15000)
        except Exception:
            prepare_wait_ms = 15000
        prepare_wait_ms = max(4500, min(prepare_wait_ms, 30000))
        if bool(target_lock.get("consoleTargetBlocked")):
            return {
                "ok": False,
                "status": "blocked_admin_surface",
                "reason": "V8 Admin console window is excluded from RPA capture.",
                "recording": recording,
            }
        if not app_id or app_id == "desktop":
            assistant = dict(recording.get("captureAssistant") or {})
            assistant.update(
                {
                    "prepareStatus": "manual_coordinate_available",
                    "preparedAt": utc_now_iso(),
                    "targetLock": target_lock,
                    "stepId": payload.get("stepId") or recording.get("stepId"),
                    "selectedStepKey": payload.get("selectedStepKey") or recording.get("selectedStepKey"),
                    "workflowSnapshot": payload.get("workflowSnapshot") or recording.get("workflowSnapshot") or {},
                    "launchStep": payload.get("launchStep"),
                    "targetStep": payload.get("targetStep"),
                }
            )
            updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
            return {
                "ok": False,
                "status": "manual_coordinate_available",
                "targetStable": False,
                "reason": "当前步骤没有明确应用或窗口目标。可以选择目标窗口，或进入手动桌面坐标模式。",
                "manualCoordinateAvailable": True,
                "recording": updated,
            }
        focused: Dict[str, Any] | None = None
        focus_error: str | None = None
        try:
            focused = computer_use_runtime.focus_window(
                app_id=app_id,
                app_name=label,
                window_title=str(window.get("title") or target_lock.get("windowTitle") or "") or None,
                window_handle=window.get("handle") if isinstance(window.get("handle"), int) else None,
                user_id="admin_rpa_recorder",
                session_id=f"rpa:recording:{recording_id}",
                goal="rpa_prepare_capture_target",
                invocation_metadata={"triggerSource": "rpa_capture_assistant"},
                post_action_settle_timeout_ms=max(5000, min(prepare_wait_ms, 15000)),
            )
        except Exception as exc:
            focus_error = str(exc)
        if not focused or str(focused.get("status") or "").lower() not in {"completed", "ok", "success"}:
            try:
                focused = computer_use_runtime.open_app(
                    app_id=app_id,
                    app_name=label,
                    user_id="admin_rpa_recorder",
                    session_id=f"rpa:recording:{recording_id}",
                    goal="rpa_prepare_capture_target_open",
                    invocation_metadata={"triggerSource": "rpa_capture_assistant"},
                    wait_timeout_ms=prepare_wait_ms,
                )
            except Exception as exc:
                focus_error = str(exc)
        target_window = dict((focused or {}).get("target") or {}) if isinstance(focused, dict) else {}
        if not target_window and isinstance(focused, dict):
            for key in ("window", "activeWindow", "focusedWindow"):
                if isinstance(focused.get(key), dict):
                    target_window = dict(focused.get(key) or {})
                    break
        if target_window and _rpa_is_admin_surface(target_window):
            target_window = {}
        target_candidates: list[Dict[str, Any]] = []
        candidate_reason = ""
        try:
            title_filter = str(window.get("title") or window.get("windowTitle") or label or "").strip() or None
            candidate_result = computer_use_runtime.list_windows(title_filter=title_filter, limit=12)
            raw_candidates = list(candidate_result.get("windows") or candidate_result.get("items") or [])
            criteria = _rpa_target_criteria(
                target_window=target_window or window,
                target_process={},
                target_lock={**target_lock, "label": label, "appId": app_id},
            )
            for item in raw_candidates:
                if not isinstance(item, dict):
                    continue
                if _rpa_is_admin_surface(item) or _rpa_is_system_shell_surface(item):
                    continue
                if target_window and _rpa_item_matches_target(item, _rpa_target_criteria(target_window=target_window)):
                    target_candidates.append(item)
                elif not target_window and _rpa_item_matches_target(item, criteria):
                    target_candidates.append(item)
                if len(target_candidates) >= 8:
                    break
            if not target_candidates and title_filter:
                fallback_result = computer_use_runtime.list_windows(title_filter=None, limit=24)
                for item in list(fallback_result.get("windows") or fallback_result.get("items") or []):
                    if not isinstance(item, dict):
                        continue
                    if _rpa_is_admin_surface(item) or _rpa_is_system_shell_surface(item):
                        continue
                    if _rpa_item_matches_target(item, criteria):
                        target_candidates.append(item)
                    if len(target_candidates) >= 8:
                        break
        except Exception as exc:
            candidate_reason = str(exc)
            target_candidates = []
        if not target_window and len(target_candidates) == 1:
            target_window = dict(target_candidates[0])
        if target_window:
            handle_for_maximize = target_window.get("handle") or target_window.get("windowHandle")
            if _rpa_maximize_capture_window(handle_for_maximize):
                target_window["maximizedForCapture"] = True
                time.sleep(0.25)
        status = "target_prepared" if target_window else "launched_no_visible_window"
        ok = bool(target_window)
        if not target_window and len(target_candidates) > 1:
            status = "target_candidates_required"
        elif not target_window and not focus_error:
            status = "launched_no_visible_window"
        assistant = dict(recording.get("captureAssistant") or {})
        assistant.update(
            {
                "prepareStatus": status,
                "targetLock": target_lock,
                "preparedAt": utc_now_iso(),
                "preparedTarget": target_window,
                "targetCandidates": _jsonable(target_candidates),
                "prepareError": focus_error,
                "candidateError": candidate_reason,
                "stepId": payload.get("stepId") or recording.get("stepId"),
                "selectedStepKey": payload.get("selectedStepKey") or recording.get("selectedStepKey"),
                "workflowSnapshot": payload.get("workflowSnapshot") or recording.get("workflowSnapshot") or {},
                "launchStep": payload.get("launchStep"),
                "targetStep": payload.get("targetStep"),
            }
        )
        updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
        return {
            "ok": ok,
            "status": status,
            "targetStable": bool(target_window),
            "targetWindow": target_window,
            "targetCandidates": _jsonable(target_candidates),
            "focusResult": _jsonable(focused),
            "reason": (
                focus_error
                or candidate_reason
                or (
                    "应用已启动，但没有发现可捕获的目标窗口。若它只在托盘/后台运行，请先打开主窗口；也可以使用坐标 fallback。"
                    if status == "launched_no_visible_window"
                    else "发现多个候选窗口，请先选择一个目标窗口。"
                    if status == "target_candidates_required"
                    else None
                )
            ),
            "recording": updated,
        }

    def start_browser_capture(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.get(recording_id)
        if not recording:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        target_id, reason = self._resolve_browser_capture_target(payload)
        if not target_id:
            return {
                "ok": False,
                "status": "browser_capture_unavailable",
                "reason": reason or "Agent Browser 没有可注入的当前 tab。",
            }
        provider = computer_use_runtime.browser_automation
        evaluated = provider._evaluate(target_id=target_id, expression=self._browser_recorder_install_script())
        return {
            "ok": True,
            "status": "recording",
            "recordingId": recording_id,
            "targetId": target_id,
            "injected": bool((evaluated.get("value") or {}).get("installed", True)) if isinstance(evaluated, dict) else True,
        }

    def poll_browser_capture(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.get(recording_id)
        if not recording:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        target_id, reason = self._resolve_browser_capture_target(payload)
        if not target_id:
            return {
                "ok": False,
                "status": "browser_capture_unavailable",
                "reason": reason or "Agent Browser 没有可轮询的当前 tab。",
                "events": [],
            }
        provider = computer_use_runtime.browser_automation
        evaluated = provider._evaluate(target_id=target_id, expression=self._browser_recorder_drain_script())
        value = dict((evaluated or {}).get("value") or {})
        raw_events = list(value.get("events") or [])
        max_events = max(1, min(int(payload.get("maxEvents") or 50), 200))
        appended: list[Dict[str, Any]] = []
        for raw_event in self._coalesce_browser_events(raw_events[:max_events]):
            event = self._browser_event_to_recording_event(raw_event, target_id=target_id)
            appended.append(self.recording_manager.append_event(recording_id, event))
        return {
            "ok": True,
            "status": "recording",
            "recordingId": recording_id,
            "targetId": target_id,
            "events": raw_events[:max_events],
            "appendedCount": len(appended),
            "recording": appended[-1].get("recording") if appended else self.recording_manager.get(recording_id),
        }

    def stop_browser_capture(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.recording_manager.get(recording_id):
            raise ValueError(f"Recording session '{recording_id}' not found.")
        target_id, reason = self._resolve_browser_capture_target(payload)
        if not target_id:
            return {
                "ok": False,
                "status": "browser_capture_unavailable",
                "reason": reason or "Agent Browser 没有可停止的当前 tab。",
            }
        provider = computer_use_runtime.browser_automation
        evaluated = provider._evaluate(target_id=target_id, expression=self._browser_recorder_drain_script(stop=True))
        raw_events = list((evaluated.get("value") or {}).get("events") or []) if isinstance(evaluated, dict) else []
        appended: list[Dict[str, Any]] = []
        for raw_event in self._coalesce_browser_events(raw_events):
            event = self._browser_event_to_recording_event(raw_event, target_id=target_id)
            appended.append(self.recording_manager.append_event(recording_id, event))
        return {
            "ok": True,
            "status": "stopped",
            "targetId": target_id,
            "remainingEvents": raw_events,
            "appendedCount": len(appended),
            "recording": appended[-1].get("recording") if appended else self.recording_manager.get(recording_id),
        }

    def capture_assistant_status(self) -> Dict[str, Any]:
        inspector_config = _native_inspector_config_from_disk()
        backend = _native_hotkey_backend_capability(inspector_config)
        return {
            "ok": True,
            "status": "ready" if backend.get("available") else "degraded",
            "platform": sys.platform,
            "nativeHotkeyBackend": backend,
            "nativeInspector": {
                "enabled": bool(inspector_config.get("enabled", True)),
                "config": inspector_config,
                "backend": backend,
            },
            "defaultBackend": backend.get("backend") if backend.get("available") else "native_helper_required",
            "serviceMode": "native_inspector_worker",
        }

    def start_capture_assistant_service(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        status = self.capture_assistant_status()
        status.update(
            {
                "requested": dict(payload or {}),
                "serviceStarted": True,
                "serviceMode": "native_inspector_worker",
            }
        )
        return status

    def save_native_inspector_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        existing_config = storage.read_json("config.json")
        rpa_config = dict(existing_config.get("rpa") or {})
        current = _native_inspector_config_from_disk()
        allowed_keys = {
            "enabled",
            "backend",
            "captureGesture",
            "cancelGesture",
            "hotkey",
            "cancelHotkey",
            "relockHotkey",
            "hoverSampleHz",
            "highlightOverlay",
            "ignoreAdminSurface",
            "helperPath",
        }
        updated = dict(current)
        for key in allowed_keys:
            if key in payload:
                updated[key] = payload.get(key)
        try:
            updated["hoverSampleHz"] = max(2, min(30, int(float(updated.get("hoverSampleHz") or 12))))
        except Exception:
            updated["hoverSampleHz"] = 12
        updated["highlightOverlay"] = bool(updated.get("highlightOverlay", True))
        updated["ignoreAdminSurface"] = bool(updated.get("ignoreAdminSurface", True))
        rpa_config["nativeInspector"] = updated
        existing_config["rpa"] = rpa_config
        storage.write_json("config.json", existing_config)
        return {
            "ok": True,
            "saved": True,
            "nativeInspector": updated,
            "nativeHotkeyBackend": _native_hotkey_backend_capability(updated),
        }

    def native_inspector_status(self) -> Dict[str, Any]:
        return self.capture_assistant_status()

    def start_native_inspector_service(self, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return self.start_capture_assistant_service(payload)

    def start_capture_assistant(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.get(recording_id)
        if not recording:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        target_lock = dict(payload.get("targetLock") or recording.get("targetLock") or {})
        if bool(target_lock.get("consoleTargetBlocked")):
            return {
                "ok": False,
                "status": "blocked_admin_surface",
                "reason": "V8 Admin console window is excluded from RPA capture.",
                "recording": recording,
            }
        assistant_state = dict(recording.get("captureAssistant") or {})
        prepared_target_from_payload = dict(payload.get("preparedTarget") or {}) if isinstance(payload.get("preparedTarget"), dict) else {}
        prepared_target_from_recording = dict(assistant_state.get("preparedTarget") or {}) if isinstance(assistant_state.get("preparedTarget"), dict) else {}
        if bool(payload.get("reusePreparedTarget")) and (prepared_target_from_payload or prepared_target_from_recording):
            prepared_target = prepared_target_from_payload or prepared_target_from_recording
            prepared = {
                "ok": True,
                "status": "target_prepared",
                "targetStable": True,
                "targetWindow": prepared_target,
                "recording": recording,
                "reusedPreparedTarget": True,
            }
        else:
            prepared = self.prepare_capture_target(recording_id, payload)
        recording = prepared.get("recording") or recording
        force_fallback_overlay = False
        if isinstance(prepared, dict) and prepared.get("ok") is False:
            status = str(prepared.get("status") or "")
            manual_fallback_requested = bool(
                payload.get("allowManualCoordinateFallback")
                or payload.get("manualCoordinateFallback")
                or payload.get("forceCoordinateFallback")
            )
            if manual_fallback_requested and status in {"manual_coordinate_available", "launched_no_visible_window"}:
                force_fallback_overlay = True
                assistant = dict(recording.get("captureAssistant") or {})
                assistant.update(
                    {
                        "state": "starting_coordinate_fallback",
                        "prepareStatus": status,
                        "targetLock": target_lock,
                        "prepared": prepared,
                        "coordinateFallbackStartedAt": utc_now_iso(),
                    }
                )
                recording = self.recording_manager.update_capture_assistant(recording_id, assistant)
                prepared = {
                    "ok": True,
                    "status": "manual_coordinate_fallback",
                    "targetStable": False,
                    "targetWindow": {},
                    "recording": recording,
                    "manualCoordinateAvailable": True,
                    "previousPrepare": prepared,
                }
            else:
                assistant = dict(recording.get("captureAssistant") or {})
                assistant.update(
                    {
                        "state": "waiting_target" if status in {"target_candidates_required", "manual_coordinate_available", "launched_no_visible_window"} else "failed",
                        "prepareStatus": status or "target_prepare_failed",
                        "targetLock": target_lock,
                        "failedAt": utc_now_iso(),
                        "prepared": prepared,
                    }
                )
                updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
                return {
                    "ok": False,
                    "status": status or "target_prepare_failed",
                    "reason": prepared.get("reason") or prepared.get("detail") or "Recording target is not ready; native inspector was not armed.",
                    "prepared": prepared,
                    "targetCandidates": prepared.get("targetCandidates") or [],
                    "manualCoordinateAvailable": bool(prepared.get("manualCoordinateAvailable") or status in {"manual_coordinate_available", "launched_no_visible_window"}),
                    "armed": False,
                    "hotkeyRegistered": False,
                    "recording": updated,
                }
        engine_base_url = str(
            payload.get("engineBaseUrl")
            or os.environ.get("V8_ENGINE_BASE_URL")
            or os.environ.get("V8_ENGINE_URL")
            or "http://127.0.0.1:9530"
        ).rstrip("/")
        inspector_config = _native_inspector_config_from_disk()
        requested_backend = str(payload.get("backend") or payload.get("captureBackend") or "auto").strip().lower() or "auto"
        native_capability = _native_hotkey_backend_capability(inspector_config)
        native_backend_aliases = {
            "auto",
            "native_hotkey",
            "native_inspector",
            "windows_register_hotkey",
            "windows_fla_ui_helper",
            "fla_ui_helper",
        }
        resolved_backend = requested_backend
        if force_fallback_overlay:
            resolved_backend = "fallback_overlay"
        elif requested_backend in native_backend_aliases:
            if bool(native_capability.get("available")):
                resolved_backend = str(native_capability.get("backend") or requested_backend)
            else:
                helper = native_capability.get("helper") if isinstance(native_capability.get("helper"), dict) else {}
                status = str(native_capability.get("state") or helper.get("state") or "native_inspector_unavailable")
                assistant = dict(recording.get("captureAssistant") or {})
                assistant.update(
                    {
                        "state": "failed",
                        "prepareStatus": prepared.get("status") if isinstance(prepared, dict) else None,
                        "targetLock": target_lock,
                        "failedAt": utc_now_iso(),
                        "prepared": prepared,
                        "captureBackend": native_capability.get("backend") or "native_inspector",
                        "nativeHotkeyBackend": native_capability,
                        "armed": False,
                        "hotkeyRegistered": False,
                        "reason": helper.get("reason") or native_capability.get("reason") or "Native inspector helper is not available.",
                    }
                )
                updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
                return {
                    "ok": False,
                    "status": status,
                    "reason": assistant["reason"],
                    "armed": False,
                    "hotkeyRegistered": False,
                    "captureGesture": inspector_config.get("captureGesture") or "LeftClick",
                    "alternateCaptureGesture": inspector_config.get("alternateCaptureGesture") or "Ctrl+LeftClick",
                    "cancelGesture": inspector_config.get("cancelGesture") or "Esc",
                    "nativeHotkeyBackend": native_capability,
                    "installCommand": helper.get("installCommand"),
                    "publishCommand": helper.get("publishCommand"),
                    "recording": updated,
                }
        native_inspector_session_id = str(payload.get("nativeInspectorSessionId") or f"native_inspector_{uuid.uuid4().hex[:12]}")
        capture_gesture = str(payload.get("captureGesture") or inspector_config.get("captureGesture") or native_capability.get("captureGesture") or "LeftClick")
        cancel_gesture = str(payload.get("cancelGesture") or inspector_config.get("cancelGesture") or native_capability.get("cancelGesture") or "Esc")
        hotkey = str(payload.get("hotkey") or inspector_config.get("hotkey") or native_capability.get("hotkey") or "Ctrl+Alt+C")
        cancel_hotkey = str(payload.get("cancelHotkey") or payload.get("cancel_hotkey") or inspector_config.get("cancelHotkey") or native_capability.get("cancelHotkey") or "Ctrl+Alt+X")
        hover_sample_hz = inspector_config.get("hoverSampleHz") or native_capability.get("hoverSampleHz") or 12
        highlight_overlay = bool(payload.get("highlightOverlay", inspector_config.get("highlightOverlay", True)))
        prepared_target = dict((prepared.get("targetWindow") or {}) if isinstance(prepared, dict) else {})
        log_path = _capture_assistant_log_path(recording_id)
        request_path: Path | None = None
        if resolved_backend == "windows_fla_ui_helper":
            helper_info = native_capability.get("helper") if isinstance(native_capability.get("helper"), dict) else {}
            helper_path = Path(str(helper_info.get("path") or ""))
            request_path = log_path.with_suffix(".request.json")
            helper_request = {
                "engineUrl": engine_base_url,
                "recordingId": recording_id,
                "stepId": str(payload.get("stepId") or payload.get("targetStepId") or payload.get("sourceStepId") or ""),
                "action": str(payload.get("action") or "click"),
                "mode": str(payload.get("mode") or "capture_only"),
                "nativeInspectorSessionId": native_inspector_session_id,
                "appId": str(recording.get("appId") or target_lock.get("appId") or ""),
                "targetWindow": prepared_target,
                "targetProcess": prepared.get("targetProcess") if isinstance(prepared, dict) else {},
                "captureGesture": capture_gesture,
                "cancelGesture": cancel_gesture,
            }
            request_path.write_text(json.dumps(_jsonable(helper_request), ensure_ascii=False, indent=2), encoding="utf-8")
            if helper_path.suffix.lower() == ".dll":
                command = ["dotnet", str(helper_path), "--request-file", str(request_path)]
            else:
                command = [str(helper_path), "--request-file", str(request_path)]
        else:
            script_path = Path(__file__).resolve().parents[2] / "scripts" / "rpa_capture_assistant.py"
            if not script_path.exists():
                return {
                    "ok": False,
                    "status": "capture_assistant_unavailable",
                    "reason": f"Capture assistant script not found: {script_path}",
                    "recording": recording,
                }
            command = [
                sys.executable,
                str(script_path),
                "--recording-id",
                recording_id,
                "--engine-url",
                engine_base_url,
                "--action",
                str(payload.get("action") or "click"),
                "--target-label",
                str(target_lock.get("label") or recording.get("appId") or "desktop"),
                "--mode",
                str(payload.get("mode") or "capture_only"),
                "--hotkey",
                hotkey,
                "--cancel-hotkey",
                cancel_hotkey,
                "--backend",
                resolved_backend,
                "--native-inspector-session-id",
                native_inspector_session_id,
                "--hover-sample-hz",
                str(hover_sample_hz),
            ]
            if bool(payload.get("persistent")):
                command.append("--persistent")
            if bool(payload.get("recordAndForward")):
                command.append("--record-and-forward")
            if highlight_overlay:
                command.append("--highlight-overlay")
            else:
                command.append("--no-highlight-overlay")
            if prepared_target.get("title"):
                command.extend(["--target-window-title", str(prepared_target.get("title"))])
            if prepared_target.get("handle") is not None:
                command.extend(["--target-window-handle", str(prepared_target.get("handle"))])
            if prepared_target.get("processId") is not None:
                command.extend(["--target-window-process-id", str(prepared_target.get("processId"))])
        log_handle = log_path.open("a", encoding="utf-8", errors="replace")
        popen_kwargs: Dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": log_handle,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            process = subprocess.Popen(  # noqa: S603 - launched from Admin-only RPA capture control.
                command,
                **popen_kwargs,
            )
        except Exception as exc:
            try:
                log_handle.close()
            except Exception:
                pass
            return {
                "ok": False,
                "status": "capture_assistant_unavailable",
                "reason": str(exc),
                "recording": recording,
            }
        finally:
            try:
                log_handle.close()
            except Exception:
                pass
        ready_event: Dict[str, Any] | None = None
        error_event: Dict[str, Any] | None = None
        exit_code: int | None = None
        deadline = time.time() + (10.0 if resolved_backend == "windows_fla_ui_helper" else 5.0)
        while time.time() < deadline:
            exit_code = process.poll()
            error_event = _capture_assistant_error_event(log_path)
            if error_event:
                break
            ready_event = _capture_assistant_last_event(log_path, ".ready")
            if ready_event:
                break
            if exit_code is not None:
                break
            time.sleep(0.1)
        exit_code = process.poll()
        if error_event:
            _terminate_process(process.pid)
            diagnostic_tail = _tail_text_file(log_path)
            reason = str(error_event.get("error") or error_event.get("warning") or diagnostic_tail or "Capture assistant failed before readiness.")
            assistant = {
                "state": "failed",
                "nativeInspectorSessionId": native_inspector_session_id,
                "mode": payload.get("mode") or "capture_only",
                "processId": process.pid,
                "exitCode": exit_code,
                "hotkey": hotkey,
                "cancelHotkey": cancel_hotkey,
                "captureGesture": capture_gesture,
                "cancelGesture": cancel_gesture,
                "armed": False,
                "hotkeyRegistered": False,
                "mouseHookInstalled": bool(error_event.get("mouseHookInstalled")),
                "keyboardHookInstalled": bool(error_event.get("keyboardHookInstalled")),
                "overlayReady": False,
                "targetReady": False,
                "targetLock": target_lock,
                "startedAt": utc_now_iso(),
                "failedAt": utc_now_iso(),
                "persistent": bool(payload.get("persistent")),
                "recordAndForward": bool(payload.get("recordAndForward")),
                "prepared": prepared,
                "captureBackend": resolved_backend,
                "manualCoordinateFallback": force_fallback_overlay,
                "serviceMode": "native_inspector_worker",
                "nativeInspectorConfig": inspector_config,
                "hoverSampleHz": hover_sample_hz,
                "highlightOverlay": highlight_overlay,
                "nativeHotkeyBackend": native_capability,
                "logPath": str(log_path),
                "requestPath": str(request_path) if request_path else None,
                "readinessEvent": error_event,
                "diagnosticTail": diagnostic_tail,
            }
            updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
            return {
                "ok": False,
                "status": str(error_event.get("stage") or "capture_assistant_readiness_failed"),
                "reason": reason,
                "armed": False,
                "hotkeyRegistered": False,
                "assistant": assistant,
                "recording": updated,
            }
        if exit_code is not None:
            diagnostic_tail = _tail_text_file(log_path)
            assistant = {
                "state": "failed",
                "nativeInspectorSessionId": native_inspector_session_id,
                "mode": payload.get("mode") or "capture_only",
                "processId": process.pid,
                "exitCode": exit_code,
                "hotkey": hotkey,
                "cancelHotkey": cancel_hotkey,
                "captureGesture": capture_gesture,
                "cancelGesture": cancel_gesture,
                "armed": False,
                "hotkeyRegistered": False,
                "targetLock": target_lock,
                "startedAt": utc_now_iso(),
                "failedAt": utc_now_iso(),
                "persistent": bool(payload.get("persistent")),
                "recordAndForward": bool(payload.get("recordAndForward")),
                "prepared": prepared,
                "captureBackend": resolved_backend,
                "manualCoordinateFallback": force_fallback_overlay,
                "serviceMode": "native_inspector_worker",
                "nativeInspectorConfig": inspector_config,
                "hoverSampleHz": hover_sample_hz,
                "highlightOverlay": highlight_overlay,
                "nativeHotkeyBackend": native_capability,
                "logPath": str(log_path),
                "requestPath": str(request_path) if request_path else None,
                "diagnosticTail": diagnostic_tail,
            }
            updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
            return {
                "ok": False,
                "status": "capture_assistant_failed",
                "reason": diagnostic_tail or f"Capture assistant exited immediately with code {exit_code}.",
                "armed": False,
                "hotkeyRegistered": False,
                "assistant": assistant,
                "recording": updated,
            }
        if not ready_event:
            _terminate_process(process.pid)
            diagnostic_tail = _tail_text_file(log_path)
            assistant = {
                "state": "failed",
                "nativeInspectorSessionId": native_inspector_session_id,
                "mode": payload.get("mode") or "capture_only",
                "processId": process.pid,
                "hotkey": hotkey,
                "cancelHotkey": cancel_hotkey,
                "captureGesture": capture_gesture,
                "cancelGesture": cancel_gesture,
                "armed": False,
                "hotkeyRegistered": False,
                "targetLock": target_lock,
                "startedAt": utc_now_iso(),
                "failedAt": utc_now_iso(),
                "persistent": bool(payload.get("persistent")),
                "recordAndForward": bool(payload.get("recordAndForward")),
                "prepared": prepared,
                "captureBackend": resolved_backend,
                "manualCoordinateFallback": force_fallback_overlay,
                "serviceMode": "native_inspector_worker",
                "nativeInspectorConfig": inspector_config,
                "hoverSampleHz": hover_sample_hz,
                "highlightOverlay": highlight_overlay,
                "nativeHotkeyBackend": native_capability,
                "logPath": str(log_path),
                "requestPath": str(request_path) if request_path else None,
                "diagnosticTail": diagnostic_tail,
            }
            updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
            return {
                "ok": False,
                "status": "capture_assistant_not_ready",
                "reason": diagnostic_tail or "Capture assistant did not report readiness. Hotkeys are not armed.",
                "armed": False,
                "hotkeyRegistered": False,
                "assistant": assistant,
                "recording": updated,
            }
        hotkey_registered = bool(ready_event.get("hotkeyRegistered"))
        input_hook_ready = bool(ready_event.get("inputHookReady", hotkey_registered))
        mouse_hook_installed = bool(ready_event.get("mouseHookInstalled"))
        keyboard_hook_installed = bool(ready_event.get("keyboardHookInstalled"))
        overlay_ready = bool(ready_event.get("overlayReady"))
        target_ready = bool(ready_event.get("targetReady", True))
        automation_ready = bool(ready_event.get("automationReady", True))
        requires_full_readiness = bool(resolved_backend in {"native_hotkey", "windows_register_hotkey", "windows_fla_ui_helper"} and highlight_overlay)
        if resolved_backend == "fallback_overlay":
            readiness_ok = bool(overlay_ready and target_ready)
        else:
            readiness_ok = bool(hotkey_registered and target_ready)
        if requires_full_readiness:
            readiness_ok = bool(readiness_ok and input_hook_ready and mouse_hook_installed and keyboard_hook_installed and overlay_ready and automation_ready)
        if not readiness_ok:
            _terminate_process(process.pid)
            diagnostic_tail = _tail_text_file(log_path)
            assistant = {
                "state": "failed",
                "nativeInspectorSessionId": native_inspector_session_id,
                "mode": payload.get("mode") or "capture_only",
                "processId": process.pid,
                "hotkey": hotkey,
                "cancelHotkey": cancel_hotkey,
                "captureGesture": capture_gesture,
                "cancelGesture": cancel_gesture,
                "armed": False,
                "hotkeyRegistered": hotkey_registered,
                "inputHookReady": input_hook_ready,
                "mouseHookInstalled": mouse_hook_installed,
                "keyboardHookInstalled": keyboard_hook_installed,
                "overlayReady": overlay_ready,
                "targetReady": target_ready,
                "automationReady": automation_ready,
                "targetLock": target_lock,
                "startedAt": utc_now_iso(),
                "failedAt": utc_now_iso(),
                "persistent": bool(payload.get("persistent")),
                "recordAndForward": bool(payload.get("recordAndForward")),
                "prepared": prepared,
                "captureBackend": resolved_backend,
                "manualCoordinateFallback": force_fallback_overlay,
                "serviceMode": "native_inspector_worker",
                "nativeInspectorConfig": inspector_config,
                "hoverSampleHz": hover_sample_hz,
                "highlightOverlay": highlight_overlay,
                "nativeHotkeyBackend": native_capability,
                "logPath": str(log_path),
                "requestPath": str(request_path) if request_path else None,
                "readinessEvent": ready_event,
                "diagnosticTail": diagnostic_tail,
            }
            updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
            return {
                "ok": False,
                "status": "capture_assistant_readiness_failed",
                "reason": "Capture assistant is not fully ready; mouse/keyboard hooks, overlay, or target readiness failed.",
                "armed": False,
                "hotkeyRegistered": False,
                "assistant": assistant,
                "recording": updated,
            }
        assistant = {
            "state": "active",
            "nativeInspectorSessionId": native_inspector_session_id,
            "mode": payload.get("mode") or "capture_only",
            "processId": process.pid,
            "hotkey": hotkey,
            "cancelHotkey": cancel_hotkey,
            "captureGesture": capture_gesture,
            "cancelGesture": cancel_gesture,
            "armed": True,
            "hotkeyRegistered": hotkey_registered,
            "inputHookReady": input_hook_ready,
            "mouseHookInstalled": mouse_hook_installed,
            "keyboardHookInstalled": keyboard_hook_installed,
            "overlayReady": overlay_ready,
            "targetReady": target_ready,
            "automationReady": automation_ready,
            "targetLock": target_lock,
            "startedAt": utc_now_iso(),
            "persistent": bool(payload.get("persistent")),
            "recordAndForward": bool(payload.get("recordAndForward")),
            "prepared": prepared,
            "captureBackend": resolved_backend,
            "manualCoordinateFallback": force_fallback_overlay,
            "serviceMode": "native_inspector_worker",
            "nativeInspectorConfig": inspector_config,
            "hoverSampleHz": hover_sample_hz,
            "highlightOverlay": highlight_overlay,
            "nativeHotkeyBackend": native_capability,
            "logPath": str(log_path),
            "requestPath": str(request_path) if request_path else None,
            "readinessEvent": ready_event,
        }
        updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
        return {
            "ok": True,
            "status": "capture_assistant_started",
            "armed": True,
            "hotkeyRegistered": hotkey_registered,
            "hotkey": hotkey,
            "cancelHotkey": cancel_hotkey,
            "captureGesture": capture_gesture,
            "cancelGesture": cancel_gesture,
            "assistant": assistant,
            "recording": updated,
        }

    def poll_capture_assistant(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.get(recording_id)
        if not recording:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        assistant = dict(recording.get("captureAssistant") or {})
        pid = assistant.get("processId")
        running = _is_process_running(pid) if isinstance(pid, int) else False
        if assistant:
            previous_state = str(assistant.get("state") or "")
            terminal_states = {"captured", "cancelled", "failed", "stopped"}
            log_path = assistant.get("logPath")
            latest_error = _capture_assistant_error_event(log_path)
            latest_captured = _capture_assistant_last_event(log_path, ".captured")
            latest_cancelled = _capture_assistant_last_event(log_path, ".cancelled")
            latest_ready = _capture_assistant_last_event(log_path, ".ready")
            if latest_error:
                assistant["state"] = "failed"
                assistant["lastInspectorEvent"] = latest_error
                assistant["error"] = latest_error.get("error") or latest_error.get("warning")
            elif latest_captured:
                assistant["state"] = "captured"
                assistant["lastInspectorEvent"] = latest_captured
            elif latest_cancelled:
                assistant["state"] = "cancelled"
                assistant["lastInspectorEvent"] = latest_cancelled
            elif running:
                assistant["state"] = previous_state if previous_state in terminal_states else "active"
            else:
                assistant["state"] = previous_state if previous_state in terminal_states else "stopped"
            if latest_ready:
                assistant["readinessEvent"] = latest_ready
                assistant["hotkeyRegistered"] = bool(latest_ready.get("hotkeyRegistered"))
                assistant["mouseHookInstalled"] = bool(latest_ready.get("mouseHookInstalled"))
                assistant["keyboardHookInstalled"] = bool(latest_ready.get("keyboardHookInstalled"))
                assistant["overlayReady"] = bool(latest_ready.get("overlayReady"))
                assistant["targetReady"] = bool(latest_ready.get("targetReady", True))
            assistant["lastPolledAt"] = utc_now_iso()
            assistant["processRunning"] = running
            if not running and not str(assistant.get("diagnosticTail") or "").strip():
                diagnostic_tail = _tail_text_file(assistant.get("logPath"))
                if diagnostic_tail:
                    assistant["diagnosticTail"] = diagnostic_tail
            recording = self.recording_manager.update_capture_assistant(recording_id, assistant)
        return {
            "ok": True,
            "status": "active" if running else str(assistant.get("state") or "stopped"),
            "processRunning": running,
            "assistant": assistant,
            "recording": recording,
        }

    def save_capture_pool_item(self, recording_id: str, temp_element_id: str, *, name: str | None = None) -> Dict[str, Any]:
        return self.recording_manager.save_capture_pool_item(recording_id, temp_element_id, name=name)

    def stop_capture_assistant(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        recording = self.recording_manager.get(recording_id)
        if not recording:
            raise ValueError(f"Recording session '{recording_id}' not found.")
        assistant = dict(recording.get("captureAssistant") or {})
        pid = assistant.get("processId")
        terminated = _terminate_process(pid) if isinstance(pid, int) else False
        assistant.update(
            {
                "state": "stopped",
                "stoppedAt": utc_now_iso(),
                "terminated": terminated,
                "reason": payload.get("reason") or "admin_stop",
            }
        )
        updated = self.recording_manager.update_capture_assistant(recording_id, assistant)
        return {
            "ok": True,
            "status": "capture_assistant_stopped",
            "assistant": assistant,
            "recording": updated,
        }

    def start_native_inspector(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        next_payload = dict(payload or {})
        next_payload.setdefault("backend", "auto")
        next_payload.setdefault("persistent", True)
        next_payload.setdefault("mode", "capture_only")
        return self.start_capture_assistant(recording_id, next_payload)

    def poll_native_inspector(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.poll_capture_assistant(recording_id, payload or {})

    def stop_native_inspector(self, recording_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.stop_capture_assistant(recording_id, payload or {})

    def stop_recording(self, recording_id: str, *, compile_draft: bool = True, save: bool = True) -> Dict[str, Any]:
        recording = self.recording_manager.stop(recording_id)
        draft = None
        compile_error = None
        if compile_draft and int(recording.get("stepCount") or 0) > 0:
            self.recording_manager.mark_compiling(recording_id)
            try:
                draft = self.compile_trace_to_draft(str(recording.get("traceRunId")), save=save)
                recording = self.recording_manager.mark_draft_ready(recording_id, draft)
            except Exception as exc:
                compile_error = str(exc)
                recording = self.recording_manager.mark_failed(recording_id, exc)
        self._log_audit(
            action=f"Stop RPA recording: {recording_id}",
            status="SUCCESS" if not compile_error else "ERROR",
            details=json.dumps(
                {
                    "recordingSessionId": recording_id,
                    "traceRunId": recording.get("traceRunId"),
                    "createdDraftId": recording.get("createdDraftId"),
                    "compileError": compile_error,
                },
                ensure_ascii=False,
            ),
        )
        return {
            "recording": recording,
            "draft": draft,
            "compileError": compile_error,
        }

    def list_templates(
        self,
        *,
        limit: int = 100,
        app_id: str | None = None,
        status: str | None = None,
        include_archived: bool = False,
    ) -> list[Dict[str, Any]]:
        return self.template_service.list_templates(limit=limit, app_id=app_id, status=status, include_archived=include_archived)

    def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        return self.template_service.get_template(template_id)

    def list_template_history(self, template_id: str, *, limit: int = 50) -> list[Dict[str, Any]]:
        return self.template_service.list_template_history(template_id, limit=limit)

    def archive_template(self, template_id: str, *, actor: str = "system", reason: str | None = None) -> Dict[str, Any]:
        payload = self.script_store.archive_template(template_id, actor=actor, reason=reason)
        self._log_audit(
            action=f"Archive RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps({"templateId": template_id, "actor": actor}, ensure_ascii=False),
        )
        return self.template_service.get_template(template_id) or payload

    def restore_template(self, template_id: str, *, actor: str = "system") -> Dict[str, Any]:
        payload = self.script_store.restore_template(template_id, actor=actor)
        self._log_audit(
            action=f"Restore RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps({"templateId": template_id, "actor": actor}, ensure_ascii=False),
        )
        return self.template_service.get_template(template_id) or payload

    def delete_template(self, template_id: str, *, confirm: bool = False, actor: str = "system") -> Dict[str, Any]:
        payload = self.script_store.delete_template(template_id, confirm=confirm, actor=actor)
        self._log_audit(
            action=f"Hard delete RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps({"templateId": template_id, "actor": actor}, ensure_ascii=False),
        )
        return payload

    def recommend_execution_route(
        self,
        *,
        goal: str,
        app_id: str | None = None,
        variables: Dict[str, Any] | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        limit: int = 5,
        allow_materialization: bool = False,
    ) -> Dict[str, Any]:
        return self.template_service.recommend_execution_route(
            goal=goal,
            app_id=app_id,
            variables=variables,
            session_id=session_id,
            run_id=run_id,
            limit=limit,
            allow_materialization=allow_materialization,
        )

    def review_template(
        self,
        template_id: str,
        *,
        decision: str,
        reviewer: str = "system",
        notes: str | None = None,
        metadata_patch: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        payload = self.template_service.review_template(
            template_id,
            decision=decision,
            reviewer=reviewer,
            notes=notes,
            metadata_patch=metadata_patch,
        )
        self._log_audit(
            action=f"Review RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps(
                {
                    "templateId": template_id,
                    "decision": decision,
                    "reviewer": reviewer,
                },
                ensure_ascii=False,
            ),
        )
        return payload

    def approve_template(
        self,
        template_id: str,
        *,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        payload = self.template_service.approve_template(template_id, reviewer=reviewer, notes=notes)
        self._log_audit(
            action=f"Approve RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps({"templateId": template_id, "reviewer": reviewer}, ensure_ascii=False),
        )
        return payload

    def freeze_template(
        self,
        template_id: str,
        *,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        payload = self.template_service.freeze_template(template_id, reviewer=reviewer, notes=notes)
        self._log_audit(
            action=f"Freeze RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps({"templateId": template_id, "reviewer": reviewer}, ensure_ascii=False),
        )
        return payload

    def rollback_template(
        self,
        template_id: str,
        *,
        revision: int | None = None,
        history_path: str | None = None,
        reviewer: str = "system",
        notes: str | None = None,
    ) -> Dict[str, Any]:
        payload = self.template_service.rollback_template(
            template_id,
            revision=revision,
            history_path=history_path,
            reviewer=reviewer,
            notes=notes,
        )
        self._log_audit(
            action=f"Rollback RPA template: {template_id}",
            status="SUCCESS",
            details=json.dumps(
                {
                    "templateId": template_id,
                    "revision": revision,
                    "historyPath": history_path,
                    "reviewer": reviewer,
                },
                ensure_ascii=False,
            ),
        )
        return payload

    def get_draft_source_traces(
        self,
        script_id: str,
        *,
        include_steps: bool = True,
        max_steps: int = 8,
    ) -> Optional[Dict[str, Any]]:
        draft = self.get_draft(script_id)
        if not draft:
            return None
        source = dict(draft.get("source") or {})
        run_ids = [str(item).strip() for item in list(source.get("traceRunIds") or []) if str(item).strip()]
        if not run_ids and str(source.get("traceRunId") or "").strip():
            run_ids = [str(source.get("traceRunId")).strip()]
        return {
            "scriptId": script_id,
            "scriptName": draft.get("name"),
            "source": source,
            "traceBundle": self.compiler.trace_store.get_trace_bundle(
                run_ids,
                include_steps=include_steps,
                max_steps=max_steps,
            ),
        }

    def export_draft_to_robot(
        self,
        *,
        script_id: str,
        output_dir: str | Path | None = None,
    ) -> Dict[str, Any]:
        draft = self.get_draft(script_id)
        if not draft:
            raise ValueError(f"未找到 draft: {script_id}")
        exported = self.adapter.export_script(
            script=draft,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
        self._log_audit(
            action=f"Export RPA draft: {script_id}",
            status="SUCCESS",
            details=json.dumps({"scriptId": script_id, "path": exported["path"]}, ensure_ascii=False),
        )
        return exported

    def prepare_draft_run(
        self,
        *,
        script_id: str,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
    ) -> Dict[str, Any]:
        prepared = self.adapter.prepare_draft_run(
            script_id=script_id,
            variables=variables,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
        self._log_audit(
            action=f"Prepare RPA draft run: {script_id}",
            status="INFO",
            details=json.dumps({"scriptId": script_id, "variables": _jsonable(variables or {})}, ensure_ascii=False),
        )
        return prepared

    def prepare_existing_run(
        self,
        *,
        robot_file: str | Path,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
    ) -> Dict[str, Any]:
        prepared = self.adapter.prepare_existing_run(
            robot_file=Path(robot_file),
            variables=variables,
            output_dir=Path(output_dir) if output_dir is not None else None,
        )
        self._log_audit(
            action=f"Prepare existing robot flow: {Path(robot_file).name}",
            status="INFO",
            details=json.dumps({"robotFile": str(robot_file), "variables": _jsonable(variables or {})}, ensure_ascii=False),
        )
        return prepared

    def _resolve_session_id(
        self,
        *,
        script_id: str | None = None,
        robot_file: str | Path | None = None,
        session_id: str | None = None,
    ) -> str:
        if session_id:
            return str(session_id)
        if script_id:
            return f"rpa:draft:{_slug(script_id)}"
        if robot_file:
            digest = hashlib.md5(str(robot_file).encode("utf-8")).hexdigest()[:10]
            return f"rpa:file:{digest}"
        return "rpa:manual"

    def _build_session_title(self, *, script_id: str | None = None, robot_file: str | Path | None = None) -> str:
        if script_id:
            return f"RPA Draft · {script_id}"
        if robot_file:
            return f"RPA Flow · {Path(robot_file).name}"
        return "RPA Runtime"

    def _build_run_metadata(
        self,
        *,
        mode: str,
        prepared: Dict[str, Any],
        variables: Dict[str, Any],
        trigger_source: str | None,
        cwd: str | None,
    ) -> Dict[str, Any]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        script_metadata = dict(script.get("metadata") or {}) if script else {}
        template_policy = self._resolve_template_execution_policy(mode=mode, prepared=prepared)
        execution_state = "queued"
        return {
            "runtime": "rpa",
            "mode": mode,
            "trigger_source": trigger_source,
            "variables": _jsonable(variables),
            "cwd": cwd,
            "command": list(prepared.get("command") or []),
            "availability": _jsonable(prepared.get("available") or {}),
            "script": _jsonable(script or {}),
            "export": _jsonable(prepared.get("export") or {}),
            "robotFile": prepared.get("robotFile"),
            "assessment": _jsonable((script or {}).get("assessment") if script else {}),
            "trustStatus": normalize_script_assessment_status(((script or {}).get("assessment") or {}).get("status") if script else None),
            "templateGovernance": _jsonable(script_metadata.get("templateGovernance") or {}),
            "templateStatus": script_metadata.get("templateStatus") or script_metadata.get("templateGovernance", {}).get("templateStatus"),
            "templateExecutionPolicy": _jsonable(template_policy),
            "templateExecutionPath": template_policy.get("executionPath"),
            "executionState": execution_state,
            "outcomeFamily": outcome_family_for_execution_state(execution_state),
        }

    def _log_audit(self, *, action: str, status: str, details: str | None = None) -> None:
        try:
            audit_logger.log("RPA", action, status, details)
        except Exception:
            pass

    def _record_run_feedback(
        self,
        *,
        prepared: Dict[str, Any],
        execution_state: str,
        feedback: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any] | None:
        outcome_family = outcome_family_for_execution_state(execution_state)
        try:
            script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
            if script:
                self.script_store.record_run_feedback(
                    script=script,
                    execution_state=execution_state,
                    outcome_family=outcome_family,
                    feedback=feedback,
                )
                template_id = str(((script.get("source") or {}).get("templateId")) or "").strip()
                if template_id:
                    return self.template_service.register_template_execution_feedback(
                        template_id,
                        execution_state=execution_state,
                        outcome_family=outcome_family,
                        feedback=dict(feedback or {}),
                        actor="rpa_runtime",
                    )
        except Exception:
            return None
        return None

    def _update_run_metadata(self, run_id: str, **updates: Any) -> None:
        try:
            next_updates = dict(updates or {})
            execution_state = next_updates.get("executionState")
            if "outcomeFamily" not in next_updates and execution_state not in (None, ""):
                next_updates["outcomeFamily"] = outcome_family_for_execution_state(execution_state)
            run_service.update_metadata(run_id, next_updates)
        except Exception:
            pass

    def _merged_feedback_suggestions(self, payload: Dict[str, Any] | None) -> Dict[str, Any]:
        feedback = dict(payload or {})
        preflight_hints = [dict(item) for item in list(feedback.get("preflightHints") or []) if isinstance(item, dict)]
        return {
            **({"selectorMemoryCandidate": dict(feedback.get("selectorMemoryCandidate") or {})} if isinstance(feedback.get("selectorMemoryCandidate"), dict) and feedback.get("selectorMemoryCandidate") else {}),
            **({"appProfileRecommendation": dict(feedback.get("appProfileRecommendation") or {})} if isinstance(feedback.get("appProfileRecommendation"), dict) and feedback.get("appProfileRecommendation") else {}),
            **({"playbookRecommendation": dict(feedback.get("playbookRecommendation") or {})} if isinstance(feedback.get("playbookRecommendation"), dict) and feedback.get("playbookRecommendation") else {}),
            **({"preflightHints": preflight_hints} if preflight_hints else {}),
        }

    def _feedback_suggestions_from_computer_use_execution(self, execution: Dict[str, Any] | None) -> Dict[str, Any]:
        payload = dict(execution or {})
        run_id = str(payload.get("runId") or "").strip()
        if not run_id:
            return {}
        trace = self.compiler.trace_store.get_trace(run_id)
        if not isinstance(trace, dict):
            return {}
        merged: Dict[str, Any] = {}
        preflight_hints: list[dict] = []
        seen_preflight_keys: set[str] = set()
        for step in list(trace.get("steps") or []):
            if not isinstance(step, dict):
                continue
            metadata = dict(step.get("metadata") or {})
            feedback = dict(metadata.get("feedbackSuggestions") or {})
            if not merged.get("selectorMemoryCandidate") and isinstance(feedback.get("selectorMemoryCandidate"), dict):
                merged["selectorMemoryCandidate"] = dict(feedback.get("selectorMemoryCandidate") or {})
            if not merged.get("appProfileRecommendation") and isinstance(feedback.get("appProfileRecommendation"), dict):
                merged["appProfileRecommendation"] = dict(feedback.get("appProfileRecommendation") or {})
            if not merged.get("playbookRecommendation") and isinstance(feedback.get("playbookRecommendation"), dict):
                merged["playbookRecommendation"] = dict(feedback.get("playbookRecommendation") or {})
            for item in list(feedback.get("preflightHints") or []):
                if not isinstance(item, dict):
                    continue
                signature = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if signature in seen_preflight_keys:
                    continue
                seen_preflight_keys.add(signature)
                preflight_hints.append(dict(item))
        if preflight_hints:
            merged["preflightHints"] = preflight_hints
        return merged

    def _consume_control_signal(self, *, run_handle, stage: str) -> Dict[str, Any] | None:
        signal = consume_stop_signal(run_handle.run_id)
        if signal is None:
            return None
        return apply_control_signal(
            run_handle,
            signal=signal,
            runtime_kind="rpa",
            node="rpa_runtime",
            extras={"stage": stage},
        )

    def _finalize_controlled(
        self,
        *,
        run_handle,
        prepared: Dict[str, Any],
        control: Dict[str, Any],
        assessment: Dict[str, Any],
        template_policy: Dict[str, Any],
        execution_state: str,
        extra_payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        self._update_run_metadata(
            run_handle.run_id,
            executionState=execution_state,
            trustStatus=normalize_script_assessment_status(assessment.get("status")),
            templateExecutionPolicy=template_policy,
            templateExecutionPath=template_policy.get("executionPath"),
            control=control,
        )
        self._record_run_feedback(prepared=prepared, execution_state=execution_state)
        result = {
            **prepared,
            "status": execution_state,
            "outcomeFamily": outcome_family_for_execution_state(execution_state),
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
            "templateExecutionPolicy": template_policy,
            "control": control,
        }
        if extra_payload:
            result.update(extra_payload)
        return result

    def _consume_or_finalize_control(
        self,
        *,
        run_handle,
        stage: str,
        prepared: Dict[str, Any],
        assessment: Dict[str, Any],
        template_policy: Dict[str, Any],
        extra_payload: Dict[str, Any] | None = None,
    ) -> Dict[str, Any] | None:
        control = self._consume_control_signal(run_handle=run_handle, stage=stage)
        if control is None:
            return None
        return self._finalize_controlled(
            run_handle=run_handle,
            prepared=prepared,
            control=control,
            assessment=assessment,
            template_policy=template_policy,
            execution_state=str(control.get("status") or "paused"),
            extra_payload=extra_payload,
        )

    def _begin_run(
        self,
        *,
        session_id: str,
        user_id: str,
        trigger_source: str | None,
        metadata: Dict[str, Any],
        title: str,
        run_id: str | None = None,
    ):
        is_manual_rpa = bool(metadata.get("nonChatRun")) or str(trigger_source or "").strip() in {
            "rpa_phone",
            "rpa_admin",
            "phone_rpa",
            "admin_rpa",
        }
        session_metadata = {
            "runtime": "rpa",
            "trigger_source": trigger_source,
            "mode": metadata.get("mode"),
        }
        if is_manual_rpa:
            session_metadata.update(
                {
                    "hiddenFromHistory": True,
                    "manualRpaRun": True,
                    "nonChatRun": True,
                }
            )
        db.create_or_update_session(
            session_id=session_id,
            title=title,
            user_id=user_id,
            metadata=session_metadata,
        )
        run_handle = erc_kernel.submit_run(
            session_id=session_id,
            conversation_id=session_id,
            user_id=user_id,
            runtime_kind="rpa",
            trigger_source=trigger_source,
            agent_id=None,
            metadata=metadata,
            run_id=run_id,
            initial_status="queued",
            component="rpa_runtime",
            node="run_manager",
        )
        run_handle.emit(
            "run.created",
            {
                "run_id": run_handle.run_id,
                "transport": "rpa",
                "trigger_source": trigger_source,
                "mode": metadata.get("mode"),
            },
        )
        return run_handle

    def _run_preflight(self, *, run_handle, trigger_source: str | None, user_id: str) -> SafetyDecision:
        decision = safety_guardian.preflight_runtime(
            runtime_kind="rpa",
            trigger_source=trigger_source,
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
            user_id=user_id,
        )
        run_handle.emit("safety.preflight.checked", decision.to_payload())
        return decision

    def _handle_preflight_decision(
        self,
        *,
        run_handle,
        decision: SafetyDecision,
        trigger_source: str | None,
        subject: str,
    ) -> Optional[Dict[str, Any]]:
        safety_guardian.log_decision_event(
            action="rpa_preflight",
            decision=decision,
            subject=subject,
            metadata={"runId": run_handle.run_id, "sessionId": run_handle.session_id, "triggerSource": trigger_source},
        )
        if decision.is_allow():
            return None
        if decision.is_review():
            approval = run_handle.request_approval(
                approval_kind="safety_review",
                request=safety_guardian.build_runtime_preflight_request(
                    runtime_kind="rpa",
                    trigger_source=trigger_source or "manual",
                    decision=decision,
                    subject=subject,
                ),
            )
            if str(approval.get("status") or "").strip().lower() != "pending":
                self._log_audit(
                    action=f"RPA preflight auto-approved: {subject}",
                    status="INFO",
                    details=json.dumps(
                        {
                            "approvalId": approval.get("approval_id"),
                            "policySource": approval.get("policySource"),
                            "reason": decision.reason,
                        },
                        ensure_ascii=False,
                    ),
                )
                return None
            self._log_audit(
                action=f"RPA preflight review: {subject}",
                status="WARNING",
                details=json.dumps({"approvalId": approval.get("approval_id"), "reason": decision.reason}, ensure_ascii=False),
            )
            return {
                "status": "review_required",
                "outcomeFamily": outcome_family_for_execution_state("review_required"),
                "reason": decision.reason,
                "approvalId": approval.get("approval_id"),
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
            }
        error_message = f"Safety Guardian blocked RPA run: {decision.reason}"
        run_handle.emit("safety.preflight.blocked", decision.to_payload())
        run_handle.fail(error_message, node="safety_guardian")
        self._log_audit(action=f"RPA preflight blocked: {subject}", status="ERROR", details=error_message)
        return {
            "status": "blocked",
            "outcomeFamily": outcome_family_for_execution_state("blocked"),
            "reason": decision.reason,
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
        }

    def _rpa_safety_gate_enabled(self) -> bool:
        # RPA flows are user-authored or user-approved automations. By default
        # the runtime records Safety evidence but does not gate execution.
        return False

    def _audit_preflight_decision(
        self,
        *,
        run_handle,
        decision: SafetyDecision,
        trigger_source: str | None,
        subject: str,
    ) -> None:
        safety_guardian.log_decision_event(
            action="rpa_preflight_audit_only",
            decision=decision,
            subject=subject,
            metadata={
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
                "triggerSource": trigger_source,
                "gate": "audit_only",
            },
        )
        run_handle.emit(
            "rpa.safety.audit_only",
            {
                "subject": subject,
                "triggerSource": trigger_source,
                "decision": decision.to_payload(),
            },
        )
        self._log_audit(
            action=f"RPA preflight audit-only: {subject}",
            status="INFO" if decision.is_allow() else "WARNING",
            details=json.dumps(
                {
                    "runId": run_handle.run_id,
                    "sessionId": run_handle.session_id,
                    "reason": decision.reason,
                    "decision": decision.to_payload(),
                },
                ensure_ascii=False,
            ),
        )

    def _resolve_template_execution_policy(self, *, mode: str, prepared: Dict[str, Any]) -> Dict[str, Any]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        metadata = dict(script.get("metadata") or {}) if script else {}
        source = dict(script.get("source") or {}) if script else {}
        governance = dict(metadata.get("templateGovernance") or {})
        promotion_gate = dict(metadata.get("templatePromotionGate") or {})
        source_kind = str(source.get("kind") or metadata.get("source") or metadata.get("createdFrom") or "").strip()
        has_template_governance = bool(governance) or bool(source.get("templateId") or source.get("templateStage") or source.get("templateStatus"))
        stage = str(governance.get("stage") or source.get("templateStage") or "").strip() or ("candidate" if has_template_governance else "unmanaged")
        status = str(metadata.get("templateStatus") or governance.get("templateStatus") or source.get("templateStatus") or "").strip() or ("candidate" if has_template_governance else "unmanaged")
        metadata_rollout = str(metadata.get("templateRolloutMode") or "").strip()
        rollout_mode = str(governance.get("rolloutMode") or metadata_rollout or "").strip() or ("candidate_shadow" if has_template_governance else "robot_default")
        allow_computer_use_fallback = bool(governance.get("allowComputerUseFallback", True))
        prefer_template_execution = bool(governance.get("preferTemplateExecution"))
        approval_required = bool(governance.get("approvalRequired", True))
        has_computer_use_source = self._has_computer_use_replay_capability(mode=mode, prepared=prepared)
        promotion_gate_blocked = bool(promotion_gate.get("blockedPromotion"))
        if source_kind == "manual_canvas" and has_computer_use_source:
            rollout_mode = "computer_use_first"
        elif promotion_gate_blocked:
            rollout_mode = "computer_use_first"
        execution_path = "robot"
        if has_computer_use_source and rollout_mode == "computer_use_first":
            execution_path = "computer_use_first"
        elif rollout_mode == "template_preferred_with_fallback":
            execution_path = "template_preferred_with_fallback"
        elif rollout_mode == "template_preferred":
            execution_path = "template_preferred"
        elif rollout_mode == "candidate_shadow":
            execution_path = "candidate_shadow"
        suppress_compile_review = bool(stage == "approved_live" and prefer_template_execution)
        bypass_compile_block = bool(execution_path == "computer_use_first" and has_computer_use_source)
        return {
            "stage": stage,
            "status": status,
            "recommendedDecision": governance.get("recommendedDecision"),
            "rolloutMode": rollout_mode,
            "executionPath": execution_path,
            "preferTemplateExecution": prefer_template_execution,
            "allowComputerUseFallback": allow_computer_use_fallback,
            "approvalRequired": approval_required,
            "hasComputerUseSource": has_computer_use_source,
            "promotionGate": promotion_gate,
            "promotionGateBlocked": promotion_gate_blocked,
            "suppressCompileReview": suppress_compile_review,
            "bypassCompileBlock": bypass_compile_block,
            "confidence": governance.get("confidence"),
            "reasons": list(governance.get("reasons") or []),
        }

    def _has_computer_use_replay_capability(self, *, mode: str, prepared: Dict[str, Any]) -> bool:
        if mode != "draft":
            return False
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        if not script or not list(script.get("steps") or []):
            return False
        metadata = script.get("metadata") if isinstance(script.get("metadata"), dict) else {}
        source = script.get("source") if isinstance(script.get("source"), dict) else {}
        source_type = str(source.get("type") or "").strip()
        source_kind = str(source.get("kind") or metadata.get("source") or metadata.get("createdFrom") or "").strip()
        if source_kind == "manual_canvas":
            return True
        if source_type in {"computer_use_trace", "computer_use_trace_merge", "rpa_template_candidate"}:
            return True
        if any(str(step.get("use") or "").strip() == "computer_use_playbook" for step in list(script.get("steps") or []) if isinstance(step, dict)):
            return True
        if str(source.get("traceRunId") or "").strip():
            return True
        if [item for item in list(source.get("traceRunIds") or []) if str(item).strip()]:
            return True
        return False

    def _required_approvals(self, prepared: Dict[str, Any], *, template_policy: Dict[str, Any] | None = None) -> list[Dict[str, Any]]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        approvals: list[Dict[str, Any]] = []
        assessment = script.get("assessment") if isinstance(script.get("assessment"), dict) else {}
        suppress_compile_review = bool((template_policy or {}).get("suppressCompileReview"))
        if (
            assessment
            and not suppress_compile_review
            and str(assessment.get("status") or "").strip().lower() in {"review_required", "fallback_heavy"}
        ):
            approvals.append(
                {
                    "stepId": "script",
                    "use": "script",
                    "mode": "compile_fallback_heavy" if str(assessment.get("status") or "").strip().lower() == "fallback_heavy" else "compile_review_required",
                    "reason": " / ".join(str(item) for item in list(assessment.get("reasons") or [])[:3]) or "编译结果需要人工复核",
                    "confidence": assessment.get("score"),
                }
            )
        for step in list(script.get("steps") or []):
            approval = step.get("approval") if isinstance(step.get("approval"), dict) else None
            if approval and approval.get("required", True):
                approvals.append(
                    {
                        "stepId": step.get("stepId"),
                        "use": step.get("use"),
                        "mode": approval.get("mode"),
                        "reason": approval.get("reason"),
                        "confidence": ((step.get("assessment") or {}).get("score") if isinstance(step.get("assessment"), dict) else None),
                    }
        )
        return approvals

    def _compile_block_result(
        self,
        *,
        prepared: Dict[str, Any],
        run_handle,
        assessment: Dict[str, Any],
        subject: str,
        template_policy: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        reasons = [str(item) for item in list(assessment.get("reasons") or []) if str(item).strip()]
        reason = " / ".join(reasons[:3]) or "编译准入未通过"
        run_handle.emit(
            "rpa.compile.blocked",
            {
                "subject": subject,
                "assessment": assessment,
                "reason": reason,
            },
        )
        run_handle.fail(f"RPA 编译准入未通过: {reason}", node="rpa_runtime")
        self._update_run_metadata(
            run_handle.run_id,
            executionState="compile_blocked",
            reason=reason,
            trustStatus=normalize_script_assessment_status(assessment.get("status")),
            assessment=assessment,
        )
        self._log_audit(
            action=f"RPA compile blocked: {subject}",
            status="ERROR",
            details=json.dumps({"runId": run_handle.run_id, "reason": reason, "assessment": assessment}, ensure_ascii=False),
        )
        self._record_run_feedback(prepared=prepared, execution_state="compile_blocked")
        return {
            **prepared,
            "status": "compile_blocked",
            "outcomeFamily": outcome_family_for_execution_state("compile_blocked"),
            "reason": reason,
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
            "assessment": assessment,
            "templateExecutionPolicy": template_policy or self._resolve_template_execution_policy(mode="draft", prepared=prepared),
        }

    def _supports_computer_use_fallback(self, *, mode: str, prepared: Dict[str, Any]) -> bool:
        if not self._has_computer_use_replay_capability(mode=mode, prepared=prepared):
            return False
        policy = self._resolve_template_execution_policy(mode=mode, prepared=prepared)
        return bool(policy.get("allowComputerUseFallback", True))

    def _extract_failed_step_context(self, *, execution: Dict[str, Any], script: Dict[str, Any]) -> Dict[str, Any]:
        script_steps = [item for item in list(script.get("steps") or []) if isinstance(item, dict)]
        if not script_steps:
            return {}
        output_chunks = [str(execution.get("stdout") or ""), str(execution.get("stderr") or "")]
        joined_output = "\n".join(chunk for chunk in output_chunks if chunk).strip()
        logged_step_ids = re.findall(r"STEP_ID:([A-Za-z0-9._:-]+)", joined_output)
        step_id_to_index = {
            str(step.get("stepId") or ""): index
            for index, step in enumerate(script_steps)
            if str(step.get("stepId") or "").strip()
        }
        if logged_step_ids:
            last_step_id = str(logged_step_ids[-1]).strip()
            if last_step_id in step_id_to_index:
                index = step_id_to_index[last_step_id]
                return {
                    "stepId": last_step_id,
                    "stepIndex": index,
                    "matchedFrom": "stdout_marker",
                    "remainingSteps": max(0, len(script_steps) - index),
                }
        for index, step in enumerate(script_steps):
            step_id = str(step.get("stepId") or "").strip()
            if step_id and step_id in joined_output:
                return {
                    "stepId": step_id,
                    "stepIndex": index,
                    "matchedFrom": "output_match",
                    "remainingSteps": max(0, len(script_steps) - index),
                }
        return {}

    def _draft_to_computer_use_steps(
        self,
        *,
        script: Dict[str, Any],
        variables: Dict[str, Any],
        start_index: int = 0,
    ) -> list[Dict[str, Any]]:
        app_id = str(script.get("appId") or "").strip() or None
        plan_steps: list[Dict[str, Any]] = []
        draft_steps = [item for item in list(script.get("steps") or []) if isinstance(item, dict)]
        safe_start_index = max(0, int(start_index or 0))
        for local_index, step in enumerate(draft_steps[safe_start_index:], start=safe_start_index):
            if not isinstance(step, dict):
                continue
            use = str(step.get("use") or "").strip()
            if not use:
                continue
            params = _render_template_value(dict(step.get("params") or {}), variables)
            step_metadata = dict(step.get("metadata") or {})
            target = dict(step.get("target") or {})
            window = dict(target.get("window") or {})
            selector = dict(target.get("selector") or {})
            risk = dict(step.get("risk") or {})
            risk_details = dict(risk.get("details") or {})
            timing = dict(step.get("timing") or {})
            target_strategy = dict(step_metadata.get("targetStrategyApplied") or {})
            strategy_payload = dict(target_strategy.get("strategy") or {})
            clipboard_payload = dict(step_metadata.get("clipboardPayload") or {})

            payload: Dict[str, Any] = {
                "action": use,
                **dict(params or {}),
            }
            if app_id and payload.get("app_id") is None:
                payload["app_id"] = app_id
            if window.get("title") and payload.get("window_title") is None:
                payload["window_title"] = window.get("title")
            if window.get("className") and payload.get("class_name") is None:
                payload["class_name"] = window.get("className")
            if window.get("windowHandle") not in (None, "") and payload.get("window_handle") is None:
                payload["window_handle"] = window.get("windowHandle")
            if window.get("processName") and payload.get("process_name") is None:
                payload["process_name"] = window.get("processName")
            if selector.get("selectorKey") and payload.get("selector_key") is None:
                payload["selector_key"] = selector.get("selectorKey")
            if selector.get("elementId") and payload.get("element_id") is None:
                payload["element_id"] = selector.get("elementId")
            if selector.get("name") and payload.get("name") is None:
                payload["name"] = selector.get("name")
            if selector.get("automationId") and payload.get("automation_id") is None:
                payload["automation_id"] = selector.get("automationId")
            if selector.get("controlType") and payload.get("control_type") is None:
                payload["control_type"] = selector.get("controlType")
            if selector.get("className") and payload.get("class_name") is None:
                payload["class_name"] = selector.get("className")
            if selector.get("handle") not in (None, "") and payload.get("handle") is None:
                payload["handle"] = selector.get("handle")
            if payload.get("toolbar_action_name") and payload.get("action_name") is None:
                payload["action_name"] = payload.get("toolbar_action_name")
            if risk_details.get("visualExpectation") and payload.get("visual_expectation") is None:
                payload["visual_expectation"] = risk_details.get("visualExpectation")
            if risk_details.get("targetText") not in (None, "") and payload.get("target_text") is None:
                payload["target_text"] = risk_details.get("targetText")
            if risk_details.get("postActionSettleTimeoutMs") not in (None, "") and payload.get("post_action_settle_timeout_ms") is None:
                payload["post_action_settle_timeout_ms"] = risk_details.get("postActionSettleTimeoutMs")
            if risk_details.get("postActionSettlePollMs") not in (None, "") and payload.get("post_action_settle_poll_ms") is None:
                payload["post_action_settle_poll_ms"] = risk_details.get("postActionSettlePollMs")
            if risk_details.get("postActionStableRounds") not in (None, "") and payload.get("post_action_stable_rounds") is None:
                payload["post_action_stable_rounds"] = risk_details.get("postActionStableRounds")
            if risk_details.get("abortOnMajorDeviation") not in (None, "") and payload.get("abort_on_major_deviation") is None:
                payload["abort_on_major_deviation"] = bool(risk_details.get("abortOnMajorDeviation"))
            if strategy_payload:
                if payload.get("query_mode") in (None, "") and strategy_payload.get("query_mode") not in (None, ""):
                    payload["query_mode"] = strategy_payload.get("query_mode")
                if payload.get("preferred_result_region") in (None, "") and strategy_payload.get("preferred_result_region") not in (None, ""):
                    payload["preferred_result_region"] = strategy_payload.get("preferred_result_region")
                if payload.get("preferred_result_index") in (None, "") and strategy_payload.get("preferred_result_index") not in (None, ""):
                    payload["preferred_result_index"] = strategy_payload.get("preferred_result_index")
                if payload.get("required_exact_match") is None and strategy_payload.get("required_exact_match") is not None:
                    payload["required_exact_match"] = bool(strategy_payload.get("required_exact_match"))
                if not list(payload.get("forbidden_result_tokens") or []) and list(strategy_payload.get("forbidden_result_tokens") or []):
                    payload["forbidden_result_tokens"] = list(strategy_payload.get("forbidden_result_tokens") or [])
                if payload.get("search_selector_key") in (None, "") and strategy_payload.get("search_selector_key") not in (None, ""):
                    payload["search_selector_key"] = strategy_payload.get("search_selector_key")
                if payload.get("result_selector_key") in (None, "") and strategy_payload.get("result_selector_key") not in (None, ""):
                    payload["result_selector_key"] = strategy_payload.get("result_selector_key")
            if clipboard_payload:
                if payload.get("text") in (None, "") and clipboard_payload.get("text") not in (None, ""):
                    payload["text"] = clipboard_payload.get("text")
                if payload.get("file_path") in (None, "") and not list(payload.get("file_paths") or []) and not list(payload.get("attachment_paths") or []):
                    file_paths = list(clipboard_payload.get("file_paths") or [])
                    if len(file_paths) == 1:
                        payload["file_path"] = file_paths[0]
                    elif file_paths:
                        payload["file_paths"] = file_paths
            if _is_unresolved_template_placeholder(payload.get("file_paths")):
                payload.pop("file_paths", None)
            if _is_unresolved_template_placeholder(payload.get("attachment_paths")):
                payload.pop("attachment_paths", None)
            if _is_unresolved_template_placeholder(payload.get("file_path")):
                payload.pop("file_path", None)
            if use == "open_app" and payload.get("wait_timeout_ms") is None and timing.get("waitTimeoutMs") not in (None, ""):
                payload["wait_timeout_ms"] = timing.get("waitTimeoutMs")
            if use == "wait_for_element" and payload.get("timeout_ms") is None and timing.get("waitTimeoutMs") not in (None, ""):
                payload["timeout_ms"] = timing.get("waitTimeoutMs")
            if payload.get("require_visual_guard") is None:
                payload["require_visual_guard"] = bool(
                    risk.get("requiresPreGuard") or risk.get("requiresPostGuard") or step.get("approval")
                )
            payload["_draft_step_id"] = step.get("stepId")
            payload["_draft_step_index"] = local_index
            plan_steps.append(payload)
        return plan_steps

    def _run_computer_use_fallback(
        self,
        *,
        prepared: Dict[str, Any],
        run_id: str | None,
        variables: Dict[str, Any],
        session_id: str,
        user_id: str,
        project_id: str | None,
        workspace_id: str | None,
        workspace_path: str | None,
        failed_step: Dict[str, Any] | None = None,
    ) -> Optional[Dict[str, Any]]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        if not script:
            return None
        failed_step = dict(failed_step or {})
        start_index = max(0, int(failed_step.get("stepIndex") or 0))
        steps = self._draft_to_computer_use_steps(script=script, variables=variables, start_index=start_index)
        if not steps:
            return None
        execution = computer_use_runtime.execute_plan(
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            goal=str(script.get("goal") or script.get("name") or script.get("id") or "computer_use_fallback"),
            steps=steps,
            continue_on_error=False,
            max_steps=max(5, len(steps)),
        )
        feedback_suggestions = self._feedback_suggestions_from_computer_use_execution(execution)
        return {
            "status": "completed",
            "type": "computer_use_fallback",
            "mode": "step_level" if failed_step else "full_script",
            "sourceScriptId": script.get("id"),
            "sourceTraceRunId": ((script.get("source") or {}).get("traceRunId") if isinstance(script.get("source"), dict) else None),
            "sourceTraceRunIds": list(((script.get("source") or {}).get("traceRunIds") or [])) if isinstance(script.get("source"), dict) else [],
            "fallbackStepId": failed_step.get("stepId") if failed_step else None,
            "fallbackStepIndex": start_index,
            "recoveredStepCount": len(steps),
            "execution": execution,
            **({"feedbackSuggestions": feedback_suggestions} if feedback_suggestions else {}),
        }

    def _repair_trace_from_fallback(
        self,
        *,
        prepared: Dict[str, Any],
        fallback_payload: Dict[str, Any],
        failed_step: Dict[str, Any] | None = None,
    ) -> Optional[Dict[str, Any]]:
        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        if not script:
            return None
        execution = fallback_payload.get("execution") if isinstance(fallback_payload.get("execution"), dict) else {}
        trace_run_id = str(execution.get("runId") or "").strip()
        trace = self.compiler.trace_store.get_trace(trace_run_id) if trace_run_id else None
        if not trace:
            executed_steps = [dict(item) for item in list(execution.get("steps") or []) if isinstance(item, dict)]
            if not executed_steps:
                return None
            synthetic_steps: list[Dict[str, Any]] = []
            for index, payload in enumerate(executed_steps, start=1):
                selector: Dict[str, Any] = {}
                if payload.get("selector_key"):
                    selector["selectorKey"] = payload.get("selector_key")
                if payload.get("element_id"):
                    selector["elementId"] = payload.get("element_id")
                if payload.get("name"):
                    selector["name"] = payload.get("name")
                if payload.get("automation_id"):
                    selector["automationId"] = payload.get("automation_id")
                if payload.get("control_type"):
                    selector["controlType"] = payload.get("control_type")
                if payload.get("class_name"):
                    selector["className"] = payload.get("class_name")
                if payload.get("handle") not in (None, ""):
                    selector["handle"] = payload.get("handle")

                window: Dict[str, Any] = {}
                if payload.get("window_title"):
                    window["title"] = payload.get("window_title")
                if payload.get("window_handle") not in (None, ""):
                    window["windowHandle"] = payload.get("window_handle")
                if payload.get("process_name"):
                    window["processName"] = payload.get("process_name")
                if payload.get("class_name"):
                    window["className"] = payload.get("class_name")

                params = {
                    key: value
                    for key, value in payload.items()
                    if not str(key).startswith("_")
                    and key
                    not in {
                        "action",
                        "name",
                        "automation_id",
                        "control_type",
                        "class_name",
                        "handle",
                        "window_title",
                        "window_handle",
                        "process_name",
                        "element_id",
                    }
                }
                synthetic_steps.append(
                    {
                        "stepId": payload.get("_draft_step_id") or f"repair_step_{index}",
                        "index": index,
                        "appId": payload.get("app_id") or script.get("appId") or "desktop",
                        "action": payload.get("action"),
                        "intent": payload.get("action"),
                        "phase": "action",
                        "params": params,
                        "rawParams": dict(params),
                        "target": {
                            "window": window,
                            "selector": selector,
                        },
                        "verification": {"passed": True, "status": "completed"},
                        "recovery": {"performed": False, "transient": False},
                        "risk": {},
                        "timing": {"attemptCount": 1, "retryLimit": 1},
                        "signals": {
                            "binding": {
                                "requestedAppId": payload.get("app_id") or script.get("appId") or "desktop",
                                "resolvedAppId": payload.get("app_id") or script.get("appId") or "desktop",
                                "bindingMode": "explicit",
                                "bindingConfidence": 1.0,
                                "bindingEvidence": {},
                            },
                            "preflight": {
                                "focusConfirmed": True,
                                "windowBound": bool(window),
                                "sceneBound": bool(window),
                                "blockerDetected": False,
                                "riskDowngraded": False,
                            },
                            "verification": {
                                "passed": True,
                                "status": "completed",
                                "level": "verified",
                            },
                            "recovery": {
                                "semanticPathTried": True,
                                "controlledFallbackTried": False,
                                "visualFallbackTried": False,
                                "strictVerificationApplied": True,
                                "finalRecoveryStage": "semantic_path",
                            },
                            "failureCategory": "unknown",
                        },
                        "metadata": {
                            "status": "completed",
                            "syntheticRepairTrace": True,
                        },
                    }
                )
            trace = {
                "version": 1,
                "runId": trace_run_id or f"repair:{script.get('id')}",
                "sessionId": execution.get("sessionId") or ((script.get("source") or {}).get("traceSessionId")),
                "runtimeKind": "computer_use",
                "goal": script.get("goal") or script.get("name") or script.get("id"),
                "metadata": {
                    "traceSchemaVersion": 2,
                    "appId": script.get("appId") or "desktop",
                    "syntheticRepairTrace": True,
                },
                "steps": synthetic_steps,
                "stepCount": len(synthetic_steps),
            }

        repaired = self.compiler.repair_script_from_trace(
            script_payload=script,
            trace=trace,
            start_index=max(0, int((failed_step or {}).get("stepIndex") or fallback_payload.get("fallbackStepIndex") or 0)),
            save=True,
        )
        return {
            "scriptId": repaired.get("id"),
            "templateId": (repaired.get("source") or {}).get("templateId"),
            "templateRevision": (repaired.get("metadata") or {}).get("templateCandidateRevision"),
            "templateGovernance": dict((repaired.get("metadata") or {}).get("templateGovernance") or {}),
            "repairTraceRunId": trace.get("runId"),
            "repairTraceSessionId": trace.get("sessionId"),
            "repairedFromStepIndex": max(0, int((failed_step or {}).get("stepIndex") or fallback_payload.get("fallbackStepIndex") or 0)),
            "patchedStepCount": int(((repaired.get("metadata") or {}).get("lastRepair") or {}).get("patchedStepCount") or 0),
            "localRepairCount": int((repaired.get("metadata") or {}).get("localRepairCount") or 0),
            "syntheticTrace": bool(dict(trace.get("metadata") or {}).get("syntheticRepairTrace")),
        }

    def _request_step_approval(self, *, run_handle, prepared: Dict[str, Any], subject: str) -> Optional[Dict[str, Any]]:
        template_policy = self._resolve_template_execution_policy(mode="draft" if isinstance(prepared.get("script"), dict) else "existing_robot", prepared=prepared)
        approvals = self._required_approvals(prepared, template_policy=template_policy)
        if not approvals:
            return None
        approval = run_handle.request_approval(
            approval_kind="rpa_review",
            request={
                "question": f"RPA 流程包含高风险步骤，是否继续执行？\n\n目标：{subject}",
                "prompt": f"RPA 流程包含高风险步骤，是否继续执行？\n\n目标：{subject}",
                "approvalKind": "rpa_review",
                "rpa": {
                    "subject": subject,
                    "scriptId": (prepared.get("script") or {}).get("id"),
                    "robotFile": prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
                    "requiredApprovals": approvals,
                },
            },
        )
        if str(approval.get("status") or "").strip().lower() != "pending":
            self._log_audit(
                action=f"RPA step approval auto-approved: {subject}",
                status="INFO",
                details=json.dumps(
                    {
                        "approvalId": approval.get("approval_id"),
                        "steps": approvals,
                        "policySource": approval.get("policySource"),
                    },
                    ensure_ascii=False,
                ),
            )
            return None
        self._log_audit(
            action=f"RPA step approval requested: {subject}",
            status="WARNING",
            details=json.dumps({"approvalId": approval.get("approval_id"), "steps": approvals}, ensure_ascii=False),
        )
        return {
            "status": "review_required",
            "outcomeFamily": outcome_family_for_execution_state("review_required"),
            "approvalId": approval.get("approval_id"),
            "requiredApprovals": approvals,
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
        }

    def _rpa_step_approval_gate_enabled(self) -> bool:
        # Same policy as Safety preflight: RPA records governance evidence by
        # default, while execution remains frictionless unless a future config
        # explicitly re-enables the gate.
        return False

    def _audit_step_approvals(
        self,
        *,
        run_handle,
        prepared: Dict[str, Any],
        subject: str,
        template_policy: Dict[str, Any],
    ) -> None:
        approvals = self._required_approvals(prepared, template_policy=template_policy)
        if not approvals:
            return
        run_handle.emit(
            "rpa.approval.audit_only",
            {
                "subject": subject,
                "requiredApprovals": approvals,
                "scriptId": (prepared.get("script") or {}).get("id") if isinstance(prepared.get("script"), dict) else None,
                "robotFile": prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
            },
        )
        self._log_audit(
            action=f"RPA step approval audit-only: {subject}",
            status="WARNING",
            details=json.dumps(
                {
                    "runId": run_handle.run_id,
                    "sessionId": run_handle.session_id,
                    "steps": approvals,
                },
                ensure_ascii=False,
            ),
        )

    def _execute_computer_use_primary(
        self,
        *,
        prepared: Dict[str, Any],
        run_handle,
        subject: str,
        mode: str,
        variables: Optional[Dict[str, Any]],
        user_id: str,
        project_id: str | None,
        workspace_id: str | None,
        workspace_path: str | None,
        assessment: Dict[str, Any],
        template_policy: Dict[str, Any],
    ) -> Dict[str, Any]:
        controlled = self._consume_or_finalize_control(
            run_handle=run_handle,
            stage="computer_use_primary_prepare",
            prepared=prepared,
            assessment=assessment,
            template_policy=template_policy,
        )
        if controlled is not None:
            return controlled
        run_handle.transition("running", reason="computer_use_first", node="rpa_runtime")
        self._update_run_metadata(
            run_handle.run_id,
            executionState="running_computer_use_primary",
            trustStatus=normalize_script_assessment_status(assessment.get("status")),
            templateExecutionPolicy=template_policy,
            templateExecutionPath=template_policy.get("executionPath"),
        )
        run_handle.emit(
            "rpa.execution.routed",
            {
                "mode": mode,
                "subject": subject,
                "routing": template_policy,
            },
        )
        run_handle.emit(
            "rpa.execution.computer_use_primary.started",
            {
                "mode": mode,
                "subject": subject,
                "routing": template_policy,
            },
        )
        execution_payload = self._run_computer_use_fallback(
            prepared=prepared,
            run_id=run_handle.run_id,
            variables=dict(variables or {}),
            session_id=run_handle.session_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            failed_step=None,
        )
        if isinstance(execution_payload, dict) and isinstance(execution_payload.get("control"), dict):
            return self._finalize_controlled(
                run_handle=run_handle,
                prepared=prepared,
                control=dict(execution_payload.get("control") or {}),
                assessment=assessment,
                template_policy=template_policy,
                execution_state=str(execution_payload.get("status") or "paused"),
                extra_payload={"computerUse": execution_payload},
            )
        if not isinstance(execution_payload, dict):
            raise RuntimeError("template governance routed to computer_use_first, but no ComputerUse plan was available")
        repair_payload = None
        try:
            repair_payload = self._repair_trace_from_fallback(
                prepared=prepared,
                fallback_payload=execution_payload,
                failed_step=None,
            )
        except Exception as repair_exc:
            repair_payload = {
                "status": "failed",
                "error": str(repair_exc),
            }
            run_handle.emit(
                "rpa.execution.repair.failed",
                {
                    "mode": mode,
                    "subject": subject,
                    "repair": repair_payload,
                },
            )
        if isinstance(repair_payload, dict) and repair_payload.get("scriptId"):
            run_handle.emit(
                "rpa.execution.repair.completed",
                {
                    "mode": mode,
                    "subject": subject,
                    "repair": repair_payload,
                },
            )
        controlled = self._consume_or_finalize_control(
            run_handle=run_handle,
            stage="computer_use_primary_finalize",
            prepared=prepared,
            assessment=assessment,
            template_policy=template_policy,
            extra_payload={"computerUse": execution_payload, "repair": repair_payload},
        )
        if controlled is not None:
            return controlled
        run_handle.emit(
            "rpa.execution.computer_use_primary.completed",
            {
                "mode": mode,
                "subject": subject,
                "computerUse": execution_payload,
                "repair": repair_payload,
                "routing": template_policy,
            },
        )
        run_handle.complete(reason="computer_use_primary", node="rpa_runtime")
        self._update_run_metadata(
            run_handle.run_id,
            executionState="completed_via_computer_use_primary",
            trustStatus=normalize_script_assessment_status(assessment.get("status")),
            templateExecutionPolicy=template_policy,
            templateExecutionPath=template_policy.get("executionPath"),
            computerUse={
                "type": "computer_use",
                "mode": execution_payload.get("mode"),
                "sourceScriptId": execution_payload.get("sourceScriptId"),
                "sourceTraceRunId": execution_payload.get("sourceTraceRunId"),
                "sourceTraceRunIds": execution_payload.get("sourceTraceRunIds"),
                "recoveredStepCount": execution_payload.get("recoveredStepCount"),
            },
            repair=repair_payload,
        )
        self._record_run_feedback(
            prepared=prepared,
            execution_state="completed_via_computer_use_primary",
            feedback={
                "computerUsePrimary": True,
                "localRepairApplied": bool(isinstance(repair_payload, dict) and repair_payload.get("scriptId")),
                "repairedSteps": int(repair_payload.get("patchedStepCount") or 0) if isinstance(repair_payload, dict) else 0,
                **self._merged_feedback_suggestions(
                    execution_payload.get("feedbackSuggestions") if isinstance(execution_payload, dict) else {}
                ),
            },
        )
        return {
            **prepared,
            "status": "completed_via_computer_use_primary",
            "outcomeFamily": outcome_family_for_execution_state("completed_via_computer_use_primary"),
            "runId": run_handle.run_id,
            "sessionId": run_handle.session_id,
            "templateExecutionPolicy": template_policy,
            "computerUse": execution_payload,
            **({"repair": repair_payload} if repair_payload is not None else {}),
        }

    def _execute_prepared(
        self,
        *,
        prepared: Dict[str, Any],
        subject: str,
        mode: str,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
        timeout_ms: int = 600000,
        cwd: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "system",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        trigger_source: str | None = "manual",
        non_chat_run: bool = False,
    ) -> Dict[str, Any]:
        effective_session_id = self._resolve_session_id(
            script_id=(prepared.get("script") or {}).get("id"),
            robot_file=prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
            session_id=session_id,
        )
        metadata = self._build_run_metadata(
            mode=mode,
            prepared=prepared,
            variables=dict(variables or {}),
            trigger_source=trigger_source,
            cwd=cwd,
        )
        if non_chat_run:
            metadata["nonChatRun"] = True
            metadata["manualRpaRun"] = True
        run_handle = self._begin_run(
            session_id=effective_session_id,
            user_id=user_id,
            trigger_source=trigger_source,
            metadata=metadata,
            title=self._build_session_title(
                script_id=(prepared.get("script") or {}).get("id"),
                robot_file=prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
            ),
            run_id=run_id,
        )
        workflow_ledger_service.activate_runtime_step(
            run_handle.run_id,
            owner_runtime="rpa",
            step_key="rpa.execute_draft" if mode == "draft" else "rpa.execute_existing",
            title="RPA 执行流程",
            owner_agent_id="rpa_runtime",
            input_payload={
                "mode": mode,
                "subject": subject,
                "scriptId": (prepared.get("script") or {}).get("id") if isinstance(prepared.get("script"), dict) else None,
                "robotFile": prepared.get("robotFile") or (prepared.get("export") or {}).get("path"),
            },
        )
        preflight_decision = self._run_preflight(run_handle=run_handle, trigger_source=trigger_source, user_id=user_id)
        if self._rpa_safety_gate_enabled():
            preflight = self._handle_preflight_decision(
                run_handle=run_handle,
                decision=preflight_decision,
                trigger_source=trigger_source,
                subject=subject,
            )
            if preflight is not None:
                self._update_run_metadata(
                    run_handle.run_id,
                    executionState=preflight.get("status"),
                    reason=preflight.get("reason"),
                    approvalId=preflight.get("approvalId"),
                )
                return {**prepared, **preflight, "templateExecutionPolicy": self._resolve_template_execution_policy(mode=mode, prepared=prepared)}
        else:
            self._audit_preflight_decision(
                run_handle=run_handle,
                decision=preflight_decision,
                trigger_source=trigger_source,
                subject=subject,
            )

        script = prepared.get("script") if isinstance(prepared.get("script"), dict) else {}
        assessment = script.get("assessment") if isinstance(script.get("assessment"), dict) else {}
        normalized_assessment_status = normalize_script_assessment_status(assessment.get("status"))
        if assessment:
            assessment = {**assessment, "status": normalized_assessment_status}
        template_policy = self._resolve_template_execution_policy(mode=mode, prepared=prepared)
        controlled = self._consume_or_finalize_control(
            run_handle=run_handle,
            stage="pre_execute",
            prepared=prepared,
            assessment=assessment,
            template_policy=template_policy,
        )
        if controlled is not None:
            return controlled
        if assessment and normalized_assessment_status == "compile_blocked" and not bool(template_policy.get("bypassCompileBlock")):
            return self._compile_block_result(
                prepared=prepared,
                run_handle=run_handle,
                assessment=assessment,
                subject=subject,
                template_policy=template_policy,
            )
        if assessment and normalized_assessment_status == "compile_blocked" and bool(template_policy.get("bypassCompileBlock")):
            run_handle.emit(
                "rpa.execution.policy.override",
                {
                    "mode": mode,
                    "subject": subject,
                    "reason": "compile_block_bypassed_by_template_policy",
                    "routing": template_policy,
                    "assessment": assessment,
                },
            )

        if self._rpa_step_approval_gate_enabled():
            step_approval = self._request_step_approval(run_handle=run_handle, prepared=prepared, subject=subject)
            if step_approval is not None:
                self._update_run_metadata(
                    run_handle.run_id,
                    executionState="review_required",
                    approvalId=step_approval.get("approvalId"),
                    requiredApprovals=step_approval.get("requiredApprovals"),
                    trustStatus=normalized_assessment_status,
                    templateExecutionPolicy=template_policy,
                    templateExecutionPath=template_policy.get("executionPath"),
                )
                self._record_run_feedback(prepared=prepared, execution_state="review_required")
                return {
                    **prepared,
                    **step_approval,
                    "outcomeFamily": outcome_family_for_execution_state("review_required"),
                    "templateExecutionPolicy": template_policy,
                }
        else:
            self._audit_step_approvals(
                run_handle=run_handle,
                prepared=prepared,
                subject=subject,
                template_policy=template_policy,
            )

        if str(template_policy.get("executionPath") or "").strip() == "computer_use_first":
            return self._execute_computer_use_primary(
                prepared=prepared,
                run_handle=run_handle,
                subject=subject,
                mode=mode,
                variables=variables,
                user_id=user_id,
                project_id=project_id,
                workspace_id=workspace_id,
                workspace_path=workspace_path,
                assessment=assessment,
                template_policy=template_policy,
            )

        controlled = self._consume_or_finalize_control(
            run_handle=run_handle,
            stage="before_robot_start",
            prepared=prepared,
            assessment=assessment,
            template_policy=template_policy,
        )
        if controlled is not None:
            return controlled
        run_handle.transition("running", reason=trigger_source or "manual", node="rpa_runtime")
        self._update_run_metadata(
            run_handle.run_id,
            executionState="running_robot",
            trustStatus=normalized_assessment_status,
            templateExecutionPolicy=template_policy,
            templateExecutionPath=template_policy.get("executionPath"),
        )
        run_handle.emit(
            "rpa.execution.routed",
            {
                "mode": mode,
                "subject": subject,
                "routing": template_policy,
            },
        )
        run_handle.emit(
            "rpa.execution.started",
            {
                "mode": mode,
                "subject": subject,
                "command": list(prepared.get("command") or []),
                "cwd": cwd,
                "routing": template_policy,
            },
        )
        self._log_audit(
            action=f"RPA execution started: {subject}",
            status="INFO",
            details=json.dumps({"runId": run_handle.run_id, "mode": mode}, ensure_ascii=False),
        )

        try:
            command_receipt = side_effect_idempotency_service.begin(
                run_handle=run_handle,
                effect_kind="rpa.external_command",
                step_key=f"rpa.execute.{mode}",
                target_identity="|".join(
                    part
                    for part in [
                        str(mode or "").strip(),
                        str(subject or "").strip(),
                        str(cwd or workspace_path or "").strip(),
                    ]
                    if part
                ),
                payload={
                    "command": list(prepared.get("command") or []),
                    "timeoutMs": timeout_ms,
                    "cwd": cwd or workspace_path,
                    "subject": subject,
                    "mode": mode,
                },
                node="rpa_runtime",
                metadata={"subject": subject, "mode": mode},
            )
            if not command_receipt.execute:
                execution = {
                    "returncode": 0,
                    "stdout": "",
                    "stderr": "",
                    "skippedDuplicate": True,
                    "receipt": command_receipt.as_dict(),
                }
                run_handle.emit(
                    "rpa.execution.deduplicated",
                    {
                        "mode": mode,
                        "subject": subject,
                        "receipt": command_receipt.as_dict(),
                    },
                )
            else:
                with bind_runtime_context(
                    runtime_kind="rpa",
                    trigger_source=trigger_source,
                    session_id=run_handle.session_id,
                    run_id=run_handle.run_id,
                    user_id=user_id,
                    project_id=project_id,
                    workspace_id=workspace_id,
                ):
                    execution = self.adapter.run_command(
                        command=list(prepared["command"]),
                        timeout_ms=timeout_ms,
                        cwd=cwd or workspace_path,
                    )
                side_effect_idempotency_service.complete(
                    run_handle=run_handle,
                    receipt=command_receipt,
                    node="rpa_runtime",
                    result={"returncode": execution.get("returncode"), "subject": subject, "mode": mode},
                )
            run_handle.emit(
                "rpa.execution.finished",
                {
                    "mode": mode,
                    "subject": subject,
                    "returncode": execution.get("returncode"),
                    "stdout": execution.get("stdout"),
                    "stderr": execution.get("stderr"),
                },
            )
            controlled = self._consume_or_finalize_control(
                run_handle=run_handle,
                stage="after_robot_execution",
                prepared=prepared,
                assessment=assessment,
                template_policy=template_policy,
                extra_payload={"execution": execution},
            )
            if controlled is not None:
                return controlled
            if int(execution.get("returncode") or 0) != 0:
                error_message = execution.get("stderr") or execution.get("stdout") or f"Robot 流程执行失败: {subject}"
                controlled = self._consume_or_finalize_control(
                    run_handle=run_handle,
                    stage="before_fallback",
                    prepared=prepared,
                    assessment=assessment,
                    template_policy=template_policy,
                    extra_payload={"execution": execution},
                )
                if controlled is not None:
                    return controlled
                fallback_payload = None
                failed_step = self._extract_failed_step_context(execution=execution, script=script)
                if self._supports_computer_use_fallback(mode=mode, prepared=prepared):
                    run_handle.emit(
                        "rpa.execution.fallback.started",
                        {
                            "mode": mode,
                            "subject": subject,
                            "reason": error_message,
                            "fallback": "computer_use",
                            "failedStep": failed_step or None,
                        },
                    )
                    try:
                        self._update_run_metadata(
                            run_handle.run_id,
                            executionState="fallback_running",
                            trustStatus=normalize_script_assessment_status(assessment.get("status")),
                            fallbackStepId=failed_step.get("stepId") if failed_step else None,
                            fallbackStepIndex=failed_step.get("stepIndex") if failed_step else None,
                            templateExecutionPolicy=template_policy,
                            templateExecutionPath=template_policy.get("executionPath"),
                        )
                        fallback_payload = self._run_computer_use_fallback(
                            prepared=prepared,
                            run_id=run_handle.run_id,
                            variables=dict(variables or {}),
                            session_id=run_handle.session_id,
                            user_id=user_id,
                            project_id=project_id,
                            workspace_id=workspace_id,
                            workspace_path=workspace_path,
                            failed_step=failed_step,
                        )
                        if isinstance(fallback_payload, dict) and isinstance(fallback_payload.get("control"), dict):
                            return self._finalize_controlled(
                                run_handle=run_handle,
                                prepared=prepared,
                                control=dict(fallback_payload.get("control") or {}),
                                assessment=assessment,
                                template_policy=template_policy,
                                execution_state=str(fallback_payload.get("status") or "paused"),
                                extra_payload={
                                    "execution": execution,
                                    "fallback": fallback_payload,
                                },
                            )
                        repair_payload = None
                        try:
                            repair_payload = self._repair_trace_from_fallback(
                                prepared=prepared,
                                fallback_payload=fallback_payload,
                                failed_step=failed_step,
                            )
                            if repair_payload is not None:
                                run_handle.emit(
                                    "rpa.execution.repair.completed",
                                    {
                                        "mode": mode,
                                        "subject": subject,
                                        "repair": repair_payload,
                                    },
                                )
                        except Exception as repair_exc:
                            repair_payload = {
                                "status": "failed",
                                "error": str(repair_exc),
                            }
                            run_handle.emit(
                                "rpa.execution.repair.failed",
                                {
                                    "mode": mode,
                                    "subject": subject,
                                    "repair": repair_payload,
                                },
                            )
                        run_handle.emit(
                            "rpa.execution.fallback.completed",
                            {
                                "mode": mode,
                                "subject": subject,
                                "fallback": fallback_payload,
                                "repair": repair_payload,
                            },
                        )
                        controlled = self._consume_or_finalize_control(
                            run_handle=run_handle,
                            stage="fallback_finalize",
                            prepared=prepared,
                            assessment=assessment,
                            template_policy=template_policy,
                            extra_payload={"execution": execution, "fallback": fallback_payload, "repair": repair_payload},
                        )
                        if controlled is not None:
                            return controlled
                        run_handle.complete(reason="computer_use_fallback", node="rpa_runtime")
                        self._update_run_metadata(
                            run_handle.run_id,
                            executionState="completed_with_fallback",
                            trustStatus=normalize_script_assessment_status(assessment.get("status")),
                            templateExecutionPolicy=template_policy,
                            templateExecutionPath=template_policy.get("executionPath"),
                            fallback={
                                "type": "computer_use",
                                "mode": fallback_payload.get("mode") if isinstance(fallback_payload, dict) else None,
                                "sourceScriptId": fallback_payload.get("sourceScriptId") if isinstance(fallback_payload, dict) else None,
                                "sourceTraceRunId": fallback_payload.get("sourceTraceRunId") if isinstance(fallback_payload, dict) else None,
                                "sourceTraceRunIds": fallback_payload.get("sourceTraceRunIds") if isinstance(fallback_payload, dict) else None,
                                "fallbackStepId": fallback_payload.get("fallbackStepId") if isinstance(fallback_payload, dict) else None,
                                "fallbackStepIndex": fallback_payload.get("fallbackStepIndex") if isinstance(fallback_payload, dict) else None,
                                "recoveredStepCount": fallback_payload.get("recoveredStepCount") if isinstance(fallback_payload, dict) else None,
                            },
                            repair=repair_payload,
                        )
                        self._log_audit(
                            action=f"RPA execution completed via fallback: {subject}",
                            status="SUCCESS",
                            details=json.dumps(
                                {
                                    "runId": run_handle.run_id,
                                    "returncode": execution.get("returncode"),
                                    "fallback": "computer_use",
                                },
                                ensure_ascii=False,
                            ),
                        )
                        self._record_run_feedback(
                            prepared=prepared,
                            execution_state="completed_with_fallback",
                            feedback={
                                "stepLevelFallback": bool(isinstance(fallback_payload, dict) and fallback_payload.get("mode") == "step_level"),
                                "recoveredSteps": int(fallback_payload.get("recoveredStepCount") or 0) if isinstance(fallback_payload, dict) else 0,
                                "localRepairApplied": bool(isinstance(repair_payload, dict) and repair_payload.get("scriptId")),
                                "repairedSteps": int(repair_payload.get("patchedStepCount") or 0) if isinstance(repair_payload, dict) else 0,
                                **self._merged_feedback_suggestions(
                                    fallback_payload.get("feedbackSuggestions") if isinstance(fallback_payload, dict) else {}
                                ),
                            },
                        )
                        return {
                            **prepared,
                            "status": "completed_with_fallback",
                            "outcomeFamily": outcome_family_for_execution_state("completed_with_fallback"),
                            "runId": run_handle.run_id,
                            "sessionId": run_handle.session_id,
                            "execution": execution,
                            "templateExecutionPolicy": template_policy,
                            "fallback": fallback_payload,
                            **({"repair": repair_payload} if repair_payload is not None else {}),
                        }
                    except Exception as fallback_exc:
                        fallback_payload = {
                            "status": "failed",
                            "type": "computer_use_fallback",
                            "error": str(fallback_exc),
                        }
                        run_handle.emit(
                            "rpa.execution.fallback.failed",
                            {
                                "mode": mode,
                                "subject": subject,
                                "reason": error_message,
                                "fallback": fallback_payload,
                            },
                        )
                        self._update_run_metadata(
                            run_handle.run_id,
                            executionState="fallback_failed",
                            trustStatus=normalize_script_assessment_status(assessment.get("status")),
                            templateExecutionPolicy=template_policy,
                            templateExecutionPath=template_policy.get("executionPath"),
                            fallback=fallback_payload,
                        )
                run_handle.fail(error_message, node="rpa_runtime")
                self._update_run_metadata(
                    run_handle.run_id,
                    executionState="failed",
                    trustStatus=normalize_script_assessment_status(assessment.get("status")),
                    templateExecutionPolicy=template_policy,
                    templateExecutionPath=template_policy.get("executionPath"),
                    error=error_message,
                )
                self._log_audit(
                    action=f"RPA execution failed: {subject}",
                    status="ERROR",
                    details=json.dumps({"runId": run_handle.run_id, "returncode": execution.get("returncode")}, ensure_ascii=False),
                )
                self._record_run_feedback(
                    prepared=prepared,
                    execution_state="fallback_failed"
                    if isinstance(fallback_payload, dict) and str(fallback_payload.get("type") or "").strip() == "computer_use_fallback"
                    else "failed",
                    feedback={
                        "stepLevelFallback": bool(isinstance(fallback_payload, dict) and fallback_payload.get("mode") == "step_level"),
                        "recoveredSteps": int(fallback_payload.get("recoveredStepCount") or 0) if isinstance(fallback_payload, dict) else 0,
                        **self._merged_feedback_suggestions(
                            fallback_payload.get("feedbackSuggestions") if isinstance(fallback_payload, dict) else {}
                        ),
                    },
                )
                return {
                    **prepared,
                    "status": "failed",
                    "outcomeFamily": outcome_family_for_execution_state("failed"),
                    "runId": run_handle.run_id,
                    "sessionId": run_handle.session_id,
                    "execution": execution,
                    "templateExecutionPolicy": template_policy,
                    **({"fallback": fallback_payload} if fallback_payload is not None else {}),
                }
            controlled = self._consume_or_finalize_control(
                run_handle=run_handle,
                stage="success_finalize",
                prepared=prepared,
                assessment=assessment,
                template_policy=template_policy,
                extra_payload={"execution": execution},
            )
            if controlled is not None:
                return controlled
            run_handle.complete(reason="rpa_finished", node="rpa_runtime")
            self._update_run_metadata(
                run_handle.run_id,
                executionState="completed",
                trustStatus=normalize_script_assessment_status(assessment.get("status")),
                templateExecutionPolicy=template_policy,
                templateExecutionPath=template_policy.get("executionPath"),
            )
            self._log_audit(
                action=f"RPA execution completed: {subject}",
                status="SUCCESS",
                details=json.dumps({"runId": run_handle.run_id, "returncode": execution.get("returncode")}, ensure_ascii=False),
            )
            self._record_run_feedback(prepared=prepared, execution_state="completed")
            return {
                **prepared,
                "status": "completed",
                "outcomeFamily": outcome_family_for_execution_state("completed"),
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
                "execution": execution,
                "templateExecutionPolicy": template_policy,
            }
        except Exception as exc:
            error_message = str(exc)
            controlled = self._consume_or_finalize_control(
                run_handle=run_handle,
                stage="exception",
                prepared=prepared,
                assessment=assessment,
                template_policy=template_policy,
                extra_payload={"error": error_message},
            )
            if controlled is not None:
                return controlled
            fallback_payload = None
            failed_step = {}
            if self._supports_computer_use_fallback(mode=mode, prepared=prepared):
                run_handle.emit(
                    "rpa.execution.fallback.started",
                    {
                        "mode": mode,
                        "subject": subject,
                        "reason": error_message,
                        "fallback": "computer_use",
                    },
                )
                try:
                    self._update_run_metadata(
                        run_handle.run_id,
                        executionState="fallback_running",
                        trustStatus=normalize_script_assessment_status(assessment.get("status")),
                        templateExecutionPolicy=template_policy,
                        templateExecutionPath=template_policy.get("executionPath"),
                    )
                    fallback_payload = self._run_computer_use_fallback(
                        prepared=prepared,
                        run_id=run_handle.run_id,
                        variables=dict(variables or {}),
                        session_id=run_handle.session_id,
                        user_id=user_id,
                        project_id=project_id,
                        workspace_id=workspace_id,
                        workspace_path=workspace_path,
                        failed_step=failed_step,
                    )
                    if isinstance(fallback_payload, dict) and isinstance(fallback_payload.get("control"), dict):
                        return self._finalize_controlled(
                            run_handle=run_handle,
                            prepared=prepared,
                            control=dict(fallback_payload.get("control") or {}),
                            assessment=assessment,
                            template_policy=template_policy,
                            execution_state=str(fallback_payload.get("status") or "paused"),
                            extra_payload={"fallback": fallback_payload, "error": error_message},
                        )
                    repair_payload = None
                    try:
                        repair_payload = self._repair_trace_from_fallback(
                            prepared=prepared,
                            fallback_payload=fallback_payload,
                            failed_step=failed_step,
                        )
                        if repair_payload is not None:
                            run_handle.emit(
                                "rpa.execution.repair.completed",
                                {
                                    "mode": mode,
                                    "subject": subject,
                                    "repair": repair_payload,
                                },
                            )
                    except Exception as repair_exc:
                        repair_payload = {
                            "status": "failed",
                            "error": str(repair_exc),
                        }
                        run_handle.emit(
                            "rpa.execution.repair.failed",
                            {
                                "mode": mode,
                                "subject": subject,
                                "repair": repair_payload,
                            },
                        )
                    run_handle.emit(
                        "rpa.execution.fallback.completed",
                        {
                            "mode": mode,
                            "subject": subject,
                            "fallback": fallback_payload,
                            "repair": repair_payload,
                        },
                    )
                    controlled = self._consume_or_finalize_control(
                        run_handle=run_handle,
                        stage="exception_fallback_finalize",
                        prepared=prepared,
                        assessment=assessment,
                        template_policy=template_policy,
                        extra_payload={"fallback": fallback_payload, "repair": repair_payload, "error": error_message},
                    )
                    if controlled is not None:
                        return controlled
                    run_handle.complete(reason="computer_use_fallback", node="rpa_runtime")
                    self._update_run_metadata(
                        run_handle.run_id,
                        executionState="completed_with_fallback",
                        trustStatus=normalize_script_assessment_status(assessment.get("status")),
                        templateExecutionPolicy=template_policy,
                        templateExecutionPath=template_policy.get("executionPath"),
                        fallback={
                            "type": "computer_use",
                            "mode": fallback_payload.get("mode") if isinstance(fallback_payload, dict) else None,
                            "sourceScriptId": fallback_payload.get("sourceScriptId") if isinstance(fallback_payload, dict) else None,
                            "sourceTraceRunId": fallback_payload.get("sourceTraceRunId") if isinstance(fallback_payload, dict) else None,
                            "sourceTraceRunIds": fallback_payload.get("sourceTraceRunIds") if isinstance(fallback_payload, dict) else None,
                            "fallbackStepId": fallback_payload.get("fallbackStepId") if isinstance(fallback_payload, dict) else None,
                            "fallbackStepIndex": fallback_payload.get("fallbackStepIndex") if isinstance(fallback_payload, dict) else None,
                            "recoveredStepCount": fallback_payload.get("recoveredStepCount") if isinstance(fallback_payload, dict) else None,
                        },
                        repair=repair_payload,
                    )
                    self._log_audit(
                        action=f"RPA execution completed via fallback: {subject}",
                        status="SUCCESS",
                        details=json.dumps(
                            {"runId": run_handle.run_id, "fallback": "computer_use"},
                            ensure_ascii=False,
                        ),
                    )
                    self._record_run_feedback(
                        prepared=prepared,
                        execution_state="completed_with_fallback",
                        feedback={
                            "stepLevelFallback": bool(isinstance(fallback_payload, dict) and fallback_payload.get("mode") == "step_level"),
                            "recoveredSteps": int(fallback_payload.get("recoveredStepCount") or 0) if isinstance(fallback_payload, dict) else 0,
                            "localRepairApplied": bool(isinstance(repair_payload, dict) and repair_payload.get("scriptId")),
                            "repairedSteps": int(repair_payload.get("patchedStepCount") or 0) if isinstance(repair_payload, dict) else 0,
                            **self._merged_feedback_suggestions(
                                fallback_payload.get("feedbackSuggestions") if isinstance(fallback_payload, dict) else {}
                            ),
                        },
                    )
                    return {
                        **prepared,
                        "status": "completed_with_fallback",
                        "outcomeFamily": outcome_family_for_execution_state("completed_with_fallback"),
                        "runId": run_handle.run_id,
                        "sessionId": run_handle.session_id,
                        "error": error_message,
                        "templateExecutionPolicy": template_policy,
                        "fallback": fallback_payload,
                        **({"repair": repair_payload} if repair_payload is not None else {}),
                    }
                except Exception as fallback_exc:
                    fallback_payload = {
                        "status": "failed",
                        "type": "computer_use_fallback",
                        "error": str(fallback_exc),
                    }
                    run_handle.emit(
                        "rpa.execution.fallback.failed",
                        {
                            "mode": mode,
                            "subject": subject,
                            "reason": error_message,
                            "fallback": fallback_payload,
                        },
                    )
                    self._update_run_metadata(
                        run_handle.run_id,
                        executionState="fallback_failed",
                        trustStatus=normalize_script_assessment_status(assessment.get("status")),
                        templateExecutionPolicy=template_policy,
                        templateExecutionPath=template_policy.get("executionPath"),
                        fallback=fallback_payload,
                    )
            run_handle.emit(
                "rpa.execution.failed",
                {"mode": mode, "subject": subject, "error": error_message},
            )
            run_handle.fail(error_message, node="rpa_runtime")
            self._update_run_metadata(
                run_handle.run_id,
                executionState="failed",
                trustStatus=normalize_script_assessment_status(assessment.get("status")),
                templateExecutionPolicy=template_policy,
                templateExecutionPath=template_policy.get("executionPath"),
                error=error_message,
            )
            self._log_audit(action=f"RPA execution failed: {subject}", status="ERROR", details=error_message)
            self._record_run_feedback(
                prepared=prepared,
                execution_state="fallback_failed"
                if isinstance(fallback_payload, dict) and str(fallback_payload.get("type") or "").strip() == "computer_use_fallback"
                else "failed",
                feedback={
                    "stepLevelFallback": bool(isinstance(fallback_payload, dict) and fallback_payload.get("mode") == "step_level"),
                    "recoveredSteps": int(fallback_payload.get("recoveredStepCount") or 0) if isinstance(fallback_payload, dict) else 0,
                    **self._merged_feedback_suggestions(
                        fallback_payload.get("feedbackSuggestions") if isinstance(fallback_payload, dict) else {}
                    ),
                },
            )
            return {
                **prepared,
                "status": "failed",
                "outcomeFamily": outcome_family_for_execution_state("failed"),
                "runId": run_handle.run_id,
                "sessionId": run_handle.session_id,
                "error": error_message,
                "templateExecutionPolicy": template_policy,
                **({"fallback": fallback_payload} if fallback_payload is not None else {}),
            }

    def run_draft(
        self,
        *,
        script_id: str,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
        timeout_ms: int = 600000,
        cwd: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "system",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        trigger_source: str | None = "manual",
        non_chat_run: bool = False,
    ) -> Dict[str, Any]:
        prepared = self.prepare_draft_run(script_id=script_id, variables=variables, output_dir=output_dir)
        subject = str(((prepared.get("script") or {}).get("name")) or script_id)
        return self._execute_prepared(
            prepared=prepared,
            subject=subject,
            mode="draft",
            variables=variables,
            output_dir=output_dir,
            timeout_ms=timeout_ms,
            cwd=cwd,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            trigger_source=trigger_source,
            non_chat_run=non_chat_run,
        )

    def run_existing_flow(
        self,
        *,
        robot_file: str | Path,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: str | Path | None = None,
        timeout_ms: int = 600000,
        cwd: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        user_id: str = "system",
        project_id: str | None = None,
        workspace_id: str | None = None,
        workspace_path: str | None = None,
        trigger_source: str | None = "manual",
        non_chat_run: bool = False,
    ) -> Dict[str, Any]:
        prepared = self.prepare_existing_run(
            robot_file=robot_file,
            variables=variables,
            output_dir=output_dir,
        )
        subject = str(Path(robot_file).name)
        return self._execute_prepared(
            prepared=prepared,
            subject=subject,
            mode="existing_robot",
            variables=variables,
            output_dir=output_dir,
            timeout_ms=timeout_ms,
            cwd=cwd,
            session_id=session_id,
            run_id=run_id,
            user_id=user_id,
            project_id=project_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            trigger_source=trigger_source,
            non_chat_run=non_chat_run,
        )


rpa_runtime = runtime_registry.register(RPARuntime())
