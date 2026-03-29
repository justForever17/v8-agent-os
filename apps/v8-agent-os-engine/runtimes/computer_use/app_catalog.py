from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from core.storage import storage


class ComputerUseAppDiscoveryProvider(Protocol):
    def discover_installed_apps(self) -> List[Dict[str, Any]]:
        ...

    def discover_running_apps(self) -> List[Dict[str, Any]]:
        ...


def _normalize(value: str | None) -> str:
    return re.sub(r"[\s_\-:./\\]+", "", str(value or "").strip().lower())


def _stem(value: str | None) -> str:
    raw = str(value or "").strip()
    return Path(raw).stem.strip() if raw else ""


def _unique(items: List[str], *, lower: bool = False) -> List[str]:
    ordered: List[str] = []
    seen = set()
    for raw in items:
        value = str(raw or "").strip()
        if not value:
            continue
        key = value.lower() if lower else value.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value.lower() if lower else value)
    return ordered


def _unique_commands(commands: List[List[str]]) -> List[List[str]]:
    ordered: List[List[str]] = []
    seen = set()
    for command in commands:
        normalized = [str(item or "").strip() for item in list(command or []) if str(item or "").strip()]
        if not normalized:
            continue
        key = json.dumps(normalized, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


class ComputerUseAppCatalog:
    def __init__(
        self,
        *,
        app_profiles,
        platform_providers: Optional[List[ComputerUseAppDiscoveryProvider]] = None,
        static_ttl_seconds: int = 900,
        running_ttl_seconds: int = 20,
    ) -> None:
        self.app_profiles = app_profiles
        self.platform_providers = list(platform_providers or [])
        self.static_ttl_seconds = max(60, int(static_ttl_seconds))
        self.running_ttl_seconds = max(5, int(running_ttl_seconds))
        self._static_entries: Dict[str, Dict[str, Any]] = {}
        self._runtime_entries: Dict[str, Dict[str, Any]] = {}
        self._last_static_refresh_ts = 0.0
        self._last_running_refresh_ts = 0.0
        self._load_cache()

    def warm_start(self) -> None:
        try:
            self._ensure_static(force=False)
        except Exception:
            return

    def has_app(self, app_id: str | None) -> bool:
        return bool(app_id and str(app_id).strip() in self._entries(include_running=False, force_refresh=False))

    def summary(self, *, include_running: bool = True) -> Dict[str, Any]:
        return self._summary(self._entries(include_running=include_running, force_refresh=False))

    def list_apps(
        self,
        *,
        query: str | None = None,
        limit: int = 20,
        include_running: bool = True,
        force_refresh: bool = False,
    ) -> Dict[str, Any]:
        entries = self._entries(include_running=include_running, force_refresh=force_refresh)
        ranked: List[tuple[int, Dict[str, Any]]] = []
        for entry in entries.values():
            score = self._match_score(entry, query)
            if query and score <= 0:
                continue
            payload = copy.deepcopy(entry)
            payload["matchScore"] = int(score)
            ranked.append((score, payload))
        ranked.sort(
            key=lambda item: (
                -item[0],
                -int(bool(item[1].get("isRunning"))),
                -int(bool(item[1].get("launchable"))),
                str(item[1].get("displayName") or "").lower(),
            )
        )
        return {
            "query": str(query or "").strip() or None,
            "apps": [payload for _score, payload in ranked[: max(1, min(int(limit), 100))]],
            "summary": self._summary(entries),
        }

    def resolve_app(
        self,
        *,
        explicit_app_id: str | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        query: str | None = None,
        include_running: bool = True,
        force_refresh: bool = False,
    ) -> Dict[str, Any] | None:
        entries = self._entries(include_running=include_running, force_refresh=force_refresh)
        explicit = str(explicit_app_id or "").strip()
        if explicit and explicit in entries:
            return copy.deepcopy(entries[explicit])

        best_entry: Dict[str, Any] | None = None
        best_score = 0
        for candidate in entries.values():
            score = 0
            if explicit and str(candidate.get("profileId") or "").strip() == explicit:
                score += 160
            for raw in (query, app_name, window_title, class_name):
                score = max(score, self._match_score(candidate, raw))
            if score > best_score:
                best_score = score
                best_entry = candidate
        if best_entry is None or best_score <= 0:
            return None
        return copy.deepcopy(best_entry)

    def binding_hints(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
        include_running: bool = True,
    ) -> Dict[str, List[str]]:
        entry = self.resolve_app(
            explicit_app_id=app_id,
            app_name=app_name,
            window_title=window_title,
            class_name=class_name,
            include_running=include_running,
        )
        if entry is None:
            return {"titles": [], "classes": [], "processNames": []}
        titles = list(entry.get("titlePatterns") or [])
        classes = list(entry.get("classNames") or [])
        process_names = list(entry.get("processNames") or [])
        for window in list(entry.get("runningWindows") or []):
            if not isinstance(window, dict):
                continue
            if window.get("title"):
                titles.append(str(window["title"]))
            if window.get("className"):
                classes.append(str(window["className"]))
            if window.get("processName"):
                process_names.append(str(window["processName"]))
        return {
            "titles": _unique(titles),
            "classes": _unique(classes),
            "processNames": _unique(process_names, lower=True),
        }

    def resolve_launch_command(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
    ) -> List[str]:
        entry = self.resolve_app(
            explicit_app_id=app_id,
            app_name=app_name,
            window_title=window_title,
            class_name=class_name,
            include_running=False,
        )
        if entry is None:
            return []
        commands = [list(command) for command in list(entry.get("launchCommands") or [])]
        for command in commands:
            if self._command_resolves(command):
                return command
        return commands[0] if commands else []

    def record_runtime_window(
        self,
        *,
        app_id: str | None,
        display_name: str | None = None,
        profile_id: str | None = None,
        launch_command: List[str] | None = None,
        window: Dict[str, Any] | None = None,
    ) -> None:
        if not app_id:
            return
        self._ensure_static(force=False)
        self._runtime_entries = copy.deepcopy(self._runtime_entries or self._static_entries)
        self._merge(
            self._runtime_entries,
            {
                "appId": str(app_id).strip(),
                "profileId": str(profile_id or "").strip() or None,
                "displayName": str(display_name or "").strip() or None,
                "launchCommands": [list(launch_command or [])] if launch_command else [],
                "runningWindows": [dict(window or {})] if isinstance(window, dict) else [],
                "sources": ["runtime_window_binding"],
            },
        )
        self._last_running_refresh_ts = time.time()
        self._save_cache()

    def _entries(self, *, include_running: bool, force_refresh: bool) -> Dict[str, Dict[str, Any]]:
        self._ensure_static(force=force_refresh)
        if include_running:
            self._ensure_running(force=force_refresh)
            return self._runtime_entries
        return self._static_entries

    def _ensure_static(self, *, force: bool) -> None:
        now = time.time()
        if not force and self._static_entries and (now - self._last_static_refresh_ts) <= self.static_ttl_seconds:
            return
        entries: Dict[str, Dict[str, Any]] = {}
        for profile in self.app_profiles.list_profiles():
            self._merge(entries, self._profile_entry(profile))
        for provider in self.platform_providers:
            try:
                discovered = provider.discover_installed_apps()
            except Exception:
                continue
            for entry in list(discovered or []):
                if isinstance(entry, dict):
                    self._merge(entries, entry)
        self._static_entries = entries
        self._runtime_entries = copy.deepcopy(entries)
        self._last_static_refresh_ts = now
        self._save_cache()

    def _ensure_running(self, *, force: bool) -> None:
        now = time.time()
        if (
            not force
            and self._runtime_entries
            and (now - self._last_running_refresh_ts) <= self.running_ttl_seconds
        ):
            return
        self._ensure_static(force=False)
        entries = copy.deepcopy(self._static_entries)
        for provider in self.platform_providers:
            try:
                discovered = provider.discover_running_apps()
            except Exception:
                continue
            for entry in list(discovered or []):
                if isinstance(entry, dict):
                    self._merge(entries, entry)
        self._runtime_entries = entries
        self._last_running_refresh_ts = now
        self._save_cache()

    def _profile_entry(self, profile) -> Dict[str, Any]:
        aliases = [
            profile.display_name,
            profile.app_id,
            *list(profile.app_names),
            *list(profile.title_patterns),
            *[_stem(item) for item in list(profile.process_names)],
            *[_stem(item) for item in list(profile.launch_command)],
        ]
        return {
            "appId": profile.app_id,
            "profileId": profile.app_id,
            "displayName": profile.display_name,
            "aliases": aliases,
            "launchCommands": [list(profile.launch_command)] if profile.launch_command else [],
            "processNames": list(profile.process_names),
            "titlePatterns": list(profile.title_patterns),
            "classNames": list(profile.class_names),
            "sources": ["app_profile"],
        }

    def _load_cache(self) -> None:
        payload = storage.get_computer_use_memory()
        catalog = dict(payload.get("appCatalog") or {})
        entries = dict(catalog.get("entries") or {})
        self._static_entries = {
            str(app_id): self._normalize_entry(dict(entry or {}), preferred_app_id=str(app_id))
            for app_id, entry in entries.items()
            if isinstance(entry, dict)
        }
        self._runtime_entries = copy.deepcopy(self._static_entries)
        self._last_static_refresh_ts = float(catalog.get("lastStaticRefreshTs") or 0.0)
        self._last_running_refresh_ts = float(catalog.get("lastRunningRefreshTs") or 0.0)

    def _save_cache(self) -> None:
        payload = storage.get_computer_use_memory()
        payload["appCatalog"] = {
            "version": 1,
            "entries": copy.deepcopy(self._runtime_entries or self._static_entries),
            "lastStaticRefreshTs": self._last_static_refresh_ts,
            "lastRunningRefreshTs": self._last_running_refresh_ts,
        }
        storage.save_computer_use_memory(payload)

    def _normalize_entry(self, entry: Dict[str, Any], *, preferred_app_id: str | None = None) -> Dict[str, Any]:
        app_id = str(preferred_app_id or entry.get("appId") or "").strip()
        if not app_id:
            app_id = self._infer_profile_id(entry) or self._fallback_app_id(entry)
        profile_id = str(entry.get("profileId") or "").strip() or self._infer_profile_id(entry)
        display_name = str(entry.get("displayName") or entry.get("name") or app_id).strip()
        normalized = {
            "appId": app_id,
            "profileId": profile_id or None,
            "displayName": display_name,
            "aliases": _unique(list(entry.get("aliases") or [])),
            "launchCommands": _unique_commands([list(item) for item in list(entry.get("launchCommands") or [])]),
            "processNames": _unique(list(entry.get("processNames") or []), lower=True),
            "titlePatterns": _unique(list(entry.get("titlePatterns") or [])),
            "classNames": _unique(list(entry.get("classNames") or [])),
            "sources": _unique(list(entry.get("sources") or [])),
            "runningWindows": self._unique_windows(list(entry.get("runningWindows") or [])),
        }
        if not normalized["aliases"]:
            normalized["aliases"] = _unique(
                [
                    normalized["displayName"],
                    normalized["appId"],
                    normalized["profileId"] or "",
                    *normalized["processNames"],
                    *normalized["titlePatterns"],
                ]
            )
        normalized["launchable"] = bool(normalized["launchCommands"])
        normalized["isRunning"] = bool(normalized["runningWindows"])
        normalized["profileBound"] = bool(normalized["profileId"])
        normalized["installed"] = bool(normalized["launchCommands"] or normalized["profileBound"])
        return normalized

    def _merge(self, bucket: Dict[str, Dict[str, Any]], raw_entry: Dict[str, Any]) -> None:
        normalized = self._normalize_entry(raw_entry)
        app_id = normalized["appId"]
        existing = copy.deepcopy(bucket.get(app_id) or {})
        if existing:
            normalized["displayName"] = self._pick_display_name(existing, normalized)
            normalized["aliases"] = _unique(list(existing.get("aliases") or []) + list(normalized.get("aliases") or []))
            normalized["launchCommands"] = _unique_commands(
                [list(item) for item in list(existing.get("launchCommands") or []) + list(normalized.get("launchCommands") or [])]
            )
            normalized["processNames"] = _unique(
                list(existing.get("processNames") or []) + list(normalized.get("processNames") or []),
                lower=True,
            )
            normalized["titlePatterns"] = _unique(list(existing.get("titlePatterns") or []) + list(normalized.get("titlePatterns") or []))
            normalized["classNames"] = _unique(list(existing.get("classNames") or []) + list(normalized.get("classNames") or []))
            normalized["sources"] = _unique(list(existing.get("sources") or []) + list(normalized.get("sources") or []))
            normalized["runningWindows"] = self._unique_windows(
                list(existing.get("runningWindows") or []) + list(normalized.get("runningWindows") or [])
            )
            normalized["profileId"] = normalized.get("profileId") or existing.get("profileId")
        bucket[app_id] = self._normalize_entry(normalized, preferred_app_id=app_id)

    def _pick_display_name(self, existing: Dict[str, Any], incoming: Dict[str, Any]) -> str:
        existing_name = str(existing.get("displayName") or "").strip()
        incoming_name = str(incoming.get("displayName") or "").strip()
        if not existing_name:
            return incoming_name
        if not incoming_name:
            return existing_name
        if existing.get("profileId") and not incoming.get("profileId"):
            return existing_name
        if incoming.get("profileId") and not existing.get("profileId"):
            return incoming_name
        return incoming_name if len(incoming_name) > len(existing_name) else existing_name

    def _infer_profile_id(self, entry: Dict[str, Any]) -> str | None:
        explicit = str(entry.get("profileId") or entry.get("appId") or "").strip()
        if explicit and self.app_profiles.get(explicit):
            return explicit
        aliases = list(entry.get("aliases") or [])
        title_patterns = list(entry.get("titlePatterns") or [])
        app_name = next((item for item in aliases if str(item or "").strip()), None) or entry.get("displayName")
        window_title = next((item for item in title_patterns if str(item or "").strip()), None) or entry.get("displayName")
        class_name = next((item for item in list(entry.get("classNames") or []) if str(item or "").strip()), None)
        process_name = next((item for item in list(entry.get("processNames") or []) if str(item or "").strip()), None)
        return self.app_profiles.infer(
            explicit_app_id=None,
            window_title=window_title,
            class_name=class_name,
            app_name=app_name,
            process_name=process_name,
        )

    def _fallback_app_id(self, entry: Dict[str, Any]) -> str:
        candidates = [
            *[_stem(item) for item in list(entry.get("processNames") or [])],
            *[_stem((command or [None])[0]) for command in list(entry.get("launchCommands") or [])],
            str(entry.get("displayName") or "").strip(),
        ]
        for candidate in candidates:
            slug = re.sub(r"[^a-z0-9]+", "_", str(candidate or "").strip().lower()).strip("_")
            if slug:
                return f"app_{slug}"
        digest_source = _normalize(str(entry.get("displayName") or "") or json.dumps(entry, ensure_ascii=False))
        digest = hashlib.md5(digest_source.encode("utf-8")).hexdigest()[:10]
        return f"app_{digest}"

    def _unique_windows(self, windows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ordered: List[Dict[str, Any]] = []
        seen = set()
        for window in windows:
            if not isinstance(window, dict):
                continue
            key = (
                window.get("handle"),
                str(window.get("title") or "").strip().lower(),
                str(window.get("processName") or "").strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            ordered.append(
                {
                    "handle": window.get("handle"),
                    "title": str(window.get("title") or "").strip() or None,
                    "className": str(window.get("className") or "").strip() or None,
                    "processName": str(window.get("processName") or "").strip().lower() or None,
                    "processId": window.get("processId"),
                    "matchScore": window.get("matchScore"),
                    "isVisible": window.get("isVisible"),
                }
            )
        return ordered[:20]

    def _command_resolves(self, command: List[str]) -> bool:
        if not command:
            return False
        executable = str(command[0] or "").strip()
        if not executable:
            return False
        return Path(executable).exists() or bool(shutil.which(executable))

    def _match_score(self, entry: Dict[str, Any], query: str | None) -> int:
        normalized_query = _normalize(query)
        score = 0
        if not normalized_query:
            return (6 if entry.get("isRunning") else 0) + (4 if entry.get("launchable") else 0) + (2 if entry.get("profileBound") else 0)
        fields = [
            str(entry.get("appId") or ""),
            str(entry.get("profileId") or ""),
            str(entry.get("displayName") or ""),
            *list(entry.get("aliases") or []),
            *list(entry.get("processNames") or []),
            *list(entry.get("titlePatterns") or []),
        ]
        compact = [_normalize(item) for item in fields if _normalize(item)]
        if normalized_query in compact:
            score = max(score, 140)
        for field in compact:
            if field == normalized_query:
                score = max(score, 140)
            elif field and field in normalized_query:
                score = max(score, 90 + min(len(field), 24))
            elif normalized_query in field:
                score = max(score, 70 + min(len(normalized_query), 20))
        score += 18 if entry.get("isRunning") else 0
        score += 12 if entry.get("launchable") else 0
        score += 8 if entry.get("profileBound") else 0
        return score

    def _summary(self, entries: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        values = list(entries.values())
        return {
            "total": len(values),
            "running": sum(1 for item in values if item.get("isRunning")),
            "launchable": sum(1 for item in values if item.get("launchable")),
            "profileBound": sum(1 for item in values if item.get("profileBound")),
            "lastStaticRefreshTs": self._last_static_refresh_ts,
            "lastRunningRefreshTs": self._last_running_refresh_ts,
        }
