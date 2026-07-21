from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .schema import PluginConfigRequirement, PluginManifest


_SECRET_HINT = re.compile(r"(api[_-]?key|token|secret|password|authorization|credential|private[_-]?key|pat)", re.I)
_URL_HINT = re.compile(r"(url|endpoint|host|origin)$", re.I)
_PLACEHOLDER_PATTERNS = (
    re.compile(r"\$\{(?:input:)?([A-Za-z_][A-Za-z0-9_.-]*)\}"),
    re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}"),
)


def _placeholder_names(value: Any) -> list[str]:
    text = str(value or "")
    result: list[str] = []
    for pattern in _PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(text):
            name = match.group(1)
            if name not in result:
                result.append(name)
    return result


def _field_kind(name: str) -> str:
    normalized = str(name or "").strip()
    if normalized.lower() == "oauth":
        return "oauth"
    if _SECRET_HINT.search(normalized):
        return "secret"
    if _URL_HINT.search(normalized):
        return "url"
    return "text"


def _requirement_id(component_id: str, target: str, name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(name or "").strip()).strip("-").lower()
    return f"{component_id}.{target}.{safe_name}"


def _inferred_requirement(
    *,
    component_id: str,
    target: str,
    name: str,
    source: str,
    confidence: str,
) -> PluginConfigRequirement:
    kind = _field_kind(name)
    return PluginConfigRequirement(
        id=_requirement_id(component_id, target, name),
        kind=kind,
        source=source,
        confidence=confidence,
        labelKey=f"plugins.config.{name}",
        helpKey=f"plugins.configHelp.{name}",
        target="oauth" if kind == "oauth" else target,
        targetName=None if kind == "oauth" else name,
        componentId=component_id,
        importSources=[f"env:{name}"] if target == "env" and kind != "oauth" else [],
    )


def _walk_template(component_id: str, value: Any, path: tuple[str, ...] = ()) -> Iterable[PluginConfigRequirement]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_template(component_id, item, (*path, str(key)))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_template(component_id, item, (*path, str(index)))
        return
    target = "config"
    target_name = path[-1] if path else "value"
    if path and path[0] == "env":
        target, target_name = "env", path[-1]
    elif path and path[0] == "headers":
        target, target_name = "header", path[-1]
    elif path and path[0] == "url":
        target = "url"
    elif path and path[0] == "args":
        target = "arg"
    for placeholder in _placeholder_names(value):
        yield _inferred_requirement(
            component_id=component_id,
            target=target,
            name=placeholder or target_name,
            source="mcp_schema",
            confidence="authoritative",
        )


def compile_plugin_requirements(
    manifest: PluginManifest,
    *,
    component_ids: Iterable[str] | None = None,
) -> list[PluginConfigRequirement]:
    """Compile one deterministic configuration contract without reading credentials.

    Explicit signed-manifest requirements win. Legacy ``authFields`` are treated as
    authoritative manifest requirements. Template placeholders are authoritative
    MCP schema requirements. CLI login availability is only a non-blocking hint.
    """

    result: dict[str, PluginConfigRequirement] = {}

    def add(item: PluginConfigRequirement) -> None:
        current = result.get(item.id)
        rank = {"hint": 0, "reviewed": 1, "authoritative": 2}
        if current is None or rank[item.confidence] > rank[current.confidence]:
            result[item.id] = item

    for server in manifest.mcpServers:
        for item in server.configRequirements:
            add(item.model_copy(update={"componentId": item.componentId or server.id}))
        for field in server.authFields:
            target = "oauth" if str(field).strip().lower() == "oauth" else "env"
            add(
                _inferred_requirement(
                    component_id=server.id,
                    target=target,
                    name=str(field).strip(),
                    source="manifest",
                    confidence="authoritative",
                )
            )
        for item in _walk_template(server.id, server.configTemplate):
            add(item)

    for profile in manifest.cliProfiles:
        for item in profile.configRequirements:
            add(item.model_copy(update={"componentId": item.componentId or profile.id}))
        if profile.login and not profile.configRequirements:
            add(
                PluginConfigRequirement(
                    id=f"{profile.id}.cli.login",
                    kind="oauth",
                    required=False,
                    source="hint",
                    confidence="hint",
                    labelKey="plugins.config.cliLogin",
                    helpKey="plugins.configHelp.cliLogin",
                    target="oauth",
                    componentId=profile.id,
                )
            )
    for adapter in manifest.providerAdapters:
        for item in adapter.configRequirements:
            add(item.model_copy(update={"componentId": item.componentId or adapter.id}))
    selected_components = {
        str(item).strip()
        for item in list(component_ids or [])
        if str(item).strip()
    }
    values = list(result.values())
    if component_ids is not None:
        values = [item for item in values if str(item.componentId or "") in selected_components]
    return sorted(values, key=lambda item: (item.componentId or "", item.id))


@dataclass(frozen=True, slots=True)
class CredentialDiscovery:
    source_id: str
    kind: str
    present: bool
    display_path: str | None = None

    def public_payload(self) -> dict[str, Any]:
        return {
            "sourceId": self.source_id,
            "kind": self.kind,
            "present": self.present,
            "displayPath": self.display_path,
        }


def discover_requirement_sources(requirement: PluginConfigRequirement) -> list[CredentialDiscovery]:
    """Presence-only discovery. This function never reads a credential value."""

    discoveries: list[CredentialDiscovery] = []
    for source in requirement.importSources:
        if source.startswith("env:"):
            name = source[4:]
            discoveries.append(CredentialDiscovery(source, "environment", name in os.environ))
        elif source.startswith("file:"):
            path = Path(source[5:]).expanduser()
            discoveries.append(CredentialDiscovery(source, "file", path.is_file(), str(path)))
    return discoveries


def read_explicit_import_source(source_id: str) -> str:
    """Read a previously advertised source only after an explicit import request."""

    normalized = str(source_id or "").strip()
    if normalized.startswith("env:"):
        name = normalized[4:]
        if not name or name not in os.environ:
            raise ValueError("credential import source is no longer available")
        return str(os.environ[name])
    raise ValueError("unsupported credential import source")
