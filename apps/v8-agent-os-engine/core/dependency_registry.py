from __future__ import annotations

import importlib.util
import platform
import re
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any

from core.system_base import detect_desktop_tools_readiness


FFMPEG_MINIMUM_VERSION = (7, 0)
FFMPEG_MINIMUM_VERSION_TEXT = "7.0"
DEPENDENCY_STATUS_CACHE_TTL_SECONDS = 30.0


_dependency_status_cache: tuple[float, list[dict[str, Any]]] | None = None
_dependency_status_cache_lock = threading.Lock()


DEPENDENCY_REGISTRY: list[dict[str, Any]] = [
    {
        "id": "python",
        "label": "Python",
        "requiredness": "required",
        "category": "core",
        "platforms": ["windows", "macos", "linux"],
        "usedBy": ["engine", "automation", "channels"],
        "installHint": "Engine 本体依赖 Python 运行。建议使用 3.11+。",
    },
    {
        "id": "git",
        "label": "Git",
        "requiredness": "conditional",
        "category": "core",
        "platforms": ["windows", "macos", "linux"],
        "usedBy": ["skills", "automation", "developer_tools"],
        "installHint": "安装技能、同步仓库或拉取外部代码时需要。",
    },
    {
        "id": "rg",
        "label": "ripgrep (rg)",
        "requiredness": "conditional",
        "category": "core",
        "platforms": ["windows", "macos", "linux"],
        "usedBy": ["agent_tools", "workspace_search"],
        "installHint": "搜索工作区、排障和代码定位时会显著加速。",
    },
    {
        "id": "ffmpeg",
        "label": "FFmpeg",
        "requiredness": "conditional",
        "category": "media",
        "platforms": ["windows", "macos", "linux"],
        "usedBy": ["tts", "video", "media_tools"],
        "minimumVersion": FFMPEG_MINIMUM_VERSION_TEXT,
        "installHint": "音视频处理、转码和媒体生成需要同一套 FFmpeg/FFprobe 7.0 或更高版本。",
    },
    {
        "id": "tesseract",
        "label": "Tesseract OCR",
        "requiredness": "conditional",
        "category": "desktop",
        "platforms": ["windows", "macos", "linux"],
        "usedBy": ["computer_use", "ocr"],
        "installHint": "OCR 识别与桌面视觉能力需要。",
    },
    {
        "id": "robotframework",
        "label": "Robot Framework",
        "requiredness": "conditional",
        "category": "automation",
        "platforms": ["windows", "macos", "linux"],
        "usedBy": ["rpa"],
        "installHint": "流程自动化与 .robot 脚本执行需要。",
    },
    {
        "id": "playwright",
        "label": "Playwright / Patchright",
        "requiredness": "conditional",
        "category": "automation",
        "platforms": ["windows", "macos", "linux"],
        "usedBy": ["browser_automation", "tests"],
        "installHint": "网页自动化与浏览器测试需要。",
    },
    {
        "id": "yt-dlp",
        "label": "yt-dlp",
        "requiredness": "optional",
        "category": "media",
        "platforms": ["windows", "macos", "linux"],
        "usedBy": ["media_download"],
        "installHint": "下载外部音视频资源时可用，缺失不会影响聊天主链。",
    },
]


def _is_windows() -> bool:
    return platform.system().lower().startswith("win")


def _detect_binary(name: str) -> bool:
    return bool(shutil.which(name))


def _detect_python_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _detection_detail(*parts: str) -> str:
    return "；".join(part for part in parts if part)


def _parse_ffmpeg_version(output: str, *, binary: str) -> tuple[int, int, int] | None:
    match = re.search(
        rf"(?im)^\s*{re.escape(binary)}\s+version\s+(\d+)\.(\d+)(?:\.(\d+))?",
        str(output or ""),
    )
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def _probe_ffmpeg_binary(binary: str) -> dict[str, Any]:
    executable = shutil.which(binary)
    if not executable:
        return {
            "available": False,
            "path": "",
            "version": "",
            "versionTuple": None,
            "preview": f"未检测到 {binary}",
        }
    try:
        completed = subprocess.run(
            [executable, "-hide_banner", "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "available": False,
            "path": executable,
            "version": "",
            "versionTuple": None,
            "preview": f"{binary} 无法执行：{type(exc).__name__}",
        }
    output = (completed.stdout or completed.stderr or "").strip()
    version_tuple = _parse_ffmpeg_version(output, binary=binary)
    version = ".".join(str(part) for part in version_tuple) if version_tuple else ""
    return {
        "available": completed.returncode == 0,
        "path": executable,
        "version": version,
        "versionTuple": version_tuple,
        "preview": output.splitlines()[0] if output else f"{binary} 未返回版本信息",
    }


def _detect_ffmpeg_pair() -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="dependency-probe") as executor:
        ffmpeg_future = executor.submit(_probe_ffmpeg_binary, "ffmpeg")
        ffprobe_future = executor.submit(_probe_ffmpeg_binary, "ffprobe")
        ffmpeg = ffmpeg_future.result()
        ffprobe = ffprobe_future.result()
    ffmpeg_version = ffmpeg.get("versionTuple")
    ffprobe_version = ffprobe.get("versionTuple")
    meets_minimum = bool(
        ffmpeg.get("available")
        and ffprobe.get("available")
        and ffmpeg_version
        and ffprobe_version
        and tuple(ffmpeg_version) >= FFMPEG_MINIMUM_VERSION
        and tuple(ffprobe_version) >= FFMPEG_MINIMUM_VERSION
    )
    ffmpeg_path = str(ffmpeg.get("path") or "")
    ffprobe_path = str(ffprobe.get("path") or "")
    paired = bool(
        ffmpeg_path
        and ffprobe_path
        and Path(ffmpeg_path).resolve().parent == Path(ffprobe_path).resolve().parent
    )
    detected = meets_minimum and paired
    detail = _detection_detail(
        str(ffmpeg.get("preview") or ""),
        str(ffprobe.get("preview") or ""),
        f"最低要求 {FFMPEG_MINIMUM_VERSION_TEXT}",
        "FFmpeg 与 FFprobe 来自同一安装目录" if paired else "FFmpeg 与 FFprobe 不属于同一安装目录",
    )
    return {
        "detected": detected,
        "installed": bool(ffmpeg.get("available") and ffprobe.get("available")),
        "meetsMinimumVersion": meets_minimum,
        "pairedInstallation": paired,
        "minimumVersion": FFMPEG_MINIMUM_VERSION_TEXT,
        "version": str(ffmpeg.get("version") or ""),
        "ffprobeVersion": str(ffprobe.get("version") or ""),
        "path": ffmpeg_path,
        "ffprobePath": ffprobe_path,
        "detail": detail,
    }


def _build_detection_snapshot(entry: dict[str, Any], desktop_readiness: dict[str, Any]) -> dict[str, Any]:
    dep_id = entry["id"]
    if dep_id == "python":
        return {"detected": True, "detail": platform.python_version()}
    if dep_id == "git":
        return {"detected": _detect_binary("git"), "detail": "检测 git 命令是否可执行"}
    if dep_id == "rg":
        return {"detected": _detect_binary("rg"), "detail": "检测 ripgrep (rg) 命令是否可执行"}
    if dep_id == "ffmpeg":
        return _detect_ffmpeg_pair()
    if dep_id == "tesseract":
        return {"detected": bool(desktop_readiness.get("ocrReady")), "detail": desktop_readiness.get("tesseractPath") or desktop_readiness.get("reason") or ""}
    if dep_id == "robotframework":
        cli_detected = _detect_binary("robot")
        module_detected = _detect_python_module("robot")
        rpa_module_detected = _detect_python_module("RPA")
        return {
            "detected": cli_detected or module_detected or rpa_module_detected,
            "detail": _detection_detail(
                "Robot Framework CLI 可执行" if cli_detected else "",
                "Python robot 模块可导入" if module_detected else "",
                "Python RPA 模块可导入" if rpa_module_detected else "",
            )
            or "未检测到 robot CLI 或 Python robot/RPA 模块",
        }
    if dep_id == "playwright":
        playwright_cli = _detect_binary("playwright")
        patchright_cli = _detect_binary("patchright")
        playwright_module = _detect_python_module("playwright")
        patchright_module = _detect_python_module("patchright")
        return {
            "detected": playwright_cli or patchright_cli or playwright_module or patchright_module,
            "detail": _detection_detail(
                "Playwright CLI 可执行" if playwright_cli else "",
                "Patchright CLI 可执行" if patchright_cli else "",
                "Python playwright 模块可导入" if playwright_module else "",
                "Python patchright 模块可导入" if patchright_module else "",
            )
            or "未检测到 Playwright/Patchright CLI 或 Python 模块",
        }
    if dep_id == "yt-dlp":
        cli_detected = _detect_binary("yt-dlp")
        module_detected = _detect_python_module("yt_dlp")
        return {
            "detected": cli_detected or module_detected,
            "detail": _detection_detail(
                "yt-dlp CLI 可执行" if cli_detected else "",
                "Python yt_dlp 模块可导入" if module_detected else "",
            )
            or "未检测到 yt-dlp CLI 或 Python yt_dlp 模块",
        }
    return {"detected": False, "detail": ""}


def build_dependency_status(
    *,
    desktop_readiness: dict[str, Any] | None = None,
    refresh: bool = False,
) -> list[dict[str, Any]]:
    global _dependency_status_cache

    now = time.monotonic()
    with _dependency_status_cache_lock:
        cached = _dependency_status_cache
        if not refresh and cached is not None and now - cached[0] < DEPENDENCY_STATUS_CACHE_TTL_SECONDS:
            return deepcopy(cached[1])

        system_name = platform.system().lower()
        current_platform = "windows" if system_name.startswith("win") else "macos" if system_name == "darwin" else "linux"
        readiness = dict(desktop_readiness) if isinstance(desktop_readiness, dict) else detect_desktop_tools_readiness()
        status: list[dict[str, Any]] = []
        for entry in DEPENDENCY_REGISTRY:
            detection = _build_detection_snapshot(entry, readiness)
            status.append(
                {
                    **entry,
                    "platforms": list(entry["platforms"]),
                    "appliesToCurrentPlatform": current_platform in entry["platforms"],
                    "currentPlatform": current_platform,
                    "detection": detection,
                }
            )
        _dependency_status_cache = (time.monotonic(), status)
        return deepcopy(status)
