from __future__ import annotations

from copy import deepcopy
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


DEFAULT_ENGINE_BASE_URL = "http://127.0.0.1:9530/v1"
DEFAULT_ADMIN_BASE_URL = "http://127.0.0.1:9528/api"
DEFAULT_DESKTOP_LIVE_CONFIG = {
    "enabled": True,
    "maxWidth": 960,
    "maxHeight": 540,
    "targetFps": 10,
    "singleViewerOnly": True,
    "idleReleaseSeconds": 15,
    "captureDisplay": "primary",
}


def _normalize_url(value: Any, fallback: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        normalized = fallback
    return normalized.rstrip("/")


def _derive_ws_url(http_url: str) -> str:
    normalized = _normalize_url(http_url, DEFAULT_ENGINE_BASE_URL)
    if normalized.startswith("https://"):
        return normalized.replace("https://", "wss://", 1)
    if normalized.startswith("http://"):
        return normalized.replace("http://", "ws://", 1)
    if normalized.startswith("ws://") or normalized.startswith("wss://"):
        return normalized
    return f"ws://{normalized.lstrip('/')}"


def get_system_base_config() -> dict[str, Any]:
    return deepcopy(storage.get_system_base_config())


def get_bridge_config() -> dict[str, Any]:
    config = get_system_base_config()
    bridge = dict(config.get("bridge") or {})
    engine_base_url = _normalize_url(bridge.get("engineBaseUrl"), DEFAULT_ENGINE_BASE_URL)
    admin_base_url = _normalize_url(bridge.get("adminBaseUrl"), DEFAULT_ADMIN_BASE_URL)
    allowed_origins: list[str] = []
    seen: set[str] = set()
    for item in bridge.get("allowedOrigins") or []:
        normalized = str(item or "").strip().rstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        allowed_origins.append(normalized)
    return {
        "engineBaseUrl": engine_base_url,
        "engineWsBaseUrl": _normalize_url(bridge.get("engineWsBaseUrl"), _derive_ws_url(engine_base_url)),
        "adminBaseUrl": admin_base_url,
        "desktopLiveBridgeBaseUrl": _normalize_url(
            bridge.get("desktopLiveBridgeBaseUrl"),
            "http://127.0.0.1:8011/v1",
        ),
        "internalSecret": str(bridge.get("internalSecret") or ""),
        "allowedOrigins": allowed_origins,
    }


def get_engine_base_url() -> str:
    return get_bridge_config()["engineBaseUrl"]


def get_engine_ws_base_url() -> str:
    return get_bridge_config()["engineWsBaseUrl"]


def get_engine_origin() -> str:
    base_url = get_engine_base_url()
    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return base_url.removesuffix("/v1")


def get_admin_base_url() -> str:
    return get_bridge_config()["adminBaseUrl"]


def get_internal_secret() -> str:
    return get_bridge_config()["internalSecret"]


def get_allowed_origins() -> list[str]:
    return list(get_bridge_config().get("allowedOrigins") or [])


def get_web_fetch_config() -> dict[str, Any]:
    config = get_system_base_config()
    return deepcopy(config.get("webFetch") or {})


def get_desktop_tools_config() -> dict[str, Any]:
    config = get_system_base_config()
    return deepcopy(config.get("desktopTools") or {})


def get_desktop_live_config() -> dict[str, Any]:
    config = get_system_base_config()
    raw = dict(config.get("desktopLive") or {})
    merged = deepcopy(DEFAULT_DESKTOP_LIVE_CONFIG)
    merged.update(raw)
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["maxWidth"] = max(320, int(merged.get("maxWidth") or DEFAULT_DESKTOP_LIVE_CONFIG["maxWidth"]))
    merged["maxHeight"] = max(180, int(merged.get("maxHeight") or DEFAULT_DESKTOP_LIVE_CONFIG["maxHeight"]))
    merged["targetFps"] = max(1, min(15, int(merged.get("targetFps") or DEFAULT_DESKTOP_LIVE_CONFIG["targetFps"])))
    merged["singleViewerOnly"] = bool(merged.get("singleViewerOnly", True))
    merged["idleReleaseSeconds"] = max(
        5,
        int(merged.get("idleReleaseSeconds") or DEFAULT_DESKTOP_LIVE_CONFIG["idleReleaseSeconds"]),
    )
    capture_display = str(merged.get("captureDisplay") or "primary").strip().lower()
    merged["captureDisplay"] = capture_display if capture_display in {"primary"} else "primary"
    return merged


def _existing_path(value: Any) -> str:
    candidate = str(value or "").strip()
    if candidate and Path(candidate).exists():
        return candidate
    return ""


def _detect_tesseract_path(config: dict[str, Any]) -> str:
    detected = _existing_path(config.get("tesseractPath"))
    if detected:
        return detected
    env_value = _existing_path(os.getenv("TESSERACT_PATH"))
    if env_value:
        return env_value
    path_hit = shutil.which("tesseract")
    if path_hit:
        return path_hit
    if sys.platform == "win32":
        for candidate in (
            Path("C:/Program Files/Tesseract-OCR/tesseract.exe"),
            Path("C:/Program Files (x86)/Tesseract-OCR/tesseract.exe"),
        ):
            if candidate.exists():
                return str(candidate)
    return ""


def detect_desktop_tools_readiness() -> dict[str, Any]:
    desktop_tools = get_desktop_tools_config()

    tesseract_path = _detect_tesseract_path(desktop_tools)
    tessdata_prefix = _existing_path(desktop_tools.get("tessdataPrefix")) or _existing_path(os.getenv("TESSDATA_PREFIX"))
    omni_root = _existing_path(desktop_tools.get("omniParserRoot")) or _existing_path(os.getenv("V8_AGENT_OS_OMNIPARSER_ROOT"))
    som_model_path = _existing_path(desktop_tools.get("omniParserSomModelPath")) or _existing_path(os.getenv("V8_AGENT_OS_OMNIPARSER_SOM_MODEL_PATH"))
    caption_model_path = _existing_path(desktop_tools.get("omniParserCaptionModelPath")) or _existing_path(os.getenv("V8_AGENT_OS_OMNIPARSER_CAPTION_MODEL_PATH"))
    caption_model_name = str(
        desktop_tools.get("omniParserCaptionModelName")
        or os.getenv("V8_AGENT_OS_OMNIPARSER_CAPTION_MODEL_NAME")
        or ""
    ).strip()
    device = str(desktop_tools.get("omniParserDevice") or os.getenv("V8_AGENT_OS_OMNIPARSER_DEVICE") or "").strip()
    ocr_ready = bool(tesseract_path)
    omni_ready = bool(omni_root and som_model_path and caption_model_path)
    image_locator_ready = omni_ready
    point_locator_ready = True

    if ocr_ready and omni_ready:
        status = "ready"
    elif ocr_ready or omni_ready:
        status = "partial"
    else:
        status = "missing"

    missing_items: list[str] = []
    if not tesseract_path:
        missing_items.append("未检测到 Tesseract")
    if not omni_root:
        missing_items.append("未检测到 OmniParser 根目录")
    if omni_root and not som_model_path:
        missing_items.append("缺少 SOM 模型")
    if omni_root and not caption_model_path:
        missing_items.append("缺少 Caption 模型")
    return {
        "status": status,
        "ocrReady": ocr_ready,
        "omniParserReady": omni_ready,
        "imageLocatorReady": image_locator_ready,
        "pointLocatorReady": point_locator_ready,
        "missingItems": missing_items,
        "detectedDesktopTools": {
            "tesseractPath": tesseract_path,
            "tessdataPrefix": tessdata_prefix,
            "omniParserRoot": omni_root,
            "omniParserSomModelPath": som_model_path,
            "omniParserCaptionModelPath": caption_model_path,
            "omniParserCaptionModelName": caption_model_name,
            "omniParserDevice": device,
        },
        "identity": storage.get_system_identity(),
        "homeDir": str(V8_AGENT_OS_HOME),
    }
