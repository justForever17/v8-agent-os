from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    import winreg  # type: ignore
except Exception:  # pragma: no cover - non-Windows hosts
    winreg = None


def _split_command(value: str | None) -> List[str]:
    raw = str(value or "").strip().strip('"')
    return [raw] if raw else []


def _normalized_stem(value: str | None) -> str:
    return Path(str(value or "").strip()).stem.strip().lower()


def _unique(values: List[str], *, lower: bool = False) -> List[str]:
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


def _infer_candidate_role(*, executable: Path, source: str) -> str:
    stem = _normalized_stem(executable.name)
    if not stem:
        return "unknown"
    if source == "windows_registry_uninstall_string":
        return "uninstall_fallback"
    if any(token in stem for token in ("uninstall", "unins", "remove", "repair")):
        return "uninstall_fallback"
    if any(token in stem for token in ("setup", "install", "bootstrap", "stub")):
        return "installer"
    if any(token in stem for token in ("update", "updater", "autoupdate", "upgrade")):
        return "updater"
    if any(token in stem for token in ("helper", "service", "proxy", "driver", "bugreporter", "crashpad", "messagehost", "hardwarecheck")):
        return "helper"
    if source == "windows_app_paths":
        return "primary_gui"
    if source == "windows_registry_display_icon":
        return "display_icon"
    if source.endswith("_scan"):
        return "install_scan"
    return "primary_gui"


def _build_launch_candidate(*, command: List[str], source: str) -> Dict[str, Any]:
    executable = Path(str((command or [None])[0] or "").strip())
    return {
        "command": [str(item or "").strip() for item in list(command or []) if str(item or "").strip()],
        "source": source,
        "role": _infer_candidate_role(executable=executable, source=source),
        "executableName": executable.name,
        "executableStem": executable.stem,
        "directory": str(executable.parent) if str(executable.parent or "").strip() else None,
    }


class WindowsAppDiscoveryProvider:
    def __init__(self, *, driver) -> None:
        self.driver = driver

    def discover_installed_apps(self) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        entries.extend(self._scan_app_paths())
        entries.extend(self._scan_uninstall_entries())
        return entries

    def discover_running_apps(self) -> List[Dict[str, Any]]:
        if not getattr(self.driver, "is_available", lambda: False)():
            return []
        try:
            windows = self.driver.list_windows(limit=120)
        except Exception:
            return []
        grouped: Dict[str, Dict[str, Any]] = {}
        for window in windows:
            if not isinstance(window, dict):
                continue
            title = str(window.get("title") or "").strip()
            process_name = str(window.get("processName") or "").strip()
            class_name = str(window.get("className") or "").strip()
            if not title and not process_name:
                continue
            display_name = title or Path(process_name).stem or "未知应用"
            key = (process_name or display_name).strip().lower() or display_name.lower()
            bucket = grouped.setdefault(
                key,
                {
                    "displayName": display_name,
                    "aliases": [],
                    "processNames": [],
                    "titlePatterns": [],
                    "classNames": [],
                    "sources": ["windows_top_level_window"],
                    "runningWindows": [],
                },
            )
            bucket["aliases"] = _unique(
                list(bucket.get("aliases") or []) + [display_name, title, Path(process_name).stem]
            )
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

    def _scan_app_paths(self) -> List[Dict[str, Any]]:
        if winreg is None:
            return []
        roots = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
        ]
        entries: List[Dict[str, Any]] = []
        for root, subkey in roots:
            try:
                parent = winreg.OpenKey(root, subkey)
            except OSError:
                continue
            with parent:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(parent, child_name)
                    except OSError:
                        continue
                    with child:
                        executable = self._query_value(child, "")
                        executable_path = Path(executable).expanduser() if executable else None
                        if not executable_path or not executable_path.exists():
                            continue
                        display_name = executable_path.stem or Path(child_name).stem
                        entries.append(
                            {
                                "displayName": display_name,
                                "aliases": _unique([display_name, Path(child_name).stem, executable_path.name]),
                                "launchCommands": [[str(executable_path)]],
                                "launchCandidates": [
                                    _build_launch_candidate(
                                        command=[str(executable_path)],
                                        source="windows_app_paths",
                                    )
                                ],
                                "processNames": [executable_path.name.lower()],
                                "titlePatterns": [display_name],
                                "sources": ["windows_app_paths"],
                            }
                        )
        return entries

    def _scan_uninstall_entries(self) -> List[Dict[str, Any]]:
        if winreg is None:
            return []
        roots = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        entries: List[Dict[str, Any]] = []
        for root, subkey in roots:
            try:
                parent = winreg.OpenKey(root, subkey)
            except OSError:
                continue
            with parent:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(parent, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(parent, child_name)
                    except OSError:
                        continue
                    with child:
                        display_name = self._query_value(child, "DisplayName")
                        if not display_name:
                            continue
                        launch_candidates = self._candidate_commands(
                            display_icon=self._query_value(child, "DisplayIcon"),
                            install_location=self._query_value(child, "InstallLocation"),
                            install_source=self._query_value(child, "InstallSource"),
                            uninstall_string=self._query_value(child, "UninstallString"),
                        )
                        commands = [list(candidate.get("command") or []) for candidate in launch_candidates]
                        process_names = [
                            Path(command[0]).name.lower()
                            for command in commands
                            if command and Path(command[0]).name
                        ]
                        entries.append(
                            {
                                "displayName": display_name,
                                "aliases": _unique([display_name, *process_names]),
                                "launchCommands": commands,
                                "launchCandidates": launch_candidates,
                                "processNames": process_names,
                                "titlePatterns": [display_name],
                                "sources": ["windows_registry_uninstall"],
                            }
                        )
        return entries

    def _candidate_commands(
        self,
        *,
        display_icon: str,
        install_location: str,
        install_source: str,
        uninstall_string: str,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for raw, source in (
            (str(display_icon or "").split(",", 1)[0].strip().strip('"'), "windows_registry_display_icon"),
            (str(uninstall_string or "").split(" /", 1)[0].strip().strip('"'), "windows_registry_uninstall_string"),
        ):
            command = _split_command(raw)
            if command:
                executable_path = Path(command[0])
                if executable_path.exists() and executable_path.suffix.lower() == ".exe":
                    candidates.append(_build_launch_candidate(command=command, source=source))
        for root, source in self._candidate_dirs(
            [
                (install_location, "windows_registry_install_location_scan"),
                (install_source, "windows_registry_install_source_scan"),
            ]
        ):
            if not root.exists():
                continue
            for executable in root.glob("*.exe"):
                candidates.append(_build_launch_candidate(command=[str(executable)], source=source))
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for candidate in candidates:
            key = tuple(str(item or "").strip().lower() for item in list(candidate.get("command") or []))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped[:8]

    def _candidate_dirs(self, values: Iterable[tuple[str, str]]) -> List[tuple[Path, str]]:
        ordered: List[tuple[Path, str]] = []
        seen = set()
        for raw, source in values:
            value = str(raw or "").strip().strip('"')
            if not value:
                continue
            path = Path(value).expanduser()
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append((path, source))
        return ordered

    def _query_value(self, key: Any, name: str) -> str:
        try:
            value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            return ""
        return str(value or "").strip()
