from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse


AGENT_BROWSER_PROFILE_MODE = "dedicated_debug_profile"
AGENT_BROWSER_PROFILE_ROOT = Path.home() / ".v8-agent-os" / "browser-profiles" / "computer_use"
SUPPORTED_AGENT_BROWSER_KINDS = {"chrome", "edge", "chromium"}


def normalize_agent_browser_kind(value: Any = None) -> str:
    token = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if token in {"msedge", "microsoft_edge", "microsoft-edge"}:
        return "edge"
    if token in {"google_chrome", "google-chrome"}:
        return "chrome"
    if token in SUPPORTED_AGENT_BROWSER_KINDS:
        return token
    return "chrome"


def default_agent_browser_profile_root() -> Path:
    return AGENT_BROWSER_PROFILE_ROOT


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
