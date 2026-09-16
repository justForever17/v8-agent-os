"""Canonical Engine identity: caller headers never choose an actor."""
from __future__ import annotations

import hmac
from dataclasses import dataclass
from fastapi import Header, HTTPException, Request


@dataclass(frozen=True)
class EngineAuthContext:
    subject: str
    session_id: str
    login: str
    role: str
    device_id: str | None
    issued_at: int
    expires_at: int
    issuer: str | None = None
    audience: str | None = None
    auth_method: str = "mobile_bearer"
    surface: str = "phone"
    device_kind: str = "human_phone"


def _read_internal_secret() -> str:
    from core.system_base import get_internal_secret
    return str(get_internal_secret() or "").strip()


def _trusted_secret(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    expected = _read_internal_secret()
    return bool(expected) and hmac.compare_digest(expected, value)


def require_local_management(request: Request) -> None:
    if request.scope.get("state", {}).get("local_management") is True:
        return
    if _trusted_secret(request.headers.get("x-v8-agent-os-secret")):
        return
    raise HTTPException(status_code=401, detail="local_management_required")


def verify_mobile_access_token(token: str, *, now: int | None = None) -> EngineAuthContext | None:
    from core.client_identity import get_identity_service, IdentityError
    try:
        return get_identity_service().verify_access(token, now=now)
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from None


def _local_context() -> EngineAuthContext:
    from core.client_identity import get_identity_service, IdentityError
    from core.client_identity.owner import session_identifier
    service = get_identity_service()
    try:
        owner = service.owners.owner()
    except IdentityError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from None
    return EngineAuthContext(owner["id"], session_identifier(owner), owner["login"], owner["role"],
        None, 0, 2**63 - 1, service.instance()["instanceId"], "v8-local", "internal_service", "local", "local_client")


def is_local_client(context: EngineAuthContext) -> bool:
    return context.auth_method == "internal_service" or (
        context.auth_method == "mobile_bearer" and context.device_kind == "local_client"
        and context.audience in ("v8-local", None)
        and context.surface in ("web", "cyber", "desktop_pet", "cli", "admin", "shell"))


def client_surface(context: EngineAuthContext) -> str:
    return context.surface


def require_engine_auth(
    request: Request = None,
    authorization: str | None = Header(default=None),
    x_v8_agent_os_secret: str | None = Header(default=None),
    x_v8_agent_os_user_email: str | None = Header(default=None),
    x_v8_admin_role: str | None = Header(default=None),
) -> EngineAuthContext:
    del x_v8_agent_os_user_email, x_v8_admin_role
    if request is not None:
        context = request.scope.get("state", {}).get("engine_auth_context")
        if isinstance(context, EngineAuthContext):
            return context
        if request.scope.get("state", {}).get("local_management") is True:
            return _local_context()
        authorization = request.headers.get("authorization")
        x_v8_agent_os_secret = request.headers.get("x-v8-agent-os-secret")
    if isinstance(authorization, str) and authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer":
            context = verify_mobile_access_token(token)
            if context:
                return context
        raise HTTPException(status_code=401, detail="engine_auth_required")
    if _trusted_secret(x_v8_agent_os_secret):
        return _local_context()
    raise HTTPException(status_code=401, detail="engine_auth_required")


def require_client_principal(request: Request) -> EngineAuthContext:
    context = require_engine_auth(request=request)
    if request.scope.get("state", {}).get("phone_gateway") and context.device_kind != "human_phone":
        raise HTTPException(status_code=403, detail="phone_device_credential_required")
    return context


def revalidate_client_principal(context: EngineAuthContext) -> None:
    """Recheck device authority during long-lived native streams, not just at open."""
    if context.auth_method == "internal_service":
        return
    from core.client_identity import get_identity_service
    service = get_identity_service()
    with service.database() as db:
        device = db.execute("SELECT * FROM client_devices WHERE id=?", (context.device_id,)).fetchone()
    if (not device or device["user_id"] != context.subject or device["revoked_at"] is not None
            or device["expires_at"] <= service.clock() or service.owners.owner()["id"] != context.subject):
        raise HTTPException(status_code=401, detail="device_session_revoked")
