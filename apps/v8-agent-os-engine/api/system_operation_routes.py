from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, Request

from core.security.credentials import CredentialStoreError
from core.system_base import get_internal_secret
from core.system_operations.service import SystemOperationError, system_operation_service

router = APIRouter(prefix="/system-operations", tags=["system-operations"])


def system_operation_owner(request: Request) -> str:
    expected = get_internal_secret()
    provided = request.headers.get("x-v8-agent-os-secret") or ""
    owner = (request.headers.get("x-v8-agent-os-user-email") or "").strip()
    if not owner or not expected or not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="system_operation_auth_required")
    return owner


@router.get("/settings")
def settings(owner: str = Depends(system_operation_owner)):
    return system_operation_service.settings(owner)


@router.post("/components/{component}/{action}")
def setup_component(component: str, action: str, request: Request, owner: str = Depends(system_operation_owner)):
    if request.headers.get("x-v8-admin-role") != "ADMIN":
        raise HTTPException(status_code=403, detail="system_setup_admin_required")
    from core.system_operations.setup import begin_setup
    try:
        return begin_setup(component, action)
    except SystemOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from None


@router.put("/credentials/{action}")
async def configure(action: str, request: Request, owner: str = Depends(system_operation_owner)):
    # Do not let validation errors reflect a submitted secret in their input field.
    try:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 32768:
                raise ValueError("oversize")
        import json
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) - {"username", "domain", "password"} or any(not isinstance(value, str) for value in payload.values()):
            raise ValueError("invalid shape")
        return system_operation_service.configure(owner=owner, action=action, username=payload.get("username", ""), domain=payload.get("domain", ""), password=payload.get("password", ""))
    except SystemOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from None
    except CredentialStoreError:
        raise HTTPException(status_code=503, detail={"code": "credential_store_unavailable", "message": "系统凭据库不可用，未保存明文密码。"}) from None
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail={"code": "system_credential_invalid", "message": "凭据输入格式无效。"}) from None


@router.delete("/credentials/{action}")
def remove(action: str, owner: str = Depends(system_operation_owner)):
    try:
        return system_operation_service.remove_credential(owner, action)
    except SystemOperationError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)}) from None
    except CredentialStoreError:
        raise HTTPException(status_code=503, detail="credential_store_unavailable") from None


def validate_system_operation_approval(approval: dict, request: Request) -> None:
    """Bind the existing approval endpoint to this operation's authenticated owner."""
    from core.database import db
    details = ((approval.get("request") or {}).get("safety") or {}).get("details") or {}
    operation_id = details.get("operationId")
    if not operation_id:
        return
    owner = system_operation_owner(request)
    with db.get_connection() as conn:
        row = conn.execute("SELECT owner_id,session_id,run_id,state,expires_at FROM system_operation_requests WHERE id=?", (operation_id,)).fetchone()
    if not row or row["owner_id"] != owner or row["session_id"] != approval.get("session_id") or row["run_id"] != approval.get("run_id"):
        raise HTTPException(status_code=403, detail="system_operation_approval_scope_mismatch")
    run = db.get_run_record(row["run_id"])
    session = db.get_session(row["session_id"])
    if not session or str(session.get("user_id") or session.get("userId")) != owner or not run or run.get("session_id") != row["session_id"] or run.get("status") in {"completed", "failed", "cancelled", "canceled", "interrupted"}:
        raise HTTPException(status_code=409, detail="system_operation_run_terminal")
    import time
    if row["state"] != "prepared" or row["expires_at"] <= time.time():
        raise HTTPException(status_code=409, detail="system_operation_approval_expired")
