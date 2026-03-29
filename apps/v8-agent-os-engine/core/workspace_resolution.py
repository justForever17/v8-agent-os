from __future__ import annotations

from pathlib import Path
from typing import Optional

from core.storage import storage
from core.v8_agent_os_paths import WORKSPACE_HOME
from runtimes.memory.scope_resolution import session_scope_binding_service


_SCOPED_WORKSPACE_RUNTIMES = {
    "chat",
    "computer_use",
    "rpa",
    "automation",
    "automation_agent",
}

_MAIN_WORKSPACE_RUNTIMES = {
    "plugin_host",
    "plugin_host_push",
}

_LEGACY_MAIN_WORKSPACE_RUNTIME_ALIASES = {
    "channel": "plugin_host",
    "channel_chat": "plugin_host",
    "channel_push": "plugin_host_push",
}


class WorkspaceResolutionService:
    def get_main_workspace_path(self) -> str:
        configured = str(storage.get_workspace_config().get("agent_workspace_path") or "").strip()
        if configured:
            return str(Path(configured).expanduser())
        return str(WORKSPACE_HOME.expanduser())

    def runtime_uses_scoped_workspace(self, runtime_kind: str | None) -> bool:
        normalized = str(runtime_kind or "").strip().lower()
        normalized = _LEGACY_MAIN_WORKSPACE_RUNTIME_ALIASES.get(normalized, normalized)
        if normalized in _MAIN_WORKSPACE_RUNTIMES:
            return False
        return normalized in _SCOPED_WORKSPACE_RUNTIMES

    def resolve_workspace_path(
        self,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_path: str | None = None,
    ) -> str:
        explicit = str(explicit_workspace_path or "").strip()
        if explicit:
            return str(Path(explicit).expanduser())

        if session_id and self.runtime_uses_scoped_workspace(runtime_kind):
            binding = session_scope_binding_service.get_binding(session_id)
            scoped = str(getattr(binding, "workspace_path", "") or "").strip() if binding else ""
            if scoped:
                return str(Path(scoped).expanduser())

        return self.get_main_workspace_path()

    def build_workspace_view(
        self,
        *,
        runtime_kind: str | None = None,
        session_id: str | None = None,
        explicit_workspace_path: str | None = None,
    ) -> dict:
        main_path = self.get_main_workspace_path()
        resolved_path = self.resolve_workspace_path(
            runtime_kind=runtime_kind,
            session_id=session_id,
            explicit_workspace_path=explicit_workspace_path,
        )
        return {
            "runtimeKind": _LEGACY_MAIN_WORKSPACE_RUNTIME_ALIASES.get(str(runtime_kind or "").strip().lower(), str(runtime_kind or "").strip().lower()) or None,
            "mainWorkspacePath": main_path,
            "resolvedWorkspacePath": resolved_path,
            "usesScopedWorkspace": self.runtime_uses_scoped_workspace(runtime_kind),
            "isScopedOverride": Path(resolved_path) != Path(main_path),
            "pluginHostUsesMainWorkspace": True,
        }


workspace_resolution_service = WorkspaceResolutionService()
