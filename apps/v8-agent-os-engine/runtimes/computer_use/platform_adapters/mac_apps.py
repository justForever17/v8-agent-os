from __future__ import annotations

import plistlib
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


class MacAppDiscoveryProvider:
    def __init__(self, *, driver) -> None:
        self.driver = driver

    def discover_installed_apps(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for bundle_path in self._app_bundles():
            info = self._bundle_info(bundle_path)
            display_name = (
                str(info.get("CFBundleDisplayName") or "").strip()
                or str(info.get("CFBundleName") or "").strip()
                or bundle_path.stem
            )
            executable_name = str(info.get("CFBundleExecutable") or "").strip()
            bundle_identifier = str(info.get("CFBundleIdentifier") or "").strip()
            process_names = [f"{executable_name}.app"] if executable_name else []
            if executable_name:
                process_names.append(executable_name)
            entries.append(
                {
                    "displayName": display_name,
                    "aliases": _unique(
                        [
                            display_name,
                            bundle_path.stem,
                            executable_name,
                            bundle_identifier,
                        ]
                    ),
                    "launchCommands": [["open", "-a", str(bundle_path)]],
                    "processNames": _unique(process_names, lower=True),
                    "bundleIdentifiers": _unique([bundle_identifier], lower=False),
                    "titlePatterns": _unique([display_name, bundle_path.stem]),
                    "sources": ["mac_application_bundle"],
                }
            )
        return entries

    def discover_running_apps(self) -> List[Dict[str, Any]]:
        if not getattr(self.driver, "is_available", lambda: False)():
            return []
        try:
            windows = self.driver.list_windows(limit=160)
        except Exception:
            return []
        grouped: Dict[str, Dict[str, Any]] = {}
        for window in windows:
            if not isinstance(window, dict):
                continue
            title = str(window.get("title") or "").strip()
            process_name = str(window.get("processName") or window.get("ownerName") or "").strip()
            bundle_id = str(window.get("bundleIdentifier") or "").strip()
            if not title and not process_name and not bundle_id:
                continue
            display_name = process_name or title or bundle_id
            key = (bundle_id or process_name or display_name).strip().lower()
            bucket = grouped.setdefault(
                key,
                {
                    "displayName": display_name,
                    "aliases": [],
                    "processNames": [],
                    "bundleIdentifiers": [],
                    "titlePatterns": [],
                    "sources": ["mac_running_window"],
                    "runningWindows": [],
                },
            )
            bucket["aliases"] = _unique(list(bucket.get("aliases") or []) + [display_name, title, process_name, bundle_id])
            if process_name:
                bucket["processNames"] = _unique(list(bucket.get("processNames") or []) + [process_name], lower=True)
            if bundle_id:
                bucket["bundleIdentifiers"] = _unique(list(bucket.get("bundleIdentifiers") or []) + [bundle_id])
            if title:
                bucket["titlePatterns"] = _unique(list(bucket.get("titlePatterns") or []) + [title])
            bucket["runningWindows"].append(
                {
                    "handle": window.get("handle"),
                    "title": title or None,
                    "className": window.get("className") or None,
                    "processName": process_name.lower() or None,
                    "bundleIdentifier": bundle_id or None,
                    "processId": window.get("processId"),
                    "matchScore": window.get("matchScore"),
                    "isVisible": window.get("isVisible"),
                }
            )
        return list(grouped.values())

    def _app_bundles(self) -> List[Path]:
        roots = [
            Path("/Applications"),
            Path("/System/Applications"),
            Path.home() / "Applications",
        ]
        bundles: List[Path] = []
        seen = set()
        for root in roots:
            if not root.exists():
                continue
            patterns = ["*.app", "*/*.app"]
            for pattern in patterns:
                for candidate in root.glob(pattern):
                    if not candidate.is_dir():
                        continue
                    key = str(candidate).lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    bundles.append(candidate)
        bundles.sort(key=lambda path: path.stem.lower())
        return bundles

    def _bundle_info(self, bundle_path: Path) -> Dict[str, Any]:
        info_path = bundle_path / "Contents" / "Info.plist"
        if not info_path.exists():
            return {}
        try:
            with info_path.open("rb") as fh:
                payload = plistlib.load(fh)
            if isinstance(payload, dict):
                return payload
        except Exception:
            return {}
        return {}
