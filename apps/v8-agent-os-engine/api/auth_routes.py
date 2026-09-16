from fastapi import APIRouter, Depends

from core.auth_context import EngineAuthContext, require_client_principal

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/context")
def auth_context(context: EngineAuthContext = Depends(require_client_principal)):
    return {"authenticated": True, "subject": context.subject, "sessionId": context.session_id, "login": context.login,
        "role": context.role, "deviceId": context.device_id, "issuedAt": context.issued_at, "expiresAt": context.expires_at,
        "issuer": context.issuer, "audience": context.audience, "authMethod": context.auth_method,
        "surface": context.surface, "deviceKind": context.device_kind}

