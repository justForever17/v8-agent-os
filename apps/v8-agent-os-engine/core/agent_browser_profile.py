from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


AGENT_BROWSER_PROFILE_MODE = "dedicated_debug_profile"
AGENT_BROWSER_PROFILE_ROOT = Path.home() / ".v8-agent-os" / "browser-profiles" / "computer_use"
SUPPORTED_AGENT_BROWSER_KINDS = {"auto", "chrome", "edge", "chromium"}


def normalize_agent_browser_kind(value: Any = None) -> str:
    token = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if token in {"msedge", "microsoft_edge", "microsoft-edge"}:
        return "edge"
    if token in {"google_chrome", "google-chrome"}:
        return "chrome"
    if token in SUPPORTED_AGENT_BROWSER_KINDS:
        return token
    return "auto"


def default_agent_browser_profile_root() -> Path:
    return AGENT_BROWSER_PROFILE_ROOT


def preferred_system_agent_browser_kinds(system_name: str | None = None) -> list[str]:
    system = str(system_name or platform.system()).strip().lower()
    return ["edge", "chrome", "chromium"] if system == "windows" else ["chrome", "chromium", "edge"]


def system_agent_browser_candidates(browser_kind: str, system_name: str | None = None) -> list[str]:
    kind = normalize_agent_browser_kind(browser_kind)
    system = str(system_name or platform.system()).strip().lower()
    if system == "windows":
        program_files = Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files")
        program_files_x86 = Path(os.environ.get("PROGRAMFILES(X86)") or r"C:\Program Files (x86)")
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        local_root = Path(local_app_data) if local_app_data else None
        if kind == "edge":
            paths = [
                program_files_x86 / "Microsoft/Edge/Application/msedge.exe",
                program_files / "Microsoft/Edge/Application/msedge.exe",
            ]
            if local_root:
                paths.append(local_root / "Microsoft/Edge/Application/msedge.exe")
            return [*(str(path) for path in paths), "msedge.exe"]
        if kind == "chrome":
            paths = [
                program_files / "Google/Chrome/Application/chrome.exe",
                program_files_x86 / "Google/Chrome/Application/chrome.exe",
            ]
            if local_root:
                paths.append(local_root / "Google/Chrome/Application/chrome.exe")
            return [*(str(path) for path in paths), "chrome.exe"]
        if kind == "chromium":
            paths = [
                program_files / "Chromium/Application/chrome.exe",
                program_files_x86 / "Chromium/Application/chrome.exe",
            ]
            if local_root:
                paths.insert(0, local_root / "Chromium/Application/chrome.exe")
            return [*(str(path) for path in paths), "chromium.exe"]
        return []
    if system == "darwin":
        roots = [Path("/Applications"), Path.home() / "Applications"]
        app_paths = {
            "chrome": "Google Chrome.app/Contents/MacOS/Google Chrome",
            "chromium": "Chromium.app/Contents/MacOS/Chromium",
            "edge": "Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        }
        suffix = app_paths.get(kind)
        return [str(root / suffix) for root in roots] if suffix else []
    commands = {
        "chrome": ["google-chrome", "google-chrome-stable"],
        "chromium": ["chromium-browser", "chromium"],
        "edge": ["microsoft-edge", "microsoft-edge-stable"],
    }
    return list(commands.get(kind) or [])


def discover_system_agent_browser(browser_kind: Any = None) -> dict[str, Any]:
    requested_kind = normalize_agent_browser_kind(browser_kind)
    kinds = (
        [requested_kind]
        if requested_kind in {"chrome", "edge", "chromium"}
        else preferred_system_agent_browser_kinds()
    )
    for kind in kinds:
        for candidate in system_agent_browser_candidates(kind):
            candidate_path = Path(candidate)
            executable = str(candidate_path) if candidate_path.is_file() else shutil.which(candidate)
            if executable:
                return {
                    "available": True,
                    "browserKind": kind,
                    "executable": executable,
                    "candidateOrder": list(kinds),
                }
    return {
        "available": False,
        "browserKind": None,
        "executable": None,
        "candidateOrder": list(kinds),
        "reason": "compatible_browser_missing",
    }


def resolve_agent_browser_profile_dir(
    *,
    browser_kind: Any = None,
    configured_user_data_dir: str | Path | None = None,
) -> Path:
    kind = normalize_agent_browser_kind(browser_kind)
    configured = Path(str(configured_user_data_dir)).expanduser() if configured_user_data_dir else None
    if configured and configured.name.lower() in SUPPORTED_AGENT_BROWSER_KINDS:
        return configured
    root = configured or AGENT_BROWSER_PROFILE_ROOT
    return root / kind


def configured_agent_browser_profile_dir(browser_kind: Any = None) -> Path:
    from core.storage import storage

    runtime_config = storage.get_computer_use_config() or {}
    browser_lane = dict(runtime_config.get("browserLane") or {})
    return resolve_agent_browser_profile_dir(
        browser_kind=browser_kind,
        configured_user_data_dir=browser_lane.get("userDataDir") or browser_lane.get("debugUserDataDir") or "",
    )


def debug_port_owned_by_profile(*, port: int, profile_dir: str | Path) -> bool:
    """Return true only when the CDP listener was launched with this profile."""

    try:
        import psutil
    except ImportError:
        return False
    expected = os.path.normcase(str(Path(profile_dir).expanduser().resolve(strict=False)))

    def _command_matches(command: list[str]) -> bool:
        profile_matches = False
        port_matches = False
        for index, argument in enumerate(command):
            value = ""
            if argument.startswith("--user-data-dir="):
                value = argument.split("=", 1)[1]
            elif argument == "--user-data-dir" and index + 1 < len(command):
                value = command[index + 1]
            if value:
                actual = os.path.normcase(str(Path(value).expanduser().resolve(strict=False)))
                profile_matches = profile_matches or actual == expected
            if argument == f"--remote-debugging-port={int(port)}":
                port_matches = True
            elif argument == "--remote-debugging-port" and index + 1 < len(command):
                port_matches = port_matches or command[index + 1] == str(int(port))
        return profile_matches and port_matches

    connection_probe_available = True
    try:
        listener_pids = {
            int(connection.pid)
            for connection in psutil.net_connections(kind="inet")
            if connection.pid
            and connection.laddr
            and int(connection.laddr.port) == int(port)
            and str(connection.status or "").upper() == "LISTEN"
        }
    except Exception:
        connection_probe_available = False
        listener_pids = set()
    for pid in listener_pids:
        try:
            command = [str(item) for item in psutil.Process(pid).cmdline()]
        except Exception:
            continue
        if _command_matches(command):
            return True
    if connection_probe_available:
        return False
    try:
        processes = psutil.process_iter(["cmdline"])
    except Exception:
        return False
    for process in processes:
        try:
            command = [str(item) for item in (process.info.get("cmdline") or [])]
        except Exception:
            continue
        if _command_matches(command):
            return True
    return False


def agent_browser_profile_summary(browser_kind: Any = None, *, include_security_note: bool = True) -> dict[str, Any]:
    kind = normalize_agent_browser_kind(browser_kind)
    profile_dir = configured_agent_browser_profile_dir(kind)
    payload: dict[str, Any] = {
        "browserKind": kind,
        "profileMode": AGENT_BROWSER_PROFILE_MODE,
        "userDataDir": str(profile_dir),
        "profilePersistent": True,
        "sharedBy": ["research.web_broker", "computer_use.browser_dom", "rpa.browser_dom"],
    }
    if include_security_note:
        payload["security"] = {
            "cookiesExportedToModel": False,
            "usesUserDefaultBrowserProfile": False,
            "note": "登录态仅保存在 V8OS Agent 浏览器 profile；工具输出不会导出 cookies/localStorage。",
        }
    return payload


def normalize_agent_browser_profile_allowlist(values: Any) -> list[str]:
    if isinstance(values, str):
        raw_items = values.replace("\r", "\n").replace(",", "\n").split("\n")
    elif isinstance(values, (list, tuple, set)):
        raw_items = list(values)
    else:
        raw_items = []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = str(item or "").strip().lower()
        if not token:
            continue
        if "://" in token:
            token = urlparse(token).hostname or token
        token = token.strip().lstrip(".")
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def agent_browser_profile_allowed_for_url(url: str, allowlist: Any) -> tuple[bool, str | None]:
    hosts = normalize_agent_browser_profile_allowlist(allowlist)
    if not hosts:
        return False, None
    host = (urlparse(str(url or "")).hostname or "").strip().lower()
    if not host:
        return False, None
    for allowed in hosts:
        if host == allowed or host.endswith(f".{allowed}"):
            return True, allowed
    return False, None
