"""Owner-selected local workspace binding. Never called from a peer envelope."""
from pathlib import Path

from fastapi import HTTPException

from core.config_distribution_service import digest


def local_workspaces(service):
    from runtimes.memory.project_registry import project_registry_service
    projects = []
    for project in project_registry_service.list_projects():
        payload = project.model_dump(by_alias=True, exclude_none=True)
        path = str(project.workspace_path or "")
        if not path or not Path(path).is_dir():
            continue
        projects.append({"projectId": project.project_id, "label": payload.get("name") or project.project_id,
                         "localPath": path, "trusted": project.workspace_trust_state == "trusted",
                         "revision": digest(payload)})
    links = []
    for link in service.neighbors.list_links()["items"]:
        if link.get("trustStatus") == "trusted":
            links.append({"linkId": link["linkId"], "displayName": link.get("remoteNickname") or link["peerId"],
                          "localRole": link["localRole"], "revision": digest(link.get("workspaceBinding") or {}),
                          "localPath": (link.get("workspaceBinding") or {}).get("workspacePath", "")})
    return {"projects": projects, "links": links}


def bind_local_workspace(service, link_id, body):
    from runtimes.memory.project_registry import project_registry_service
    if set(body) != {"projectId", "projectRevision", "linkRevision", "trustConfirmed"} or body["trustConfirmed"] is not True:
        raise HTTPException(422, "distribution_local_trust_confirmation_required")
    link = service.neighbors._link_or_404(link_id)
    if link.get("trustStatus") != "trusted" or link["peerId"] not in service.network._trusted_peer_map():
        raise HTTPException(403, "distribution_target_authority_changed")
    project = project_registry_service.get_project(body["projectId"])
    if not project or digest(project.model_dump(by_alias=True, exclude_none=True)) != body["projectRevision"] or digest(link.get("workspaceBinding") or {}) != body["linkRevision"]:
        raise HTTPException(409, "distribution_local_workspace_changed")
    path = Path(project.workspace_path)
    if not path.is_dir() or path.resolve() != path.absolute():
        raise HTTPException(409, "distribution_local_workspace_changed")
    if project.workspace_trust_state != "trusted":
        project = project_registry_service.bind_workspace(project_id=project.project_id, workspace_id=project.workspace_id,
            workspace_path=str(path), workspace_trust_state="trusted", workspace_trust_source="user_confirmed",
            source="phone_selected", confidence=1.0)
    service.neighbors.update_link(link_id, {"workspaceBinding": {"projectId": project.project_id,
        "workspaceId": project.workspace_id, "workspacePath": str(path)}})
    return local_workspaces(service)
