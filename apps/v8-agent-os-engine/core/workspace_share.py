from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from core.scoped_workspace_resource import (
    build_workspace_resource_ref,
    normalize_workspace_relative_path,
)
from core.workspace_resolution import workspace_resolution_service
from erc.runtime_context import get_runtime_context


_SHARE_WORKSPACE_FILE_MODES = {"auto", "preview", "download"}


def viewer_kind_for_file(path: Path, mime_type: str | None) -> str:
    ext = path.suffix.lower()
    mime = str(mime_type or "").strip().lower()
    if ext == ".pdf" or mime == "application/pdf":
        return "pdf"
    if ext in {".ppt", ".pptx", ".odp"} or "powerpoint" in mime or "presentation" in mime:
        return "ppt"
    if ext in {".glb", ".gltf"} or mime in {"model/gltf-binary", "model/gltf+json"}:
        return "model"
    if ext in {".html", ".htm"} or mime in {"text/html", "application/xhtml+xml"}:
        return "html"
    return "download"


def resolve_workspace_file_to_share(path: str, mode: str) -> dict[str, Any]:
    runtime_context = get_runtime_context()
    normalized_mode = str(mode or "auto").strip().lower() or "auto"
    if normalized_mode not in _SHARE_WORKSPACE_FILE_MODES:
        raise ValueError("mode 仅允许 auto、preview 或 download。")

    descriptor = workspace_resolution_service.resolve_workspace_descriptor(
        runtime_kind=str(runtime_context.get("runtime_kind") or "chat").strip() or "chat",
        session_id=str(runtime_context.get("session_id") or "").strip() or None,
        explicit_workspace_id=str(runtime_context.get("workspace_id") or "").strip() or None,
        explicit_project_id=str(runtime_context.get("project_id") or "").strip() or None,
        explicit_workspace_path=str(runtime_context.get("workspace_path") or "").strip() or None,
    )
    workspace_root = Path(str(descriptor.get("workspaceRoot") or "")).expanduser().resolve(strict=False)
    if not workspace_root.exists() or not workspace_root.is_dir():
        raise FileNotFoundError("当前 workspace 根目录不存在或不是目录。")

    main_workspace_root = Path(workspace_resolution_service.get_main_workspace_path()).expanduser().resolve(strict=False)
    workspace_id = str(descriptor.get("workspaceId") or "").strip() or None
    project_id = str(descriptor.get("projectId") or "").strip() or None
    if workspace_root != main_workspace_root and not (workspace_id or project_id):
        raise PermissionError("当前 project workspace 缺少 allowlisted workspace_id/project_id 绑定，拒绝生成远程 URL。")

    raw_path = str(path or "").strip()
    if not raw_path:
        raise ValueError("path 不能为空。")
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        absolute_path = candidate.resolve(strict=False)
        try:
            relative_path = absolute_path.relative_to(workspace_root)
        except ValueError as exc:
            raise PermissionError("目标文件不在当前 workspace/project workspace 内，拒绝分享。") from exc
        workspace_relative_path = relative_path.as_posix()
    else:
        workspace_relative_path = normalize_workspace_relative_path(raw_path)
        absolute_path = (workspace_root / Path(*workspace_relative_path.split("/"))).resolve(strict=False)
        try:
            absolute_path.relative_to(workspace_root)
        except ValueError as exc:
            raise PermissionError("目标文件路径越界，拒绝分享。") from exc

    if not absolute_path.exists() or not absolute_path.is_file():
        raise FileNotFoundError("目标文件不存在或不是文件。")

    mime_type, _ = mimetypes.guess_type(str(absolute_path))
    if absolute_path.suffix.lower() == ".glb" and not mime_type:
        mime_type = "model/gltf-binary"
    if absolute_path.suffix.lower() == ".gltf" and not mime_type:
        mime_type = "model/gltf+json"
    viewer_kind = viewer_kind_for_file(absolute_path, mime_type)
    effective_mode = normalized_mode
    if normalized_mode == "auto":
        effective_mode = "preview" if viewer_kind != "download" else "download"

    resource_ref = build_workspace_resource_ref(
        workspace_relative_path=workspace_relative_path,
        path_plane="workspace_artifact",
        workspace_root=workspace_root,
        workspace_id=workspace_id,
        project_id=project_id,
        mime_type=mime_type,
        display_label=absolute_path.name,
        previewable=True,
        downloadable=True,
        surface_visible=True,
    )
    admin_path = str(resource_ref.get("adminPath") or "").strip()

    return {
        "ok": True,
        "filename": absolute_path.name,
        "mimeType": mime_type or "application/octet-stream",
        "mode": effective_mode,
        "url": admin_path,
        "resourceRef": resource_ref,
        "previewable": viewer_kind != "download",
        "downloadable": True,
        "viewerKind": viewer_kind,
        "workspaceRelativePath": workspace_relative_path,
        "workspaceId": workspace_id,
        "projectId": project_id,
    }
