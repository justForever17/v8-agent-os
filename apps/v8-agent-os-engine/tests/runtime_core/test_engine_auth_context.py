import base64
import hashlib
import hmac
import json

from core import auth_context


def _token(secret="fixture-secret", **claims):
    enc = lambda value: base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")
    header, payload = enc({"alg": "HS256", "typ": "JWT"}), enc({"type": "mobile_access", "sub": "u1", "sid": "owner@example.invalid", "login": "owner", "role": "ADMIN", "iat": 100, "exp": 200, **claims})
    sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def test_engine_verifies_existing_mobile_token_without_issuing_one(monkeypatch):
    monkeypatch.setattr(auth_context, "_read_secret", lambda: "fixture-secret")
    context = auth_context.verify_mobile_access_token(_token(), now=150)
    assert context and context.subject == "u1" and context.role == "ADMIN"


def test_engine_rejects_expired_or_wrong_secret(monkeypatch):
    monkeypatch.setattr(auth_context, "_read_secret", lambda: "fixture-secret")
    assert auth_context.verify_mobile_access_token(_token(), now=200) is None
    assert auth_context.verify_mobile_access_token(_token(secret="other"), now=150) is None

