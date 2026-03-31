from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

try:
    import mss
    import mss.tools
except Exception:  # pragma: no cover
    mss = None

from runtimes.computer_use.types import ComputerUseElement, ComputerUseObservation


def tool_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(
    command: Sequence[str],
    *,
    input_text: str | None = None,
    timeout_seconds: float = 8.0,
    check: bool = True,
    env: Dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=check,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def json_command(
    command: Sequence[str],
    payload: Dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 12.0,
) -> Dict[str, Any]:
    completed = run_command(
        command,
        input_text=json.dumps(payload or {}, ensure_ascii=False),
        timeout_seconds=timeout_seconds,
    )
    stdout = str(completed.stdout or "").strip()
    if not stdout:
        return {}
    return json.loads(stdout)


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_bounds(value: Any) -> List[int] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [int(value[0]), int(value[1]), int(value[2]), int(value[3])]
        except Exception:
            return None
    if isinstance(value, dict):
        try:
            left = int(value.get("left"))
            top = int(value.get("top"))
            right = int(value.get("right"))
            bottom = int(value.get("bottom"))
            return [left, top, right, bottom]
        except Exception:
            return None
    return None


def center_point(bounds: Iterable[int] | None) -> List[int] | None:
    normalized = list(bounds or [])
    if len(normalized) != 4:
        return None
    left, top, right, bottom = [int(item) for item in normalized]
    return [int((left + right) / 2), int((top + bottom) / 2)]


def capture_with_mss(
    output_path: str | Path,
    *,
    bounds: List[int] | None = None,
) -> Dict[str, Any]:
    if mss is None:
        raise RuntimeError("缺少 mss，当前平台无法通过内置截图链路抓屏。")
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with mss.mss() as sct:
        if bounds and len(bounds) == 4:
            left, top, right, bottom = [int(item) for item in bounds]
            monitor = {
                "left": left,
                "top": top,
                "width": max(1, right - left),
                "height": max(1, bottom - top),
            }
        else:
            monitor = dict(sct.monitors[1])
            bounds = [
                int(monitor["left"]),
                int(monitor["top"]),
                int(monitor["left"] + monitor["width"]),
                int(monitor["top"] + monitor["height"]),
            ]
        shot = sct.grab(monitor)
        mss.tools.to_png(shot.rgb, shot.size, output=str(target_path))
    return {
        "path": str(target_path),
        "bounds": list(bounds or []),
        "sha256": hash_file(target_path),
    }


def temp_png_path(prefix: str) -> str:
    handle, path = tempfile.mkstemp(prefix=prefix, suffix=".png")
    os.close(handle)
    return path


def build_observation(
    *,
    platform: str,
    backend: str,
    window: Dict[str, Any] | None,
    elements: List[ComputerUseElement],
    metadata: Dict[str, Any] | None = None,
    screenshot_artifact: Dict[str, Any] | None = None,
) -> ComputerUseObservation:
    payload = dict(window or {})
    snapshot_basis = json.dumps(
        {
            "platform": platform,
            "backend": backend,
            "handle": payload.get("handle"),
            "title": payload.get("title"),
            "processName": payload.get("processName"),
            "elementCount": len(elements),
            "timestampBucket": int(time.time() * 2),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    element_payload = [item.as_dict() for item in elements]
    tree_hash = hash_text(json.dumps(element_payload, ensure_ascii=False, sort_keys=True))
    screen_hash = hash_text(
        json.dumps(
            {
                "window": payload,
                "screenshot": dict(screenshot_artifact or {}),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return ComputerUseObservation(
        snapshot_id=hash_text(snapshot_basis)[:16],
        platform=platform,
        backend=backend,
        app=str(payload.get("processName") or payload.get("ownerName") or payload.get("app") or "").strip(),
        window_title=str(payload.get("title") or "").strip(),
        screen_hash=screen_hash,
        tree_hash=tree_hash,
        elements=elements,
        focused_element_id=None,
        screenshot_artifact=dict(screenshot_artifact or {}) if screenshot_artifact else None,
        metadata={
            "windowHandle": payload.get("handle"),
            "className": payload.get("className"),
            "processName": payload.get("processName"),
            "processId": payload.get("processId"),
            "bounds": normalize_bounds(payload.get("bounds")),
            **dict(metadata or {}),
        },
    )
