from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtimes.computer_use.app_catalog import ComputerUseAppCatalog
from runtimes.computer_use.app_profiles import ComputerUseAppProfiles
from runtimes.computer_use.drivers.windows_uia import WindowsUIADriver
from runtimes.computer_use.post_action_visual_check import summarize_semantic_post_action_verification
from runtimes.computer_use.runtime import computer_use_runtime


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _run_launch_candidate_fixture() -> List[Dict[str, Any]]:
    catalog = ComputerUseAppCatalog(app_profiles=ComputerUseAppProfiles(), platform_providers=[])
    fixtures = [
        {
            "displayName": "QQ",
            "appId": "app_qq",
            "aliases": ["qq"],
            "launchCandidates": [
                {
                    "command": [r"D:\Program Files\Tencent\QQNT\Uninstall.exe"],
                    "source": "windows_registry_uninstall_string",
                    "role": "uninstall_fallback",
                },
                {
                    "command": [r"D:\Program Files\Tencent\QQNT\QQ.exe"],
                    "source": "windows_registry_display_icon",
                    "role": "display_icon",
                },
                {
                    "command": [r"D:\Program Files\Tencent\QQNT\QQRepair.exe"],
                    "source": "install_location_scan",
                    "role": "helper",
                },
            ],
            "expectedStem": "QQ",
        },
        {
            "displayName": "QQMusic",
            "appId": "app_qqmusic",
            "aliases": ["qqmusic"],
            "launchCandidates": [
                {
                    "command": [r"D:\Program Files\Tencent\QQMusic\QQMusicUninst.exe"],
                    "source": "windows_registry_uninstall_string",
                    "role": "uninstall_fallback",
                },
                {
                    "command": [r"D:\Program Files\Tencent\QQMusic\QQMusic.exe"],
                    "source": "windows_app_paths",
                    "role": "primary_gui",
                },
                {
                    "command": [r"D:\Program Files\Tencent\QQMusic\QQMusicHelper.exe"],
                    "source": "install_location_scan",
                    "role": "helper",
                },
            ],
            "expectedStem": "QQMusic",
        },
        {
            "displayName": "QQLive",
            "appId": "app_qqlive",
            "aliases": ["qqlive"],
            "launchCandidates": [
                {
                    "command": [r"D:\Program Files\Tencent\QQLive\QQLiveHelper.exe"],
                    "source": "install_location_scan",
                    "role": "helper",
                },
                {
                    "command": [r"D:\Program Files\Tencent\QQLive\QQLive.exe"],
                    "source": "windows_registry_display_icon",
                    "role": "display_icon",
                },
            ],
            "expectedStem": "QQLive",
        },
        {
            "displayName": "Adobe Photoshop",
            "appId": "app_adobe_photoshop",
            "aliases": ["photoshop", "adobephotoshop"],
            "launchCandidates": [
                {
                    "command": [r"C:\Program Files\Adobe\Photoshop\helper.exe"],
                    "source": "install_location_scan",
                    "role": "helper",
                },
                {
                    "command": [r"C:\Program Files\Adobe\Photoshop\Photoshop.exe"],
                    "source": "windows_registry_display_icon",
                    "role": "display_icon",
                },
                {
                    "command": [r"C:\Program Files\Adobe\Photoshop\AdobeCrashpad.exe"],
                    "source": "install_location_scan",
                    "role": "helper",
                },
            ],
            "expectedStem": "Photoshop",
        },
    ]
    results: List[Dict[str, Any]] = []
    for fixture in fixtures:
        entry = dict(fixture)
        expected_stem = entry.pop("expectedStem")
        selected = catalog._select_launch_candidate(entry)
        _assert(bool(selected), f"{fixture['displayName']} 未选出启动候选")
        _assert(str((selected or {}).get("executableStem") or "") == expected_stem, f"{fixture['displayName']} 选中了错误候选: {selected}")
        results.append(
            {
                "displayName": fixture["displayName"],
                "selectedStem": selected.get("executableStem"),
                "selectionReason": selected.get("selectionReason"),
                "source": selected.get("source"),
                "role": selected.get("role"),
                "score": selected.get("score"),
            }
        )
    return results


def _run_semantic_fixture() -> List[Dict[str, Any]]:
    fixtures = [
        {
            "name": "input_text_verified",
            "actionType": "type_text",
            "actionPayload": {"text": "https://example.com"},
            "verificationDetails": {"actualText": "https://example.com", "focusState": {"hasKeyboardFocus": True}},
            "bundle": {
                "enabled": True,
                "samplingSource": "computer_use_local_capture",
                "frames": {
                    "preAction": {"windowTitle": "Chrome", "pageIdentity": "地址栏", "textDigest": "地址栏"},
                    "midAction": {"windowTitle": "Chrome", "pageIdentity": "地址栏", "textDigest": "地址栏"},
                    "postAction": {"windowTitle": "Chrome", "pageIdentity": "地址栏", "textDigest": "https://example.com"},
                },
                "diff": {
                    "preToPost": {"treeChanged": True, "screenChanged": True},
                    "midToPost": {"treeChanged": True, "screenChanged": True},
                    "stateAdvanced": True,
                },
            },
            "expectedStatus": "semantic_text_verified",
        },
        {
            "name": "click_unconfirmed",
            "actionType": "click",
            "actionPayload": {"target_text": "提交"},
            "verificationDetails": {"focusState": {"hasKeyboardFocus": False, "isActiveWindow": False}},
            "bundle": {
                "enabled": True,
                "samplingSource": "computer_use_local_capture",
                "frames": {
                    "preAction": {"windowTitle": "表单", "pageIdentity": "page-a", "textDigest": "提交"},
                    "midAction": {"windowTitle": "表单", "pageIdentity": "page-a", "textDigest": "提交"},
                    "postAction": {"windowTitle": "表单", "pageIdentity": "page-a", "textDigest": "提交"},
                },
                "diff": {
                    "preToPost": {"treeChanged": False, "screenChanged": False, "windowChanged": False},
                    "midToPost": {"treeChanged": False, "screenChanged": False, "windowChanged": False},
                    "stateAdvanced": False,
                },
            },
            "expectedStatus": "semantic_click_unconfirmed",
        },
        {
            "name": "scroll_failed",
            "actionType": "scroll",
            "actionPayload": {"amount": 600},
            "verificationDetails": {},
            "bundle": {
                "enabled": True,
                "samplingSource": "computer_use_local_capture",
                "frames": {
                    "preAction": {"windowTitle": "页面", "pageIdentity": "feed", "textDigest": "row1"},
                    "midAction": {"windowTitle": "页面", "pageIdentity": "feed", "textDigest": "row1"},
                    "postAction": {"windowTitle": "页面", "pageIdentity": "feed", "textDigest": "row1"},
                },
                "diff": {
                    "preToPost": {"treeChanged": False, "screenChanged": False},
                    "midToPost": {"treeChanged": False, "screenChanged": False},
                    "stateAdvanced": False,
                },
            },
            "expectedStatus": "semantic_scroll_unconfirmed",
        },
    ]
    results: List[Dict[str, Any]] = []
    for fixture in fixtures:
        semantic = summarize_semantic_post_action_verification(
            action_type=fixture["actionType"],
            action_payload=fixture["actionPayload"],
            verification_details=fixture["verificationDetails"],
            observation_bundle=fixture["bundle"],
        )
        _assert(str(semantic.get("status")) == fixture["expectedStatus"], f"{fixture['name']} 语义验证状态异常: {semantic}")
        results.append(
            {
                "name": fixture["name"],
                "status": semantic.get("status"),
                "evidenceType": semantic.get("evidenceType"),
                "reason": semantic.get("reason"),
            }
        )
    return results


def _run_tray_fixture() -> Dict[str, Any]:
    driver = WindowsUIADriver()
    root = SimpleNamespace(element_info=SimpleNamespace(class_name="Shell_TrayWnd", handle=101))
    fake_element = SimpleNamespace(
        name="QQ",
        class_name="SystemTray.Icon",
        bounds=[20, 20, 60, 60],
        metadata={"isVisible": True},
        role="Button",
    )
    clicks: List[Dict[str, Any]] = []
    restore_calls = {"count": 0}

    driver._shell_surface_roots = lambda backend_name="uia", include_overflow=False: [root]  # type: ignore[assignment]
    driver._enumerate_elements = lambda _root, depth_limit=5, limit=260, backend_name="uia": [fake_element]  # type: ignore[assignment]
    driver._resolve_root_resilient = lambda window_handle=None, backend_name="uia", **_kwargs: root  # type: ignore[assignment]

    def _fake_click(*, point, root, double, prefer_sendinput_click):
        clicks.append({"point": list(point), "double": bool(double)})
        return "sendinput_click"

    driver._coordinate_click = _fake_click  # type: ignore[assignment]

    def _fake_restore_process_window(**_kwargs):
        restore_calls["count"] += 1
        if restore_calls["count"] < 2:
            return None
        return {
            "title": "QQ",
            "handle": 2024,
            "metadata": {},
        }

    driver.restore_process_window = _fake_restore_process_window  # type: ignore[assignment]
    restored = driver.restore_app_from_tray(
        labels=["QQ"],
        process_names=["QQ.exe"],
        title_filters=["QQ"],
        class_names=[],
    )
    _assert(bool(restored), "托盘恢复 fixture 未返回窗口")
    metadata = dict((restored or {}).get("metadata") or {})
    _assert(metadata.get("restoreStrategy") == "tray_icon", f"托盘恢复策略异常: {metadata}")
    _assert(metadata.get("trayRestoreMatchedLabel") == "QQ", f"托盘恢复标签异常: {metadata}")
    _assert(bool(clicks), "托盘恢复 fixture 没有触发托盘点击")
    return {
        "restoreStrategy": metadata.get("restoreStrategy"),
        "trayRestoreMatchedLabel": metadata.get("trayRestoreMatchedLabel"),
        "clicks": clicks,
        "restoreCalls": restore_calls["count"],
    }


def _run_input_preflight_fixture() -> List[Dict[str, Any]]:
    fixtures = [
        {
            "name": "url_ascii",
            "payload": {"text": "https://example.com", "window_handle": None},
            "browserLaneAvailable": False,
        },
        {
            "name": "plain_chinese",
            "payload": {"text": "你好，今天辛苦了", "window_handle": None},
            "browserLaneAvailable": False,
        },
    ]
    results: List[Dict[str, Any]] = []
    for fixture in fixtures:
        preflight = computer_use_runtime._prepare_input_preflight(  # noqa: SLF001
            action_payload=dict(fixture["payload"]),
            browser_decision=None,
        )
        _assert(isinstance(preflight, dict), f"{fixture['name']} 未返回 preflight 字典")
        for required_key in (
            "targetInputKind",
            "layoutBefore",
            "layoutAfter",
            "imeStateBefore",
            "imeStateAfter",
            "normalizationApplied",
        ):
            _assert(required_key in preflight, f"{fixture['name']} 缺少 preflight 字段 {required_key}")
        restored = computer_use_runtime._restore_input_preflight(preflight)  # noqa: SLF001
        _assert(isinstance(restored, dict), f"{fixture['name']} 未返回 restore 字典")
        results.append(
            {
                "name": fixture["name"],
                "targetInputKind": restored.get("targetInputKind"),
                "imeStateBefore": restored.get("imeStateBefore"),
                "imeStateAfter": restored.get("imeStateAfter"),
                "layoutBefore": restored.get("layoutBefore"),
                "layoutAfter": restored.get("layoutAfter"),
                "normalizationApplied": restored.get("normalizationApplied"),
                "restoreApplied": restored.get("restoreApplied"),
            }
        )
    return results


def _count_processes(process_name: str) -> int:
    try:
        import psutil  # type: ignore
    except Exception:
        return 0
    count = 0
    for proc in psutil.process_iter(attrs=["name"]):
        try:
            if str((proc.info or {}).get("name") or "").strip().lower() == process_name.lower():
                count += 1
        except Exception:
            continue
    return count


def _live_notepad_restore_smoke() -> Dict[str, Any]:
    if os.name != "nt":
        return {"status": "skipped", "reason": "只在 Windows 上执行 live notepad smoke。"}
    before_count = _count_processes("notepad.exe")
    opened = computer_use_runtime.open_app(app_id="notepad", wait_timeout_ms=8000)
    action_result = dict((opened.get("result") or {}).get("result") or {})
    target = dict(action_result.get("target") or {})
    handle = int(target.get("windowHandle") or target.get("handle") or 0)
    if handle:
        ctypes.windll.user32.ShowWindow(int(handle), 6)
        time.sleep(0.35)
    restored = computer_use_runtime.open_app(app_id="notepad", wait_timeout_ms=8000)
    restored_action = dict((restored.get("result") or {}).get("result") or {})
    metadata = dict(restored_action.get("metadata") or {})
    after_count = _count_processes("notepad.exe")
    _assert(after_count <= max(before_count + 1, 1), f"notepad 恢复 smoke 生成了额外实例: before={before_count} after={after_count}")
    return {
        "status": "ok",
        "beforeCount": before_count,
        "afterCount": after_count,
        "restoreStrategy": metadata.get("restoreStrategy"),
        "spawnSuppressedByRestore": metadata.get("spawnSuppressedByRestore"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="computer_use phase1 closure smoke")
    parser.add_argument("--live-notepad", action="store_true", help="执行最小化 notepad 恢复 smoke。")
    args = parser.parse_args()

    payload: Dict[str, Any] = {
        "launchCandidates": _run_launch_candidate_fixture(),
        "semanticVerification": _run_semantic_fixture(),
        "trayRestore": _run_tray_fixture(),
        "inputPreflight": _run_input_preflight_fixture(),
    }
    if args.live_notepad:
        payload["liveNotepad"] = _live_notepad_restore_smoke()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
