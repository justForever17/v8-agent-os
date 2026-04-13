from __future__ import annotations

import os
import platform
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Protocol


def _normalize(value: Any) -> str:
    return re.sub(r"[\s_\-:./\\]+", "", str(value or "").strip().lower())


def _basename(value: Any) -> str:
    token = str(value or "").strip().replace("\\", "/").split("/")[-1]
    return token.lower()


def _split_vscode_goto_target(raw: str) -> tuple[str, int | None, int | None]:
    value = str(raw or "").strip()
    if not value:
        return "", None, None
    parts = value.rsplit(":", 2)
    if len(parts) == 1:
        return value, None, None
    if len(parts) == 2 and str(parts[-1]).isdigit():
        return str(parts[0]).strip(), int(parts[-1]), None
    if len(parts) == 3 and str(parts[-1]).isdigit() and str(parts[-2]).isdigit():
        return str(parts[0]).strip(), int(parts[-2]), int(parts[-1])
    return value, None, None


def _looks_like_vscode_uri(raw: Any) -> bool:
    value = str(raw or "").strip().lower()
    return value.startswith("vscode://") or value.startswith("vscode-insiders://")


@dataclass(slots=True)
class ComputerUseAppAdapterMatch:
    adapter_id: str
    control_class: str
    confidence: float
    reason: str
    adapter: Any | None = None
    supported_platforms: List[str] = field(default_factory=list)
    validation_level: str = "fixture_only"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "adapterId": self.adapter_id,
            "controlClass": self.control_class,
            "confidence": float(self.confidence),
            "reason": self.reason,
            "supportedPlatforms": list(self.supported_platforms),
            "validationLevel": self.validation_level,
        }


class ComputerUseAppAdapter(Protocol):
    adapter_id: str
    control_class: str
    supported_platforms: List[str]

    def match(
        self,
        *,
        app_id: str | None = None,
        explicit_app_id: str | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        process_name: str | None = None,
        process_names: Iterable[str] | None = None,
        title_patterns: Iterable[str] | None = None,
        launch_candidates: Iterable[Dict[str, Any]] | None = None,
        catalog_entry: Dict[str, Any] | None = None,
    ) -> ComputerUseAppAdapterMatch | None:
        ...

    def build_open_command(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        launch_target_path: str | None = None,
        goto_line: int | None = None,
        goto_character: int | None = None,
        profile_name: str | None = None,
        reuse_window: bool = True,
    ) -> Dict[str, Any] | None:
        ...

    def capability_summary(self) -> Dict[str, Any]:
        ...


class VSCodeAppAdapter:
    adapter_id = "vscode"
    control_class = "electron_shell_app"
    supported_platforms = ["windows", "macos", "linux"]
    _match_tokens = {
        "vscode",
        "visualstudiocode",
        "visualstudiocode.exe",
        "code",
        "code.exe",
        "codecmd",
    }

    def _resolve_cli_candidates(self) -> List[str]:
        system = platform.system().lower()
        if system == "windows":
            candidates = [
                r"D:\Program Files\Microsoft VS Code\bin\code.cmd",
                r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
                r"C:\Users\%USERNAME%\AppData\Local\Programs\Microsoft VS Code\bin\code.cmd",
                "code.cmd",
                "code",
            ]
        elif system == "darwin":
            candidates = [
                "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
                "code",
            ]
        else:
            candidates = ["code", "/usr/bin/code", "/snap/bin/code"]
        resolved: List[str] = []
        for candidate in candidates:
            expanded = os.path.expandvars(candidate)
            path = Path(expanded)
            if path.exists():
                resolved.append(str(path))
                continue
            which = shutil.which(expanded)
            if which:
                resolved.append(str(Path(which)))
        seen = set()
        ordered: List[str] = []
        for item in resolved:
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(item)
        return ordered

    def _resolve_gui_candidates(self) -> List[str]:
        system = platform.system().lower()
        if system == "windows":
            candidates = [
                r"D:\Program Files\Microsoft VS Code\Code.exe",
                r"C:\Program Files\Microsoft VS Code\Code.exe",
                r"C:\Users\%USERNAME%\AppData\Local\Programs\Microsoft VS Code\Code.exe",
            ]
        elif system == "darwin":
            candidates = ["/Applications/Visual Studio Code.app/Contents/MacOS/Electron"]
        else:
            candidates = ["/usr/share/code/code", "/usr/bin/code"]
        resolved: List[str] = []
        for candidate in candidates:
            expanded = os.path.expandvars(candidate)
            path = Path(expanded)
            if path.exists():
                resolved.append(str(path))
        return resolved

    def _match_score(self, *values: Any) -> int:
        score = 0
        for value in values:
            normalized = _normalize(value)
            if not normalized:
                continue
            if normalized in self._match_tokens:
                score = max(score, 120)
            elif any(token in normalized for token in ("visualstudiocode", "vscode")):
                score = max(score, 96)
            elif normalized in {"code", "codeexe", "codecmd"}:
                score = max(score, 72)
        return score

    def match(
        self,
        *,
        app_id: str | None = None,
        explicit_app_id: str | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        process_name: str | None = None,
        process_names: Iterable[str] | None = None,
        title_patterns: Iterable[str] | None = None,
        launch_candidates: Iterable[Dict[str, Any]] | None = None,
        catalog_entry: Dict[str, Any] | None = None,
    ) -> ComputerUseAppAdapterMatch | None:
        score = 0
        score = max(score, self._match_score(app_id, explicit_app_id, app_name, window_title, process_name))
        score = max(score, self._match_score(*(process_names or []), *(title_patterns or [])))
        entry = dict(catalog_entry or {})
        score = max(
            score,
            self._match_score(
                entry.get("appId"),
                entry.get("profileId"),
                entry.get("displayName"),
                *list(entry.get("aliases") or []),
                *list(entry.get("processNames") or []),
                *list(entry.get("titlePatterns") or []),
            ),
        )
        score = max(score, self._match_score(*[_basename((candidate or {}).get("executableName")) for candidate in list(launch_candidates or [])]))
        if score <= 0:
            return None
        validation_level = "real_host" if self.capability_summary().get("available") else "fixture_only"
        return ComputerUseAppAdapterMatch(
            adapter_id=self.adapter_id,
            control_class=self.control_class,
            confidence=min(0.99, max(0.35, score / 140.0)),
            reason="matches_vscode_surface",
            adapter=self,
            supported_platforms=list(self.supported_platforms),
            validation_level=str(validation_level),
        )

    def build_open_command(
        self,
        *,
        app_id: str | None = None,
        app_name: str | None = None,
        launch_target_path: str | None = None,
        goto_line: int | None = None,
        goto_character: int | None = None,
        profile_name: str | None = None,
        reuse_window: bool = True,
    ) -> Dict[str, Any] | None:
        cli_candidates = self._resolve_cli_candidates()
        gui_candidates = self._resolve_gui_candidates()
        executable = cli_candidates[0] if cli_candidates else (gui_candidates[0] if gui_candidates else None)
        if not executable:
            return None

        target = str(launch_target_path or "").strip()
        command: List[str] = [executable]
        target_kind = "reuse_window"
        title_hints = ["Visual Studio Code", "VS Code", "Code"]

        if profile_name:
            command.extend(["--profile", str(profile_name).strip()])
        if reuse_window:
            command.append("--reuse-window")

        if target and _looks_like_vscode_uri(target):
            gui_executable = gui_candidates[0] if gui_candidates else executable
            command = [gui_executable, "--open-url", "--", target]
            target_kind = "uri"
        elif target:
            parsed_path, parsed_line, parsed_char = _split_vscode_goto_target(target)
            effective_line = goto_line if goto_line not in (None, 0) else parsed_line
            effective_char = goto_character if goto_character not in (None, 0) else parsed_char
            path_obj = Path(parsed_path)
            if effective_line and path_obj.suffix:
                goto_target = f"{parsed_path}:{int(effective_line)}"
                if effective_char:
                    goto_target = f"{goto_target}:{int(effective_char)}"
                command.extend(["--goto", goto_target])
                target_kind = "goto"
                title_hints.insert(0, path_obj.name)
            else:
                command.append(parsed_path)
                target_kind = "path"
                title_hints.insert(0, path_obj.name or parsed_path)

        return {
            "command": command,
            "targetKind": target_kind,
            "selectionReason": f"app_adapter_{target_kind}",
            "windowTitleHints": title_hints,
            "processNames": ["code.exe"],
            "appAdapterId": self.adapter_id,
            "controlClass": self.control_class,
            "verificationHints": {
                "targetPath": target or None,
                "profileName": str(profile_name or "").strip() or None,
                "targetKind": target_kind,
            },
        }

    def capability_summary(self) -> Dict[str, Any]:
        cli_candidates = self._resolve_cli_candidates()
        gui_candidates = self._resolve_gui_candidates()
        available = bool(cli_candidates or gui_candidates)
        host_platform = platform.system().lower()
        validation_level = "real_host" if available else ("fixture_only" if host_platform in {"windows", "darwin", "linux"} else "not_validated")
        return {
            "adapterId": self.adapter_id,
            "displayName": "VS Code Adapter",
            "supportedPlatforms": list(self.supported_platforms),
            "implemented": True,
            "available": available,
            "validationLevel": validation_level,
            "controlClass": self.control_class,
            "cliCandidates": list(cli_candidates),
            "guiCandidates": list(gui_candidates),
        }


class ComputerUseAppAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: Dict[str, ComputerUseAppAdapter] = {
            "vscode": VSCodeAppAdapter(),
        }

    def list_adapters(self) -> List[ComputerUseAppAdapter]:
        return list(self._adapters.values())

    def get(self, adapter_id: str | None) -> ComputerUseAppAdapter | None:
        if not adapter_id:
            return None
        return self._adapters.get(str(adapter_id).strip().lower())

    def match(
        self,
        *,
        explicit_app_id: str | None = None,
        app_id: str | None = None,
        app_name: str | None = None,
        window_title: str | None = None,
        process_name: str | None = None,
        process_names: Iterable[str] | None = None,
        title_patterns: Iterable[str] | None = None,
        launch_candidates: Iterable[Dict[str, Any]] | None = None,
        catalog_entry: Dict[str, Any] | None = None,
    ) -> ComputerUseAppAdapterMatch | None:
        best: ComputerUseAppAdapterMatch | None = None
        for adapter in self._adapters.values():
            match = adapter.match(
                app_id=app_id,
                explicit_app_id=explicit_app_id,
                app_name=app_name,
                window_title=window_title,
                process_name=process_name,
                process_names=process_names,
                title_patterns=title_patterns,
                launch_candidates=launch_candidates,
                catalog_entry=catalog_entry,
            )
            if match is None:
                continue
            if best is None or match.confidence > best.confidence:
                best = match
        return best

    def capability_summary(self) -> Dict[str, Any]:
        payload = [adapter.capability_summary() for adapter in self._adapters.values()]
        available = any(bool(item.get("available")) for item in payload)
        validation = "real_host" if any(str(item.get("validationLevel")) == "real_host" for item in payload) else "fixture_only"
        return {
            "implemented": bool(payload),
            "available": available,
            "validationLevel": validation if payload else "not_validated",
            "adapters": payload,
        }
