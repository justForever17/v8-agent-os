from __future__ import annotations

import configparser
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List


def _unique(values: Iterable[str], *, lower: bool = False) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value.lower() if lower else value)
    return ordered


def _strip_desktop_exec_tokens(command: str) -> List[str]:
    raw = str(command or "").strip()
    if not raw:
        return []
    tokens = [token for token in shlex.split(raw) if not token.startswith("%")]
    if tokens and tokens[0] == "env":
        return tokens
    return tokens


class LinuxAppDiscoveryProvider:
    def __init__(self, *, driver) -> None:
        self.driver = driver

    def discover_installed_apps(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for desktop_file in self._desktop_entries():
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read(desktop_file, encoding="utf-8")
            except Exception:
                continue
            if not parser.has_section("Desktop Entry"):
                continue
            section = parser["Desktop Entry"]
            if section.get("NoDisplay", "").strip().lower() == "true":
                continue
            display_name = str(section.get("Name") or "").strip() or desktop_file.stem
            exec_tokens = _strip_desktop_exec_tokens(section.get("Exec") or "")
            startup_wm_class = str(section.get("StartupWMClass") or "").strip()
            icon_name = str(section.get("Icon") or "").strip()
            process_names: List[str] = []
            if exec_tokens:
                process_names.append(Path(exec_tokens[0]).name)
            if startup_wm_class:
                process_names.append(startup_wm_class)
            entries.append(
                {
                    "displayName": display_name,
                    "aliases": _unique([display_name, desktop_file.stem, startup_wm_class, icon_name]),
                    "launchCommands": [exec_tokens] if exec_tokens else [],
                    "processNames": _unique(process_names, lower=True),
                    "titlePatterns": _unique([display_name, startup_wm_class]),
                    "classNames": _unique([startup_wm_class]),
                    "sources": ["linux_desktop_entry"],
                }
            )
        return entries

    def discover_running_apps(self) -> List[Dict[str, Any]]:
        if not getattr(self.driver, "is_available", lambda: False)():
            return []
        try:
            windows = self.driver.list_windows(limit=180)
        except Exception:
            return []
        grouped: Dict[str, Dict[str, Any]] = {}
        for window in windows:
            if not isinstance(window, dict):
                continue
            title = str(window.get("title") or "").strip()
            process_name = str(window.get("processName") or window.get("app") or "").strip()
            class_name = str(window.get("className") or "").strip()
            if not title and not process_name and not class_name:
                continue
            display_name = process_name or title or class_name
            key = (process_name or class_name or display_name).strip().lower()
            bucket = grouped.setdefault(
                key,
                {
                    "displayName": display_name,
                    "aliases": [],
                    "processNames": [],
                    "titlePatterns": [],
                    "classNames": [],
                    "sources": ["linux_running_window"],
                    "runningWindows": [],
                },
            )
            bucket["aliases"] = _unique(list(bucket.get("aliases") or []) + [display_name, title, process_name, class_name])
            if process_name:
                bucket["processNames"] = _unique(list(bucket.get("processNames") or []) + [process_name], lower=True)
            if title:
                bucket["titlePatterns"] = _unique(list(bucket.get("titlePatterns") or []) + [title])
            if class_name:
                bucket["classNames"] = _unique(list(bucket.get("classNames") or []) + [class_name])
            bucket["runningWindows"].append(
                {
                    "handle": window.get("handle"),
                    "title": title or None,
                    "className": class_name or None,
                    "processName": process_name.lower() or None,
                    "processId": window.get("processId"),
                    "matchScore": window.get("matchScore"),
                    "isVisible": window.get("isVisible"),
                }
            )
        return list(grouped.values())

    def _desktop_entries(self) -> List[Path]:
        roots = [
            Path.home() / ".local" / "share" / "applications",
            Path("/usr/local/share/applications"),
            Path("/usr/share/applications"),
        ]
        files: List[Path] = []
        seen = set()
        for root in roots:
            if not root.exists():
                continue
            for candidate in root.glob("*.desktop"):
                key = str(candidate).lower()
                if key in seen:
                    continue
                seen.add(key)
                files.append(candidate)
        files.sort(key=lambda path: path.stem.lower())
        return files
