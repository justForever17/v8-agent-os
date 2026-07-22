from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Dict, Iterable, List
from urllib.parse import urlparse


_CATALOG_DIR = Path(__file__).resolve().parent / "shortcut_catalog"
_SYSTEM_CATALOG_PATH = _CATALOG_DIR / "system_shortcuts.json"
_APPLICATION_CATALOG_PATH = _CATALOG_DIR / "application_shortcuts.json"
_LEARNED_PROFILE_LIMIT = 64
_LEARNED_SHORTCUT_LIMIT = 24
_LEARNED_PROFILE_LOCK = RLock()


def normalize_shortcut_platform(value: str | None = None) -> str:
    raw = str(value or sys.platform or "").strip().lower()
    if raw.startswith("win"):
        return "windows"
    if raw in {"darwin", "mac", "macos", "osx"}:
        return "macos"
    if raw.startswith("linux"):
        return "linux"
    return raw or "unknown"


def _normalized_token(value: Any) -> str:
    return re.sub(r"[\s_\-:./\\]+", "", str(value or "").strip().lower()).removesuffix("exe")


def _unique_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in result:
            result.append(item)
    return result


class ShortcutRegistryError(ValueError):
    pass


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _load_learned_profiles() -> Dict[str, Any]:
    from core.storage import storage

    payload = storage.get_computer_use_memory()
    learned = payload.get("shortcutProfiles")
    return deepcopy(learned) if isinstance(learned, dict) else {"version": 1, "applications": {}}


def _save_learned_profiles(learned: Dict[str, Any]) -> None:
    from core.storage import storage

    with _LEARNED_PROFILE_LOCK:
        payload = storage.get_computer_use_memory()
        payload["shortcutProfiles"] = deepcopy(learned)
        storage.save_computer_use_memory(payload)


def compile_human_shortcut(keys: str, *, platform: str | None = None) -> Dict[str, str]:
    """Compile one documented app-local chord into the driver grammar.

    Learned shortcuts deliberately accept one chord, not arbitrary key scripts.
    Global OS chords, close commands, deletion chords, and bare printable keys
    remain outside this path.
    """

    raw = str(keys or "").strip()
    if not raw or len(raw) > 48 or any(token in raw for token in ("{", "}", "(", ")", ",", ";")):
        raise ShortcutRegistryError("keys must be one human-readable shortcut chord")
    normalized_platform = normalize_shortcut_platform(platform)
    aliases = {
        "CONTROL": "CTRL",
        "CTL": "CTRL",
        "CMD": "COMMAND",
        "⌘": "COMMAND",
        "OPTION": "ALT",
        "OPT": "ALT",
        "WINDOWS": "WIN",
        "SUPER": "WIN",
        "META": "WIN",
        "RETURN": "ENTER",
        "ESCAPE": "ESC",
        " ": "SPACE",
    }
    parts = [aliases.get(part.strip().upper(), part.strip().upper()) for part in raw.split("+")]
    if not parts or any(not part for part in parts):
        raise ShortcutRegistryError("shortcut chord contains an empty key")
    key = aliases.get(parts[-1], parts[-1])
    modifiers = parts[:-1]
    allowed_modifiers = {"CTRL", "ALT", "SHIFT", "COMMAND", "WIN"}
    if any(item not in allowed_modifiers for item in modifiers) or len(set(modifiers)) != len(modifiers):
        raise ShortcutRegistryError("shortcut chord contains unsupported or repeated modifiers")
    named_keys = {
        "SPACE",
        "ENTER",
        "ESC",
        "TAB",
        "HOME",
        "END",
        "PGUP",
        "PGDN",
        "LEFT",
        "RIGHT",
        "UP",
        "DOWN",
    }
    allowed_key = key in named_keys or bool(re.fullmatch(r"F(?:[1-9]|1[0-9]|2[0-4])", key)) or bool(
        re.fullmatch(r"[A-Z0-9]", key)
    )
    if not allowed_key:
        raise ShortcutRegistryError(f"unsupported shortcut key: {key}")
    if not modifiers and bool(re.fullmatch(r"[A-Z0-9]", key)):
        raise ShortcutRegistryError("bare printable keys cannot be learned")
    if "WIN" in modifiers:
        raise ShortcutRegistryError("global Win/Super shortcuts cannot be learned")
    if normalized_platform == "macos" and "CTRL" in modifiers:
        raise ShortcutRegistryError("macOS learned shortcuts must use Command rather than ambiguous Ctrl")
    if normalized_platform != "macos" and "COMMAND" in modifiers:
        raise ShortcutRegistryError("Command shortcuts are only valid on macOS")
    modifier_set = set(modifiers)
    dangerous = (
        key in {"DELETE", "BACKSPACE"}
        or (key == "F4" and "ALT" in modifier_set)
        or (normalized_platform == "macos" and key in {"Q", "W"} and "COMMAND" in modifier_set)
        or (key == "TAB" and "ALT" in modifier_set)
        or ({"CTRL", "ALT"}.issubset(modifier_set) and key == "DELETE")
        or ({"CTRL", "SHIFT"}.issubset(modifier_set) and key == "DELETE")
    )
    if dangerous:
        raise ShortcutRegistryError("system, close, or destructive shortcut chords cannot be learned")

    ordered_modifiers = [item for item in ("CTRL", "ALT", "SHIFT", "COMMAND") if item in modifier_set]
    display_modifiers = {
        "CTRL": "Ctrl",
        "ALT": "Alt",
        "SHIFT": "Shift",
        "COMMAND": "Command",
    }
    display = "+".join(
        [
            *[display_modifiers[item] for item in ordered_modifiers],
            "Space" if key == "SPACE" else key.title() if key in named_keys else key,
        ]
    )
    prefix_map = {
        "CTRL": "^",
        "ALT": "%",
        "SHIFT": "+",
        "COMMAND": "^",
    }
    prefix = "".join(prefix_map[item] for item in ordered_modifiers)
    driver_key = key.lower() if len(key) == 1 and key.isalpha() else key
    if key in named_keys or key.startswith("F"):
        driver_key = "{" + key + "}"
    return {"displaySequence": display, "driverSequence": prefix + driver_key}


class ComputerUseShortcutRegistry:
    """Read-only shortcut truth for the model-driven Computer Use executor.

    The registry deliberately separates platform conventions from app-specific
    guides. Only entries matching the current platform and detected application
    are projected into the model context; raw catalog files never become prompt
    payloads.
    """

    def __init__(
        self,
        *,
        system_catalog_path: Path | None = None,
        application_catalog_path: Path | None = None,
        learned_profile_loader: Callable[[], Dict[str, Any]] | None = None,
        learned_profile_saver: Callable[[Dict[str, Any]], None] | None = None,
    ) -> None:
        self.system_catalog_path = Path(system_catalog_path or _SYSTEM_CATALOG_PATH)
        self.application_catalog_path = Path(application_catalog_path or _APPLICATION_CATALOG_PATH)
        self.system_catalog = self._read_json(self.system_catalog_path)
        self.application_catalog = self._read_json(self.application_catalog_path)
        self._learned_profile_loader = learned_profile_loader or _load_learned_profiles
        self._learned_profile_saver = learned_profile_saver or _save_learned_profiles
        self.schema_version = max(
            int(self.system_catalog.get("schemaVersion") or 0),
            int(self.application_catalog.get("schemaVersion") or 0),
        )
        self._validate()

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ShortcutRegistryError(f"Unable to read shortcut catalog: {path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ShortcutRegistryError(f"Shortcut catalog must be a JSON object: {path}")
        return payload

    def _validate(self) -> None:
        if self.schema_version != 1:
            raise ShortcutRegistryError(f"Unsupported shortcut catalog schema: {self.schema_version}")
        platforms = self.system_catalog.get("platforms")
        if not isinstance(platforms, dict):
            raise ShortcutRegistryError("system shortcut catalog is missing platforms")
        for platform in ("windows", "linux", "macos"):
            entries = platforms.get(platform)
            if not isinstance(entries, list) or not entries:
                raise ShortcutRegistryError(f"system shortcut catalog is missing {platform}")
            self._validate_entries(entries, scope=f"system:{platform}")
        applications = self.application_catalog.get("applications")
        if not isinstance(applications, list):
            raise ShortcutRegistryError("application shortcut catalog is missing applications")
        guide_ids: set[str] = set()
        for application in applications:
            if not isinstance(application, dict):
                raise ShortcutRegistryError("application shortcut entry must be an object")
            guide_id = str(application.get("guideId") or "").strip()
            if not guide_id or guide_id in guide_ids:
                raise ShortcutRegistryError(f"invalid or duplicate application guideId: {guide_id!r}")
            guide_ids.add(guide_id)
            platform_entries = application.get("platforms")
            if not isinstance(platform_entries, dict):
                raise ShortcutRegistryError(f"application guide {guide_id} is missing platforms")
            for platform, payload in platform_entries.items():
                shortcuts = dict(payload or {}).get("shortcuts") if isinstance(payload, dict) else None
                if not isinstance(shortcuts, list) or not shortcuts:
                    raise ShortcutRegistryError(f"application guide {guide_id}:{platform} is missing shortcuts")
                self._validate_entries(shortcuts, scope=f"application:{guide_id}:{platform}")

    @staticmethod
    def _validate_entries(entries: List[Any], *, scope: str) -> None:
        seen: set[str] = set()
        for raw in entries:
            if not isinstance(raw, dict):
                raise ShortcutRegistryError(f"shortcut entry must be an object: {scope}")
            shortcut_id = str(raw.get("id") or "").strip()
            action = str(raw.get("action") or "").strip()
            sequence = str(raw.get("driverSequence") or "").strip()
            if not shortcut_id or shortcut_id in seen or not action or not sequence:
                raise ShortcutRegistryError(f"invalid or duplicate shortcut entry: {scope}:{shortcut_id!r}")
            seen.add(shortcut_id)
            preconditions = _unique_strings(raw.get("preconditions") or [])
            if len(sequence) == 1 and sequence.isprintable():
                required = {"window_focused", "not_text_input"}
                if not required.issubset(set(preconditions)):
                    raise ShortcutRegistryError(
                        f"printable shortcut requires focus and text-input guards: {scope}:{shortcut_id}"
                    )

    def _system_entries(self, platform: str) -> List[Dict[str, Any]]:
        entries = dict(self.system_catalog.get("platforms") or {}).get(platform) or []
        return [deepcopy(item) for item in entries if isinstance(item, dict)]

    def _learned_applications(self) -> List[Dict[str, Any]]:
        try:
            payload = self._learned_profile_loader()
        except Exception:
            return []
        applications = dict(payload or {}).get("applications")
        values = list(applications.values()) if isinstance(applications, dict) else list(applications or [])
        result: List[Dict[str, Any]] = []
        for raw in values:
            if not isinstance(raw, dict):
                continue
            application = deepcopy(raw)
            application["catalogSource"] = "learned"
            try:
                platform_entries = application.get("platforms")
                if not str(application.get("guideId") or "").strip() or not isinstance(platform_entries, dict):
                    continue
                for platform, platform_payload in platform_entries.items():
                    shortcuts = dict(platform_payload or {}).get("shortcuts") if isinstance(platform_payload, dict) else None
                    if not isinstance(shortcuts, list) or not shortcuts:
                        raise ShortcutRegistryError(f"learned shortcut profile is missing {platform}")
                    self._validate_entries(shortcuts, scope=f"learned:{application.get('guideId')}:{platform}")
            except ShortcutRegistryError:
                continue
            result.append(application)
        return result

    def _application_entries(self) -> List[Dict[str, Any]]:
        built_in: List[Dict[str, Any]] = []
        for raw in list(self.application_catalog.get("applications") or []):
            if isinstance(raw, dict):
                item = deepcopy(raw)
                item["catalogSource"] = "built_in"
                built_in.append(item)
        return [*built_in, *self._learned_applications()]

    @staticmethod
    def _application_tokens(app: Dict[str, Any] | None) -> set[str]:
        payload = dict(app or {})
        values: List[Any] = [
            payload.get("appId"),
            payload.get("profileId"),
            payload.get("displayName"),
            *list(payload.get("aliases") or []),
            *list(payload.get("processNames") or []),
            *list(payload.get("titlePatterns") or []),
        ]
        for window in list(payload.get("runningWindows") or []):
            if isinstance(window, dict):
                values.extend((window.get("title"), window.get("processName"), window.get("className")))
        return {_normalized_token(value) for value in values if _normalized_token(value)}

    def _matching_applications(self, app: Dict[str, Any] | None, platform: str) -> List[Dict[str, Any]]:
        app_tokens = self._application_tokens(app)
        if not app_tokens:
            return []
        ranked: List[tuple[int, int, Dict[str, Any]]] = []
        for application in self._application_entries():
            if not isinstance(application, dict) or platform not in dict(application.get("platforms") or {}):
                continue
            match = dict(application.get("match") or {})
            exact_tokens = {
                _normalized_token(value)
                for value in [*list(match.get("appIds") or []), *list(match.get("processNames") or [])]
                if _normalized_token(value)
            }
            title_tokens = {
                _normalized_token(value)
                for value in list(match.get("titlePatterns") or [])
                if _normalized_token(value)
            }
            exact_hits = len(app_tokens & exact_tokens)
            title_hits = sum(
                1
                for expected in title_tokens
                if any(expected in observed or observed in expected for observed in app_tokens)
            )
            score = exact_hits * 10 + title_hits * 3
            if score > 0:
                learned_priority = 1 if str(application.get("catalogSource") or "") == "learned" else 0
                ranked.append((score, learned_priority, application))
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [deepcopy(item[2]) for item in ranked]

    def _matching_application(self, app: Dict[str, Any] | None, platform: str) -> Dict[str, Any] | None:
        matches = self._matching_applications(app, platform)
        return matches[-1] if matches else None

    def guide_for(
        self,
        *,
        app: Dict[str, Any] | None = None,
        platform: str | None = None,
    ) -> Dict[str, Any]:
        resolved_platform = normalize_shortcut_platform(platform)
        entries = self._system_entries(resolved_platform)
        by_id = {str(item.get("id")): item for item in entries}
        has_application_identity = bool(self._application_tokens(app))
        matches = self._matching_applications(app, resolved_platform)
        matched_payload: Dict[str, Any] | None = None
        contributing_guide_ids: List[str] = []
        application_actions: List[str] = []
        for matched in matches:
            platform_payload = dict(dict(matched.get("platforms") or {}).get(resolved_platform) or {})
            for item in list(platform_payload.get("shortcuts") or []):
                if isinstance(item, dict):
                    by_id[str(item.get("id"))] = deepcopy(item)
                    application_actions.append(str(item.get("action") or "").strip())
            guide_id = str(matched.get("guideId") or "").strip()
            if guide_id:
                contributing_guide_ids.append(guide_id)
            matched_payload = {
                "guideId": guide_id,
                "displayName": matched.get("displayName"),
                "testedVersions": list(platform_payload.get("testedVersions") or []),
                "source": matched.get("catalogSource") or "built_in",
            }
        shortcuts: List[Dict[str, Any]] = []
        for shortcut_id, item in by_id.items():
            source = dict(item.get("source") or {})
            shortcuts.append(
                {
                    "id": shortcut_id,
                    "action": item.get("action"),
                    "keys": item.get("displaySequence"),
                    "requires": list(item.get("preconditions") or []),
                    "stateChangeRequired": bool(item.get("stateChangeRequired")),
                    "preferBeforeCoordinates": bool(item.get("preferBeforeCoordinates")),
                    "dispatchTool": item.get("dispatchTool") or "desktop_shortcut",
                    "confidence": item.get("confidence") or "reviewed",
                    "provenance": source.get("kind") or "unknown",
                }
            )
        available_actions = _unique_strings(application_actions)
        if matched_payload:
            action_summary = ", ".join(available_actions) if available_actions else "none"
            application_warning = (
                f"The bound app shortcut profile is partial and currently covers only: {action_summary}. "
                "If another app-specific action is needed, call desktop_shortcut_research before repeating "
                "an unverified coordinate action."
            )
        elif has_application_identity:
            application_warning = (
                "No verified app-specific shortcut profile is bound. When a hidden or transient control "
                "matters, call desktop_shortcut_research for that action before using coordinates."
            )
        else:
            application_warning = None
        return {
            "schemaVersion": self.schema_version,
            "platform": resolved_platform,
            "priorityOrder": list(self.system_catalog.get("priorityOrder") or []),
            "matchedApplication": matched_payload,
            "applicationProfile": {
                "status": "bound" if matched_payload else "missing" if has_application_identity else "unavailable",
                "guideId": (matched_payload or {}).get("guideId"),
                "source": (matched_payload or {}).get("source"),
                "contributingGuideIds": _unique_strings(contributing_guide_ids),
                "availableActions": available_actions,
                "researchOnMissingAction": bool(has_application_identity),
                "warning": application_warning,
            },
            "shortcuts": shortcuts,
        }

    def resolve(
        self,
        shortcut_id: str,
        *,
        app: Dict[str, Any] | None = None,
        platform: str | None = None,
    ) -> Dict[str, Any]:
        requested_id = str(shortcut_id or "").strip()
        if not requested_id:
            raise ShortcutRegistryError("shortcut_id is required")
        resolved_platform = normalize_shortcut_platform(platform)
        matches = self._matching_applications(app, resolved_platform)
        for matched in reversed(matches):
            platform_payload = dict(dict(matched.get("platforms") or {}).get(resolved_platform) or {})
            for raw in list(platform_payload.get("shortcuts") or []):
                if isinstance(raw, dict) and str(raw.get("id") or "") == requested_id:
                    return self._resolved_entry(
                        raw,
                        platform=resolved_platform,
                        scope="application",
                        guide_id=str(matched.get("guideId") or ""),
                        tested_versions=list(platform_payload.get("testedVersions") or []),
                    )
        for raw in self._system_entries(resolved_platform):
            if str(raw.get("id") or "") == requested_id:
                return self._resolved_entry(
                    raw,
                    platform=resolved_platform,
                    scope="system",
                    guide_id=None,
                    tested_versions=[],
                )
        raise ShortcutRegistryError(
            f"shortcut {requested_id!r} is not registered for {resolved_platform} and the detected application"
        )

    def learn_verified_shortcut(
        self,
        *,
        app: Dict[str, Any],
        platform: str | None,
        shortcut_id: str,
        action: str,
        keys: str,
        source_url: str,
        verification: Dict[str, Any],
    ) -> Dict[str, Any]:
        resolved_platform = normalize_shortcut_platform(platform)
        app_id = str(app.get("appId") or "").strip()
        display_name = str(app.get("displayName") or app_id).strip()
        process_names = _unique_strings(
            [
                *list(app.get("processNames") or []),
                *[
                    dict(item).get("processName")
                    for item in list(app.get("runningWindows") or [])
                    if isinstance(item, dict)
                ],
            ]
        )
        if not app_id or not process_names:
            raise ShortcutRegistryError("learned shortcuts require an exact appId and observed process name")
        parsed_url = urlparse(str(source_url or "").strip())
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ShortcutRegistryError("learned shortcut source_url must be an http/https page")
        normalized_id = str(shortcut_id or "").strip().lower()
        normalized_action = str(action or "").strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{2,63}", normalized_id):
            raise ShortcutRegistryError("shortcut_id must be a stable semantic identifier")
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", normalized_action):
            raise ShortcutRegistryError("action must be a stable semantic identifier")
        compiled = compile_human_shortcut(keys, platform=resolved_platform)
        verification_payload = dict(verification or {})
        details = dict(verification_payload.get("details") or {})
        if not verification_payload.get("passed") or not details.get("stateChanged"):
            raise ShortcutRegistryError("shortcut binding requires verified application state change")
        now = _utc_now_iso()
        safe_app = re.sub(r"[^a-z0-9]+", "-", app_id.lower()).strip("-") or "application"
        guide_id = f"learned.{safe_app}.{resolved_platform}"
        shortcut = {
            "id": normalized_id,
            "action": normalized_action,
            **compiled,
            "preconditions": ["window_focused", "not_text_input", "lock_screen_absent"],
            "intentHints": _unique_strings([normalized_action.replace("_", " "), normalized_id.replace(".", " ")]),
            "coordinatePriorityHints": [],
            "preferBeforeCoordinates": True,
            "stateChangeRequired": True,
            "confidence": "runtime_verified",
            "source": {
                "kind": "runtime_verified_web_guide",
                "url": str(source_url).strip(),
                "verifiedAt": now,
            },
        }
        self._validate_entries([shortcut], scope=f"learned:{guide_id}:{resolved_platform}")
        with _LEARNED_PROFILE_LOCK:
            try:
                payload = deepcopy(self._learned_profile_loader())
            except Exception:
                payload = {}
            payload["version"] = 1
            applications = payload.get("applications")
            if not isinstance(applications, dict):
                applications = {}
            profile = deepcopy(applications.get(guide_id) or {})
            profile.update(
                {
                    "guideId": guide_id,
                    "displayName": display_name,
                    "catalogSource": "learned",
                    "updatedAt": now,
                    "match": {
                        "appIds": [app_id],
                        "processNames": [str(item).lower() for item in process_names],
                        "titlePatterns": _unique_strings([display_name]),
                    },
                }
            )
            platforms = dict(profile.get("platforms") or {})
            platform_payload = dict(platforms.get(resolved_platform) or {})
            shortcuts = [
                deepcopy(item)
                for item in list(platform_payload.get("shortcuts") or [])
                if isinstance(item, dict) and str(item.get("id") or "") != normalized_id
            ]
            shortcuts.append(shortcut)
            platform_payload["shortcuts"] = shortcuts[-_LEARNED_SHORTCUT_LIMIT:]
            platform_payload["lastVerifiedAt"] = now
            platforms[resolved_platform] = platform_payload
            profile["platforms"] = platforms
            applications[guide_id] = profile
            if len(applications) > _LEARNED_PROFILE_LIMIT:
                ordered = sorted(
                    applications.items(),
                    key=lambda item: str(dict(item[1] or {}).get("updatedAt") or ""),
                    reverse=True,
                )
                applications = dict(ordered[:_LEARNED_PROFILE_LIMIT])
            payload["applications"] = applications
            self._learned_profile_saver(payload)
        return {
            "guideId": guide_id,
            "appId": app_id,
            "platform": resolved_platform,
            "shortcutId": normalized_id,
            "keys": compiled["displaySequence"],
            "sourceUrl": str(source_url).strip(),
            "verifiedAt": now,
        }

    def preferred_for_target(
        self,
        target: str,
        *,
        app: Dict[str, Any] | None = None,
        platform: str | None = None,
    ) -> Dict[str, Any] | None:
        normalized_target = str(target or "").strip().lower()
        if not normalized_target:
            return None
        guide = self.guide_for(app=app, platform=platform)
        ordered_ids = [str(item.get("id") or "") for item in list(guide.get("shortcuts") or [])]
        for shortcut_id in ordered_ids:
            try:
                resolved = self.resolve(shortcut_id, app=app, platform=platform)
            except ShortcutRegistryError:
                continue
            if not bool(resolved.get("preferBeforeCoordinates")):
                continue
            hints = [
                str(item or "").strip().lower()
                for item in list(resolved.get("coordinatePriorityHints") or [])
            ]
            exclusions = [
                str(item or "").strip().lower()
                for item in list(resolved.get("coordinateExclusionHints") or [])
            ]
            if any(item and item in normalized_target for item in exclusions):
                continue
            if any(hint and hint in normalized_target for hint in hints):
                return resolved
        return None

    def _resolved_entry(
        self,
        raw: Dict[str, Any],
        *,
        platform: str,
        scope: str,
        guide_id: str | None,
        tested_versions: List[Any],
    ) -> Dict[str, Any]:
        source = dict(raw.get("source") or {})
        return {
            **deepcopy(raw),
            "registrySchemaVersion": self.schema_version,
            "platform": platform,
            "scope": scope,
            "guideId": guide_id,
            "testedVersions": _unique_strings(tested_versions),
            "source": source,
        }


shortcut_registry = ComputerUseShortcutRegistry()
