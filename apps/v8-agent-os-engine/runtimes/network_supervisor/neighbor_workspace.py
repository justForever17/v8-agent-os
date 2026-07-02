from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from core.workspace_resolution import workspace_resolution_service


def _slug(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip(".-_")
    return (normalized or fallback)[:80]


def _short_hash(value: str) -> str:
    return hashlib.sha1(str(value or "").encode("utf-8")).hexdigest()[:10]


def resolve_network_neighbor_workspace_binding(
    *,
    peer_id: str,
    local_role: str | None = None,
    remote_project_id: str | None = None,
    remote_workspace_id: str | None = None,
    remote_workspace_path: str | None = None,
    configured_binding: dict[str, Any] | None = None,
    create: bool = True,
) -> dict[str, Any]:
    """Map a remote workspace hint to a local safe workspace binding.

    Remote filesystem paths are never reused as local execution paths. They are
    retained only as source metadata so Windows/Linux/macOS peers do not leak
    each other's filesystem semantics into local runtime execution.
    """

    configured = dict(configured_binding or {})
    configured_path = str(
        configured.get("workspacePath")
        or configured.get("localWorkspacePath")
        or ""
    ).strip()
    configured_workspace_id = str(
        configured.get("workspaceId")
        or configured.get("localWorkspaceId")
        or ""
    ).strip()
    configured_project_id = str(
        configured.get("projectId")
        or configured.get("localProjectId")
        or ""
    ).strip()

    if configured_path:
        descriptor = workspace_resolution_service.resolve_workspace_descriptor(
            runtime_kind="chat",
            explicit_project_id=configured_project_id or None,
            explicit_workspace_id=configured_workspace_id or None,
            explicit_workspace_path=configured_path,
        )
        local_path = str(descriptor.get("workspaceRoot") or configured_path)
        if create:
            Path(local_path).expanduser().mkdir(parents=True, exist_ok=True)
        return {
            "projectId": str(descriptor.get("projectId") or configured_project_id or "").strip() or None,
            "workspaceId": str(descriptor.get("workspaceId") or configured_workspace_id or "").strip() or None,
            "workspacePath": local_path,
            "source": "network_configured_workspace",
            "remoteProjectId": str(remote_project_id or "").strip() or None,
            "remoteWorkspaceId": str(remote_workspace_id or "").strip() or None,
            "remoteWorkspacePath": str(remote_workspace_path or "").strip() or None,
            "localRole": str(local_role or "").strip() or None,
        }

    remote_workspace_id = str(remote_workspace_id or "").strip()
    remote_project_id = str(remote_project_id or "").strip()
    remote_workspace_path = str(remote_workspace_path or "").strip()
    has_remote_scope = bool(remote_workspace_id or remote_project_id or remote_workspace_path)
    if not has_remote_scope:
        descriptor = workspace_resolution_service.resolve_workspace_descriptor(runtime_kind="chat")
        local_path = str(descriptor.get("workspaceRoot") or workspace_resolution_service.get_main_workspace_path())
        if create:
            Path(local_path).expanduser().mkdir(parents=True, exist_ok=True)
        return {
            "projectId": str(descriptor.get("projectId") or "").strip() or None,
            "workspaceId": str(descriptor.get("workspaceId") or "").strip() or None,
            "workspacePath": local_path,
            "source": "local_default_workspace",
            "remoteProjectId": None,
            "remoteWorkspaceId": None,
            "remoteWorkspacePath": None,
            "localRole": str(local_role or "").strip() or None,
        }

    main_path = Path(workspace_resolution_service.get_main_workspace_path()).expanduser()
    peer_slug = _slug(peer_id, fallback="peer")
    remote_key = remote_workspace_id or remote_project_id or _short_hash(remote_workspace_path)
    workspace_slug = _slug(remote_key, fallback=f"workspace-{_short_hash(remote_workspace_path or peer_id)}")
    local_workspace_id = f"network-{peer_slug}-{workspace_slug}"[:120]
    local_path = main_path / "network" / peer_slug / workspace_slug
    if create:
        local_path.mkdir(parents=True, exist_ok=True)
    return {
        "projectId": None,
        "workspaceId": local_workspace_id,
        "workspacePath": str(local_path),
        "source": "network_compatible_workspace",
        "remoteProjectId": remote_project_id or None,
        "remoteWorkspaceId": remote_workspace_id or None,
        "remoteWorkspacePath": remote_workspace_path or None,
        "localRole": str(local_role or "").strip() or None,
    }
