"""Owner-facing adapter; remote peers never receive this local HTTP API."""
from fastapi import APIRouter, HTTPException, Request

from core.auth_context import require_engine_auth
from core.config_distribution_service import config_distribution_service as service, identifier

router = APIRouter()


async def distribution_request(request, principal, path):
    if principal.role.upper() != "ADMIN":
        raise HTTPException(403, "owner_access_required")
    parts = path.strip("/").split("/") if path.strip("/") else []
    if request.method == "GET":
        if not parts:
            return service.inventory(principal.subject, principal.issuer)
        if parts == ["jobs"]:
            cursor = request.query_params.get("cursor", "0")
            if not cursor.isdigit() or len(cursor) > 9:
                raise HTTPException(422, "distribution_cursor_invalid")
            return service.store.page(principal.subject, int(cursor))
        if parts == ["local-workspaces"]:
            from core.config_distribution_local import local_workspaces
            return local_workspaces(service)
        if len(parts) == 2 and parts[0] == "targets":
            return await service.target_capabilities(identifier(parts[1]))
        if len(parts) == 1:
            return service.public(service.store.get(identifier(parts[0]), principal.subject))
    if request.method == "POST":
        from api.client_routes import _payload
        body = await _payload(request)
        if len(parts) == 2 and parts[0] == "local-workspaces":
            from core.config_distribution_local import bind_local_workspace
            return bind_local_workspace(service, identifier(parts[1]), body)
        if not parts:
            authority = {"subject": principal.subject, "issuer": principal.issuer, "deviceId": principal.device_id}
            return service.create(principal.subject, authority, body)
        if len(parts) == 2:
            return service.action(identifier(parts[0]), principal.subject, parts[1], body,
                                  authority={"subject": principal.subject, "issuer": principal.issuer, "deviceId": principal.device_id})
    raise HTTPException(404, "distribution_route_not_found")


@router.api_route("/config-distribution", methods=["GET", "POST"])
@router.api_route("/config-distribution/{path:path}", methods=["GET", "POST"])
async def config_distribution(request: Request, path: str = ""):
    return await distribution_request(request, require_engine_auth(request=request), path)
