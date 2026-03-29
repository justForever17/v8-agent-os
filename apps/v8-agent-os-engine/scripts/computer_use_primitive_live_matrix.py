from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.v8_agent_os_paths import ensure_v8_agent_os_tmp_path
from runtimes.computer_use.visual_locator_runtime import RPADesktopVisualLocatorRuntime
from runtimes.computer_use.visual_locator_runtime import resolve_visual_locator_asset_path
from scripts.computer_use_primitive_live_validation import _current_display_context, run_primitive


REPORT_ROOT = Path.home() / ".v8-agent-os" / "reports" / "computer_use"
TEST_TMP_ROOT = ensure_v8_agent_os_tmp_path(scope="tests")
VISUAL_LOCATOR_KEYS = (
    "visual_locator",
    "post_action_visual_locator",
    "start_visual_locator",
    "end_visual_locator",
)


def _visual_locator_capability_summary() -> Dict[str, Any]:
    try:
        return RPADesktopVisualLocatorRuntime().availability_summary()
    except Exception as exc:
        return {
            "providerId": "rpa_desktop_visual_locator",
            "status": "error",
            "runtimeAvailable": False,
            "recognitionAvailable": False,
            "tesseractAvailable": False,
            "supportsImageLocator": False,
            "supportsOcrLocator": False,
            "supportsPointLocator": False,
            "supportsRegionLocator": False,
            "supportsReadText": False,
            "mode": "online_locator_only",
            "notes": [f"visual locator capability summary 失败: {exc.__class__.__name__}: {exc}"],
        }


def _safe_print_json(payload: Dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _load_manifest(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


def _normalize_capture_config(value: Any) -> Dict[str, Any] | None:
    if value in (None, False):
        return None
    if value is True:
        return {"enabled": True, "when": "always"}
    if not isinstance(value, dict):
        raise ValueError("capture 配置必须是布尔值或对象。")
    normalized = {
        "enabled": bool(value.get("enabled", True)),
        "when": str(value.get("when") or "always").strip().lower() or "always",
        "label": str(value.get("label") or "").strip(),
        "windowTitle": str(value.get("windowTitle") or "").strip(),
        "elementId": str(value.get("elementId") or "").strip(),
        "useResultWindow": bool(value.get("useResultWindow", True)),
    }
    if normalized["when"] not in {"always", "on_success", "on_failure"}:
        raise ValueError("capture.when 仅支持 always / on_success / on_failure。")
    return normalized


def _substitute_templates(value: Any, variables: Dict[str, Any]) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{{") and stripped.endswith("}}") and stripped.count("{{") == 1 and stripped.count("}}") == 1:
            key = stripped[2:-2].strip()
            return variables.get(key)
        result = value
        for key, item in variables.items():
            result = result.replace(f"{{{{{key}}}}}", str(item))
        return result
    if isinstance(value, list):
        return [_substitute_templates(item, variables) for item in value]
    if isinstance(value, dict):
        return {str(key): _substitute_templates(item, variables) for key, item in value.items()}
    return value


def _window_rect_from_handle(handle: Any) -> Dict[str, int]:
    if sys.platform != "win32":
        raise RuntimeError("resolve_window_point 当前仅支持 Windows。")
    try:
        import ctypes

        rect = ctypes.wintypes.RECT()
        hwnd = int(handle)
        if hwnd <= 0:
            raise RuntimeError("window handle 无效。")
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("GetWindowRect 调用失败。")
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        return {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
            "width": width,
            "height": height,
        }
    except Exception as exc:
        raise RuntimeError(f"无法读取窗口矩形：{exc}") from exc


def _execute_prepare_action(action: Dict[str, Any], variables: Dict[str, Any]) -> None:
    action_name = str(action.get("action") or "").strip().lower()
    if not action_name:
        raise ValueError("prepare/cleanup action 缺少 action 字段。")

    resolved = _substitute_templates(action, variables)
    if action_name == "create_temp_dir":
        key = str(resolved.get("key") or "").strip()
        prefix = str(resolved.get("prefix") or "v8chat-primitive-live-").strip() or "v8chat-primitive-live-"
        base_dir = str(resolved.get("baseDir") or "").strip()
        root = Path(base_dir).expanduser() if base_dir else TEST_TMP_ROOT
        root.mkdir(parents=True, exist_ok=True)
        path = Path(tempfile.mkdtemp(prefix=prefix, dir=str(root)))
        if key:
            variables[key] = str(path)
        return

    if action_name == "create_temp_file":
        key = str(resolved.get("key") or "").strip()
        parent = Path(str(resolved.get("parent") or TEST_TMP_ROOT)).expanduser()
        parent.mkdir(parents=True, exist_ok=True)
        name = str(resolved.get("name") or "primitive-live.txt").strip() or "primitive-live.txt"
        content = str(resolved.get("content") or "primitive live file").strip()
        path = parent / name
        path.write_text(content, encoding="utf-8")
        if key:
            variables[key] = str(path)
        return

    if action_name == "resolve_window_point":
        key = str(resolved.get("key") or "").strip()
        ensure_payload = {
            "app": str(resolved.get("app") or "").strip(),
            "window_title": str(resolved.get("windowTitle") or "").strip(),
            "class_name": str(resolved.get("className") or "").strip(),
        }
        result = run_primitive("ensure_window", ensure_payload)
        if not bool(result.get("ok")):
            raise RuntimeError(f"resolve_window_point 前置 ensure_window 失败：{result.get('summary') or result}")
        handle = ((result.get("window") or {}).get("handle"))
        rect = _window_rect_from_handle(handle)
        x_ratio = float(resolved.get("x") if resolved.get("x") is not None else 0.5)
        y_ratio = float(resolved.get("y") if resolved.get("y") is not None else 0.5)
        point = [
            int(round(rect["left"] + rect["width"] * x_ratio)),
            int(round(rect["top"] + rect["height"] * y_ratio)),
        ]
        if key:
            variables[key] = point
        return

    if action_name == "remove_path":
        raw_path = resolved.get("path")
        if raw_path in (None, ""):
            return
        path = Path(str(raw_path)).expanduser()
        if not path.exists():
            return
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        return

    raise ValueError(f"未知 prepare/cleanup action: {action_name}")


def _normalize_case(case: Dict[str, Any], *, index: int) -> Dict[str, Any]:
    payload = dict(case.get("payload") or {})
    expectation = dict(case.get("expectation") or {})
    normalized = {
        "id": str(case.get("id") or f"case_{index+1}").strip(),
        "label": str(case.get("label") or case.get("id") or f"Case {index+1}").strip(),
        "primitive": str(case.get("primitive") or "").strip(),
        "runtimeActions": [str(item).strip() for item in _ensure_list(case.get("runtimeActions")) if str(item).strip()],
        "enabled": bool(case.get("enabled", True)),
        "required": bool(case.get("required", True)),
        "payload": payload,
        "prepareActions": [dict(item) for item in _ensure_list(case.get("prepareActions")) if isinstance(item, dict)],
        "cleanupActions": [dict(item) for item in _ensure_list(case.get("cleanupActions")) if isinstance(item, dict)],
        "captureAfter": _normalize_capture_config(case.get("captureAfter")),
        "allowMissingVisualLocatorKeys": [
            str(item).strip()
            for item in _ensure_list(case.get("allowMissingVisualLocatorKeys"))
            if str(item).strip() in VISUAL_LOCATOR_KEYS
        ],
        "expectation": {
            "ok": expectation.get("ok"),
            "blocked": expectation.get("blocked"),
            "statusIn": [str(item).strip() for item in _ensure_list(expectation.get("statusIn")) if str(item).strip()],
            "recommendedNextActionIn": [
                str(item).strip()
                for item in _ensure_list(expectation.get("recommendedNextActionIn"))
                if str(item).strip()
            ],
            "verificationStatusIn": [
                str(item).strip()
                for item in _ensure_list(expectation.get("verificationStatusIn"))
                if str(item).strip()
            ],
            "budgetWithin": expectation.get("budgetWithin"),
            "maxElapsedMs": expectation.get("maxElapsedMs"),
            "pathExistsAll": [str(item).strip() for item in _ensure_list(expectation.get("pathExistsAll")) if str(item).strip()],
            "windowTitleContains": [str(item).strip() for item in _ensure_list(expectation.get("windowTitleContains")) if str(item).strip()],
            "windowTitleNotContains": [str(item).strip() for item in _ensure_list(expectation.get("windowTitleNotContains")) if str(item).strip()],
            "postActionVisualTextContains": [
                str(item).strip()
                for item in _ensure_list(expectation.get("postActionVisualTextContains"))
                if str(item).strip()
            ],
            "postActionVisualTextContainsIfOcrAvailable": [
                str(item).strip()
                for item in _ensure_list(expectation.get("postActionVisualTextContainsIfOcrAvailable"))
                if str(item).strip()
            ],
            "postActionVisualStatusIn": [
                str(item).strip()
                for item in _ensure_list(expectation.get("postActionVisualStatusIn"))
                if str(item).strip()
            ],
        },
        "notes": [str(item).strip() for item in _ensure_list(case.get("notes")) if str(item).strip()],
    }
    if not normalized["primitive"]:
        raise ValueError(f"case `{normalized['id']}` 缺少 primitive")
    return normalized


def _should_capture_after(*, capture_config: Dict[str, Any] | None, passed: bool) -> bool:
    if not capture_config or not bool(capture_config.get("enabled", True)):
        return False
    when = str(capture_config.get("when") or "always").strip().lower()
    if when == "on_success":
        return passed
    if when == "on_failure":
        return not passed
    return True


def _run_scene_capture(
    *,
    capture_config: Dict[str, Any],
    case: Dict[str, Any],
    payload: Dict[str, Any],
    action_result: Dict[str, Any],
) -> Dict[str, Any]:
    requested_window_title = str(capture_config.get("windowTitle") or "").strip()
    requested_window_handle = capture_config.get("windowHandle")
    action_window = dict(action_result.get("window") or {})
    payload_window_title = str(payload.get("window_title") or "").strip()
    payload_window_handle = payload.get("window_handle")
    payload_app = str(payload.get("app") or "").strip()
    payload_target = str(payload.get("target") or payload.get("target_path") or "").strip()
    capture_payload: Dict[str, Any] = {
        "element_id": str(capture_config.get("elementId") or "").strip(),
    }
    if requested_window_handle not in (None, ""):
        capture_payload["window_handle"] = requested_window_handle
    if requested_window_title:
        capture_payload["window_title"] = requested_window_title
    elif bool(capture_config.get("useResultWindow", True)):
        if action_window.get("handle") not in (None, ""):
            capture_payload["window_handle"] = action_window.get("handle")
        if action_window.get("title"):
            capture_payload["window_title"] = str(action_window.get("title") or "")
    if "window_handle" not in capture_payload and payload_window_handle not in (None, ""):
        capture_payload["window_handle"] = payload_window_handle
    if "window_title" not in capture_payload and payload_window_title:
        capture_payload["window_title"] = payload_window_title
    if (
        "window_handle" not in capture_payload
        and "window_title" not in capture_payload
        and payload_app
    ):
        ensure_payload: Dict[str, Any] = {"app": payload_app}
        if payload_target:
            ensure_payload["target"] = payload_target
        ensure_result = run_primitive("ensure_window", ensure_payload)
        ensure_window = dict(ensure_result.get("window") or {})
        if ensure_window.get("handle") not in (None, ""):
            capture_payload["window_handle"] = ensure_window.get("handle")
        if ensure_window.get("title"):
            capture_payload["window_title"] = str(ensure_window.get("title") or "")

    capture_result = run_primitive("capture_screenshot", capture_payload)
    artifact = dict(capture_result.get("artifact") or {})
    target = dict(capture_result.get("target") or {})
    target_window = dict(target.get("window") or {})
    requested_window_title = str(capture_payload.get("window_title") or "").strip()
    captured_window_title = str(target_window.get("title") or "").strip()
    return {
        "requested": True,
        "label": str(capture_config.get("label") or case.get("id") or "").strip(),
        "ok": bool(capture_result.get("ok")),
        "status": str(capture_result.get("status") or "").strip(),
        "summary": str(capture_result.get("summary") or "").strip(),
        "window": dict(capture_result.get("window") or {}),
        "artifact": artifact,
        "artifactPath": str(artifact.get("filePath") or artifact.get("path") or "").strip(),
        "previewUrl": str(artifact.get("previewUrl") or "").strip(),
        "workspacePath": str(artifact.get("workspacePath") or "").strip(),
        "target": target,
        "requestedWindowTitle": requested_window_title,
        "capturedWindowTitle": captured_window_title,
        "windowTitleMismatch": bool(requested_window_title and captured_window_title and requested_window_title != captured_window_title),
    }


def _normalize_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    cases = [_normalize_case(item, index=index) for index, item in enumerate(list(manifest.get("cases") or []))]
    if not cases:
        raise ValueError("manifest 至少需要一个 case")
    expected_display = dict(manifest.get("expectedDisplay") or {})
    return {
        "name": str(manifest.get("name") or "computer_use_primitive_live_matrix").strip(),
        "acceptanceId": str(manifest.get("acceptanceId") or manifest.get("name") or "computer_use_primitive_live_matrix").strip(),
        "description": str(manifest.get("description") or "").strip(),
        "tags": [str(item).strip() for item in _ensure_list(manifest.get("tags")) if str(item).strip()],
        "stopOnFailure": bool(manifest.get("stopOnFailure", False)),
        "sharedSetup": [dict(item) for item in _ensure_list(manifest.get("sharedSetup")) if isinstance(item, dict)],
        "sharedCleanup": [dict(item) for item in _ensure_list(manifest.get("sharedCleanup")) if isinstance(item, dict)],
        "expectedDisplay": {
            "resolution": list(expected_display.get("resolution") or []),
            "dpiScale": expected_display.get("dpiScale"),
        },
        "cases": cases,
    }


def _match_expectation(result: Dict[str, Any], expectation: Dict[str, Any]) -> Tuple[bool, List[str]]:
    failures: List[str] = []
    if expectation.get("ok") is not None and bool(result.get("ok")) is not bool(expectation.get("ok")):
        failures.append(f"ok 期望 {expectation.get('ok')}，实际 {result.get('ok')}")
    if expectation.get("blocked") is not None and bool(result.get("blocked")) is not bool(expectation.get("blocked")):
        failures.append(f"blocked 期望 {expectation.get('blocked')}，实际 {result.get('blocked')}")

    status_in = list(expectation.get("statusIn") or [])
    actual_status = str(result.get("status") or ("completed" if bool(result.get("ok")) else "")).strip()
    if status_in and actual_status not in status_in:
        failures.append(f"status 不在允许集合中: {actual_status}")

    next_actions = list(expectation.get("recommendedNextActionIn") or [])
    if next_actions and str(result.get("recommendedNextAction") or "").strip() not in next_actions:
        failures.append(f"recommendedNextAction 不匹配: {result.get('recommendedNextAction')}")

    verification_status = str(((result.get("verification") or {}).get("status")) or "").strip()
    verification_status_in = list(expectation.get("verificationStatusIn") or [])
    if verification_status_in and verification_status not in verification_status_in:
        failures.append(f"verification.status 不匹配: {verification_status}")

    if expectation.get("budgetWithin") is not None:
        budget_payload = dict(result.get("budget") or {})
        budget_within = (
            bool(budget_payload.get("withinBudget"))
            if budget_payload
            else bool(result.get("ok"))
        )
        if budget_within is not bool(expectation.get("budgetWithin")):
            failures.append(f"budget.withinBudget 期望 {expectation.get('budgetWithin')}，实际 {budget_within}")

    if expectation.get("maxElapsedMs") not in (None, ""):
        elapsed_ms = int(((result.get("budget") or {}).get("elapsedMs")) or 0)
        if elapsed_ms > int(expectation.get("maxElapsedMs")):
            failures.append(f"elapsedMs 超过上限: {elapsed_ms} > {expectation.get('maxElapsedMs')}")

    for raw_path in list(expectation.get("pathExistsAll") or []):
        target_path = Path(str(raw_path)).expanduser()
        if not target_path.exists():
            failures.append(f"pathExistsAll 期望存在文件或目录: {target_path}")

    window_title = str(((result.get("window") or {}).get("title")) or "").strip()
    window_title_lower = window_title.lower()
    for token in list(expectation.get("windowTitleContains") or []):
        normalized = str(token).strip().lower()
        if normalized and normalized not in window_title_lower:
            failures.append(f"window.title 未包含期望片段: {token}")
    for token in list(expectation.get("windowTitleNotContains") or []):
        normalized = str(token).strip().lower()
        if normalized and normalized in window_title_lower:
            failures.append(f"window.title 命中了禁止片段: {token}")

    post_action_visual = dict(((result.get("evidence") or {}).get("postActionVisualLocator")) or {})
    post_action_visual_status = str(post_action_visual.get("status") or "").strip()
    post_action_visual_status_in = list(expectation.get("postActionVisualStatusIn") or [])
    if post_action_visual_status_in and post_action_visual_status not in post_action_visual_status_in:
        failures.append(f"postActionVisualLocator.status 不匹配: {post_action_visual_status}")
    post_action_visual_text = str(post_action_visual.get("readText") or "").strip().lower()
    for token in list(expectation.get("postActionVisualTextContains") or []):
        normalized = str(token).strip().lower()
        if normalized and normalized not in post_action_visual_text:
            failures.append(f"postActionVisualLocator.readText 未包含期望片段: {token}")

    return not failures, failures


def _match_display_expectation(result: Dict[str, Any], expected_display: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    display = dict(((result.get("environment") or {}).get("display")) or {})
    expected_resolution = list(expected_display.get("resolution") or [])
    if len(expected_resolution) == 2:
        actual_resolution = list(display.get("resolution") or [])
        if actual_resolution != expected_resolution:
            failures.append(f"display.resolution 不匹配: {actual_resolution} != {expected_resolution}")
    expected_dpi_scale = expected_display.get("dpiScale")
    if expected_dpi_scale not in (None, ""):
        actual_dpi_scale = display.get("dpiScale")
        if actual_dpi_scale != expected_dpi_scale:
            failures.append(f"display.dpiScale 不匹配: {actual_dpi_scale} != {expected_dpi_scale}")
    return failures


def _asset_status_for_payload(
    payload: Dict[str, Any],
    *,
    allow_missing_keys: List[str] | None = None,
) -> Dict[str, Any]:
    allowed_set = {str(item).strip() for item in list(allow_missing_keys or []) if str(item).strip()}
    resolved_assets: List[Dict[str, Any]] = []
    missing_assets: List[Dict[str, Any]] = []
    allowed_missing_assets: List[Dict[str, Any]] = []
    blocking_missing_assets: List[Dict[str, Any]] = []
    for key in VISUAL_LOCATOR_KEYS:
        candidate = resolve_visual_locator_asset_path(payload.get(key))
        if candidate is None:
            continue
        entry = {
            "key": key,
            "locator": str(payload.get(key) or ""),
            "path": str(candidate),
            "exists": bool(candidate.exists()),
            "allowedMissing": key in allowed_set,
        }
        resolved_assets.append(entry)
        if not entry["exists"]:
            missing_assets.append(entry)
            if key in allowed_set:
                allowed_missing_assets.append(entry)
            else:
                blocking_missing_assets.append(entry)
    return {
        "ready": not blocking_missing_assets,
        "bootstrapReady": bool(allowed_missing_assets) and not blocking_missing_assets,
        "locatorCount": len(resolved_assets),
        "resolvedAssets": resolved_assets,
        "missingAssets": missing_assets,
        "allowedMissingAssets": allowed_missing_assets,
        "blockingMissingAssets": blocking_missing_assets,
    }


def _strip_allowed_missing_visual_locators(
    payload: Dict[str, Any],
    expectation: Dict[str, Any],
    *,
    asset_status: Dict[str, Any],
) -> None:
    for item in list(asset_status.get("allowedMissingAssets") or []):
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        payload.pop(key, None)
        if key == "post_action_visual_locator":
            payload.pop("post_action_visual_locator_read_text", None)
            expectation["postActionVisualStatusIn"] = []
            expectation["postActionVisualTextContains"] = []


def _apply_optional_visual_ocr(
    payload: Dict[str, Any],
    expectation: Dict[str, Any],
    *,
    capability: Dict[str, Any],
) -> Dict[str, Any]:
    supports_read_text = bool(capability.get("supportsReadText"))
    requested = False
    enabled = False
    for prefix in ("", "post_action_"):
        helper_key = f"{prefix}visual_locator_read_text_if_supported"
        explicit_key = f"{prefix}visual_locator_read_text"
        helper_value = payload.pop(helper_key, None)
        if helper_value is True:
            requested = True
            if supports_read_text:
                payload[explicit_key] = True
                enabled = True
        elif helper_value is False and explicit_key in payload:
            payload.pop(explicit_key, None)
    conditional_tokens = list(expectation.pop("postActionVisualTextContainsIfOcrAvailable", []) or [])
    if conditional_tokens and enabled:
        existing = [str(item).strip() for item in list(expectation.get("postActionVisualTextContains") or []) if str(item).strip()]
        expectation["postActionVisualTextContains"] = list(dict.fromkeys(existing + conditional_tokens))
    return {
        "requested": requested or bool(conditional_tokens),
        "enabled": enabled,
        "available": supports_read_text,
    }


def _visual_locator_evidence_summary(result: Dict[str, Any]) -> Dict[str, Any]:
    evidence = dict(result.get("evidence") or {})
    candidates = [
        dict(evidence.get("visualLocator") or {}),
        dict(evidence.get("postActionVisualLocator") or {}),
    ]
    providers: List[str] = []
    verified = 0
    used = 0
    read_text_nonempty = 0
    for item in candidates:
        if not item:
            continue
        used += 1
        provider_id = str(item.get("providerId") or item.get("parserId") or "").strip()
        if provider_id:
            providers.append(provider_id)
        if str(item.get("status") or "").strip().lower() == "verified":
            verified += 1
        if str(item.get("readText") or "").strip():
            read_text_nonempty += 1
    return {
        "used": used > 0,
        "providerIds": list(dict.fromkeys(providers)),
        "usedCount": used,
        "verifiedCount": verified,
        "readTextCount": read_text_nonempty,
    }


def _summarize_case_result(
    case: Dict[str, Any],
    result: Dict[str, Any],
    *,
    passed: bool,
    failures: List[str],
    asset_status: Dict[str, Any] | None = None,
    ocr_enhancement: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    budget = dict(result.get("budget") or {})
    verification = dict(result.get("verification") or {})
    normalized_status = str(result.get("status") or ("completed" if bool(result.get("ok")) else "")).strip()
    normalized_budget_within = bool(budget.get("withinBudget")) if budget else bool(result.get("ok"))
    visual_locator_summary = _visual_locator_evidence_summary(result)
    return {
        "id": case["id"],
        "label": case["label"],
        "primitive": case["primitive"],
        "runtimeActions": list(case.get("runtimeActions") or []),
        "required": bool(case["required"]),
        "enabled": bool(case["enabled"]),
        "passed": bool(passed),
        "failures": list(failures),
        "ok": bool(result.get("ok")),
        "blocked": bool(result.get("blocked")),
        "status": normalized_status,
        "summary": str(result.get("summary") or "").strip(),
        "recommendedNextAction": str(result.get("recommendedNextAction") or "").strip(),
        "verificationStatus": str(verification.get("status") or "").strip(),
        "verificationLevel": str(verification.get("level") or "").strip(),
        "budget": {
            "elapsedMs": int(budget.get("elapsedMs") or 0),
            "withinBudget": normalized_budget_within,
            "exceeded": list(budget.get("exceeded") or []),
        },
        "window": dict(result.get("window") or {}),
        "environment": dict(result.get("environment") or {}),
        "assetStatus": dict(asset_status or {"ready": True, "resolvedAssets": [], "missingAssets": []}),
        "ocrEnhancement": dict(ocr_enhancement or {"requested": False, "enabled": False, "available": False}),
        "visualLocator": visual_locator_summary,
        "updateRequest": dict(result.get("updateRequest") or {}) if isinstance(result.get("updateRequest"), dict) else None,
    }


def _manifest_asset_readiness(case_reports: List[Dict[str, Any]]) -> Dict[str, Any]:
    locator_case_count = 0
    ready_case_count = 0
    missing_case_count = 0
    resolved_asset_count = 0
    missing_asset_count = 0
    missing_assets: List[Dict[str, Any]] = []
    for case in case_reports:
        asset_status = dict(case.get("assetStatus") or {})
        locator_count = int(asset_status.get("locatorCount") or 0)
        if locator_count <= 0:
            continue
        locator_case_count += 1
        resolved_asset_count += locator_count
        missing_items = [dict(item) for item in list(asset_status.get("missingAssets") or []) if isinstance(item, dict)]
        if missing_items:
            missing_case_count += 1
            missing_asset_count += len(missing_items)
            for item in missing_items:
                item.setdefault("caseId", str(case.get("id") or ""))
                missing_assets.append(item)
        else:
            ready_case_count += 1
    return {
        "locatorCaseCount": locator_case_count,
        "readyCaseCount": ready_case_count,
        "missingCaseCount": missing_case_count,
        "resolvedAssetCount": resolved_asset_count,
        "missingAssetCount": missing_asset_count,
        "ready": locator_case_count == 0 or missing_case_count == 0,
        "missingAssets": missing_assets,
    }


def _build_status_only_report(manifest: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_manifest(manifest)
    capability = _visual_locator_capability_summary()
    case_reports: List[Dict[str, Any]] = []
    for case in normalized["cases"]:
        payload = _substitute_templates(case["payload"], {})
        expectation = _substitute_templates(case["expectation"], {})
        asset_status = _asset_status_for_payload(
            payload,
            allow_missing_keys=list(case.get("allowMissingVisualLocatorKeys") or []),
        )
        ocr_enhancement = _apply_optional_visual_ocr(payload, expectation, capability=capability)
        case_reports.append(
            {
                "id": case["id"],
                "label": case["label"],
                "primitive": case["primitive"],
                "required": bool(case["required"]),
                "enabled": bool(case["enabled"]),
                "captureAfter": dict(case.get("captureAfter") or {}) if case.get("captureAfter") else None,
                "assetStatus": asset_status,
                "ocrEnhancement": ocr_enhancement,
                "status": (
                    "disabled"
                    if not case["enabled"]
                    else "bootstrap_ready"
                    if bool(asset_status.get("bootstrapReady"))
                    else "asset_missing"
                    if not bool(asset_status.get("ready"))
                    else "ready"
                ),
            }
        )
    return {
        "name": normalized["name"],
        "acceptanceId": normalized["acceptanceId"],
        "description": normalized["description"],
        "tags": list(normalized.get("tags") or []),
        "generatedAt": datetime.now().isoformat(),
        "mode": "status_only",
        "visualLocatorCapability": capability,
        "assetReadiness": _manifest_asset_readiness(case_reports),
        "cases": case_reports,
    }


def _run_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _normalize_manifest(manifest)
    capability = _visual_locator_capability_summary()
    case_reports: List[Dict[str, Any]] = []
    required_failures = 0
    runtime_display = _current_display_context()
    variables: Dict[str, Any] = {}
    try:
        for action in normalized.get("sharedSetup") or []:
            _execute_prepare_action(action, variables)
        for case in normalized["cases"]:
            if not case["enabled"]:
                case_reports.append(
                    {
                        "id": case["id"],
                        "label": case["label"],
                        "primitive": case["primitive"],
                        "required": bool(case["required"]),
                        "enabled": False,
                        "passed": True,
                        "skipped": True,
                        "reason": "disabled",
                    }
                )
                continue
            try:
                for action in case.get("prepareActions") or []:
                    _execute_prepare_action(action, variables)
                resolved_payload = _substitute_templates(case["payload"], variables)
                resolved_expectation = _substitute_templates(case["expectation"], variables)
                capture_after = dict(case.get("captureAfter") or {}) if case.get("captureAfter") else None
                ocr_enhancement = _apply_optional_visual_ocr(
                    resolved_payload,
                    resolved_expectation,
                    capability=capability,
                )
                asset_status = _asset_status_for_payload(
                    resolved_payload,
                    allow_missing_keys=list(case.get("allowMissingVisualLocatorKeys") or []),
                )
                if bool(asset_status.get("allowedMissingAssets")):
                    _strip_allowed_missing_visual_locators(
                        resolved_payload,
                        resolved_expectation,
                        asset_status=asset_status,
                    )
                if not bool(asset_status.get("ready")):
                    missing_assets = list(asset_status.get("blockingMissingAssets") or asset_status.get("missingAssets") or [])
                    if case["required"]:
                        passed = False
                        failures = [f"缺少 visual locator 图片资产：{', '.join(item.get('path') or '' for item in missing_assets)}"]
                        report = {
                            "id": case["id"],
                            "label": case["label"],
                            "primitive": case["primitive"],
                            "runtimeActions": list(case.get("runtimeActions") or []),
                            "required": bool(case["required"]),
                            "enabled": True,
                            "passed": False,
                            "blocked": True,
                            "skipped": False,
                            "failures": failures,
                            "status": "blocked_asset_missing",
                            "summary": "缺少 visual locator 图片资产，无法执行 required case。",
                            "sceneCapture": None,
                            "assetStatus": asset_status,
                            "ocrEnhancement": ocr_enhancement,
                            "visualLocator": {"used": False, "providerIds": [], "usedCount": 0, "verifiedCount": 0, "readTextCount": 0},
                        }
                    else:
                        passed = False
                        failures = []
                        report = {
                            "id": case["id"],
                            "label": case["label"],
                            "primitive": case["primitive"],
                            "runtimeActions": list(case.get("runtimeActions") or []),
                            "required": bool(case["required"]),
                            "enabled": True,
                            "passed": False,
                            "blocked": False,
                            "skipped": True,
                            "reason": "asset_missing",
                            "status": "asset_missing",
                            "summary": "缺少 visual locator 图片资产，已安全跳过 optional case。",
                            "sceneCapture": None,
                            "assetStatus": asset_status,
                            "ocrEnhancement": ocr_enhancement,
                            "visualLocator": {"used": False, "providerIds": [], "usedCount": 0, "verifiedCount": 0, "readTextCount": 0},
                        }
                    case_reports.append(report)
                    if not passed and case["required"]:
                        required_failures += 1
                        if normalized["stopOnFailure"]:
                            break
                    continue
                result = run_primitive(case["primitive"], resolved_payload)
                passed, failures = _match_expectation(result, resolved_expectation)
                failures.extend(_match_display_expectation(result, normalized.get("expectedDisplay") or {}))
                passed = passed and not failures
                report = _summarize_case_result(
                    case,
                    result,
                    passed=passed,
                    failures=failures,
                    asset_status=asset_status,
                    ocr_enhancement=ocr_enhancement,
                )
                if _should_capture_after(capture_config=capture_after, passed=passed):
                    report["sceneCapture"] = _run_scene_capture(
                        capture_config=capture_after or {"enabled": True, "when": "always"},
                        case=case,
                        payload=resolved_payload,
                        action_result=result,
                    )
                else:
                    report["sceneCapture"] = None
            except Exception as exc:
                passed = False
                failures = [f"执行异常: {exc}"]
                report = {
                    "id": case["id"],
                    "label": case["label"],
                    "primitive": case["primitive"],
                    "runtimeActions": list(case.get("runtimeActions") or []),
                    "required": bool(case["required"]),
                    "enabled": True,
                    "passed": False,
                    "failures": failures,
                    "status": "error",
                    "summary": str(exc),
                    "sceneCapture": None,
                    "assetStatus": {"ready": True, "resolvedAssets": [], "missingAssets": []},
                    "ocrEnhancement": {"requested": False, "enabled": False, "available": bool(capability.get("supportsReadText"))},
                    "visualLocator": {"used": False, "providerIds": [], "usedCount": 0, "verifiedCount": 0, "readTextCount": 0},
                }
            finally:
                for action in reversed(list(case.get("cleanupActions") or [])):
                    try:
                        _execute_prepare_action(action, variables)
                    except Exception:
                        pass
            case_reports.append(report)
            if not passed and case["required"]:
                required_failures += 1
                if normalized["stopOnFailure"]:
                    break
    finally:
        for action in reversed(list(normalized.get("sharedCleanup") or [])):
            try:
                _execute_prepare_action(action, variables)
            except Exception:
                pass
    total_cases = sum(1 for item in case_reports if item.get("enabled", True))
    skipped_cases = sum(1 for item in case_reports if item.get("enabled", True) and item.get("skipped") is True)
    asset_skipped_cases = sum(
        1
        for item in case_reports
        if item.get("enabled", True) and item.get("skipped") is True and str(item.get("reason") or "").strip() == "asset_missing"
    )
    executed_cases = max(0, total_cases - skipped_cases)
    passed_cases = sum(
        1
        for item in case_reports
        if item.get("enabled", True) and item.get("skipped") is not True and item.get("passed") is True
    )
    blocked_cases = sum(1 for item in case_reports if item.get("blocked") is True)
    scene_capture_count = sum(1 for item in case_reports if isinstance(item.get("sceneCapture"), dict))
    scene_capture_failed_count = sum(
        1 for item in case_reports if isinstance(item.get("sceneCapture"), dict) and not bool((item.get("sceneCapture") or {}).get("ok"))
    )
    return {
        "name": normalized["name"],
        "acceptanceId": normalized["acceptanceId"],
        "description": normalized["description"],
        "tags": list(normalized.get("tags") or []),
        "generatedAt": datetime.now().isoformat(),
        "environment": {
            "display": runtime_display,
            "expectedDisplay": dict(normalized.get("expectedDisplay") or {}),
        },
        "visualLocatorCapability": capability,
        "assetReadiness": _manifest_asset_readiness(case_reports),
        "summary": {
            "caseCount": total_cases,
            "executedCount": executed_cases,
            "passedCount": passed_cases,
            "failedCount": max(0, executed_cases - passed_cases),
            "skippedCount": skipped_cases,
            "assetSkippedCount": asset_skipped_cases,
            "requiredFailureCount": required_failures,
            "blockedCount": blocked_cases,
            "sceneCaptureCount": scene_capture_count,
            "sceneCaptureFailedCount": scene_capture_failed_count,
            "allRequiredPassed": required_failures == 0,
        },
        "cases": case_reports,
    }


def _self_check() -> Dict[str, Any]:
    temp_root = Path(tempfile.mkdtemp(prefix="v8chat-matrix-selfcheck-", dir=str(TEST_TMP_ROOT)))
    temp_file = temp_root / "exists.txt"
    temp_file.write_text("ok", encoding="utf-8")
    manifest = {
        "name": "self_check",
        "stopOnFailure": False,
        "cases": [
            {
                "id": "disabled_case",
                "primitive": "observe_scene",
                "enabled": False,
                "required": False,
            },
            {
                "id": "asset_missing_optional",
                "primitive": "click_target",
                "enabled": True,
                "required": False,
                "payload": {"visual_locator": "image:assets/not-found.png"},
            },
            {
                "id": "ocr_helper_case",
                "primitive": "click_target",
                "enabled": True,
                "required": False,
                "payload": {
                    "post_action_visual_locator": "image:assets/not-found.png",
                    "post_action_visual_locator_read_text_if_supported": True,
                },
                "expectation": {
                    "postActionVisualTextContainsIfOcrAvailable": ["联系人"],
                },
            },
        ],
    }
    original_run_primitive = globals()["run_primitive"]

    def _fake_run_primitive(primitive: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
        if primitive == "observe_scene":
            return {
                "ok": True,
                "status": "completed",
                "summary": "观察完成。",
                "window": {"title": "Self Check", "handle": 123},
                "environment": {"display": {"resolution": [2560, 1440]}},
            }
        if primitive == "capture_screenshot":
            return {
                "ok": True,
                "status": "completed",
                "summary": "截图已保存。",
                "artifact": {
                    "filePath": str(temp_root / "capture.png"),
                    "workspacePath": ".v8-agent-os-artifacts/computer_use/selfcheck/capture.png",
                    "previewUrl": "/preview/selfcheck-capture.png",
                },
                "target": {"captured": "window"},
            }
        raise AssertionError(f"self_check 不应执行真实原语: {primitive}")

    globals()["run_primitive"] = _fake_run_primitive
    manifest["cases"].append(
        {
            "id": "capture_after_case",
            "primitive": "observe_scene",
            "enabled": True,
            "required": False,
            "payload": {"window_title": "Self Check"},
            "captureAfter": {"label": "capture_after_case", "when": "always"},
        }
    )
    try:
        report = _run_manifest(manifest)
    finally:
        globals()["run_primitive"] = original_run_primitive
    assert report["summary"]["caseCount"] == 3
    assert report["summary"]["executedCount"] == 1
    assert report["summary"]["assetSkippedCount"] == 2
    assert "visualLocatorCapability" in report
    assert "assetReadiness" in report
    assert report["cases"][0]["skipped"] is True
    assert report["cases"][1]["skipped"] is True
    assert report["cases"][1]["reason"] == "asset_missing"
    assert report["cases"][2]["ocrEnhancement"]["requested"] is True
    assert report["cases"][3]["sceneCapture"]["ok"] is True
    assert report["summary"]["sceneCaptureCount"] == 1
    status_only = _build_status_only_report(manifest)
    assert status_only["mode"] == "status_only"
    assert status_only["assetReadiness"]["missingCaseCount"] == 2
    assert status_only["cases"][3]["captureAfter"]["label"] == "capture_after_case"
    ok, failures = _match_expectation(
        {
            "ok": True,
            "blocked": False,
            "status": "completed",
            "recommendedNextAction": "continue",
            "verification": {"status": "verified"},
            "budget": {"withinBudget": True, "elapsedMs": 200},
        },
        {
            "ok": True,
            "blocked": False,
            "statusIn": ["completed"],
            "recommendedNextActionIn": ["continue"],
            "verificationStatusIn": ["verified"],
            "budgetWithin": True,
            "maxElapsedMs": 500,
            "pathExistsAll": [str(temp_file)],
        },
    )
    assert ok is True and not failures
    shutil.rmtree(temp_root, ignore_errors=True)
    return {
        "status": "ok",
        "checked": [
            "manifest_normalization",
            "disabled_case_skip",
            "asset_missing_skip",
            "status_only_report",
            "visual_locator_capability_projection",
                    "optional_ocr_expectation_projection",
                    "scene_capture_after_projection",
                    "expectation_matching",
            "summary_aggregation",
            "shared_prepare_cleanup",
            "filesystem_expectation",
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行 computer use 基础桌面原语的逐项 live 验证矩阵。")
    parser.add_argument("--manifest", default="", help="矩阵清单 JSON 路径。")
    parser.add_argument("--write-report", action="store_true", help="将结果写入 ~/.v8-agent-os/reports/computer_use/")
    parser.add_argument("--self-check", action="store_true", help="只验证矩阵脚本自身，不触发桌面操作。")
    parser.add_argument("--status-only", action="store_true", help="只输出视觉能力和资产就绪状态，不触发桌面操作。")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.self_check:
        _safe_print_json(_self_check())
        return
    manifest_path = Path(str(args.manifest or "").strip())
    if not manifest_path.is_file():
        raise SystemExit("请通过 --manifest 提供有效的矩阵清单 JSON 文件。")
    manifest = _load_manifest(manifest_path)
    report = _build_status_only_report(manifest) if args.status_only else _run_manifest(manifest)
    if args.write_report:
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        report_name = (
            f"primitive_live_matrix_status_{_timestamp()}.json"
            if args.status_only
            else f"primitive_live_matrix_{_timestamp()}.json"
        )
        report_path = REPORT_ROOT / report_name
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["reportPath"] = str(report_path)
    _safe_print_json(report)


if __name__ == "__main__":
    main()
