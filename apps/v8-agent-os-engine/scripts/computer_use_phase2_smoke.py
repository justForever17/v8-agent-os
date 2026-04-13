from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtimes.computer_use.app_adapters import ComputerUseAppAdapterRegistry
from runtimes.computer_use.browser_automation import BrowserAutomationProvider
from runtimes.computer_use.route_policy import build_platform_route_policy, decide_execution_route
from runtimes.computer_use.runtime import computer_use_runtime


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _capability_matrix_fixture() -> Dict[str, Any]:
    matrix = computer_use_runtime._runtime_capability_matrix()
    platforms = dict(matrix.get("platforms") or {})
    _assert({"windows", "macos", "linux"}.issubset(platforms.keys()), "capability matrix 缺少预期平台。")
    facet_names = None
    for platform_name, payload in platforms.items():
        facets = dict(payload.get("facets") or {})
        if facet_names is None:
            facet_names = set(facets.keys())
        _assert(set(facets.keys()) == facet_names, f"{platform_name} facet 结构不一致。")
        for facet_name, facet in facets.items():
            implemented = bool(facet.get("implemented"))
            available = bool(facet.get("available"))
            _assert(not (not implemented and available), f"{platform_name}.{facet_name} 出现 implemented=false 但 available=true。")
            _assert(
                str(facet.get("validationLevel") or "") in {"real_host", "fixture_only", "not_validated"},
                f"{platform_name}.{facet_name} validationLevel 非法。",
            )
    return {
        "currentPlatform": matrix.get("currentPlatform"),
        "platforms": {
            key: {
                "validationLevel": value.get("validationLevel"),
                "facets": {
                    facet_name: {
                        "implemented": facet_value.get("implemented"),
                        "available": facet_value.get("available"),
                        "validationLevel": facet_value.get("validationLevel"),
                    }
                    for facet_name, facet_value in dict(value.get("facets") or {}).items()
                },
            }
            for key, value in platforms.items()
        },
    }


def _route_policy_fixture() -> Dict[str, Any]:
    matrix = computer_use_runtime._runtime_capability_matrix()
    current_platform = str(matrix.get("currentPlatform") or "")
    current_truth = dict(matrix.get("truth") or {})
    policy = build_platform_route_policy(platform_name=current_platform, capability_truth=current_truth)
    browser_route = decide_execution_route(
        action_type="click_target",
        current_platform=current_platform,
        capability_truth=current_truth,
        control_class="browser_host_app",
        browser_lane_available=bool(current_truth.get("supportsBrowserAutomation")),
        browser_target_family="chromium",
        browser_lane_reason="fixture",
        has_visual_locator=False,
        coordinate_fallback=False,
        human_approval_required=False,
    )
    native_route = decide_execution_route(
        action_type="click_target",
        current_platform=current_platform,
        capability_truth=current_truth,
        control_class="native_window_app",
        browser_lane_available=False,
        browser_target_family=None,
        browser_lane_reason=None,
        has_visual_locator=False,
        coordinate_fallback=False,
        human_approval_required=False,
    )
    _assert(browser_route.get("route") in {"browser_automation", "structured_accessibility"}, "browser host route 异常。")
    _assert(native_route.get("route") == "structured_accessibility", "native app route 应优先走 structured_accessibility。")
    return {
        "platform": current_platform,
        "policy": policy,
        "browserHostDecision": browser_route,
        "nativeWindowDecision": native_route,
    }


def _app_adapter_fixture() -> Dict[str, Any]:
    registry = ComputerUseAppAdapterRegistry()
    match = registry.match(
        app_id="vscode",
        app_name="Visual Studio Code",
        process_names=["code.exe"],
        title_patterns=["Visual Studio Code"],
        launch_candidates=[],
    )
    _assert(match is not None, "VS Code adapter 未命中。")
    adapter = match.adapter
    goto_target = str(Path(tempfile.gettempdir()) / "phase2-smoke.py") + ":12:3"
    goto_open = adapter.build_open_command(
        app_id="vscode",
        app_name="Visual Studio Code",
        launch_target_path=goto_target,
    )
    uri_open = adapter.build_open_command(
        app_id="vscode",
        app_name="Visual Studio Code",
        launch_target_path="vscode://file/c:/temp/demo.py:9:1",
    )
    _assert("--reuse-window" in list(goto_open.get("command") or []), "VS Code adapter 应默认复用现有窗口。")
    _assert("--goto" in list(goto_open.get("command") or []), "VS Code adapter 未生成 goto 命令。")
    _assert(str(uri_open.get("targetKind") or "") == "uri", "VS Code URI open 未识别。")
    return {
        "adapterId": match.adapter_id,
        "controlClass": match.control_class,
        "gotoCommand": goto_open,
        "uriCommand": uri_open,
        "capabilitySummary": registry.capability_summary(),
    }


def _live_vscode_smoke() -> Dict[str, Any]:
    code_cmd = Path(r"D:\Program Files\Microsoft VS Code\bin\code.cmd")
    if not code_cmd.exists():
        return {"status": "skipped", "reason": "当前机器未发现 VS Code CLI。"}
    temp_dir = Path(tempfile.mkdtemp(prefix="v8-vscode-smoke-"))
    temp_file = temp_dir / "smoke.txt"
    temp_file.write_text("phase2 smoke", encoding="utf-8")
    try:
        result = computer_use_runtime.open_app(
            app_id="vscode",
            launch_target_path=f"{temp_file}:1:1",
            wait_timeout_ms=12000,
            poll_ms=300,
        )
        action_result = dict(result.get("result") or {})
        metadata = dict(action_result.get("metadata") or {})
        _assert(str(metadata.get("appAdapterId") or "") == "vscode", "VS Code live smoke 未走 app adapter。")
        _assert(str(metadata.get("controlClass") or "") == "electron_shell_app", "VS Code live smoke controlClass 异常。")
        command = list(metadata.get("launchCommand") or [])
        _assert(any(str(token).lower() == "--goto" for token in command), "VS Code live smoke 未生成 goto CLI。")
        return {
            "status": "ok",
            "windowTitle": ((action_result.get("target") or {}).get("title") if isinstance(action_result.get("target"), dict) else None),
            "metadata": metadata,
        }
    finally:
        try:
            temp_file.unlink(missing_ok=True)
            temp_dir.rmdir()
        except Exception:
            pass


def _obsidian_negative_fixture(executable: str | None) -> Dict[str, Any]:
    target = str(executable or "").strip() or r"D:\Program Files (x86)\Obsidian\Obsidian.exe"
    if not Path(target).exists():
        return {"status": "skipped", "reason": f"未找到 Obsidian: {target}"}
    provider = BrowserAutomationProvider()
    provider.configure(
        {
            "browserLane": {
                "enabled": True,
                "mode": "auto_if_available",
                "provider": "engine_managed_cdp",
                "proxyPort": 3472,
                "connectTimeoutMs": 2500,
                "targetFamilies": ["chromium", "electron", "webview2"],
                "allowManagedLaunch": True,
            }
        }
    )
    updated_command, updated_env, meta = provider.prepare_launch(
        app_id="obsidian",
        launch_command=[target],
        environment=os.environ.copy(),
    )
    _assert(bool(meta), "Obsidian 作为 packaged Electron 应产生 managed launch metadata。")
    decision = provider.decide_lane(
        action_type="observe",
        action_payload={"app_id": "obsidian", "window_title": "Obsidian"},
        app_id="obsidian",
        window_title="Obsidian",
    )
    provider.shutdown()
    return {
        "status": "ok",
        "launchMetadata": meta,
        "decision": decision.as_dict(),
        "expectedPhase2Semantics": "managed_launch_shell_only_or_unreachable",
        "preparedCommand": updated_command,
        "environmentKeys": sorted([key for key in dict(updated_env or {}).keys() if key.startswith("WEBVIEW2_")])[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="computer_use phase2 smoke")
    parser.add_argument("--live-vscode", action="store_true")
    parser.add_argument("--obsidian-negative", action="store_true")
    parser.add_argument("--obsidian-executable")
    args = parser.parse_args()

    payload: Dict[str, Any] = {
        "status": "ok",
        "capabilityMatrixFixture": _capability_matrix_fixture(),
        "routePolicyFixture": _route_policy_fixture(),
        "appAdapterFixture": _app_adapter_fixture(),
    }
    if args.live_vscode:
        payload["liveVSCode"] = _live_vscode_smoke()
    if args.obsidian_negative:
        payload["obsidianNegative"] = _obsidian_negative_fixture(args.obsidian_executable)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
