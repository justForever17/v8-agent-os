from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

from core.storage import storage
from core.workspace_identity import get_main_workspace_path, workspace_path_key
from runtimes.memory.project_registry import ProjectRegistryService, project_registry_service


def workspace_identity_key(workspace_path: str) -> str:
    path_key = workspace_path_key(workspace_path)
    return f"ws_{hashlib.sha256(path_key.encode('utf-8')).hexdigest()[:16]}"


def canonical_workspace_scope(workspace_path: str | None) -> str:
    path_key = workspace_path_key(workspace_path)
    if not path_key:
        return ""
    return f"workspace:{workspace_identity_key(str(workspace_path or ''))}"


def legacy_external_workspace_scope(workspace_path: str | None) -> str:
    """Return the old path-derived alias without making it the write truth."""

    path_key = workspace_path_key(workspace_path)
    if not path_key:
        return ""
    return f"workspace:external:{hashlib.sha1(path_key.encode('utf-8')).hexdigest()[:12]}"


def workspace_directory_exists(workspace_path: str | None) -> bool:
    raw_path = str(workspace_path or "").strip()
    if not raw_path:
        return False
    try:
        return Path(raw_path).expanduser().is_dir()
    except OSError:
        return False


def _active_projects(project_registry: ProjectRegistryService) -> list[Any]:
    list_projects = getattr(project_registry, "list_projects", None)
    projects = list_projects() if callable(list_projects) else []
    return [project for project in projects if bool(getattr(project, "active", True))]


def build_workspace_scope_catalog(
    *,
    project_registry: ProjectRegistryService = project_registry_service,
) -> Dict[str, object]:
    """Project registered workspaces onto one physical-path identity.

    Raw Memory scopes remain storage aliases. They never become parallel
    user-facing workspace identities.
    """

    projects = _active_projects(project_registry)
    projects_by_path: Dict[str, list[Any]] = {}
    for project in projects:
        path_key = workspace_path_key(getattr(project, "workspace_path", None))
        if path_key:
            projects_by_path.setdefault(path_key, []).append(project)

    list_presentations = getattr(project_registry, "list_workspace_presentations", None)
    raw_presentations = list_presentations() if callable(list_presentations) else []
    presentations = {
        workspace_path_key(item.get("workspacePath")): item
        for item in raw_presentations
        if workspace_path_key(item.get("workspacePath"))
    }
    workspace_config = storage.get_workspace_config() or {}
    configured_project_id = str(
        workspace_config.get("projectId") or workspace_config.get("project_id") or ""
    ).strip()
    configured_workspace_id = str(
        workspace_config.get("workspaceId") or workspace_config.get("workspace_id") or ""
    ).strip()
    main_workspace_path = get_main_workspace_path()
    main_path_key = workspace_path_key(main_workspace_path)
    groups: Dict[str, Dict[str, object]] = {}

    def register(workspace_path: str, *, is_default: bool = False) -> Optional[Dict[str, object]]:
        path_key = workspace_path_key(workspace_path)
        if not path_key or not workspace_directory_exists(workspace_path):
            return None
        path_projects = list(projects_by_path.get(path_key, []))
        presentation = presentations.get(path_key) or {}
        display_name = str(presentation.get("displayName") or "").strip()
        if not display_name and path_projects:
            display_name = str(getattr(path_projects[0], "name", "") or "").strip()
        if not display_name:
            display_name = Path(workspace_path).name or workspace_path

        workspace_key = workspace_identity_key(workspace_path)
        group = groups.setdefault(
            workspace_key,
            {
                "workspaceKey": workspace_key,
                "workspacePath": workspace_path,
                "label": display_name,
                "isDefault": False,
                "projectId": None,
                "workspaceId": None,
                "writeScope": None,
                "_pathKey": path_key,
                "_projectIds": set(),
                "_workspaceIds": set(),
                "_scopes": set(),
                "_scopeAliases": set(),
            },
        )
        canonical_scope = canonical_workspace_scope(workspace_path)
        legacy_path_scope = legacy_external_workspace_scope(workspace_path)
        if canonical_scope:
            group["_scopes"].add(canonical_scope)  # type: ignore[union-attr]
            group["_scopeAliases"].add(canonical_scope)  # type: ignore[union-attr]
        if legacy_path_scope:
            group["_scopes"].add(legacy_path_scope)  # type: ignore[union-attr]
            group["_scopeAliases"].add(legacy_path_scope)  # type: ignore[union-attr]
        group["isDefault"] = bool(group.get("isDefault")) or is_default
        for project in path_projects:
            project_id = str(getattr(project, "project_id", "") or "").strip()
            workspace_id = str(getattr(project, "workspace_id", "") or "").strip()
            if project_id:
                group["_projectIds"].add(project_id)  # type: ignore[union-attr]
                group["_scopeAliases"].add(f"project:{project_id}")  # type: ignore[union-attr]
            if workspace_id:
                group["_workspaceIds"].add(workspace_id)  # type: ignore[union-attr]
                group["_scopeAliases"].add(f"workspace:{workspace_id}")  # type: ignore[union-attr]
        if is_default:
            # `workspace:main` was a mutable alias. Historical rows do not
            # carry enough path evidence to attach them to today's default
            # workspace, so it may resolve an old binding but is never part of
            # the automatic recall surface.
            group["_scopeAliases"].add("workspace:main")  # type: ignore[union-attr]
        return group

    # IDs are aliases only when the registry proves that they belong to this
    # physical path.  A stale workspace_config ID must never bridge two
    # different workspaces.
    register(main_workspace_path, is_default=True)

    for project in projects:
        workspace_path = str(getattr(project, "workspace_path", "") or "").strip()
        register(workspace_path, is_default=workspace_path_key(workspace_path) == main_path_key)

    items: list[Dict[str, object]] = []
    for group in groups.values():
        project_ids = sorted(str(item) for item in group["_projectIds"])  # type: ignore[arg-type]
        workspace_ids = sorted(str(item) for item in group["_workspaceIds"])  # type: ignore[arg-type]
        is_default = bool(group.get("isDefault"))
        preferred_project_id = (
            configured_project_id
            if is_default and configured_project_id in project_ids
            else (project_ids[0] if project_ids else "")
        )
        preferred_workspace_id = (
            configured_workspace_id
            if is_default and configured_workspace_id in workspace_ids
            else (workspace_ids[0] if workspace_ids else "")
        )
        group["projectId"] = preferred_project_id or None
        group["workspaceId"] = preferred_workspace_id or None
        group["writeScope"] = canonical_workspace_scope(str(group.get("workspacePath") or "")) or None
        items.append(group)

    items.sort(
        key=lambda item: (
            0 if bool(item.get("isDefault")) else 1,
            str(item.get("label") or "").lower(),
            str(item.get("workspaceKey") or ""),
        )
    )
    default_workspace_key = next(
        (str(item.get("workspaceKey") or "") for item in items if bool(item.get("isDefault"))),
        "",
    )
    return {"defaultWorkspaceKey": default_workspace_key, "items": items}


def resolve_workspace_scope_identity(
    *,
    workspace_path: str | None = None,
    workspace_key: str | None = None,
    scope_alias: str | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
    project_registry: ProjectRegistryService = project_registry_service,
) -> Optional[Dict[str, object]]:
    catalog = build_workspace_scope_catalog(project_registry=project_registry)
    items = list(catalog.get("items") or [])
    requested_path_key = workspace_path_key(workspace_path)
    requested_workspace_key = str(workspace_key or "").strip()
    requested_scope_alias = str(scope_alias or "").strip()
    requested_project_id = str(project_id or "").strip()
    requested_workspace_id = str(workspace_id or "").strip()
    # The physical path is the strongest anchor.  Never fall through to a
    # stale ID/scope alias when that path is missing or points elsewhere.
    if requested_path_key:
        return next(
            (item for item in items if str(item.get("_pathKey") or "") == requested_path_key),
            None,
        )
    if requested_workspace_key:
        return next(
            (item for item in items if str(item.get("workspaceKey") or "") == requested_workspace_key),
            None,
        )

    anchor_matches: list[set[str]] = []
    if requested_scope_alias:
        anchor_matches.append(
            {
                str(item.get("workspaceKey") or "")
                for item in items
                if requested_scope_alias in item["_scopeAliases"]  # type: ignore[operator]
            }
        )
    if requested_project_id:
        anchor_matches.append(
            {
                str(item.get("workspaceKey") or "")
                for item in items
                if requested_project_id in item["_projectIds"]  # type: ignore[operator]
            }
        )
    if requested_workspace_id:
        requested_workspace_scope = f"workspace:{requested_workspace_id}"
        anchor_matches.append(
            {
                str(item.get("workspaceKey") or "")
                for item in items
                if requested_workspace_id in item["_workspaceIds"]  # type: ignore[operator]
                or requested_workspace_scope in item["_scopeAliases"]  # type: ignore[operator]
            }
        )
    if not anchor_matches:
        return None
    matching_keys = set.intersection(*anchor_matches)
    if len(matching_keys) != 1:
        return None
    matching_key = next(iter(matching_keys))
    return next(
        (item for item in items if str(item.get("workspaceKey") or "") == matching_key),
        None,
    )


def expand_workspace_scope_chain(
    *,
    resolved_scope: str,
    workspace_path: str | None = None,
    project_id: str | None = None,
    workspace_id: str | None = None,
    project_registry: ProjectRegistryService = project_registry_service,
) -> list[str]:
    """Return the exact recall surface for one physical workspace.

    `global` is intentionally shared. Only path-derived scopes participate in
    recall. Project/workspace ids remain lookup metadata and can never become a
    parallel memory partition or bridge a renamed/rebound workspace. Missing
    workspaces do not receive stale workspace memory.
    """

    chain = ["global"]
    if workspace_path:
        if not workspace_directory_exists(workspace_path):
            return chain
        # A verified physical path is already the strongest ownership proof.
        # Avoid scanning the whole project catalog on every prompt/tool recall;
        # catalog aliases are lookup metadata and never join the read surface.
        for scope in sorted(
            item
            for item in (
                canonical_workspace_scope(workspace_path),
                legacy_external_workspace_scope(workspace_path),
            )
            if item
        ):
            if scope != "global" and scope not in chain:
                chain.append(scope)
        return chain

    identity = resolve_workspace_scope_identity(
        scope_alias=resolved_scope,
        project_id=project_id,
        workspace_id=workspace_id,
        project_registry=project_registry,
    )
    if identity is not None:
        for scope in sorted(str(item) for item in identity["_scopes"]):  # type: ignore[arg-type]
            if scope not in chain:
                chain.append(scope)
    # A caller-provided project/channel/workspace alias is never sufficient
    # evidence by itself.  Only aliases proven by the registry for this exact
    # existing directory are included above.  If the directory is missing or
    # no physical identity can be established, the safe read surface is the
    # shared global layer only.
    return chain
