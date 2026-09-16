"""HTTP integration fixture: production Engine identity, isolated external keychain.

This is not a full Engine or OS credential acceptance test. No production handler,
credential comparison, Owner persistence or management authorization is mocked.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--engine-root", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.engine_root.resolve()))

import uvicorn
from fastapi import FastAPI
import core.client_identity as identity
from core.client_identity.service import ClientIdentityService
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
from core.client_auth_boundary import EngineControlBoundary
from api.client_identity_routes import management_router, router

state_root = Path(os.environ["V8_AGENT_OS_HOME"]).resolve()
if not state_root.name.startswith("v8-admin-login-interaction-"):
    raise RuntimeError("An explicitly isolated Admin fixture state is required")
identity._service = ClientIdentityService(state_root, CredentialRefStore(MemoryCredentialBackend()))
app = FastAPI()
app.add_middleware(EngineControlBoundary)
app.include_router(management_router)
app.include_router(router)
counts: dict[str, int] = {}


@app.middleware("http")
async def record_status(request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/v1/client-identity/"):
        key = f"{request.method} {request.url.path} {response.status_code}"
        counts[key] = counts.get(key, 0) + 1
        # Only route/status counts are recorded, never headers, bodies or tokens.
        (state_root / "identity-http-receipt.json").write_text(json.dumps(counts), encoding="utf-8")
    return response


@app.get("/readyz")
def ready():
    return {"fixture": "production-engine-identity", "pid": os.getpid()}


uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
