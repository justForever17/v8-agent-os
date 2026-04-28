from __future__ import annotations

import json
import platform
import time
from pathlib import Path
from typing import Any

from core.multimodal_payload_adapter import utc_now_iso
from core.v8_agent_os_paths import runtime_private_root
from runtimes.computer_use.platform_probe_runner import PROBE_CHECKS, build_platform_probe_matrix


MATRIX_ROOT = runtime_private_root("computer_use") / "real_host_matrix"
LATEST_PATH = MATRIX_ROOT / "latest.json"


def matrix_storage_paths() -> dict[str, str]:
    MATRIX_ROOT.mkdir(parents=True, exist_ok=True)
    return {"root": str(MATRIX_ROOT), "latest": str(LATEST_PATH)}


def build_real_host_matrix_payload(
    *,
    runtime: Any | None = None,
    real_host: bool = False,
    allow_input: bool = False,
    browser_probe: dict[str, Any] | None = None,
    probe_results: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    driver_summary: dict[str, Any] = {}
    browser_summary = dict(browser_probe or {})
    current_platform = platform.system()
    if runtime is not None:
        try:
            driver_summary = {
                "available": runtime.driver.is_available(),
                "platform": getattr(runtime.driver, "platform", None),
                "backend": getattr(runtime.driver, "backend", None),
            }
            current_platform = str(getattr(runtime.driver, "platform", None) or current_platform)
        except Exception as exc:
            driver_summary = {"available": False, "error": str(exc)}
        if not browser_summary:
            try:
                browser_summary = dict(runtime.browser_automation.availability_summary() or {})
            except Exception as exc:
                browser_summary = {"error": str(exc)}
    matrix = build_platform_probe_matrix(
        current_platform=current_platform,
        driver_summary=driver_summary,
        browser_summary=browser_summary,
        mode="real_host" if real_host else "dry_run",
    )
    if real_host:
        explicit_results = {str(key): dict(value or {}) for key, value in dict(probe_results or {}).items()}
        current_key = matrix.get("currentPlatform")
        platform_payload = dict((matrix.get("platforms") or {}).get(current_key) or {})
        checks = []
        for item in list(platform_payload.get("checks") or []):
            check = dict(item)
            key = str(check.get("key") or "")
            if key in explicit_results:
                result = explicit_results[key]
                check["status"] = str(result.get("status") or "failed")
                check["blockingReason"] = result.get("blockingReason")
                check["evidence"] = {k: v for k, v in result.items() if k not in {"status", "blockingReason"}}
            elif key in {"click", "type_text", "drag", "hotkey"} and not allow_input:
                check["status"] = "blocked_by_permission"
                check["blockingReason"] = "allow_input_required"
            elif key in {"window_enumeration", "foreground_focus", "screenshot", "clipboard_text", "scroll"}:
                check["status"] = "not_run"
                check["blockingReason"] = "probe_result_not_supplied"
            elif key == "permission_probe":
                check["status"] = "theory_aligned"
                check["blockingReason"] = "permission_probe_requires_platform_specific_runner"
            checks.append(check)
        platform_payload["checks"] = checks
        platform_payload["statusCounts"] = {
            status: sum(1 for check in checks if check.get("status") == status)
            for status in sorted({str(check.get("status") or "unknown") for check in checks})
        }
        matrix["platforms"][current_key] = platform_payload
    return {
        "version": 1,
        "generatedAt": utc_now_iso(),
        "realHost": bool(real_host),
        "allowInput": bool(allow_input),
        "platform": platform.system(),
        "matrix": matrix,
        "storage": matrix_storage_paths(),
        "policy": "dry_run never performs input; real_host input checks require --allow-input",
    }


def write_real_host_matrix(payload: dict[str, Any], *, output_path: str | Path | None = None) -> dict[str, Any]:
    MATRIX_ROOT.mkdir(parents=True, exist_ok=True)
    target = Path(output_path) if output_path else MATRIX_ROOT / f"matrix_{int(time.time())}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    LATEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(target), "latest": str(LATEST_PATH)}


def read_latest_real_host_matrix() -> dict[str, Any]:
    if not LATEST_PATH.exists():
        return {"ok": True, "exists": False, "storage": matrix_storage_paths()}
    try:
        payload = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "exists": True, "error": str(exc), "storage": matrix_storage_paths()}
    return {"ok": True, "exists": True, "payload": payload, "storage": matrix_storage_paths()}


def merge_latest_real_host_matrix(base_matrix: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_matrix or {})
    latest = read_latest_real_host_matrix()
    merged["latestRealHostMatrix"] = {
        key: value
        for key, value in latest.items()
        if key in {"ok", "exists", "error", "storage"}
    }
    if not latest.get("ok") or not latest.get("exists"):
        return merged
    payload = dict(latest.get("payload") or {})
    matrix = dict(payload.get("matrix") or {})
    current_platform = str(matrix.get("currentPlatform") or payload.get("platform") or "").strip().lower()
    latest_platforms = dict(matrix.get("platforms") or {})
    merged_platforms = dict(merged.get("platforms") or {})
    if payload.get("realHost") and current_platform and current_platform in latest_platforms:
        merged_platforms[current_platform] = {
            **dict(merged_platforms.get(current_platform) or {}),
            **dict(latest_platforms.get(current_platform) or {}),
            "realHostMatrixRef": {
                "generatedAt": payload.get("generatedAt"),
                "realHost": payload.get("realHost"),
                "allowInput": payload.get("allowInput"),
                "source": "latest_ingested_or_script_output",
            },
        }
        merged["platforms"] = merged_platforms
    merged["latestRealHostMatrix"] = {
        "ok": True,
        "exists": True,
        "generatedAt": payload.get("generatedAt"),
        "realHost": payload.get("realHost"),
        "allowInput": payload.get("allowInput"),
        "platform": payload.get("platform"),
        "storage": latest.get("storage"),
    }
    return merged


def ingest_real_host_matrix(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("matrix payload must be an object")
    if "matrix" not in payload:
        payload = {
            "version": 1,
            "generatedAt": utc_now_iso(),
            "realHost": False,
            "allowInput": False,
            "matrix": payload,
            "storage": matrix_storage_paths(),
        }
    paths = write_real_host_matrix(payload)
    return {"ok": True, "ingested": True, **paths}
