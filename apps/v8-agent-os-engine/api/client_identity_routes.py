"""Phone identity adapter and separately authenticated local management API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from core.auth_context import EngineAuthContext, require_client_principal, require_local_management
from core.client_identity import IdentityError, get_identity_service, public_user

router = APIRouter(prefix="/api/client", tags=["client-identity"])
management_router = APIRouter(prefix="/v1/client-identity", tags=["local-identity"], dependencies=[Depends(require_local_management)])


def failure(exc: IdentityError):
    return JSONResponse({"ok": False, "error": exc.code, "code": exc.code}, status_code=exc.status)


async def body(request: Request) -> dict:
    try:
        value = await request.json()
        if isinstance(value, dict):
            return value
    except (ValueError, UnicodeError):
        pass
    raise IdentityError("request_body_invalid")


def origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def client_pair(result: dict) -> dict:
    service = get_identity_service()
    context = service.verify_access(result["accessToken"])
    if context is None:
        raise IdentityError("device_session_revoked", 401)
    return result


@router.get("/instance")
@management_router.get("/instance")
def instance(request: Request):
    try:
        service = get_identity_service()
        identity = service.instance()
        return {"ok": True, "kind": "v8_instance_manifest", **identity, "version": "1",
            "initialized": service.owners.owner(required=False) is not None, "ownerMode": "single_owner", "clientGateway": "engine",
            "admin": service.manifest(origin(request))["admin"], "warnings": [], "capabilities": {
                "pairing": True, "localTrustedSession": True, "localTrustedSurfaces": ["web", "cyber", "desktop_pet", "cli"],
                "passwordLoginFallback": False, "publicRegistration": False}}
    except IdentityError as exc:
        return failure(exc)


@management_router.get("/owner")
def owner():
    try:
        user = get_identity_service().owners.owner(required=False)
        return {"initialized": user is not None, "needsSetup": bool(user and user.get("localBootstrapPending") and not user.get("password")), "user": public_user(user) if user else None}
    except IdentityError as exc:
        return failure(exc)


@management_router.get("/link-manifest")
def management_link_manifest(baseUrl: str = ""):
    return get_identity_service().manifest(baseUrl)


@management_router.get("/users")
def users():
    try:
        return {"users": [public_user(user) for user in get_identity_service().owners.payload()["users"]]}
    except IdentityError as exc:
        return failure(exc)


@management_router.post("/bootstrap")
async def bootstrap(request: Request):
    try:
        data, service = await body(request), get_identity_service()
        password_hash = ""
        if data.get("password"):
            import bcrypt
            password_value = str(data["password"])
            if len(password_value) < 6 or len(password_value.encode()) > 72:
                raise IdentityError("password_length_invalid")
            password_hash = (await run_in_threadpool(bcrypt.hashpw, password_value.encode(), bcrypt.gensalt(rounds=10))).decode()
        await run_in_threadpool(service.initialize)
        user = await run_in_threadpool(service.owners.bootstrap, login=str(data.get("login") or "owner"), name=str(data.get("name") or ""), now=service.clock(), password_hash=password_hash, local_pending=not bool(password_hash))
        return {"ok": True, "success": True, "user": public_user(user)}
    except IdentityError as exc:
        return failure(exc)


@management_router.post("/local-session")
async def local_session(request: Request):
    try:
        data, service = await body(request), get_identity_service()
        surface = str(data.get("surface") or "web")
        if surface not in ("web", "cyber", "desktop_pet", "cli", "admin", "shell"):
            raise IdentityError("unsupported_local_surface")
        pair = await run_in_threadpool(service.create_session, name=str(data.get("deviceName") or "v8-local-" + surface), surface=surface, hidden=True)
        return {"ok": True, "kind": "v8_local_client_session", "ownerMode": "single_owner", "surface": surface,
            "instanceId": service.instance()["instanceId"], "trustedLocal": True, "adminBaseUrl": origin(request), "linkManifest": service.manifest(origin(request)), **pair}
    except IdentityError as exc:
        return failure(exc)


@management_router.post("/pairing-ticket")
async def pairing_ticket(request: Request):
    try:
        data = await body(request)
        return await run_in_threadpool(get_identity_service().create_ticket, base_url=str(data.get("adminBaseUrl") or data.get("baseUrl") or ""),
            device_name=str(data.get("deviceName") or ""), ttl_ms=int(data.get("ttlMs") or 300000), surface=str(data.get("surface") or "phone"))
    except (TypeError, ValueError):
        return failure(IdentityError("pairing_ttl_invalid"))
    except IdentityError as exc:
        return failure(exc)


@management_router.delete("/pairing-ticket/{ticket_id}")
def revoke_ticket(ticket_id: str):
    return {"ok": True, "revoked": get_identity_service().revoke_ticket(ticket_id)}


@router.post("/pairing/consume")
async def consume(request: Request):
    try:
        data = await body(request)
        if data.get("sessionKind") == "web_session":
            raise IdentityError("web_local_session_required")
        return await run_in_threadpool(lambda: client_pair(get_identity_service().consume_ticket(code=str(data.get("code") or ""), instance_id=str(data.get("instanceId") or ""), device_name=str(data.get("deviceName") or ""))))
    except IdentityError as exc:
        return failure(exc)


@router.post("/auth/refresh")
async def refresh(request: Request):
    try:
        data = await body(request)
        return await run_in_threadpool(lambda: client_pair(get_identity_service().refresh(str(data.get("refreshToken") or ""), rotation_id=str(data.get("rotationId") or ""), device_name=str(data.get("deviceName") or ""), phone_only=bool(request.scope.get("state", {}).get("phone_gateway")))))
    except IdentityError as exc:
        return failure(exc)


@router.post("/auth/logout")
async def logout(request: Request):
    try:
        data = await body(request)
        return {"ok": True, "revoked": await run_in_threadpool(get_identity_service().logout, str(data.get("refreshToken") or ""), phone_only=bool(request.scope.get("state", {}).get("phone_gateway")))}
    except IdentityError as exc:
        return failure(exc)


@router.get("/auth/me")
def me(context: EngineAuthContext = Depends(require_client_principal)):
    return {"user": get_identity_service().client_user(context)}


@router.get("/auth/profile")
def client_profile(context: EngineAuthContext = Depends(require_client_principal)):
    return get_identity_service().client_user(context)


@router.patch("/auth/profile")
async def client_profile_update(request: Request, context: EngineAuthContext = Depends(require_client_principal)):
    try:
        service = get_identity_service()
        data = await body(request)
        await run_in_threadpool(lambda: service.owners.update(context.subject, service.clean_profile_patch(context, data), now=service.clock()))
        return {"success": True, "user": await run_in_threadpool(service.client_user, context)}
    except IdentityError as exc:
        return failure(exc)


@management_router.get("/profile")
def management_profile():
    try:
        return public_user(get_identity_service().owners.owner())
    except IdentityError as exc:
        return failure(exc)


@management_router.patch("/profile")
async def management_profile_update(request: Request):
    try:
        service = get_identity_service()
        data = await body(request)
        return {"success": True, "user": await run_in_threadpool(lambda: public_user(service.owners.update(service.owners.owner()["id"], data, now=service.clock())))}
    except IdentityError as exc:
        return failure(exc)


@management_router.get("/devices")
def devices():
    try:
        service = get_identity_service()
        return {"devices": service.devices(service.owners.owner()["id"])}
    except IdentityError as exc:
        return failure(exc)


@management_router.delete("/devices/{device_id}")
def revoke_device(device_id: str):
    service = get_identity_service()
    return {"ok": True, "revoked": service.revoke(service.owners.owner()["id"], device_id)}


@management_router.post("/import-legacy")
def import_legacy():
    try:
        return get_identity_service().initialize()
    except IdentityError as exc:
        return failure(exc)


@management_router.get("/migration")
def migration_status():
    return get_identity_service().migration_status()


@management_router.post("/verify-credentials")
async def verify_credentials(request: Request):
    import bcrypt
    try:
        data, service = await body(request), get_identity_service()
        user = service.owners.owner()
        identifier = str(data.get("login") or data.get("identifier") or "").strip().lower()
        valid = identifier in (user["login"].lower(), str(user.get("email") or "").lower())
        password = str(data.get("password") or "").encode()
        valid = valid and bool(user.get("password")) and bool(password) and await run_in_threadpool(bcrypt.checkpw, password[:72], user["password"].encode())
        if not valid:
            raise IdentityError("invalid_credentials", 401)
        return {"ok": True, "user": public_user(user)}
    except (ValueError, TypeError):
        return failure(IdentityError("invalid_credentials", 401))
    except IdentityError as exc:
        return failure(exc)


@management_router.post("/resource-link")
async def resource_link(request: Request):
    try:
        data = await body(request)
        return {"signedUrl": get_identity_service().sign_resource_for_owner(str(data.get("path") or ""), str(data.get("sessionId") or data.get("session_id") or ""))}
    except IdentityError as exc:
        return failure(exc)


@management_router.post("/password")
async def password(request: Request):
    try:
        data, service = await body(request), get_identity_service()
        text = str(data.get("password") or data.get("newPassword") or "")
        user = await run_in_threadpool(service.owners.change_password, text, old_password=str(data.get("oldPassword") or ""), force=data.get("forceMode") is True, now=service.clock())
        return {"ok": True, "success": True, "user": public_user(user)}
    except IdentityError as exc:
        return failure(exc)
