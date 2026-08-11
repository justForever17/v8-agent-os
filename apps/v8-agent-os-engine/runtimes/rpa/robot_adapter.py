from __future__ import annotations

import json
import os
import re
import sys
import time
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.process_launch import run_windowless
from core.agent_browser_profile import (
    configured_agent_browser_profile_dir,
    discover_system_agent_browser,
    normalize_agent_browser_kind,
)
from runtimes.computer_use.app_profiles import ComputerUseAppProfiles
from runtimes.rpa.keyword_contract import bridge_keyword_issues, is_supported_bridge_use, keyword_name_for_use
from runtimes.rpa.store import RPAScriptStore, rpa_script_store


_ROBOT_CHILD_LAUNCHER = (
    "import runpy,sys; "
    "target,engine_root=sys.argv[1:3]; "
    "sys.path.insert(0,target); "
    "sys.path.insert(1,engine_root); "
    "sys.argv=['robot',*sys.argv[3:]]; "
    "runpy.run_module('robot',run_name='__main__')"
)
_RPA_AVAILABILITY_CHILD_LAUNCHER = r"""
import importlib
import json
import pathlib
import site
import sys

target = pathlib.Path(sys.argv[1]).resolve()
site.addsitedir(str(target))
if str(target) in sys.path:
    sys.path.remove(str(target))
sys.path.insert(0, str(target))

def probe(name):
    try:
        module = importlib.import_module(name)
        spec = getattr(module, "__spec__", None)
        candidates = []
        for value in (getattr(module, "__file__", None), getattr(spec, "origin", None)):
            if value and value not in {"built-in", "frozen"}:
                candidates.append(pathlib.Path(str(value)).resolve())
        candidates.extend(
            pathlib.Path(str(value)).resolve()
            for value in list(getattr(spec, "submodule_search_locations", None) or [])
        )
        in_target = any(candidate == target or target in candidate.parents for candidate in candidates)
        return {
            "detected": True,
            "importable": in_target,
            "origin": str(candidates[0]) if candidates else None,
            "error": None if in_target else "module_origin_outside_feature_pack",
        }
    except Exception as error:
        return {
            "detected": False,
            "importable": False,
            "origin": None,
            "error": type(error).__name__,
        }

names = ["robot", "RPA", "RPA.Windows", "RPA.Browser.Selenium", "RPA.Excel.Files"]
print("V8OS_RPA_AVAILABILITY=" + json.dumps({name: probe(name) for name in names}))
"""
_RPA_TARGET_UNRESOLVED = object()
_RPA_AVAILABILITY_PROBE_TIMEOUT_SECONDS = 8
_RPA_AVAILABILITY_FAILURE_RETRY_SECONDS = 5
_ROBOT_CHILD_ENV_ALLOWLIST = frozenset({
    "APPDATA",
    "COMSPEC",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LANGUAGE",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMDATA",
    "SESSIONNAME",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USER",
    "USERNAME",
    "USERPROFILE",
    "V8_AGENT_OS_HOME",
    "WAYLAND_DISPLAY",
    "WINDIR",
    "XAUTHORITY",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_RUNTIME_DIR",
    "XDG_SESSION_TYPE",
})


class RobotFrameworkAdapter:
    def __init__(self, *, script_store: RPAScriptStore = rpa_script_store) -> None:
        self.script_store = script_store
        self.app_profiles = ComputerUseAppProfiles()
        self._rpa_target_cache: object | Path | None = _RPA_TARGET_UNRESOLVED
        self._availability_cache: Dict[str, Any] | None = None
        self._availability_cache_expires_at: float | None = None

    @staticmethod
    def _engine_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def _rpa_target_dir(self) -> Path | None:
        """Resolve only the receipt-governed RPA target for this Engine boot."""

        if self._rpa_target_cache is not _RPA_TARGET_UNRESOLVED:
            return self._rpa_target_cache if isinstance(self._rpa_target_cache, Path) else None

        try:
            from core.runtime.feature_packs import build_feature_pack_statuses
            from core.storage import storage

            status = next(
                item
                for item in build_feature_pack_statuses(storage.get_runtime_registry_config())
                if item.get("id") == "rpa_automation"
            )
        except Exception:
            self._rpa_target_cache = None
            return None
        if status.get("status") != "installed" or status.get("restartRequired"):
            self._rpa_target_cache = None
            return None
        target_value = str(status.get("targetDir") or "").strip()
        if not target_value:
            self._rpa_target_cache = None
            return None
        target = Path(target_value).expanduser().resolve(strict=False)
        self._rpa_target_cache = target if target.is_dir() else None
        return self._rpa_target_cache

    def _require_rpa_target_dir(self) -> Path:
        target = self._rpa_target_dir()
        if target is None:
            raise RuntimeError("RPA 能力包尚未安装完成或仍需重启 Engine。")
        return target

    def _robot_child_environment(self, target: Path) -> Dict[str, str]:
        environment: Dict[str, str] = {}
        for key, value in os.environ.items():
            normalized = str(key or "")
            upper = normalized.upper()
            if upper not in _ROBOT_CHILD_ENV_ALLOWLIST and not upper.startswith("LC_"):
                continue
            environment[normalized] = str(value)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["V8_AGENT_OS_RPA_TARGET"] = str(target)
        return environment

    def is_available(self) -> bool:
        return bool(self.availability().get("robotFramework"))

    def rpa_framework_available(self) -> bool:
        return bool(self.availability().get("rpaFramework"))

    def _probe_availability_in_child(self, target: Path) -> Dict[str, Dict[str, Any]]:
        try:
            completed = run_windowless(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    _RPA_AVAILABILITY_CHILD_LAUNCHER,
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=_RPA_AVAILABILITY_PROBE_TIMEOUT_SECONDS,
                cwd=str(self._engine_root()),
                env=self._robot_child_environment(target),
            )
        except Exception as error:
            raise RuntimeError(f"rpa_availability_probe_failed:{type(error).__name__}") from error
        if completed.returncode != 0:
            raise RuntimeError("rpa_availability_probe_failed:nonzero_exit")
        marker = "V8OS_RPA_AVAILABILITY="
        payload_line = next(
            (line[len(marker):] for line in reversed((completed.stdout or "").splitlines()) if line.startswith(marker)),
            None,
        )
        if payload_line is None:
            raise RuntimeError("rpa_availability_probe_failed:missing_payload")
        payload = json.loads(payload_line)
        if not isinstance(payload, dict):
            raise RuntimeError("rpa_availability_probe_failed:invalid_payload")
        return {str(name): dict(detail or {}) for name, detail in payload.items()}

    def availability(self) -> Dict[str, Any]:
        if self._availability_cache is not None:
            if self._availability_cache_expires_at is None or time.monotonic() < self._availability_cache_expires_at:
                return deepcopy(self._availability_cache)
            self._availability_cache = None
            self._availability_cache_expires_at = None
        target = self._rpa_target_dir()
        if target is None:
            unavailable = {
                "detected": False,
                "importable": False,
                "origin": None,
                "error": "rpa_feature_pack_not_ready",
            }
            library_names = (
                "RPA.Windows",
                "RPA.Browser.Selenium",
                "RPA.Excel.Files",
            )
            payload = {
                "robotFramework": False,
                "robotFrameworkDetail": unavailable,
                "rpaFramework": False,
                "rpaFrameworkDetail": unavailable,
                "libraries": {name: False for name in library_names},
                "libraryDetails": {name: unavailable for name in library_names},
            }
            self._availability_cache = payload
            self._availability_cache_expires_at = None
            return deepcopy(payload)
        probe_failed = False
        try:
            details = self._probe_availability_in_child(target)
        except Exception as error:
            probe_failed = True
            unavailable = {
                "detected": False,
                "importable": False,
                "origin": None,
                "error": str(error),
            }
            details = {
                name: unavailable
                for name in ("robot", "RPA", "RPA.Windows", "RPA.Browser.Selenium", "RPA.Excel.Files")
            }
        robot_detail = details.get("robot") or {}
        rpa_detail = details.get("RPA") or {}
        library_details = {
            name: details.get(name) or {}
            for name in ("RPA.Windows", "RPA.Browser.Selenium", "RPA.Excel.Files")
        }
        payload = {
            "robotFramework": bool(robot_detail.get("importable")),
            "robotFrameworkDetail": robot_detail,
            "rpaFramework": bool(rpa_detail.get("importable")),
            "rpaFrameworkDetail": rpa_detail,
            "libraries": {
                name: bool(detail.get("importable"))
                for name, detail in library_details.items()
            },
            "libraryDetails": library_details,
        }
        self._availability_cache = payload
        self._availability_cache_expires_at = (
            time.monotonic() + _RPA_AVAILABILITY_FAILURE_RETRY_SECONDS
            if probe_failed
            else None
        )
        return deepcopy(payload)

    def _safe_name(self, value: str, fallback: str = "rpa_workflow") -> str:
        normalized = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(value or "").strip())
        normalized = normalized.strip("._")
        return normalized or fallback

    def _robot_var(self, value: str) -> str:
        return "${" + str(value).strip() + "}"

    def _convert_value(self, value: Any) -> str:
        if isinstance(value, str):
            match = re.fullmatch(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}", value.strip())
            if match:
                return self._robot_var(match.group(1))
            return value
        if isinstance(value, bool):
            return "True" if value else "False"
        if value is None:
            return ""
        if isinstance(value, (int, float)):
            return str(value)
        return json.dumps(value, ensure_ascii=False)

    def _keyword_name(self, use: str) -> str:
        return keyword_name_for_use(use)

    def _pipe_row(self, cells: List[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    def _library_available(self, library_name: str) -> bool:
        if library_name == "runtimes.rpa.robot_keywords.V8ChatRPAKeywords":
            return True
        return bool((self.availability().get("libraries") or {}).get(library_name))

    def _robot_quote(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if re.search(r"\s", text):
            return f"\"{text}\""
        return text

    def _robot_control_type(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        return text if text.endswith("Control") else f"{text}Control"

    def _windows_selector_locator(self, selector: Dict[str, Any]) -> str:
        parts: List[str] = []
        selector_key = str(selector.get("selectorKey") or "").strip()
        if selector_key:
            parts.append(f"selector:{selector_key}")
        if selector.get("name"):
            parts.append(f"name:{self._robot_quote(selector['name'])}")
        if selector.get("automationId"):
            parts.append(f"id:{self._robot_quote(selector['automationId'])}")
        if selector.get("controlType"):
            parts.append(f"type:{self._robot_quote(self._robot_control_type(selector['controlType']))}")
        if selector.get("className"):
            parts.append(f"class:{self._robot_quote(selector['className'])}")
        if selector.get("handle") not in (None, ""):
            parts.append(f"handle:{selector['handle']}")
        return " and ".join(part for part in parts if part)

    def _windows_window_locator(self, window: Dict[str, Any]) -> str:
        parts: List[str] = []
        if window.get("processName"):
            parts.append(f"executable:{self._robot_quote(window['processName'])}")
        if window.get("title"):
            parts.append(f"subname:{self._robot_quote(window['title'])}")
        parts.append("type:WindowControl")
        if window.get("className"):
            parts.append(f"class:{self._robot_quote(window['className'])}")
        if window.get("windowHandle") not in (None, ""):
            parts.append(f"handle:{window['windowHandle']}")
        return " and ".join(part for part in parts if part)

    def _combined_windows_locator(self, step: Dict[str, Any]) -> str:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        window = dict(target.get("window") or {})
        selector_locator = self._windows_selector_locator(selector)
        window_locator = self._windows_window_locator(window)
        if window_locator and selector_locator:
            if selector_locator.startswith("selector:"):
                return selector_locator
            return f"{window_locator} > {selector_locator}"
        return selector_locator or window_locator

    def _browser_selector_locator(self, step: Dict[str, Any]) -> str:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = dict(selector.get("metadata") or {}) if isinstance(selector.get("metadata"), dict) else {}
        for key in ("browserLocator", "locator", "css", "xpath"):
            value = metadata.get(key)
            if value not in (None, ""):
                if key == "css":
                    return f"css:{value}"
                if key == "xpath":
                    return f"xpath:{value}"
                return str(value).strip()
        selector_key = str(selector.get("selectorKey") or "").strip()
        if re.match(r"^(css|xpath|id|name|alias|class|link|partial link):", selector_key, re.IGNORECASE):
            return selector_key
        automation_id = str(selector.get("automationId") or "").strip()
        if automation_id:
            return f"id:{automation_id}"
        name = str(selector.get("name") or "").strip()
        if name:
            escaped = name.replace('"', '\\"')
            return f'xpath://*[contains(normalize-space(.), "{escaped}")]'
        return ""

    def _browser_url_for_step(self, step: Dict[str, Any]) -> str:
        params = dict(step.get("params") or {})
        for key in ("url", "target_url", "page_url", "href"):
            value = params.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return "about:blank"

    def _browser_open_arguments(self, *, app_id: str, step: Dict[str, Any]) -> List[str]:
        params = dict(step.get("params") or {})
        browser_hint = params.get("browserKind") or params.get("browser_kind") or app_id
        browser_kind = normalize_agent_browser_kind(str(browser_hint or "auto"))
        if browser_kind == "auto":
            browser_kind = str(discover_system_agent_browser().get("browserKind") or "chrome")
        browser_selection = "Edge" if browser_kind == "edge" else "Chrome"
        profile_dir = configured_agent_browser_profile_dir(browser_kind)
        return [
            self._browser_url_for_step(step),
            "use_profile=True",
            f"profile_path={profile_dir}",
            f"browser_selection={browser_selection}",
            "maximized=True",
        ]

    def _profile_augmented_step(self, app_id: str, step: Dict[str, Any], *, action_name: str | None = None) -> Dict[str, Any]:
        if not app_id:
            return dict(step)
        profile = self.app_profiles.get(app_id)
        if profile is None:
            return dict(step)
        target = dict(step.get("target") or {})
        next_target = dict(target)
        window = dict(target.get("window") or {})
        if not window:
            if profile.title_patterns:
                window["title"] = profile.title_patterns[0]
            if profile.class_names:
                window["className"] = profile.class_names[0]
            if profile.process_names:
                window["processName"] = profile.process_names[0]
            if window:
                window["profileAugmented"] = True
                next_target["window"] = window
        selector = dict(target.get("selector") or {})
        if selector:
            next_step = dict(step)
            next_step["target"] = next_target
            return next_step
        candidates = self.app_profiles.action_selector_keys_for(app_id, action_name)
        if not candidates:
            next_step = dict(step)
            next_step["target"] = next_target
            return next_step
        for selector_key in candidates:
            profile_selector = profile.selector_for(selector_key)
            if not profile_selector:
                profile_selector = profile.toolbar_selector_for(selector_key)
            if not profile_selector:
                continue
            profile_selector.setdefault("selectorKey", selector_key)
            metadata = dict(profile_selector.get("metadata") or {})
            metadata["profileAugmented"] = True
            profile_selector["metadata"] = metadata
            next_target["selector"] = profile_selector
            next_step = dict(step)
            next_step["target"] = next_target
            return next_step
        next_step = dict(step)
        next_step["target"] = next_target
        return next_step

    def _is_password_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        if selector_key and any(token in selector_key for token in ("password", "passwd", "pwd", "密码")):
            return True
        name = str(selector.get("name") or "").strip().lower()
        return bool(name and any(token in name for token in ("password", "passwd", "pwd", "密码")))

    def _selector_metadata(self, step: Dict[str, Any]) -> Dict[str, Any]:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        return dict(selector.get("metadata") or {}) if isinstance(selector.get("metadata"), dict) else {}

    def _browser_select_value(self, params: Dict[str, Any]) -> str:
        for key in ("value", "selected_value", "option_value", "selected", "selection", "text"):
            value = params.get(key)
            if isinstance(value, list) and value:
                return str(value[0]).strip()
            if value not in (None, ""):
                return str(value).strip()
        return ""

    def _is_browser_select_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = self._selector_metadata(step)
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        control_type = str(selector.get("controlType") or metadata.get("controlType") or "").strip().lower()
        name = str(selector.get("name") or "").strip().lower()
        role = str(metadata.get("role") or metadata.get("ariaRole") or "").strip().lower()
        tag_name = str(metadata.get("browserTagName") or metadata.get("tagName") or "").strip().lower()
        if control_type in {"combobox", "list", "listitem"}:
            return True
        if role in {"combobox", "listbox"}:
            return True
        if tag_name == "select":
            return True
        return any(
            token in value
            for value in (selector_key, name)
            for token in ("select", "dropdown", "combobox", "listbox", "country", "province", "option", "选项", "地区", "国家")
            if value
        )

    def _is_browser_checkbox_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = self._selector_metadata(step)
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        control_type = str(selector.get("controlType") or metadata.get("controlType") or "").strip().lower()
        name = str(selector.get("name") or "").strip().lower()
        role = str(metadata.get("role") or metadata.get("ariaRole") or "").strip().lower()
        tag_name = str(metadata.get("browserTagName") or metadata.get("tagName") or "").strip().lower()
        input_type = str(metadata.get("inputType") or "").strip().lower()
        if control_type in {"checkbox", "checkbutton"}:
            return True
        if role == "checkbox":
            return True
        if tag_name == "input" and input_type == "checkbox":
            return True
        tokens = ("checkbox", "agree", "consent", "terms", "accept", "remember", "subscribe", "勾选", "同意", "协议", "条款")
        return any(token in value for value in (selector_key, name) for token in tokens if value)

    def _is_browser_file_input_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = self._selector_metadata(step)
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        name = str(selector.get("name") or "").strip().lower()
        tag_name = str(metadata.get("browserTagName") or metadata.get("tagName") or "").strip().lower()
        input_type = str(metadata.get("inputType") or "").strip().lower()
        if tag_name == "input" and input_type == "file":
            return True
        tokens = ("file", "upload", "附件", "上传", "choose_file")
        return any(token in value for value in (selector_key, name) for token in tokens if value)

    def _is_browser_button_like(self, step: Dict[str, Any], params: Dict[str, Any]) -> bool:
        target = dict(step.get("target") or {})
        selector = dict(target.get("selector") or {})
        metadata = self._selector_metadata(step)
        selector_key = str(selector.get("selectorKey") or params.get("selector_key") or "").strip().lower()
        control_type = str(selector.get("controlType") or metadata.get("controlType") or "").strip().lower()
        name = str(selector.get("name") or "").strip().lower()
        role = str(metadata.get("role") or metadata.get("ariaRole") or "").strip().lower()
        tag_name = str(metadata.get("browserTagName") or metadata.get("tagName") or "").strip().lower()
        if control_type in {"button", "hyperlink"}:
            return True
        if role in {"button", "link"}:
            return True
        if tag_name in {"button", "a"}:
            return True
        tokens = ("button", "btn", "play", "submit", "confirm", "pay", "send", "播放", "提交", "确认", "支付", "发送")
        return any(token in value for value in (selector_key, name) for token in tokens if value)

    def _launch_text_for_app(self, script: Dict[str, Any], step: Dict[str, Any]) -> str:
        params = dict(step.get("params") or {})
        for key in ("command", "launch_command", "launchCommand", "app_name", "appName"):
            value = params.get(key)
            if isinstance(value, list) and value:
                return str(value[0]).strip()
            if value not in (None, ""):
                return str(value).strip()
        app_id = str(script.get("appId") or "").strip()
        launch_command = self.app_profiles.launch_command_for(app_id)
        if launch_command:
            return str(launch_command[0]).strip()
        return app_id

    def _derived_step_robot_semantic(self, script: Dict[str, Any], step: Dict[str, Any]) -> Dict[str, Any]:
        app_id = str(script.get("appId") or "").strip()
        raw_use = self._step_use(step)
        use = {
            "browser_click": "click_toolbar_action",
            "browser_type": "find_and_type",
            "browser_assert": "wait_for_element",
            "screenshot": "capture_screenshot",
            "scroll": "scroll_list",
            "type_text": "find_and_type",
        }.get(raw_use, raw_use)
        params = dict(step.get("params") or {})
        action_name = str(params.get("action_name") or params.get("toolbar_action_name") or "").strip().lower()
        step = self._profile_augmented_step(app_id, step, action_name=action_name)
        if app_id in {"browser_checkout", "browser", "chrome", "edge"}:
            browser_locator = self._browser_selector_locator(step)
            if use == "open_app":
                return {
                    "library": "RPA.Browser.Selenium",
                    "keyword": "Open Available Browser",
                    "arguments": self._browser_open_arguments(app_id=app_id, step=step),
                    "fallbackKeyword": "Open App",
                    "locator": "",
                    "notes": ["兼容旧 draft 的浏览器 native 语义；使用 Agent 专用浏览器 profile。"],
                }
            select_value = self._browser_select_value(params)
            if use == "find_and_type" and browser_locator and select_value:
                if self._is_browser_file_input_like(step, params):
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Choose File",
                        "arguments": [browser_locator, select_value],
                        "fallbackKeyword": "Find And Type",
                        "locator": browser_locator,
                        "notes": ["兼容旧 draft 的浏览器文件上传 native 语义。"],
                    }
                if self._is_browser_select_like(step, params):
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Select From List By Value",
                        "arguments": [browser_locator, select_value],
                        "fallbackKeyword": "Find And Type",
                        "locator": browser_locator,
                        "notes": ["兼容旧 draft 的浏览器下拉选择 native 语义。"],
                    }
                keyword = "Input Password" if self._is_password_like(step, params) else "Input Text When Element Is Visible"
                return {
                    "library": "RPA.Browser.Selenium",
                    "keyword": keyword,
                    "arguments": [browser_locator, select_value],
                    "fallbackKeyword": "Find And Type",
                    "locator": browser_locator,
                    "notes": ["兼容旧 draft 的浏览器输入 native 语义。"],
                }
            if use == "focus_window":
                window_title = str(((step.get("target") or {}).get("window") or {}).get("title") or "").strip()
                if window_title:
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Switch Window",
                        "arguments": [window_title],
                        "fallbackKeyword": "Focus Window",
                        "locator": "",
                        "notes": ["兼容旧 draft 的浏览器窗口切换 native 语义。"],
                    }
            if use == "click_toolbar_action" and browser_locator:
                action_name = str(params.get("action_name") or "").strip().lower()
                if action_name == "refresh":
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Reload Page",
                        "arguments": [],
                        "fallbackKeyword": "Click Toolbar Action",
                        "locator": "",
                        "notes": ["兼容旧 draft 的浏览器刷新 native 语义。"],
                    }
                if action_name == "back":
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Go Back",
                        "arguments": [],
                        "fallbackKeyword": "Click Toolbar Action",
                        "locator": "",
                        "notes": ["兼容旧 draft 的浏览器返回 native 语义。"],
                    }
                if action_name == "forward":
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Execute Javascript",
                        "arguments": ["window.history.forward();"],
                        "fallbackKeyword": "Click Toolbar Action",
                        "locator": "",
                        "notes": ["兼容旧 draft 的浏览器前进语义，当前改用 history.forward()。"],
                    }
                if self._is_browser_checkbox_like(step, params):
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Select Checkbox",
                        "arguments": [browser_locator],
                        "fallbackKeyword": "Click Toolbar Action",
                        "locator": browser_locator,
                        "notes": ["兼容旧 draft 的浏览器复选框勾选 native 语义。"],
                    }
                if action_name not in {"confirm", "pay", "submit"} and self._is_browser_button_like(step, params):
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Click Button",
                        "arguments": [browser_locator],
                        "fallbackKeyword": "Click Toolbar Action",
                        "locator": browser_locator,
                        "notes": ["兼容旧 draft 的浏览器按钮点击 native 语义。"],
                    }
                keyword = "Click Element When Clickable" if action_name in {"confirm", "pay", "submit"} else "Click Element When Visible"
                return {
                    "library": "RPA.Browser.Selenium",
                    "keyword": keyword,
                    "arguments": [browser_locator],
                    "fallbackKeyword": "Click Toolbar Action",
                    "locator": browser_locator,
                    "notes": ["兼容旧 draft 的浏览器点击 native 语义。"],
                }
            if use == "double_click" and browser_locator:
                return {
                    "library": "RPA.Browser.Selenium",
                    "keyword": "Double Click Element",
                    "arguments": [browser_locator],
                    "fallbackKeyword": "Double Click",
                    "locator": browser_locator,
                    "notes": ["兼容旧 draft 的浏览器双击 native 语义。"],
                }
            if use == "hotkey" and params.get("sequence") not in (None, ""):
                return {
                    "library": "RPA.Browser.Selenium",
                    "keyword": "Press Keys",
                    "arguments": ["None", params.get("sequence")],
                    "fallbackKeyword": "Hotkey",
                    "locator": "",
                    "notes": ["兼容旧 draft 的浏览器快捷键 native 语义。"],
                }
            if use == "scroll_list":
                amount = int(params.get("amount") or params.get("delta") or 600)
                if browser_locator:
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Scroll Element Into View",
                        "arguments": [browser_locator],
                        "fallbackKeyword": "Scroll List",
                        "locator": browser_locator,
                        "notes": ["兼容旧 draft 的浏览器滚动 native 语义。"],
                    }
                return {
                    "library": "RPA.Browser.Selenium",
                    "keyword": "Execute Javascript",
                    "arguments": [f"window.scrollBy(0, {amount});"],
                    "fallbackKeyword": "Scroll List",
                    "locator": "",
                    "notes": ["兼容旧 draft 的浏览器滚动脚本语义。"],
                }
            if use == "wait_for_element" and browser_locator:
                return {
                    "library": "RPA.Browser.Selenium",
                    "keyword": "Wait Until Element Is Visible",
                    "arguments": [browser_locator],
                    "fallbackKeyword": "Wait For Element",
                    "locator": browser_locator,
                    "notes": ["兼容旧 draft 的浏览器等待 native 语义。"],
                }
            if use == "capture_screenshot":
                if browser_locator:
                    return {
                        "library": "RPA.Browser.Selenium",
                        "keyword": "Capture Element Screenshot",
                        "arguments": [browser_locator, "${OUTPUT DIR}${/}step_capture.png"],
                        "fallbackKeyword": "Capture Screenshot",
                        "locator": browser_locator,
                        "notes": ["兼容旧 draft 的浏览器元素截图 native 语义。"],
                    }
                return {
                    "library": "RPA.Browser.Selenium",
                    "keyword": "Capture Page Screenshot",
                    "arguments": ["${OUTPUT DIR}${/}step_capture.png"],
                    "fallbackKeyword": "Capture Screenshot",
                    "locator": "",
                    "notes": ["兼容旧 draft 的浏览器页面截图 native 语义。"],
                }
            return {}
        if not (
            params.get("prefer_native_windows_semantics") is True
            or params.get("use_native_windows_semantics") is True
        ):
            return {}
        locator = self._combined_windows_locator(step)
        if use == "open_app":
            launch_text = self._launch_text_for_app(script, step)
            if launch_text:
                return {
                    "library": "RPA.Windows",
                    "keyword": "Windows Run",
                    "arguments": [launch_text],
                    "fallbackKeyword": "Open App",
                    "locator": "",
                    "notes": ["兼容旧 draft 的默认 native 语义。"],
                }
        if use == "focus_window" and locator:
            return {
                "library": "RPA.Windows",
                "keyword": "Control Window",
                "arguments": [locator],
                "fallbackKeyword": "Focus Window",
                "locator": locator,
                "notes": ["兼容旧 draft 的默认 native 语义。"],
            }
        if use == "find_and_type" and locator and params.get("text") not in (None, ""):
            return {
                "library": "RPA.Windows",
                "keyword": "Set Value",
                "arguments": [locator, params.get("text")],
                "fallbackKeyword": "Find And Type",
                "locator": locator,
                "notes": ["兼容旧 draft 的默认 native 语义。"],
            }
        if use == "click_toolbar_action" and locator:
            return {
                "library": "RPA.Windows",
                "keyword": "Click",
                "arguments": [locator],
                "fallbackKeyword": "Click Toolbar Action",
                "locator": locator,
                "notes": ["兼容旧 draft 的默认 native 语义。"],
            }
        if use == "double_click" and locator:
            return {
                "library": "RPA.Windows",
                "keyword": "Double Click",
                "arguments": [locator],
                "fallbackKeyword": "Double Click",
                "locator": locator,
                "notes": ["兼容旧 draft 的桌面双击 native 语义。"],
            }
        if use == "hotkey" and params.get("sequence") not in (None, ""):
            return {
                "library": "RPA.Windows",
                "keyword": "Send Keys",
                "arguments": [params.get("sequence")],
                "fallbackKeyword": "Hotkey",
                "locator": "",
                "notes": ["兼容旧 draft 的热键 native 语义。"],
            }
        if use == "scroll_list":
            amount = int(params.get("amount") or params.get("delta") or 1)
            repeat = max(1, min(abs(amount), 5))
            key = "{PGDN}" if amount >= 0 else "{PGUP}"
            return {
                "library": "RPA.Windows",
                "keyword": "Send Keys",
                "arguments": [key * repeat],
                "fallbackKeyword": "Scroll List",
                "locator": locator,
                "notes": ["兼容旧 draft 的分页滚动 native 语义。"],
            }
        return {}

    def _step_robot_semantic(self, script: Dict[str, Any], step: Dict[str, Any]) -> Dict[str, Any]:
        semantic = step.get("robot") if isinstance(step.get("robot"), dict) else {}
        if not isinstance(semantic, dict):
            return {}
        normalized = {
            "library": str(semantic.get("library") or "").strip(),
            "keyword": str(semantic.get("keyword") or "").strip(),
            "arguments": list(semantic.get("arguments") or []),
            "fallbackKeyword": str(semantic.get("fallbackKeyword") or "").strip(),
            "locator": str(semantic.get("locator") or "").strip(),
            "notes": [str(item).strip() for item in list(semantic.get("notes") or []) if str(item).strip()],
        }
        if normalized["library"] and normalized["keyword"]:
            return normalized
        return self._derived_step_robot_semantic(script, step)

    def _custom_keyword_row(self, step: Dict[str, Any], *, indent: int = 1) -> List[str]:
        params = dict(step.get("params") or {})
        args = [f"{key}={self._convert_value(value)}" for key, value in params.items() if value not in (None, "")]
        return [""] * indent + [self._keyword_name(self._step_use(step)), *args]

    def _native_keyword_row(self, step: Dict[str, Any], semantic: Dict[str, Any], *, indent: int = 1) -> List[str]:
        arguments = [self._convert_value(value) for value in list(semantic.get("arguments") or [])]
        return [""] * indent + [str(semantic.get("keyword") or "").strip(), *arguments]

    def _step_key(self, step: Dict[str, Any], index: int) -> str:
        return str(step.get("stepId") or step.get("id") or step.get("key") or f"step:{index}").strip()

    def _step_use(self, step: Dict[str, Any]) -> str:
        return str(step.get("use") or step.get("action") or "").strip().lower()

    def _step_params(self, step: Dict[str, Any]) -> Dict[str, Any]:
        return dict(step.get("params") or {}) if isinstance(step.get("params"), dict) else {}

    def _step_body_indices(self, steps: List[Dict[str, Any]], step: Dict[str, Any]) -> List[int]:
        params = self._step_params(step)
        keys = [self._step_key(item, index) for index, item in enumerate(steps)]
        current_index = next((index for index, candidate in enumerate(steps) if candidate is step), -1)
        current_key = keys[current_index] if current_index >= 0 else ""
        explicit_keys = [
            str(item).strip()
            for item in list(params.get("bodyStepKeys") or params.get("thenStepKeys") or [])
            if str(item).strip()
        ]
        if explicit_keys:
            return [index for index, key in enumerate(keys) if key in explicit_keys and key != current_key]
        start_key = str(params.get("loopStartStepKey") or params.get("startStepKey") or "").strip()
        end_key = str(params.get("loopEndStepKey") or params.get("endStepKey") or "").strip()
        if not start_key or not end_key or start_key not in keys or end_key not in keys:
            return []
        start = keys.index(start_key)
        end = keys.index(end_key)
        if start > end:
            start, end = end, start
        return [index for index in range(start, end + 1) if keys[index] != current_key]

    def _control_body_skip_indices(self, steps: List[Dict[str, Any]]) -> set[int]:
        skip: set[int] = set()
        for step in steps:
            use = self._step_use(step)
            if use not in {"loop", "if", "try_catch"}:
                continue
            skip.update(self._step_body_indices(steps, step))
        return skip

    def _append_regular_step_rows(
        self,
        lines: List[str],
        *,
        script: Dict[str, Any],
        step: Dict[str, Any],
        indent: int = 1,
    ) -> None:
        approval = step.get("approval") if isinstance(step.get("approval"), dict) else None
        assessment = step.get("assessment") if isinstance(step.get("assessment"), dict) else {}
        semantic = self._step_robot_semantic(script, step)
        step_id = str(step.get("stepId") or "step").strip() or "step"
        lines.append(
            self._pipe_row(
                [""] * indent
                + [
                    "Comment",
                    f"STEP {step_id} · use={step.get('use') or step.get('action')} · intent={step.get('intent') or step.get('use') or step.get('action')}",
                ]
            )
        )
        lines.append(self._pipe_row([""] * indent + ["Log To Console", f"STEP_ID:{step_id}"]))
        if assessment:
            lines.append(
                self._pipe_row(
                    [""] * indent
                    + [
                        "Comment",
                        f"STEP CONFIDENCE: {assessment.get('score')} · {assessment.get('status')}",
                    ]
                )
            )
            if assessment.get("band"):
                lines.append(self._pipe_row([""] * indent + ["Comment", f"STEP BAND: {assessment.get('band')}"]))
            for reason in list(assessment.get("reasons") or [])[:3]:
                lines.append(self._pipe_row([""] * indent + ["Comment", f"STEP REVIEW: {reason}"]))
        if approval:
            reason = str(approval.get("reason") or approval.get("mode") or "需要审批")
            lines.append(self._pipe_row([""] * indent + ["Comment", f"APPROVAL REQUIRED: {reason}"]))
        if semantic.get("locator"):
            lines.append(self._pipe_row([""] * indent + ["Comment", f"ROBOT LOCATOR: {semantic['locator']}"]))
        for note in list(semantic.get("notes") or []):
            lines.append(self._pipe_row([""] * indent + ["Comment", f"ROBOT NOTE: {note}"]))

        native_library = str(semantic.get("library") or "").strip()
        native_keyword = str(semantic.get("keyword") or "").strip()
        if native_library and native_keyword and self._library_available(native_library):
            lines.append(self._pipe_row([""] * indent + ["Comment", f"ROBOT NATIVE: {native_library} -> {native_keyword}"]))
            lines.append(self._pipe_row(self._native_keyword_row(step, semantic, indent=indent)))
        else:
            if native_library and native_keyword:
                lines.append(
                    self._pipe_row(
                        [""] * indent
                        + [
                            "Comment",
                            f"ROBOT NATIVE UNAVAILABLE: {native_library} -> {native_keyword}，已回退到 v8chat bridge keyword",
                        ]
                    )
                )
            lines.append(self._pipe_row(self._custom_keyword_row(step, indent=indent)))

    def _append_control_step_rows(
        self,
        lines: List[str],
        *,
        script: Dict[str, Any],
        steps: List[Dict[str, Any]],
        step: Dict[str, Any],
        indent: int = 1,
    ) -> bool:
        use = self._step_use(step)
        params = self._step_params(step)
        if use == "comment":
            text = str(params.get("text") or step.get("intent") or "RPA comment").strip()
            lines.append(self._pipe_row([""] * indent + ["Comment", text]))
            return True
        if use == "if":
            condition = self._convert_value(params.get("condition") or params.get("expression") or "${TRUE}")
            lines.append(self._pipe_row([""] * indent + ["IF", condition]))
            body = self._step_body_indices(steps, step)
            if body:
                for index in body:
                    self._append_step_rows(lines, script=script, steps=steps, step=steps[index], indent=indent + 1)
            else:
                lines.append(self._pipe_row([""] * (indent + 1) + ["Comment", "IF body is empty."]))
            lines.append(self._pipe_row([""] * indent + ["END"]))
            return True
        if use == "loop":
            raw_count = params.get("count") or params.get("times") or 1
            try:
                count = max(1, int(raw_count))
            except Exception:
                count = 1
            lines.append(self._pipe_row([""] * indent + ["FOR", "${rpa_loop_index}", "IN RANGE", str(count)]))
            body = self._step_body_indices(steps, step)
            if body:
                for index in body:
                    self._append_step_rows(lines, script=script, steps=steps, step=steps[index], indent=indent + 1)
            else:
                lines.append(self._pipe_row([""] * (indent + 1) + ["Comment", "LOOP body is empty."]))
            lines.append(self._pipe_row([""] * indent + ["END"]))
            return True
        if use == "try_catch":
            lines.append(self._pipe_row([""] * indent + ["TRY"]))
            body = self._step_body_indices(steps, step)
            if body:
                for index in body:
                    self._append_step_rows(lines, script=script, steps=steps, step=steps[index], indent=indent + 1)
            else:
                lines.append(self._pipe_row([""] * (indent + 1) + ["Comment", "TRY body is empty."]))
            lines.append(self._pipe_row([""] * indent + ["EXCEPT", "AS", "${rpa_error}"]))
            lines.append(self._pipe_row([""] * (indent + 1) + ["Log To Console", "RPA_EXCEPTION:${rpa_error}"]))
            lines.append(self._pipe_row([""] * indent + ["END"]))
            return True
        return False

    def _append_step_rows(
        self,
        lines: List[str],
        *,
        script: Dict[str, Any],
        steps: List[Dict[str, Any]],
        step: Dict[str, Any],
        indent: int = 1,
    ) -> None:
        if self._append_control_step_rows(lines, script=script, steps=steps, step=step, indent=indent):
            return
        self._append_regular_step_rows(lines, script=script, step=step, indent=indent)

    def _default_robot_options(self, script: Dict[str, Any]) -> Dict[str, Any]:
        app_id = str(script.get("appId") or "desktop")
        step_uses = {self._step_use(item) for item in list(script.get("steps") or []) if isinstance(item, dict)}
        has_high_risk = any(
            isinstance(item, dict) and isinstance(item.get("approval"), dict)
            for item in list(script.get("steps") or [])
        )
        assessment = script.get("assessment") if isinstance(script.get("assessment"), dict) else {}
        libraries: List[Dict[str, Any]] = [
            {
                "name": "runtimes.rpa.robot_keywords.V8ChatRPAKeywords",
                "required": True,
                "purpose": "v8chat ComputerUse / RPA bridge keywords",
            }
        ]
        if app_id in {"browser_checkout", "browser", "chrome", "edge"}:
            libraries.append(
                {
                    "name": "RPA.Browser.Selenium",
                    "required": False,
                    "purpose": "browser-native keywords for future exporter upgrades",
                }
            )
        tags = ["v8chat", "rpa-draft", f"app:{app_id}"]
        if has_high_risk:
            tags.append("risk:high")
        trust_status = str(assessment.get("status") or "").strip().lower()
        if trust_status:
            tags.append(f"trust:{trust_status}")
        if trust_status in {"review_required", "fallback_heavy"}:
            tags.append("review:required")
        return {
            "tags": tags,
            "libraries": libraries,
            "metadata": {
                "App Id": app_id,
                "Goal": script.get("goal") or script.get("name") or script.get("id"),
                "Source Trace Run": (script.get("source") or {}).get("traceRunId") if isinstance(script.get("source"), dict) else None,
                "Source Trace Session": (script.get("source") or {}).get("traceSessionId") if isinstance(script.get("source"), dict) else None,
                "Generated By": "v8chat RobotFrameworkAdapter",
                "Trust Status": trust_status or None,
            },
        }

    def render_script(self, script: Dict[str, Any]) -> str:
        script_id = str(script.get("id") or "rpa.workflow")
        script_name = str(script.get("name") or script_id)
        app_id = str(script.get("appId") or "desktop")
        goal = str(script.get("goal") or script_name)
        default_robot_options = self._default_robot_options(script)
        raw_robot_options = script.get("robot") if isinstance(script.get("robot"), dict) else {}
        robot_options = {
            "tags": list(raw_robot_options.get("tags") or default_robot_options.get("tags") or []),
            "libraries": list(raw_robot_options.get("libraries") or default_robot_options.get("libraries") or []),
            "metadata": dict(raw_robot_options.get("metadata") or default_robot_options.get("metadata") or {}),
            "taskSetup": raw_robot_options.get("taskSetup") or default_robot_options.get("taskSetup"),
            "taskTeardown": raw_robot_options.get("taskTeardown") or default_robot_options.get("taskTeardown"),
        }
        lines: List[str] = [
            "*** Settings ***",
            self._pipe_row(["Documentation", f"Generated from v8chat draft {script_id}"]),
        ]
        libraries = list(robot_options.get("libraries") or [])
        if not libraries:
            libraries = [{"name": "runtimes.rpa.robot_keywords.V8ChatRPAKeywords", "required": True}]
        for library in libraries:
            if not isinstance(library, dict):
                continue
            library_name = str(library.get("name") or "").strip()
            if not library_name:
                continue
            alias = str(library.get("alias") or "").strip()
            purpose = str(library.get("purpose") or "").strip()
            if self._library_available(library_name):
                row = ["Library", library_name]
                if alias:
                    row.extend(["WITH NAME", alias])
                lines.append(self._pipe_row(row))
            else:
                prefix = "REQUIRED LIBRARY MISSING" if bool(library.get("required")) else "OPTIONAL LIBRARY UNAVAILABLE"
                message = f"{prefix}: {library_name}"
                if purpose:
                    message = f"{message} ({purpose})"
                lines.append(self._pipe_row(["Metadata", "Missing Library", message]))
        for key, value in dict(robot_options.get("metadata") or {}).items():
            if value in (None, ""):
                continue
            lines.append(self._pipe_row(["Metadata", str(key), self._convert_value(value)]))

        lines.extend([
            "",
            "*** Variables ***",
            self._pipe_row(["${APP_ID}", app_id]),
            self._pipe_row(["${GOAL}", goal]),
        ])
        for variable in list(script.get("variables") or []):
            name = str(variable.get("name") or "").strip()
            if not name:
                continue
            example = variable.get("exampleValue")
            default_value = "__REQUIRED__" if example in (None, "") else self._convert_value(example)
            lines.append(self._pipe_row([self._robot_var(name), default_value]))

        lines.extend(["", "*** Tasks ***", self._pipe_row([script_name])])
        tags = [str(item).strip() for item in list(robot_options.get("tags") or []) if str(item).strip()]
        if tags:
            lines.append(self._pipe_row(["", "[Tags]", *tags]))
        assessment = script.get("assessment") if isinstance(script.get("assessment"), dict) else {}
        if assessment:
            score = assessment.get("score")
            status = assessment.get("status")
            if score not in (None, ""):
                lines.append(self._pipe_row(["", "Comment", f"SCRIPT CONFIDENCE: {score} · {status or 'unknown'}"]))
            if assessment.get("band"):
                lines.append(self._pipe_row(["", "Comment", f"SCRIPT BAND: {assessment.get('band')}"]))
            signals = assessment.get("signals") if isinstance(assessment.get("signals"), dict) else {}
            if signals:
                accepted_ratio = signals.get("acceptedRatio")
                native_ratio = signals.get("nativeSemanticRatio")
                if accepted_ratio not in (None, "") or native_ratio not in (None, ""):
                    lines.append(
                        self._pipe_row(
                            [
                                "",
                                "Comment",
                                f"SCRIPT SIGNALS: accepted_ratio={accepted_ratio} · native_ratio={native_ratio}",
                            ]
                        )
                    )
            for reason in list(assessment.get("reasons") or [])[:4]:
                lines.append(self._pipe_row(["", "Comment", f"SCRIPT REVIEW: {reason}"]))
        task_setup = str(robot_options.get("taskSetup") or "").strip()
        if task_setup:
            lines.append(self._pipe_row(["", "[Setup]", task_setup]))
        steps = [step for step in list(script.get("steps") or []) if isinstance(step, dict)]
        skip_indices = self._control_body_skip_indices(steps)
        for index, step in enumerate(steps):
            if index in skip_indices:
                continue
            self._append_step_rows(lines, script=script, steps=steps, step=step)
        task_teardown = str(robot_options.get("taskTeardown") or "").strip()
        if task_teardown:
            lines.append(self._pipe_row(["", "[Teardown]", task_teardown]))
        lines.append("")
        return "\n".join(lines)

    def _export_contract_issues(self, script: Dict[str, Any]) -> List[str]:
        issues: List[str] = []
        step_uses: List[str] = []
        for index, step in enumerate(list(script.get("steps") or []), start=1):
            if not isinstance(step, dict):
                continue
            use = self._step_use(step)
            step_uses.append(use)
            semantic = self._step_robot_semantic(script, step)
            native_library = str(semantic.get("library") or "").strip()
            native_keyword = str(semantic.get("keyword") or "").strip()
            if native_library and native_keyword and self._library_available(native_library):
                continue
            if not is_supported_bridge_use(use):
                issues.append(f"STEP {index} use={use or '<empty>'} 缺少 bridge keyword 契约。")
        issues.extend(bridge_keyword_issues(step_uses))
        return list(dict.fromkeys(issue for issue in issues if issue))

    def _sync_draft_export_metadata(
        self,
        *,
        script_id: str,
        robot_path: str | None,
        exportability: str,
        dry_run_passed: bool,
        dry_run_error: str | None = None,
        compile_issues: Optional[List[str]] = None,
        dry_run_output_dir: str | None = None,
    ) -> None:
        draft = self.script_store.get_draft(script_id)
        if not draft:
            return
        metadata = dict(draft.get("metadata") or {})
        metadata["exportability"] = exportability
        metadata["dryRunPassed"] = bool(dry_run_passed)
        metadata["dryRunError"] = str(dry_run_error or "").strip() or None
        metadata["compileIssues"] = [str(item) for item in list(compile_issues or []) if str(item).strip()]
        metadata["robotFilePath"] = robot_path
        metadata["dryRunOutputDir"] = dry_run_output_dir
        draft["metadata"] = metadata
        self.script_store.save_draft(draft)

    def validate_robot_file(
        self,
        *,
        robot_file: str | Path,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: Path | None = None,
    ) -> Dict[str, Any]:
        robot_path = Path(robot_file)
        dry_run_output_dir = Path(output_dir) if output_dir is not None else robot_path.with_name(f"{robot_path.stem}_dryrun")
        if not self.is_available():
            return {
                "passed": False,
                "command": [],
                "stdout": "",
                "stderr": "当前环境未安装 Robot Framework，无法执行 dry-run 校验。",
                "error": "当前环境未安装 Robot Framework，无法执行 dry-run 校验。",
                "outputDir": str(dry_run_output_dir),
            }
        command = self.build_command(
            robot_file=robot_path,
            variables=variables,
            output_dir=dry_run_output_dir,
            dry_run=True,
        )
        target = self._require_rpa_target_dir()
        completed = run_windowless(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(Path(__file__).resolve().parents[2]),
            env=self._robot_child_environment(target),
        )
        error = None
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "Robot dry-run failed").strip()
        return {
            "passed": completed.returncode == 0,
            "command": list(command),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "error": error,
            "outputDir": str(dry_run_output_dir),
        }

    def export_script(
        self,
        *,
        script: Dict[str, Any],
        output_dir: Path | None = None,
    ) -> Dict[str, Any]:
        target_dir = Path(output_dir) if output_dir is not None else self.script_store.script_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        script_id = str(script.get("id") or "rpa.workflow")
        path = target_dir / f"{self._safe_name(script_id)}.robot"
        contract_issues = self._export_contract_issues(script)
        if contract_issues:
            self._sync_draft_export_metadata(
                script_id=script_id,
                robot_path=str(path),
                exportability="contract_failed",
                dry_run_passed=False,
                dry_run_error="; ".join(contract_issues),
                compile_issues=contract_issues,
            )
            raise ValueError("RPA 导出失败：存在未覆盖的 bridge keyword。\n" + "\n".join(contract_issues))
        content = self.render_script(script)
        path.write_text(content, encoding="utf-8")
        validation = self.validate_robot_file(robot_file=path, output_dir=target_dir / f"{path.stem}_dryrun")
        if not validation.get("passed"):
            self._sync_draft_export_metadata(
                script_id=script_id,
                robot_path=str(path),
                exportability="dry_run_failed",
                dry_run_passed=False,
                dry_run_error=str(validation.get("error") or "").strip() or "Robot dry-run failed",
                compile_issues=[],
                dry_run_output_dir=str(validation.get("outputDir") or ""),
            )
            raise ValueError(f"RPA 导出失败：{validation.get('error') or 'Robot dry-run failed'}")
        self._sync_draft_export_metadata(
            script_id=script_id,
            robot_path=str(path),
            exportability="dry_run_passed",
            dry_run_passed=True,
            compile_issues=[],
            dry_run_output_dir=str(validation.get("outputDir") or ""),
        )
        return {
            "path": str(path),
            "scriptId": script_id,
            "taskName": str(script.get("name") or script_id),
            "content": content,
            "dryRunPassed": True,
            "dryRunError": None,
            "dryRunOutputDir": str(validation.get("outputDir") or ""),
        }

    def build_command(
        self,
        *,
        robot_file: str | Path,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: Path | None = None,
        dry_run: bool = False,
    ) -> List[str]:
        target_output_dir = Path(output_dir) if output_dir is not None else Path(robot_file).with_suffix("")
        target = self._require_rpa_target_dir()
        command: List[str] = [
            sys.executable,
            "-I",
            "-c",
            _ROBOT_CHILD_LAUNCHER,
            str(target),
            str(self._engine_root()),
            "--consolecolors",
            "off",
            "--outputdir",
            str(target_output_dir),
        ]
        if dry_run:
            command.append("--dryrun")
        for key, value in dict(variables or {}).items():
            command.extend(["--variable", f"{key}:{self._convert_value(value)}"])
        command.append(str(robot_file))
        return command

    def prepare_draft_run(
        self,
        *,
        script_id: str,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: Path | None = None,
    ) -> Dict[str, Any]:
        draft = self.script_store.get_draft(script_id)
        if not draft:
            raise ValueError(f"未找到 draft: {script_id}")
        exported = self.export_script(script=draft, output_dir=output_dir)
        command = self.build_command(
            robot_file=exported["path"],
            variables=variables,
            output_dir=output_dir,
        )
        return {
            "available": self.availability(),
            "script": draft,
            "export": exported,
            "command": command,
        }

    def prepare_existing_run(
        self,
        *,
        robot_file: str | Path,
        variables: Optional[Dict[str, Any]] = None,
        output_dir: Path | None = None,
    ) -> Dict[str, Any]:
        robot_path = Path(robot_file)
        if not robot_path.exists():
            raise ValueError(f"未找到 robot 文件: {robot_path}")
        with tempfile.TemporaryDirectory(prefix="v8os-rpa-existing-dryrun-") as temporary:
            validation = self.validate_robot_file(
                robot_file=robot_path,
                variables=variables,
                output_dir=Path(temporary),
            )
        if not validation.get("passed"):
            raise ValueError(
                "RPA 流程依赖校验失败。受管能力包支持 Browser.Selenium、Excel.Files"
                "，Windows 另支持 RPA.Windows；请移除或另行治理未包含的 RPA 库。"
            )
        command = self.build_command(robot_file=robot_path, variables=variables, output_dir=output_dir)
        return {
            "available": self.availability(),
            "robotFile": str(robot_path),
            "command": command,
        }

    def run_command(
        self,
        *,
        command: List[str],
        timeout_ms: int = 600000,
        cwd: str | None = None,
    ) -> Dict[str, Any]:
        if not self.is_available():
            raise RuntimeError("当前环境未安装 Robot Framework，无法执行 .robot 流程。")
        target = self._require_rpa_target_dir()
        completed = run_windowless(
            command,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_ms / 1000)),
            cwd=cwd or str(self._engine_root()),
            env=self._robot_child_environment(target),
        )
        return {
            "returncode": int(completed.returncode),
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "command": list(command),
        }


robot_framework_adapter = RobotFrameworkAdapter()
