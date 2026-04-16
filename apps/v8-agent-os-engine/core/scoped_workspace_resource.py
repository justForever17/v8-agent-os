from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from core.workspace_resolution import workspace_resolution_service
from runtimes.memory.project_registry import project_registry_service


_VISIBLE_WORKSPACE_PLANES = {"workspace_download", "workspace_artifact"}


@dataclass(slots=True)
class ScopedWorkspaceResource:
    workspace_root: Path
    workspace_relative_path: str
    path_plane: str
    absolute_path: Path
    workspace_id: str | None = None
    project_id: str | None = None


def normalize_workspace_relative_path(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("workspace_relative_path 不能为空。")
    if Path(raw).is_absolute() or raw.startswith(("/", "\\")):
        raise ValueError("workspace_relative_path 必须是相对路径，不能是绝对路径。")

    normalized = raw.replace("\\", "/").strip("/")
    parts = [segment for segment in normalized.split("/") if segment and segment != "."]
    if not parts:
        raise ValueError("workspace_relative_path 不能为空。")
    if any(segment == ".." for segment in parts):
        raise ValueError("workspace_relative_path 不允许包含 '..'。")

    candidate = PurePosixPath(*parts)
    normalized_path = candidate.as_posix()
    if not normalized_path or normalized_path in {".", ".."}:
        raise ValueError("workspace_relative_path 非法。")
    return normalized_path


def normalize_workspace_path_plane(value: str | None) -> str:
    normalized = str(value or "").strip()
    if normalized not in _VISIBLE_WORKSPACE_PLANES:
        raise ValueError("path_plane 仅允许 workspace_download 或 workspace_artifact。")
    return normalized


def build_client_workspace_resource_admin_path(
    *,
    workspace_relative_path: str,
    path_plane: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
) -> str:
    normalized_path = normalize_workspace_relative_path(workspace_relative_path)
    normalized_plane = normalize_workspace_path_plane(path_plane)
    query: dict[str, str] = {
        "workspace_relative_path": normalized_path,
        "path_plane": normalized_plane,
    }
    if workspace_id:
        query["workspace_id"] = str(workspace_id).strip()
    if project_id:
        query["project_id"] = str(project_id).strip()
    return f"/api/client/workspace/resource?{urlencode(query)}"


def resolve_scoped_workspace_resource(
    *,
    workspace_relative_path: str,
    path_plane: str,
    workspace_id: str | None = None,
    project_id: str | None = None,
) -> ScopedWorkspaceResource:
    normalized_relative_path = normalize_workspace_relative_path(workspace_relative_path)
    normalized_plane = normalize_workspace_path_plane(path_plane)
    normalized_workspace_id = str(workspace_id or "").strip() or None
    normalized_project_id = str(project_id or "").strip() or None

    resolved_root = ""
    resolved_project_id = normalized_project_id
    resolved_workspace_id = normalized_workspace_id

    if normalized_workspace_id:
        project = project_registry_service.find_project_for_workspace(workspace_id=normalized_workspace_id)
        if project is None or not str(project.workspace_path or "").strip():
            raise FileNotFoundError("workspace_id 未命中 allowlisted project workspace 绑定。")
        if normalized_project_id and str(project.project_id or "").strip() != normalized_project_id:
            raise PermissionError("workspace_id 与 project_id 绑定不匹配。")
        resolved_root = str(project.workspace_path or "").strip()
        resolved_project_id = str(project.project_id or "").strip() or normalized_project_id
        resolved_workspace_id = str(project.workspace_id or "").strip() or normalized_workspace_id
    elif normalized_project_id:
        project = project_registry_service.get_project(normalized_project_id)
        if project is None or not str(project.workspace_path or "").strip():
            raise FileNotFoundError("project_id 未命中 allowlisted project workspace 绑定。")
        resolved_root = str(project.workspace_path or "").strip()
        resolved_project_id = str(project.project_id or "").strip() or normalized_project_id
        resolved_workspace_id = str(project.workspace_id or "").strip() or normalized_workspace_id
    else:
        resolved_root = workspace_resolution_service.get_main_workspace_path()

    workspace_root = Path(resolved_root).expanduser().resolve(strict=False)
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise FileNotFoundError("workspace 根目录不存在或不是目录。")

    relative_target = Path(*normalized_relative_path.split("/"))
    absolute_path = (workspace_root / relative_target).resolve(strict=False)
    try:
        absolute_path.relative_to(workspace_root)
    except ValueError as exc:
        raise PermissionError("workspace_relative_path 越界，拒绝访问。") from exc

    if not absolute_path.exists() or not absolute_path.is_file():
        raise FileNotFoundError("目标文件不存在。")

    return ScopedWorkspaceResource(
        workspace_root=workspace_root,
        workspace_relative_path=normalized_relative_path,
        path_plane=normalized_plane,
        absolute_path=absolute_path,
        workspace_id=resolved_workspace_id,
        project_id=resolved_project_id,
    )


def build_workspace_resource_ref(
    *,
    workspace_relative_path: str,
    path_plane: str,
    workspace_root: str | Path,
    workspace_id: str | None = None,
    project_id: str | None = None,
    mime_type: str | None = None,
    display_label: str | None = None,
    previewable: bool | None = None,
    downloadable: bool | None = None,
    surface_visible: bool | None = None,
) -> dict[str, object]:
    normalized_relative_path = normalize_workspace_relative_path(workspace_relative_path)
    normalized_plane = normalize_workspace_path_plane(path_plane)
    normalized_workspace_id = str(workspace_id or "").strip() or None
    normalized_project_id = str(project_id or "").strip() or None
    return {
        "kind": "workspace_file",
        "workspacePath": normalized_relative_path,
        "workspaceRelativePath": normalized_relative_path,
        "workspaceRoot": str(Path(str(workspace_root)).expanduser()),
        "workspaceId": normalized_workspace_id,
        "projectId": normalized_project_id,
        "pathPlane": normalized_plane,
        "adminPath": build_client_workspace_resource_admin_path(
            workspace_relative_path=normalized_relative_path,
            path_plane=normalized_plane,
            workspace_id=normalized_workspace_id,
            project_id=normalized_project_id,
        ),
        "mimeType": mime_type,
        "displayLabel": display_label,
        "previewable": previewable,
        "downloadable": downloadable,
        "surfaceVisible": surface_visible,
    }
