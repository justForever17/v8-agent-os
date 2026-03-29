from __future__ import annotations

import importlib.util
import os
from typing import Any, Mapping, Protocol


class EnvironmentProbeDriver(Protocol):
    def list_windows(self, *, limit: int = 20, backend_name: str = "uia") -> list[dict[str, Any]]: ...


def environment_probe_capabilities() -> dict[str, Any]:
    windows_supported = os.name == "nt"
    pycaw_available = bool(importlib.util.find_spec("pycaw")) and bool(importlib.util.find_spec("comtypes"))
    return {
        "mode": "on_demand_only",
        "notification": {
            "supported": windows_supported,
            "providerId": "windows_window_snapshot_notification_probe" if windows_supported else None,
            "requiresListener": False,
            "collectorKind": "window_snapshot",
        },
        "sound": {
            "supported": windows_supported,
            "providerId": "pycaw_core_audio_session" if pycaw_available else None,
            "requiresListener": False,
            "collectorKind": "session_snapshot" if pycaw_available else "capability_only",
        },
    }


def parse_environment_probe_request(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    probe_payload = dict(payload or {})

    def _pick_bool(*keys: str) -> bool:
        for key in keys:
            if key in probe_payload:
                return bool(probe_payload.get(key))
        return False

    def _pick_text(*keys: str) -> str | None:
        for key in keys:
            value = str(probe_payload.get(key) or "").strip()
            if value:
                return value
        return None

    mode = _pick_text("environment_probe_mode", "ambient_probe_mode") or "on_demand"
    notification_mode = _pick_text("notification_probe_mode") or mode
    sound_mode = _pick_text("sound_probe_mode") or mode
    notification_requested = _pick_bool(
        "observe_notifications",
        "notification_probe",
        "notification_sensing",
    )
    sound_requested = _pick_bool(
        "observe_sound",
        "sound_probe",
        "sound_sensing",
    )
    if not notification_requested and not sound_requested:
        return {}
    return {
        "notification": {
            "requested": notification_requested,
            "mode": notification_mode,
        },
        "sound": {
            "requested": sound_requested,
            "mode": sound_mode,
        },
    }


def _normalize_bounds(window: Mapping[str, Any] | None) -> list[int] | None:
    payload = dict(window or {})
    bounds = payload.get("bounds") or payload.get("rectangle")
    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        try:
            return [int(bounds[0]), int(bounds[1]), int(bounds[2]), int(bounds[3])]
        except Exception:
            return None
    return None


def _looks_like_notification_window(window: Mapping[str, Any] | None) -> bool:
    payload = dict(window or {})
    title = str(payload.get("title") or payload.get("windowTitle") or "").strip().lower()
    class_name = str(payload.get("className") or payload.get("class_name") or "").strip().lower()
    process_name = str(payload.get("processName") or "").strip().lower()
    if not bool(payload.get("isVisible", True)):
        return False
    tokens = ("toast", "notification", "通知")
    if any(token in title for token in tokens):
        return True
    if any(token in class_name for token in ("toast", "notification", "xaml")):
        return True
    if process_name in {"shellexperiencehost.exe", "startmenuexperiencehost.exe"}:
        return True
    bounds = _normalize_bounds(payload)
    if bounds:
        left, top, right, bottom = bounds
        width = max(0, right - left)
        height = max(0, bottom - top)
        if 180 <= width <= 560 and 60 <= height <= 320 and right >= 1200:
            return process_name in {"explorer.exe", "shellexperiencehost.exe"} or not title
    return False


def _sample_notification_probe(
    *,
    driver: EnvironmentProbeDriver,
    requested: bool,
    mode: str | None,
) -> dict[str, Any]:
    available = os.name == "nt"
    providers = ["windows_window_snapshot_notification_probe"] if available else []
    payload: dict[str, Any] = {
        "requested": requested,
        "available": available,
        "mode": mode or "on_demand",
        "providers": providers,
        "observed": False,
        "candidateCount": 0,
    }
    if not requested or not available:
        return payload
    try:
        windows = list(driver.list_windows(limit=30, backend_name="uia") or [])
    except Exception as exc:
        payload["error"] = str(exc)
        return payload
    candidates = [dict(item) for item in windows if _looks_like_notification_window(item)]
    payload["observed"] = bool(candidates)
    payload["candidateCount"] = len(candidates)
    if candidates:
        first = candidates[0]
        payload["topCandidateWindowTitle"] = str(first.get("title") or first.get("windowTitle") or "").strip() or None
        payload["topCandidateProcessName"] = str(first.get("processName") or "").strip() or None
    return payload


def _sample_sound_probe(*, requested: bool, mode: str | None) -> dict[str, Any]:
    pycaw_available = bool(importlib.util.find_spec("pycaw")) and bool(importlib.util.find_spec("comtypes"))
    providers = ["pycaw_core_audio_session"] if pycaw_available else []
    payload: dict[str, Any] = {
        "requested": requested,
        "available": pycaw_available,
        "mode": mode or "on_demand",
        "providers": providers,
        "observed": False,
        "activeSessionCount": 0,
    }
    if not requested or not pycaw_available:
        return payload
    try:
        from pycaw.pycaw import AudioUtilities  # type: ignore

        sessions = list(AudioUtilities.GetAllSessions() or [])
        active_count = 0
        for session in sessions:
            process = getattr(session, "Process", None)
            if process is not None:
                active_count += 1
        payload["activeSessionCount"] = active_count
        payload["observed"] = active_count > 0
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


def collect_environment_probe_snapshot(
    *,
    driver: EnvironmentProbeDriver,
    request: Mapping[str, Any] | None,
) -> dict[str, Any]:
    request_payload = dict(request or {})
    notification_request = dict(request_payload.get("notification") or {})
    sound_request = dict(request_payload.get("sound") or {})
    if not notification_request and not sound_request:
        return {}
    return {
        "notification": _sample_notification_probe(
            driver=driver,
            requested=bool(notification_request.get("requested")),
            mode=str(notification_request.get("mode") or "").strip() or None,
        ),
        "sound": _sample_sound_probe(
            requested=bool(sound_request.get("requested")),
            mode=str(sound_request.get("mode") or "").strip() or None,
        ),
    }
