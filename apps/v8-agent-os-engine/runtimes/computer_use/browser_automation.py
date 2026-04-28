from __future__ import annotations

import atexit
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import requests

from runtimes.computer_use.input_policy import looks_like_url


_CHROMIUM_PROCESS_NAMES = {
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "vivaldi.exe",
    "opera.exe",
    "chromium.exe",
}
_ELECTRON_PROCESS_NAMES = {"electron.exe"}
_WEBVIEW_HINTS = ("webview2", "webview", "msedgewebview2")
_DEFAULT_TARGET_PORT = 9222
_DEFAULT_PROXY_PORT = 3456
_DEFAULT_WINDOW_SIZE = "1600,1000"
_GENERIC_BROWSER_APP_IDS = {"browser_checkout", "browser", "chromium"}
_CHROME_APP_IDS = {"chrome", "google_chrome", "google-chrome"}
_EDGE_APP_IDS = {"edge", "msedge", "microsoft_edge", "microsoft-edge"}
_BROWSER_KIND_PROCESS_NAMES = {
    "chrome": ["chrome.exe", "chromium.exe"],
    "edge": ["msedge.exe"],
    "chromium": ["chromium.exe", "chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe"],
}
_PERSISTENT_PROFILE_BLOCKED_ARGS = {
    "--guest",
    "--incognito",
    "--inprivate",
}
_DEDICATED_PROFILE_MODE = "dedicated_debug_profile"
_DEFAULT_PROFILE_ROOT = Path.home() / ".v8-agent-os" / "browser-profiles" / "computer_use"


def _basename(value: str | None) -> str:
    token = str(value or "").strip().replace("\\", "/").split("/")[-1]
    return token.lower()


def _normalized_tokens(values: Iterable[Any]) -> List[str]:
    normalized: List[str] = []
    for item in values:
        token = str(item or "").strip()
        if token:
            normalized.append(token)
    return normalized


def _normalize_browser_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _is_packaged_electron_executable(value: Any) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    path = Path(raw)
    if not path.exists() or not path.is_file():
        return False
    sibling_roots = [
        path.parent / "resources" / "app.asar",
        path.parent / "resources" / "app" / "package.json",
    ]
    if any(candidate.exists() for candidate in sibling_roots):
        license_file = path.parent / "LICENSE.electron.txt"
        if license_file.exists():
            return True
        executable_name = path.name.lower()
        if executable_name.endswith(".exe") and executable_name not in {"chrome.exe", "msedge.exe", "chromium.exe", "brave.exe", "opera.exe", "vivaldi.exe"}:
            return True
    return False


@dataclass(slots=True)
class BrowserLaneDecision:
    enabled: bool
    available: bool
    family: str | None = None
    reason: str | None = None
    route: str = "browser_automation"
    provider: str = "engine_managed_cdp"
    target_port: int | None = None
    managed_launch: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "family": self.family,
            "reason": self.reason,
            "route": self.route,
            "provider": self.provider,
            "targetPort": self.target_port,
            "managedLaunch": self.managed_launch,
        }


class BrowserAutomationProvider:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._proxy_process: subprocess.Popen[str] | None = None
        self._proxy_port: int = _DEFAULT_PROXY_PORT
        self._target_port: int = _DEFAULT_TARGET_PORT
        self._connect_timeout_ms: int = 3000
        self._enabled: bool = False
        self._mode: str = "auto_if_available"
        self._provider_id: str = "engine_managed_cdp"
        self._allow_managed_launch: bool = True
        self._profile_mode: str = _DEDICATED_PROFILE_MODE
        self._user_data_dir: Path | None = None
        self._target_families: List[str] = ["chromium", "electron", "webview2"]
        self._managed_launches: Dict[str, Dict[str, Any]] = {}
        self._node_path: str | None = shutil.which("node")
        self._playwright_probe_cache: Dict[str, Any] | None = None
        atexit.register(self.shutdown)

    def configure(self, config: Dict[str, Any] | None) -> None:
        payload = dict(config or {})
        lane = dict(payload.get("browserLane") or {})
        self._enabled = bool(lane.get("enabled", False))
        self._mode = str(lane.get("mode") or "auto_if_available").strip().lower() or "auto_if_available"
        self._provider_id = str(lane.get("provider") or "engine_managed_cdp").strip() or "engine_managed_cdp"
        self._proxy_port = int(lane.get("proxyPort") or _DEFAULT_PROXY_PORT)
        self._connect_timeout_ms = int(lane.get("connectTimeoutMs") or 3000)
        self._allow_managed_launch = bool(lane.get("allowManagedLaunch", True))
        self._profile_mode = (
            str(lane.get("profileMode") or _DEDICATED_PROFILE_MODE).strip().lower()
            or _DEDICATED_PROFILE_MODE
        )
        raw_user_data_dir = str(
            lane.get("userDataDir")
            or lane.get("debugUserDataDir")
            or ""
        ).strip()
        self._user_data_dir = Path(raw_user_data_dir).expanduser() if raw_user_data_dir else None
        self._target_families = [
            str(item).strip().lower()
            for item in list(lane.get("targetFamilies") or ["chromium", "electron", "webview2"])
            if str(item).strip()
        ]
        self._target_port = max(_DEFAULT_TARGET_PORT, self._proxy_port + 100)

    def availability_summary(self) -> Dict[str, Any]:
        connected = False
        health: Dict[str, Any] = {}
        try:
            health = dict(self._health() or {})
            connected = bool(health.get("connected"))
        except Exception:
            connected = False
        helper_script = self._helper_script_path()
        helper_exists = helper_script.exists()
        playwright_probe = self._probe_playwright_dependency()
        return {
            "enabled": self._enabled,
            "mode": self._mode,
            "provider": self._provider_id,
            "proxyPort": self._proxy_port,
            "targetPort": self._target_port,
            "connectTimeoutMs": self._connect_timeout_ms,
            "targetFamilies": list(self._target_families),
            "profileMode": self._profile_mode,
            "profileRoot": str(self._profile_root()),
            "defaultUserDataDir": str(self._dedicated_user_data_dir("chrome")),
            "nodeAvailable": bool(self._node_path),
            "helperScriptPath": str(helper_script),
            "helperScriptExists": bool(helper_exists),
            "playwrightAvailable": bool(playwright_probe.get("available")),
            "playwrightProbe": playwright_probe,
            "helperHealth": health,
            "connected": connected,
            "managedLaunchCount": len(self._managed_launches),
        }

    def lane_capabilities(self) -> Dict[str, Any]:
        helper_script = self._helper_script_path()
        helper_exists = helper_script.exists()
        playwright_probe = self._probe_playwright_dependency()
        implemented = bool(self._node_path and helper_exists)
        available = bool(self._enabled and self._node_path and helper_exists and playwright_probe.get("available"))
        return {
            "browserLaneImplemented": implemented,
            "supportsBrowserAutomation": available,
            "browserLaneAvailable": available,
            "browserLaneProvider": self._provider_id if implemented else None,
            "browserLaneEnabled": bool(self._enabled),
            "nodeAvailable": bool(self._node_path),
            "helperScriptPath": str(helper_script),
            "helperScriptExists": bool(helper_exists),
            "playwrightAvailable": bool(playwright_probe.get("available")),
            "playwrightProbe": playwright_probe,
            "profileMode": self._profile_mode,
            "profileRoot": str(self._profile_root()),
        }

    def _probe_playwright_dependency(self) -> Dict[str, Any]:
        if self._playwright_probe_cache is not None:
            return dict(self._playwright_probe_cache)
        if not self._node_path:
            self._playwright_probe_cache = {"available": False, "reason": "node_unavailable"}
            return dict(self._playwright_probe_cache)
        script_path = self._helper_script_path()
        driver_package = self._resolve_playwright_driver_package()
        probe_script = (
            "const p=process.env.PLAYWRIGHT_DRIVER_PACKAGE;"
            "if(p){require(p); console.log('ok:python-driver');}"
            "else{try{require.resolve('playwright'); console.log('ok:playwright');}"
            "catch(e){require.resolve('playwright-core'); console.log('ok:playwright-core');}}"
        )
        env = os.environ.copy()
        if driver_package:
            env["PLAYWRIGHT_DRIVER_PACKAGE"] = str(driver_package)
        try:
            completed = subprocess.run(
                [self._node_path, "-e", probe_script],
                cwd=str(script_path.parent),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3,
            )
            available = completed.returncode == 0
            self._playwright_probe_cache = {
                "available": available,
                "reason": "ok" if available else "playwright_module_missing",
                "source": "python_driver_package" if driver_package else "node_module",
                "driverPackage": str(driver_package) if driver_package else None,
                "stdout": (completed.stdout or "").strip()[-120:],
                "stderr": (completed.stderr or "").strip()[-300:],
            }
        except Exception as exc:
            self._playwright_probe_cache = {
                "available": False,
                "reason": "playwright_probe_failed",
                "error": str(exc),
            }
        return dict(self._playwright_probe_cache)

    def _resolve_playwright_driver_package(self) -> Path | None:
        for module_name in ("playwright", "patchright"):
            try:
                spec = importlib.util.find_spec(module_name)
            except Exception:
                spec = None
            if not spec or not spec.origin:
                continue
            package_dir = Path(spec.origin).parent / "driver" / "package"
            if (package_dir / "package.json").exists() and (package_dir / "index.js").exists():
                return package_dir
        return None

    def _resolve_executable_command(self, command: List[str]) -> List[str] | None:
        normalized = [str(item or "").strip() for item in list(command or []) if str(item or "").strip()]
        if not normalized:
            return None
        executable = normalized[0]
        executable_path = Path(executable)
        if executable_path.exists():
            return [str(executable_path), *normalized[1:]]
        resolved = shutil.which(executable)
        if resolved:
            return [str(resolved), *normalized[1:]]
        return None

    def _profile_root(self) -> Path:
        return self._user_data_dir or _DEFAULT_PROFILE_ROOT

    def _browser_kind_from_command(self, command: List[str] | str | None, *, app_id: str | None = None) -> str:
        requested = _normalize_browser_key(app_id)
        if requested in _EDGE_APP_IDS:
            return "edge"
        if requested in _CHROME_APP_IDS:
            return "chrome"
        executable = ""
        if isinstance(command, list) and command:
            executable = command[0]
        elif isinstance(command, str):
            executable = command
        name = _basename(executable)
        if "msedge" in name or name == "edge":
            return "edge"
        if "chrome" in name or "chromium" in name:
            return "chrome"
        return "chromium"

    def _dedicated_user_data_dir(self, browser_kind: str) -> Path:
        normalized = str(browser_kind or "chromium").strip().lower() or "chromium"
        if self._user_data_dir and self._user_data_dir.name.lower() in {"chrome", "edge", "chromium"}:
            return self._user_data_dir
        return self._profile_root() / normalized

    def _is_profile_arg(self, value: str) -> bool:
        lowered = str(value or "").strip().lower()
        return lowered.startswith("--user-data-dir=") or lowered.startswith("--profile-directory=")

    def _platform_browser_candidates(self, family: str) -> List[List[str]]:
        system = platform.system().lower()
        if system == "windows":
            if family == "chrome":
                return [
                    [r"C:\Program Files\Google\Chrome\Application\chrome.exe"],
                    [r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
                    ["chrome.exe"],
                ]
            if family == "edge":
                return [
                    [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"],
                    [r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"],
                    ["msedge.exe"],
                ]
        if system == "darwin":
            if family == "chrome":
                return [[r"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]]
            if family == "edge":
                return [[r"/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]]
        if family == "chrome":
            return [["google-chrome"], ["google-chrome-stable"], ["chromium-browser"], ["chromium"]]
        if family == "edge":
            return [["microsoft-edge"], ["microsoft-edge-stable"]]
        return []

    def _preferred_browser_kinds(self, *, app_id: str | None = None, app_name: str | None = None) -> List[str]:
        requested = {
            _normalize_browser_key(app_id),
            _normalize_browser_key(app_name),
        }
        if requested & _CHROME_APP_IDS:
            return ["chrome", "chromium", "edge"]
        if requested & _EDGE_APP_IDS:
            return ["edge", "chrome", "chromium"]
        return ["chrome", "edge", "chromium"]

    def _devtools_active_port_files(self, browser_kind: str) -> List[Path]:
        home = Path.home()
        local_app_data = Path(os.environ.get("LOCALAPPDATA") or "")
        system = platform.system().lower()
        if system == "windows":
            mapping = {
                "chrome": [local_app_data / "Google/Chrome/User Data/DevToolsActivePort"],
                "edge": [local_app_data / "Microsoft/Edge/User Data/DevToolsActivePort"],
                "chromium": [local_app_data / "Chromium/User Data/DevToolsActivePort"],
            }
            return [path for path in mapping.get(browser_kind, []) if str(path)]
        if system == "darwin":
            mapping = {
                "chrome": [home / "Library/Application Support/Google/Chrome/DevToolsActivePort"],
                "edge": [home / "Library/Application Support/Microsoft Edge/DevToolsActivePort"],
                "chromium": [home / "Library/Application Support/Chromium/DevToolsActivePort"],
            }
            return mapping.get(browser_kind, [])
        mapping = {
            "chrome": [home / ".config/google-chrome/DevToolsActivePort"],
            "edge": [home / ".config/microsoft-edge/DevToolsActivePort"],
            "chromium": [home / ".config/chromium/DevToolsActivePort"],
        }
        return mapping.get(browser_kind, [])

    def _discover_existing_debug_port(self, *, app_id: str | None = None, app_name: str | None = None) -> int | None:
        for browser_kind in self._preferred_browser_kinds(app_id=app_id, app_name=app_name):
            for file_path in self._devtools_active_port_files(browser_kind):
                try:
                    lines = file_path.read_text(encoding="utf-8").strip().splitlines()
                except Exception:
                    continue
                if not lines:
                    continue
                try:
                    port = int(str(lines[0]).strip())
                except Exception:
                    continue
                if port > 0 and self._is_debug_port_reachable(port):
                    return port
        for port in [9222, 9229, 9333, self._target_port]:
            if port and self._is_debug_port_reachable(port):
                return int(port)
        return None

    def resolve_preferred_launch_command(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        launch_command: List[str] | str | None = None,
    ) -> List[str] | str | None:
        requested = {
            _normalize_browser_key(app_id),
            _normalize_browser_key(app_name),
        }
        preferred_families: List[str] = []
        if requested & _CHROME_APP_IDS:
            preferred_families = ["chrome"]
        elif requested & _EDGE_APP_IDS:
            preferred_families = ["edge"]
        elif requested & _GENERIC_BROWSER_APP_IDS:
            if launch_command:
                return launch_command
            preferred_families = ["chrome", "edge"]
        if not preferred_families:
            return launch_command

        tail: List[str] = []
        if isinstance(launch_command, list):
            tail = [str(item or "").strip() for item in list(launch_command[1:]) if str(item or "").strip()]
        for family in preferred_families:
            for candidate in self._platform_browser_candidates(family):
                resolved = self._resolve_executable_command(candidate)
                if resolved:
                    return [*resolved, *tail]
        return launch_command

    def preferred_window_process_names(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
    ) -> List[str]:
        requested = {
            _normalize_browser_key(app_id),
            _normalize_browser_key(app_name),
        }
        if requested & _CHROME_APP_IDS:
            return ["chrome.exe", "chromium.exe"]
        elif requested & _EDGE_APP_IDS:
            return ["msedge.exe"]
        else:
            browser_kinds = self._preferred_browser_kinds(app_id=app_id, app_name=app_name)
        ordered: List[str] = []
        seen: set[str] = set()
        for browser_kind in browser_kinds:
            for process_name in _BROWSER_KIND_PROCESS_NAMES.get(browser_kind, []):
                normalized = str(process_name or "").strip().lower()
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)
                ordered.append(normalized)
        return ordered

    def infer_family(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        class_name: str | None = None,
        process_name: str | None = None,
        launch_command: List[str] | str | None = None,
        window_title: str | None = None,
    ) -> str | None:
        tokens = {
            _basename(process_name),
            _basename(app_name),
            _basename(app_id),
            _basename(window_title),
        }
        if isinstance(launch_command, list) and launch_command:
            tokens.add(_basename(launch_command[0]))
        if isinstance(launch_command, str):
            tokens.add(_basename(launch_command))
        launch_executable = None
        if isinstance(launch_command, list) and launch_command:
            launch_executable = launch_command[0]
        elif isinstance(launch_command, str):
            launch_executable = launch_command
        class_token = str(class_name or "").strip().lower()
        if any(token in _CHROMIUM_PROCESS_NAMES for token in tokens):
            return "chromium"
        if any(token in _ELECTRON_PROCESS_NAMES for token in tokens):
            return "electron"
        if _is_packaged_electron_executable(launch_executable):
            return "electron"
        if any(hint in class_token for hint in _WEBVIEW_HINTS):
            return "webview2"
        for token in tokens:
            if token in {"chrome", "edge", "browser", "chromium"}:
                return "chromium"
            if token == "electron":
                return "electron"
            if any(hint in token for hint in _WEBVIEW_HINTS):
                return "webview2"
        return None

    def prepare_launch(
        self,
        *,
        app_id: str | None,
        launch_command: List[str] | str,
        environment: Dict[str, str] | None = None,
    ) -> Tuple[List[str] | str, Dict[str, str] | None, Dict[str, Any] | None]:
        if not self._enabled or not self._allow_managed_launch:
            return launch_command, environment, None
        family = self.infer_family(app_id=app_id, launch_command=launch_command)
        if family not in {"chromium", "electron", "webview2"}:
            return launch_command, environment, None
        debug_port = self._target_port
        updated_env = dict(environment or os.environ.copy())
        updated_command: List[str] | str = launch_command
        removed_args: List[str] = []
        injected_args: List[str] = []
        profile_dir: Path | None = None
        if family in {"chromium", "electron"} and isinstance(launch_command, list):
            browser_kind = self._browser_kind_from_command(launch_command, app_id=app_id)
            sanitized_command: List[str] = []
            for index, raw_arg in enumerate(launch_command):
                arg = str(raw_arg or "").strip()
                if not arg:
                    continue
                if index == 0:
                    sanitized_command.append(arg)
                    continue
                lowered_arg = arg.lower()
                if lowered_arg in _PERSISTENT_PROFILE_BLOCKED_ARGS or self._is_profile_arg(arg):
                    removed_args.append(arg)
                    continue
                sanitized_command.append(arg)
            updated_command = sanitized_command
            debug_flag = f"--remote-debugging-port={debug_port}"
            if debug_flag not in updated_command:
                updated_command = [*updated_command, debug_flag]
                injected_args.append(debug_flag)
            if family == "chromium":
                if "--start-maximized" not in updated_command:
                    updated_command = [*updated_command, "--start-maximized"]
                    injected_args.append("--start-maximized")
                if not any(str(arg).startswith("--window-size=") for arg in updated_command):
                    size_arg = f"--window-size={_DEFAULT_WINDOW_SIZE}"
                    updated_command = [*updated_command, size_arg]
                    injected_args.append(size_arg)
            if family == "chromium" and self._profile_mode == _DEDICATED_PROFILE_MODE:
                profile_dir = self._dedicated_user_data_dir(browser_kind)
                profile_dir.mkdir(parents=True, exist_ok=True)
                profile_arg = f"--user-data-dir={profile_dir}"
                updated_command = [*updated_command, profile_arg]
                injected_args.append(profile_arg)
        elif family == "webview2":
            existing = str(updated_env.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS") or "").strip()
            injection = f"--remote-debugging-port={debug_port}"
            if injection not in existing:
                updated_env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = f"{existing} {injection}".strip()
                injected_args.append(injection)
        else:
            return launch_command, environment, None
        metadata = {
            "browserTargetFamily": family,
            "browserLaneProvider": self._provider_id,
            "browserTargetPort": debug_port,
            "managedLaunch": True,
            "profilePersistenceMode": self._profile_mode if profile_dir else "managed_launch_debuggable",
            "browserUserDataDir": str(profile_dir) if profile_dir else None,
            "sanitizedLaunchArgsRemoved": list(removed_args),
            "browserLaunchArgsInjected": list(injected_args),
        }
        self._managed_launches[str(app_id or "").strip().lower() or family] = {
            "family": family,
            "targetPort": debug_port,
            "profilePersistenceMode": metadata["profilePersistenceMode"],
            "browserUserDataDir": metadata.get("browserUserDataDir"),
            "launchedAt": time.time(),
        }
        return updated_command, updated_env, metadata

    def _start_managed_chromium_debug_browser(
        self,
        *,
        app_id: str | None,
        app_name: str | None,
    ) -> BrowserLaneDecision | None:
        if not self._allow_managed_launch:
            return None
        app_key = str(app_id or "browser_checkout").strip().lower() or "browser_checkout"
        launch_command = self.resolve_preferred_launch_command(
            app_id=app_key,
            app_name=app_name or "browser",
            launch_command=None,
        )
        if not isinstance(launch_command, list) or not launch_command:
            return None
        try:
            prepared_command, prepared_env, metadata = self.prepare_launch(
                app_id=app_key,
                launch_command=launch_command,
                environment=None,
            )
            if not isinstance(prepared_command, list):
                return None
            subprocess.Popen(
                prepared_command,
                env=prepared_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            target_port = int((metadata or {}).get("browserTargetPort") or self._target_port)
            deadline = time.time() + max(5.0, self._connect_timeout_ms / 1000.0)
            while time.time() < deadline:
                if self._is_debug_port_reachable(target_port):
                    return BrowserLaneDecision(
                        enabled=True,
                        available=True,
                        family="chromium",
                        reason="managed_debug_browser_started",
                        target_port=target_port,
                        managed_launch=True,
                    )
                time.sleep(0.2)
        except Exception:
            self._managed_launches.pop(app_key, None)
            return None
        self._managed_launches.pop(app_key, None)
        return None

    def decide_lane(
        self,
        *,
        action_type: str,
        action_payload: Dict[str, Any],
        app_id: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        process_name: str | None = None,
    ) -> BrowserLaneDecision:
        if not self._enabled:
            return BrowserLaneDecision(enabled=False, available=False, reason="browser_lane_disabled")
        if not self._node_path:
            return BrowserLaneDecision(enabled=True, available=False, reason="node_unavailable")
        if not self._helper_script_path().exists():
            return BrowserLaneDecision(enabled=True, available=False, reason="helper_script_missing")
        if not self._probe_playwright_dependency().get("available"):
            return BrowserLaneDecision(enabled=True, available=False, reason="playwright_module_missing")
        family = self.infer_family(
            app_id=app_id or action_payload.get("app_id"),
            app_name=action_payload.get("app_name") or action_payload.get("app"),
            class_name=class_name or action_payload.get("class_name"),
            process_name=process_name,
            window_title=window_title or action_payload.get("window_title"),
        )
        if family not in self._target_families:
            return BrowserLaneDecision(enabled=True, available=False, family=family, reason="family_not_targeted")
        managed_state = self._managed_launches.get(str(app_id or "").strip().lower())
        requested_app_name = str(action_payload.get("app_name") or action_payload.get("app") or "").strip() or None
        if family in {"electron", "webview2"} and managed_state is None:
            return BrowserLaneDecision(
                enabled=True,
                available=False,
                family=family,
                reason="managed_debug_port_unavailable",
            )
        if family == "chromium" and managed_state is None:
            discovered_port = self._discover_existing_debug_port(
                app_id=app_id or action_payload.get("app_id") or action_payload.get("resolved_app_id"),
                app_name=requested_app_name,
            )
            if discovered_port:
                return BrowserLaneDecision(
                    enabled=True,
                    available=True,
                    family=family,
                    reason="attached_existing_debug_browser",
                    target_port=discovered_port,
                    managed_launch=False,
                )
            managed_decision = self._start_managed_chromium_debug_browser(
                app_id=app_id or action_payload.get("app_id") or action_payload.get("resolved_app_id"),
                app_name=requested_app_name,
            )
            if managed_decision is not None:
                return managed_decision
            return BrowserLaneDecision(
                enabled=True,
                available=False,
                family=family,
                reason="remote_debug_port_unreachable",
                target_port=self._target_port,
                managed_launch=False,
            )
        target_port = int((managed_state or {}).get("targetPort") or self._target_port)
        if not self._is_debug_port_reachable(target_port):
            return BrowserLaneDecision(
                enabled=True,
                available=False,
                family=family,
                reason=(
                    "managed_debug_port_unreachable"
                    if managed_state is not None
                    else "remote_debug_port_unreachable"
                ),
                target_port=target_port,
                managed_launch=bool(managed_state),
            )
        return BrowserLaneDecision(
            enabled=True,
            available=True,
            family=family,
            reason="auto_if_available",
            target_port=target_port,
            managed_launch=bool(managed_state),
        )

    def shutdown(self) -> None:
        with self._lock:
            proc = self._proxy_process
            self._proxy_process = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _helper_script_path(self) -> Path:
        return Path(__file__).resolve().parents[2] / "scripts" / "browser_cdp_proxy.mjs"

    def _proxy_base_url(self) -> str:
        return f"http://127.0.0.1:{self._proxy_port}"

    def _health(self) -> Dict[str, Any]:
        response = requests.get(f"{self._proxy_base_url()}/health", timeout=max(1.0, self._connect_timeout_ms / 1000.0))
        response.raise_for_status()
        return dict(response.json() or {})

    def _ensure_proxy(self, *, target_port: int | None = None) -> None:
        with self._lock:
            if self._proxy_process is not None and self._proxy_process.poll() is None:
                return
            script_path = self._helper_script_path()
            node_path = self._node_path
            if not node_path:
                raise RuntimeError("当前环境缺少 node，无法启用 browser automation lane。")
            if not script_path.exists():
                raise RuntimeError(f"browser automation helper 缺失：{script_path}")
            env = os.environ.copy()
            env["CDP_PROXY_PORT"] = str(self._proxy_port)
            if target_port:
                env["CDP_TARGET_PORT"] = str(target_port)
            driver_package = self._resolve_playwright_driver_package()
            if driver_package:
                env["PLAYWRIGHT_DRIVER_PACKAGE"] = str(driver_package)
            self._proxy_process = subprocess.Popen(
                [node_path, str(script_path)],
                cwd=str(script_path.parent),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        deadline = time.time() + (self._connect_timeout_ms / 1000.0)
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                self._health()
                return
            except Exception as exc:
                last_error = exc
                time.sleep(0.12)
        raise RuntimeError(f"browser automation helper 未能按时启动：{last_error}")

    def _is_debug_port_reachable(self, port: int | None) -> bool:
        if not port:
            return False
        try:
            response = requests.get(
                f"http://127.0.0.1:{int(port)}/json/version",
                timeout=min(1.0, max(0.3, self._connect_timeout_ms / 1000.0)),
            )
            response.raise_for_status()
            return True
        except Exception:
            return False

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] | None = None,
        body: str | Dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._proxy_base_url()}{path}"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        payload = None
        if isinstance(body, dict):
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            payload = body.encode("utf-8")
            headers["Content-Type"] = "text/plain; charset=utf-8"
        response = requests.request(
            method.upper(),
            url,
            params=params,
            data=payload,
            headers=headers,
            timeout=max(1.0, self._connect_timeout_ms / 1000.0) + 10.0,
        )
        response.raise_for_status()
        if not response.text:
            return {}
        return response.json()

    def _list_targets(self) -> List[Dict[str, Any]]:
        response = self._request_json("GET", "/targets")
        if isinstance(response, list):
            return [dict(item or {}) for item in response]
        targets = response.get("targets")
        if isinstance(targets, list):
            return [dict(item or {}) for item in targets]
        return []

    def _select_target_id(
        self,
        *,
        window_title: str | None = None,
        target_id: str | None = None,
        target_url: str | None = None,
    ) -> str:
        explicit_target_id = str(target_id or "").strip()
        if explicit_target_id:
            return explicit_target_id
        targets = self._list_targets()
        hint = str(window_title or "").strip().lower()
        url_hint = str(target_url or "").strip().lower()
        if url_hint:
            for item in targets:
                url = str(item.get("url") or "").strip().lower()
                if url and (url == url_hint or url_hint in url):
                    return str(item.get("targetId") or item.get("id") or "").strip()
        if hint:
            for item in targets:
                title = str(item.get("title") or "").strip().lower()
                if hint and hint in title:
                    return str(item.get("targetId") or item.get("id") or "").strip()
        preferred_targets = []
        for item in targets:
            url = str(item.get("url") or "").strip().lower()
            if url.startswith(("http://", "https://", "file://")):
                preferred_targets.append(item)
        for item in preferred_targets or targets:
            target_id = str(item.get("targetId") or item.get("id") or "").strip()
            if target_id:
                return target_id
        created = self._request_json("GET", "/new", params={"url": "about:blank"})
        target_id = str(created.get("targetId") or "").strip()
        if not target_id:
            raise RuntimeError("browser automation 未能创建可用 tab。")
        return target_id

    def open_tab(self, *, url: str, decision: BrowserLaneDecision) -> Dict[str, Any]:
        self._ensure_proxy(target_port=decision.target_port)
        created = self._request_json("GET", "/new", params={"url": url})
        target_id = str(created.get("targetId") or "").strip()
        if not target_id:
            raise RuntimeError("browser automation 未能创建目标页面。")
        try:
            self._request_json("POST", "/bringToFront", params={"target": target_id})
        except Exception:
            pass
        try:
            self._request_json("POST", "/maximize", params={"target": target_id})
        except Exception:
            pass
        return {
            "targetId": target_id,
            "family": decision.family,
            "provider": decision.provider,
            "targetPort": decision.target_port,
            "url": url,
        }

    def close_tab(
        self,
        *,
        target_id: str,
        decision: BrowserLaneDecision | None = None,
        target_port: int | None = None,
    ) -> Dict[str, Any]:
        normalized_target = str(target_id or "").strip()
        if not normalized_target:
            return {"closed": False, "reason": "missing_target_id"}
        self._ensure_proxy(target_port=target_port or (decision.target_port if decision else None))
        try:
            response = self._request_json("POST", "/close", params={"target": normalized_target})
            return dict(response or {})
        except Exception as exc:
            return {"closed": False, "targetId": normalized_target, "error": str(exc)}

    def _evaluate(self, *, target_id: str, expression: str) -> Dict[str, Any]:
        return self._request_json("POST", "/eval", params={"target": target_id}, body=expression)

    def bring_to_front(self, *, target_id: str, target_port: int | None = None) -> Dict[str, Any]:
        self._ensure_proxy(target_port=target_port)
        return dict(self._request_json("POST", "/bringToFront", params={"target": str(target_id or "")}) or {})

    def maximize_tab_window(self, *, target_id: str, target_port: int | None = None) -> Dict[str, Any]:
        self._ensure_proxy(target_port=target_port)
        return dict(self._request_json("POST", "/maximize", params={"target": str(target_id or "")}) or {})

    def _browser_input_script(self, *, text: str, payload: Dict[str, Any]) -> str:
        selector = str(
            payload.get("browser_selector")
            or payload.get("browserSelector")
            or payload.get("dom_selector")
            or payload.get("domSelector")
            or payload.get("css_selector")
            or payload.get("cssSelector")
            or ""
        ).strip()
        target_texts = _normalized_tokens(
            [
                payload.get("name"),
                payload.get("name_contains"),
                payload.get("target_text"),
                payload.get("selector_key"),
            ]
        )
        text_json = json.dumps(text, ensure_ascii=False)
        selector_json = json.dumps(selector, ensure_ascii=False)
        target_texts_json = json.dumps(target_texts, ensure_ascii=False)
        return (
            "(() => {\n"
            f"  const selector = {selector_json};\n"
            f"  const targetTexts = {target_texts_json};\n"
            "  const editable = (el) => !!el && (el.matches('input,textarea,select') || el.isContentEditable || ['textbox','searchbox','combobox'].includes((el.getAttribute('role')||'').toLowerCase()));\n"
            "  const bySelector = selector ? document.querySelector(selector) : null;\n"
            "  const normalizedText = (value) => String(value || '').trim().toLowerCase();\n"
            "  const candidates = Array.from(document.querySelectorAll('input, textarea, select, [contenteditable=\"true\"], [role=\"textbox\"], [role=\"searchbox\"], [role=\"combobox\"]'));\n"
            "  let target = editable(bySelector) ? bySelector : null;\n"
            "  if (!target && editable(document.activeElement)) target = document.activeElement;\n"
            "  if (!target && targetTexts.length) {\n"
            "    target = candidates.find((el) => {\n"
            "      const haystacks = [el.placeholder, el.name, el.id, el.getAttribute('aria-label'), el.getAttribute('title')].map(normalizedText);\n"
            "      return targetTexts.some((token) => haystacks.some((hay) => hay && hay.includes(normalizedText(token))));\n"
            "    }) || null;\n"
            "  }\n"
            "  if (!target && candidates.length) target = candidates[0];\n"
            "  if (!editable(target)) return { ok: false, error: '未找到可编辑 DOM 输入目标' };\n"
            "  target.focus();\n"
            f"  const nextValue = {text_json};\n"
            "  if (target.isContentEditable) target.textContent = nextValue;\n"
            "  else target.value = nextValue;\n"
            "  target.dispatchEvent(new Event('input', { bubbles: true }));\n"
            "  target.dispatchEvent(new Event('change', { bubbles: true }));\n"
            "  return { ok: true, tag: target.tagName, id: target.id || null, name: target.name || null, selector: selector || null };\n"
            "})()"
        )

    def _browser_click_script(self, *, payload: Dict[str, Any]) -> str:
        selector = str(
            payload.get("browser_selector")
            or payload.get("browserSelector")
            or payload.get("dom_selector")
            or payload.get("domSelector")
            or payload.get("css_selector")
            or payload.get("cssSelector")
            or ""
        ).strip()
        target_texts = _normalized_tokens(
            [
                payload.get("name"),
                payload.get("name_contains"),
                payload.get("target_text"),
                payload.get("selector_key"),
            ]
        )
        selector_json = json.dumps(selector, ensure_ascii=False)
        target_texts_json = json.dumps(target_texts, ensure_ascii=False)
        return (
            "(() => {\n"
            f"  const selector = {selector_json};\n"
            f"  const targetTexts = {target_texts_json};\n"
            "  const normalizedText = (value) => String(value || '').trim().toLowerCase();\n"
            "  const bySelector = selector ? document.querySelector(selector) : null;\n"
            "  const clickable = Array.from(document.querySelectorAll('button, a, [role=\"button\"], input[type=\"button\"], input[type=\"submit\"], input[type=\"checkbox\"], input[type=\"radio\"]'));\n"
            "  let target = bySelector;\n"
            "  if (!target && targetTexts.length) {\n"
            "    target = clickable.find((el) => {\n"
            "      const haystacks = [el.textContent, el.value, el.getAttribute('aria-label'), el.getAttribute('title'), el.id, el.name].map(normalizedText);\n"
            "      return targetTexts.some((token) => haystacks.some((hay) => hay && hay.includes(normalizedText(token))));\n"
            "    }) || null;\n"
            "  }\n"
            "  if (!target && document.activeElement && ['BUTTON','A'].includes(document.activeElement.tagName)) target = document.activeElement;\n"
            "  if (!target) return { ok: false, error: '未找到可点击 DOM 目标' };\n"
            "  target.scrollIntoView({ block: 'center', inline: 'center' });\n"
            "  const rect = target.getBoundingClientRect();\n"
            "  target.click();\n"
            "  return { ok: true, tag: target.tagName, text: (target.textContent || target.value || '').slice(0, 120), x: rect.x + rect.width/2, y: rect.y + rect.height/2 };\n"
            "})()"
        )

    def observe(
        self,
        *,
        window_title: str | None,
        decision: BrowserLaneDecision,
        target_id: str | None = None,
        target_url: str | None = None,
    ) -> Dict[str, Any]:
        self._ensure_proxy(target_port=decision.target_port)
        resolved_target_id = self._select_target_id(
            window_title=window_title,
            target_id=target_id,
            target_url=target_url,
        )
        info = self._request_json("GET", "/info", params={"target": resolved_target_id})
        return {
            "available": True,
            "targetId": resolved_target_id,
            "info": info,
            "family": decision.family,
            "provider": decision.provider,
        }

    def type_text(self, *, payload: Dict[str, Any], decision: BrowserLaneDecision, target_input_kind: str) -> Dict[str, Any]:
        self._ensure_proxy(target_port=decision.target_port)
        target_id = self._select_target_id(
            window_title=str(payload.get("window_title") or ""),
            target_id=payload.get("browser_target_id") or payload.get("browserTargetId"),
            target_url=payload.get("browser_target_url") or payload.get("browserTargetUrl"),
        )
        text = str(payload.get("text") or "")
        if target_input_kind == "browser_address_bar" or looks_like_url(text):
            self._request_json("GET", "/navigate", params={"target": target_id, "url": text})
            return {
                "title": payload.get("window_title"),
                "windowTitle": payload.get("window_title"),
                "windowHandle": payload.get("window_handle"),
                "role": "BrowserNavigation",
                "metadata": {
                    "route": "browser_automation",
                    "browserLaneProvider": decision.provider,
                    "browserTargetFamily": decision.family,
                    "browserTargetId": target_id,
                    "inputStrategy": "browser_navigate",
                    "targetInputKind": target_input_kind,
                },
            }
        evaluation = self._evaluate(
            target_id=target_id,
            expression=self._browser_input_script(text=text, payload=payload),
        )
        value = dict(evaluation.get("value") or {})
        if not value.get("ok"):
            raise RuntimeError(str(value.get("error") or "browser automation 输入失败。"))
        return {
            "title": payload.get("window_title"),
            "windowTitle": payload.get("window_title"),
            "windowHandle": payload.get("window_handle"),
            "role": "BrowserDomInput",
            "metadata": {
                "route": "browser_automation",
                "browserLaneProvider": decision.provider,
                "browserTargetFamily": decision.family,
                "browserTargetId": target_id,
                "inputStrategy": "browser_dom_eval",
                "targetInputKind": target_input_kind,
                "browserResult": value,
            },
        }

    def click_target(self, *, payload: Dict[str, Any], decision: BrowserLaneDecision) -> Dict[str, Any]:
        self._ensure_proxy(target_port=decision.target_port)
        target_id = self._select_target_id(
            window_title=str(payload.get("window_title") or ""),
            target_id=payload.get("browser_target_id") or payload.get("browserTargetId"),
            target_url=payload.get("browser_target_url") or payload.get("browserTargetUrl"),
        )
        evaluation = self._evaluate(
            target_id=target_id,
            expression=self._browser_click_script(payload=payload),
        )
        value = dict(evaluation.get("value") or {})
        if not value.get("ok"):
            raise RuntimeError(str(value.get("error") or "browser automation 点击失败。"))
        point = [int(value.get("x") or 0), int(value.get("y") or 0)]
        return {
            "title": payload.get("window_title"),
            "windowTitle": payload.get("window_title"),
            "windowHandle": payload.get("window_handle"),
            "role": "BrowserDomClick",
            "clickedPoint": point,
            "metadata": {
                "route": "browser_automation",
                "browserLaneProvider": decision.provider,
                "browserTargetFamily": decision.family,
                "browserTargetId": target_id,
                "browserResult": value,
            },
        }

    def scroll_view(self, *, payload: Dict[str, Any], decision: BrowserLaneDecision) -> Dict[str, Any]:
        self._ensure_proxy(target_port=decision.target_port)
        target_id = self._select_target_id(
            window_title=str(payload.get("window_title") or ""),
            target_id=payload.get("browser_target_id") or payload.get("browserTargetId"),
            target_url=payload.get("browser_target_url") or payload.get("browserTargetUrl"),
        )
        amount = int(payload.get("amount") or payload.get("y") or 1200)
        direction = str(payload.get("direction") or ("down" if amount >= 0 else "up")).strip().lower() or "down"
        response = self._request_json(
            "GET",
            "/scroll",
            params={"target": target_id, "y": abs(amount), "direction": direction},
        )
        return {
            "title": payload.get("window_title"),
            "windowTitle": payload.get("window_title"),
            "windowHandle": payload.get("window_handle"),
            "role": "BrowserViewport",
            "metadata": {
                "route": "browser_automation",
                "browserLaneProvider": decision.provider,
                "browserTargetFamily": decision.family,
                "browserTargetId": target_id,
                "browserResult": response,
            },
        }

    def set_files(self, *, payload: Dict[str, Any], decision: BrowserLaneDecision) -> Dict[str, Any]:
        self._ensure_proxy(target_port=decision.target_port)
        target_id = self._select_target_id(
            window_title=str(payload.get("window_title") or ""),
            target_id=payload.get("browser_target_id") or payload.get("browserTargetId"),
            target_url=payload.get("browser_target_url") or payload.get("browserTargetUrl"),
        )
        selector = str(
            payload.get("browser_selector")
            or payload.get("browserSelector")
            or payload.get("dom_selector")
            or payload.get("domSelector")
            or payload.get("css_selector")
            or payload.get("cssSelector")
            or ""
        ).strip()
        file_paths = _normalized_tokens(
            payload.get("file_paths")
            or payload.get("attachment_paths")
            or ([payload.get("file_path")] if payload.get("file_path") else [])
        )
        if not selector:
            raise RuntimeError("browser automation 文件上传当前要求显式提供 DOM/CSS selector。")
        response = self._request_json(
            "POST",
            "/setFiles",
            params={"target": target_id},
            body={"selector": selector, "files": file_paths},
        )
        return {
            "title": payload.get("window_title"),
            "windowTitle": payload.get("window_title"),
            "windowHandle": payload.get("window_handle"),
            "role": "BrowserFileInput",
            "metadata": {
                "route": "browser_automation",
                "browserLaneProvider": decision.provider,
                "browserTargetFamily": decision.family,
                "browserTargetId": target_id,
                "browserResult": response,
            },
        }
