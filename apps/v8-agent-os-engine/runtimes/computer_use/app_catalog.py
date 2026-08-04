from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import threading
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


def _normalize_launch_candidate(candidate: Dict[str, Any] | None) -> Dict[str, Any] | None:
    payload = dict(candidate or {})
    command = [str(item or "").strip() for item in list(payload.get("command") or []) if str(item or "").strip()]
    if not command:
        return None
    executable = str(command[0] or "").strip()
    executable_path = Path(executable)
    return {
        "command": command,
        "source": str(payload.get("source") or "").strip() or "unknown",
        "role": str(payload.get("role") or "").strip() or "unknown",
        "executableName": str(payload.get("executableName") or executable_path.name or "").strip() or None,
        "executableStem": str(payload.get("executableStem") or executable_path.stem or "").strip() or None,
        "directory": str(payload.get("directory") or executable_path.parent or "").strip() or None,
    }


def _derive_launch_candidates_from_commands(
    commands: List[List[str]],
    *,
    fallback_source: str = "unknown",
    fallback_role: str = "unknown",
) -> List[Dict[str, Any]]:
    derived: List[Dict[str, Any]] = []
    for command in list(commands or []):
        normalized = _normalize_launch_candidate(
            {
                "command": command,
                "source": fallback_source,
                "role": fallback_role,
            }
        )
        if normalized is not None:
            derived.append(normalized)
    return derived


def _unique_launch_candidates(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered: List[Dict[str, Any]] = []
    seen = set()
    for candidate in list(candidates or []):
        normalized = _normalize_launch_candidate(candidate)
        if normalized is None:
            continue
        key = json.dumps(
            {
                "command": normalized["command"],
                "source": normalized["source"],
                "role": normalized["role"],
            },
            ensure_ascii=False,
        )
        if key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ordered


_SUSPICIOUS_LAUNCH_TOKEN_GROUPS: Dict[str, tuple[str, ...]] = {
    "uninstall": ("uninstall", "unins", "remove", "repair"),
    "installer": ("setup", "install", "bootstrap", "stub"),
    "updater": ("update", "updater", "autoupdate", "upgrade"),
    "helper": (
        "helper",
        "service",
        "proxy",
        "crashpad",
        "bugreporter",
        "driver",
        "native",
        "messagehost",
        "hardwarecheck",
        "elevation",
        "elevate",
    ),
}


def _is_suspicious_launch_stem(stem: str) -> str | None:
    normalized = _normalize(stem)
    if not normalized:
        return None
    for group, tokens in _SUSPICIOUS_LAUNCH_TOKEN_GROUPS.items():
        if any(token in normalized for token in tokens):
            return group
    return None


class ComputerUseAppCatalog:
    def __init__(
        self,
        *,
        app_profiles,
        app_adapters=None,
        platform_providers: Optional[List[ComputerUseAppDiscoveryProvider]] = None,
        static_ttl_seconds: int = 900,
        running_ttl_seconds: int = 20,
    ) -> None:
        self.app_profiles = app_profiles
        self.app_adapters = app_adapters
        self.platform_providers = list(platform_providers or [])
        self.static_ttl_seconds = max(60, int(static_ttl_seconds))
        self.running_ttl_seconds = max(5, int(running_ttl_seconds))
        self._static_entries: Dict[str, Dict[str, Any]] = {}
        self._runtime_entries: Dict[str, Dict[str, Any]] = {}
        self._last_static_refresh_ts = 0.0
        self._last_running_refresh_ts = 0.0
        self._refresh_lock = threading.RLock()
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
        include_learned: bool = True,
    ) -> Dict[str, Any]:
        entries = self._entries(include_running=include_running, force_refresh=force_refresh)
        if include_learned:
            entries = self._with_learned_app_marks(entries)
        ranked: List[tuple[int, Dict[str, Any]]] = []
        for entry in entries.values():
            score = self._match_score(entry, query)
            if query and score <= 0:
                continue
            payload = copy.deepcopy(entry)
            payload["matchScore"] = int(score)
            selected_candidate = self._select_launch_candidate(payload)
            if selected_candidate:
                payload["launchSelectionReason"] = selected_candidate.get("selectionReason")
                payload["launchCandidateSource"] = selected_candidate.get("source")
                payload["launchCandidateRole"] = selected_candidate.get("role")
                payload["launchCandidateScore"] = selected_candidate.get("score")
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

    def _with_learned_app_marks(self, entries: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        payload = storage.get_computer_use_memory()
        apps_payload = payload.get("apps") or {}
        learned = dict(apps_payload) if isinstance(apps_payload, dict) else {}
        if not learned:
            return entries
        merged = copy.deepcopy(entries)
        for app_id, profile in learned.items():
            if not isinstance(profile, dict):
                continue
            app_key = str(app_id or "").strip()
            if not app_key:
                continue
            existing = dict(merged.get(app_key) or {})
            selectors_payload = profile.get("selectors") or {}
            selectors_count = len(selectors_payload) if isinstance(selectors_payload, (dict, list)) else 0
            windows = list(profile.get("windows") or [])
            interactions = list(profile.get("interactions") or [])
            learned_titles = []
            for window in windows:
                if isinstance(window, dict) and window.get("title"):
                    learned_titles.append(str(window.get("title")))
            learned_entry = {
                "appId": app_key,
                "profileId": existing.get("profileId") or app_key,
                "displayName": existing.get("displayName") or app_key,
                "aliases": _unique([*list(existing.get("aliases") or []), app_key, *learned_titles]),
                "titlePatterns": _unique([*list(existing.get("titlePatterns") or []), *learned_titles]),
                "sources": _unique([*list(existing.get("sources") or []), "computer_use_memory"]),
                "learned": True,
                "learnedSelectorCount": selectors_count,
                "learnedInteractionCount": len(interactions),
                "learnedWindowCount": len(windows),
            }
            self._merge(merged, learned_entry)
            merged[app_key]["learned"] = True
            merged[app_key]["learnedSelectorCount"] = selectors_count
            merged[app_key]["learnedInteractionCount"] = len(interactions)
            merged[app_key]["learnedWindowCount"] = len(windows)
        return merged

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
        candidate = self.resolve_launch_candidate(
            app_id=app_id,
            app_name=app_name,
            window_title=window_title,
            class_name=class_name,
        )
        return list(candidate.get("command") or []) if candidate else []

    def resolve_launch_candidate(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        class_name: str | None = None,
    ) -> Dict[str, Any] | None:
        entry = self.resolve_app(
            explicit_app_id=app_id,
            app_name=app_name,
            window_title=window_title,
            class_name=class_name,
            include_running=False,
        )
        if entry is None:
            return None
        return self._select_launch_candidate(entry)

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
        with self._refresh_lock:
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
        with self._refresh_lock:
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
        with self._refresh_lock:
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
            changed = entries != self._runtime_entries
            self._runtime_entries = entries
            self._last_running_refresh_ts = now
            if changed:
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
            "controlClass": profile.control_class,
            "appAdapterId": profile.app_adapter_id or None,
            "aliases": aliases,
            "launchCommands": [list(profile.launch_command)] if profile.launch_command else [],
            "launchCandidates": _derive_launch_candidates_from_commands(
                [list(profile.launch_command)] if profile.launch_command else [],
                fallback_source="app_profile",
                fallback_role="profile_launch",
            ),
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
            "controlClass": str(entry.get("controlClass") or "").strip() or None,
            "appAdapterId": str(entry.get("appAdapterId") or "").strip() or None,
            "aliases": _unique(list(entry.get("aliases") or [])),
            "launchCommands": _unique_commands([list(item) for item in list(entry.get("launchCommands") or [])]),
            "launchCandidates": [],
            "processNames": _unique(list(entry.get("processNames") or []), lower=True),
            "titlePatterns": _unique(list(entry.get("titlePatterns") or [])),
            "classNames": _unique(list(entry.get("classNames") or [])),
            "sources": _unique(list(entry.get("sources") or [])),
            "runningWindows": self._unique_windows(list(entry.get("runningWindows") or [])),
        }
        explicit_launch_candidates = list(entry.get("launchCandidates") or [])
        fallback_launch_candidates = []
        if not explicit_launch_candidates:
            fallback_launch_candidates = _derive_launch_candidates_from_commands(
                normalized["launchCommands"],
                fallback_source=normalized["sources"][0] if normalized["sources"] else "unknown",
                fallback_role="unknown",
            )
        launch_candidates = _unique_launch_candidates(
            explicit_launch_candidates + fallback_launch_candidates
        )
        normalized["launchCandidates"] = launch_candidates
        normalized["launchCommands"] = _unique_commands([list(item.get("command") or []) for item in launch_candidates])
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
        normalized["controlClass"] = self._infer_control_class(normalized)
        normalized["appAdapterId"] = self._infer_app_adapter_id(normalized)
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
            normalized["launchCandidates"] = _unique_launch_candidates(
                list(existing.get("launchCandidates") or []) + list(normalized.get("launchCandidates") or [])
            )
            normalized["launchCommands"] = _unique_commands(
                [list(item.get("command") or []) for item in list(normalized.get("launchCandidates") or [])]
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
            normalized["controlClass"] = normalized.get("controlClass") or existing.get("controlClass")
            normalized["appAdapterId"] = normalized.get("appAdapterId") or existing.get("appAdapterId")
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

    def _infer_app_adapter_id(self, entry: Dict[str, Any]) -> str | None:
        explicit = str(entry.get("appAdapterId") or "").strip()
        if explicit:
            return explicit
        if self.app_adapters is None:
            return None
        match = self.app_adapters.match(
            app_id=entry.get("appId"),
            app_name=entry.get("displayName"),
            process_names=list(entry.get("processNames") or []),
            title_patterns=list(entry.get("titlePatterns") or []),
            launch_candidates=list(entry.get("launchCandidates") or []),
            catalog_entry=entry,
        )
        return match.adapter_id if match is not None else None

    def _infer_control_class(self, entry: Dict[str, Any]) -> str:
        explicit = str(entry.get("controlClass") or "").strip()
        if explicit:
            return explicit
        adapter_id = str(entry.get("appAdapterId") or "").strip()
        if adapter_id and self.app_adapters is not None:
            adapter = self.app_adapters.get(adapter_id)
            if adapter is not None:
                return str(getattr(adapter, "control_class", "") or "native_window_app")
        aliases = " ".join(
            [
                str(entry.get("appId") or ""),
                str(entry.get("displayName") or ""),
                " ".join(list(entry.get("aliases") or [])),
                " ".join(list(entry.get("processNames") or [])),
            ]
        ).lower()
        if any(token in aliases for token in ("chrome", "edge", "chromium", "firefox", "browser")):
            return "browser_host_app"
        if any(token in aliases for token in ("obsidian", "code", "kiro", "electron")):
            return "electron_shell_app"
        return "native_window_app"

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

    def _select_launch_candidate(self, entry: Dict[str, Any]) -> Dict[str, Any] | None:
        candidates = _unique_launch_candidates(list(entry.get("launchCandidates") or []))
        if not candidates:
            return None
        ranked: List[tuple[int, Dict[str, Any]]] = []
        for candidate in candidates:
            resolved = self._command_resolves(list(candidate.get("command") or []))
            score, reasons = self._score_launch_candidate(entry, candidate, resolved=resolved)
            if not resolved:
                score -= 120
                reasons.append("command_unresolved")
            payload = dict(candidate)
            payload["score"] = score
            payload["selectionReason"] = ",".join(reasons) if reasons else "fallback_candidate"
            ranked.append((score, payload))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]

    def _score_launch_candidate(self, entry: Dict[str, Any], candidate: Dict[str, Any], *, resolved: bool) -> tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        source = str(candidate.get("source") or "").strip().lower()
        role = str(candidate.get("role") or "").strip().lower()
        executable_stem = _normalize(str(candidate.get("executableStem") or ""))
        executable_name = str(candidate.get("executableName") or "").strip().lower()
        if source == "app_profile":
            score += 260
            reasons.append("profile_launch")
        elif source == "windows_app_paths":
            score += 180
            reasons.append("app_paths")
        elif source == "windows_registry_display_icon":
            score += 120
            reasons.append("display_icon")
        elif source.endswith("_scan"):
            score += 40
            reasons.append("install_scan")
        elif source == "windows_registry_uninstall_string":
            score -= 80
            reasons.append("uninstall_string_low_priority")

        if role in {"primary_gui", "display_icon", "profile_launch"}:
            score += 90
            reasons.append(f"role_{role}")
        elif role == "install_scan":
            score += 24
            reasons.append("role_install_scan")
        elif role in {"helper", "updater", "installer"}:
            score -= 120
            reasons.append(f"role_{role}")
        elif role == "uninstall_fallback":
            score -= 220
            reasons.append("role_uninstall")

        suspicious_group = _is_suspicious_launch_stem(executable_stem)
        if suspicious_group == "uninstall":
            score -= 220
            reasons.append("stem_uninstall")
        elif suspicious_group == "installer":
            score -= 120
            reasons.append("stem_installer")
        elif suspicious_group == "updater":
            score -= 90
            reasons.append("stem_updater")
        elif suspicious_group == "helper":
            score -= 110
            reasons.append("stem_helper")

        primary_tokens = {
            _normalize(str(entry.get("displayName") or "")),
            _normalize(str(entry.get("appId") or "")),
            _normalize(str(entry.get("profileId") or "")),
        }
        for alias in list(entry.get("aliases") or []):
            normalized = _normalize(alias)
            if normalized and not _is_suspicious_launch_stem(normalized):
                primary_tokens.add(normalized)
        for process_name in list(entry.get("processNames") or []):
            stem = _normalize(_stem(process_name))
            if stem and not _is_suspicious_launch_stem(stem):
                primary_tokens.add(stem)
        primary_tokens = {token for token in primary_tokens if token and len(token) >= 2}

        best_token_score = 0
        for token in primary_tokens:
            if executable_stem == token:
                best_token_score = max(best_token_score, 180)
            elif executable_stem and token and token in executable_stem:
                best_token_score = max(best_token_score, 90 + min(len(token), 24))
            elif executable_stem and token and executable_stem in token:
                best_token_score = max(best_token_score, 70 + min(len(executable_stem), 20))
        if best_token_score:
            score += best_token_score
            reasons.append("matches_primary_name")

        if executable_name.endswith(".exe"):
            score += 8
        if resolved:
            score += 16
        return score, reasons

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
