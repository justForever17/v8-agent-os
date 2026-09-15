from fastapi import APIRouter, Header, HTTPException

from core.auth_context import verify_mobile_access_token

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/context")
def auth_context(authorization: str | None = Header(default=None)):
    scheme, _, token = str(authorization or "").partition(" ")
    context = verify_mobile_access_token(token if scheme.lower() == "bearer" else "")
    if context is None:
        raise HTTPException(status_code=401, detail="engine_auth_required")
    return {"authenticated": True, "subject": context.subject, "sessionId": context.session_id, "login": context.login, "role": context.role, "deviceId": context.device_id, "issuedAt": context.issued_at, "expiresAt": context.expires_at, "issuer": context.issuer, "audience": context.audience}

