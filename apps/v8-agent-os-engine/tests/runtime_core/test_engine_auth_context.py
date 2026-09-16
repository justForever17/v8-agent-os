from fastapi import HTTPException
from starlette.requests import Request
import pytest

from core import auth_context
from core.auth_context import EngineAuthContext


def test_service_proof_cannot_choose_actor(monkeypatch):
    canonical = EngineAuthContext("owner-id", "owner-session", "owner", "ADMIN", None, 0, 999999, auth_method="internal_service")
    monkeypatch.setattr(auth_context, "_read_internal_secret", lambda: "fixture-service")
    monkeypatch.setattr(auth_context, "_local_context", lambda: canonical)
    assert auth_context.require_engine_auth(x_v8_agent_os_secret="fixture-service", x_v8_agent_os_user_email="attacker", x_v8_admin_role="ROOT") is canonical


def test_invalid_bearer_does_not_fall_back_to_service(monkeypatch):
    monkeypatch.setattr(auth_context, "verify_mobile_access_token", lambda *_: None)
    monkeypatch.setattr(auth_context, "_read_internal_secret", lambda: "fixture-service")
    with pytest.raises(HTTPException) as exc:
        auth_context.require_engine_auth(authorization="Bearer invalid", x_v8_agent_os_secret="fixture-service")
    assert exc.value.status_code == 401


def test_internal_scope_preserves_verified_principal_without_header_spoofing():
    canonical = EngineAuthContext("u", "session", "owner", "ADMIN", "device", 0, 99999)
    scope = {"type": "http", "headers": [(b"x-v8-agent-os-user-email", b"attacker")], "state": {"engine_auth_context": canonical}}
    assert auth_context.require_client_principal(Request(scope)) is canonical


def test_untyped_scope_dictionary_is_not_identity():
    request = Request({"type": "http", "headers": [], "state": {"engine_auth_context": {"subject": "u", "role": "ADMIN"}}})
    with pytest.raises(HTTPException):
        auth_context.require_client_principal(request)
