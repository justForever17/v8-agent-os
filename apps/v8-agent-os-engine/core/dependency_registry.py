from __future__ import annotations

import platform
import shutil
import importlib.util
from typing import Any

from core.system_base import detect_desktop_tools_readiness


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
        "installHint": "音视频处理、转码和媒体生成时需要。",
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


def _build_detection_snapshot(entry: dict[str, Any], desktop_readiness: dict[str, Any]) -> dict[str, Any]:
    dep_id = entry["id"]
    if dep_id == "python":
        return {"detected": True, "detail": platform.python_version()}
    if dep_id == "git":
        return {"detected": _detect_binary("git"), "detail": "检测 git 命令是否可执行"}
    if dep_id == "rg":
        return {"detected": _detect_binary("rg"), "detail": "检测 ripgrep (rg) 命令是否可执行"}
    if dep_id == "ffmpeg":
        return {"detected": _detect_binary("ffmpeg"), "detail": "检测 ffmpeg 命令是否可执行"}
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


def build_dependency_status() -> list[dict[str, Any]]:
    system_name = platform.system().lower()
    current_platform = "windows" if system_name.startswith("win") else "macos" if system_name == "darwin" else "linux"
    desktop_readiness = detect_desktop_tools_readiness()
    status: list[dict[str, Any]] = []
    for entry in DEPENDENCY_REGISTRY:
        detection = _build_detection_snapshot(entry, desktop_readiness)
        status.append(
            {
                **entry,
                "platforms": list(entry["platforms"]),
                "appliesToCurrentPlatform": current_platform in entry["platforms"],
                "currentPlatform": current_platform,
                "detection": detection,
            }
        )
    return status
