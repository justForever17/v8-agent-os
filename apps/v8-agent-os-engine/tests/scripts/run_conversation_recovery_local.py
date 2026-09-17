"""Opt-in loopback Engine/SQLite acceptance surface using synthetic state only.

The real Engine application and authentication middleware run without background
services. /fixture is a test-only proxy with an in-process test credential, not a
product authentication path. No provider or user credential is loaded.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from fastapi import Request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--port", type=int, default=19536)
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args()
    root = (args.state_root or Path(tempfile.mkdtemp(prefix="v8-conversation-local-"))).resolve()
    if root == (Path.home() / ".v8-agent-os").resolve():
        raise SystemExit("Real user state is forbidden")
    root.mkdir(parents=True, exist_ok=True)
    os.environ["V8_AGENT_OS_HOME"] = str(root)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.database import db
    from core.client_auth_boundary import EngineControlBoundary
    import main as engine
    import httpx
    import uvicorn
    from fastapi.responses import JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    fixture_key = secrets.token_urlsafe(32)
    for middleware in engine.app.user_middleware:
        if middleware.cls is EngineControlBoundary:
            middleware.kwargs["secret_reader"] = lambda: fixture_key
    engine.app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    owner = "recovery-fixture"
    from core import client_identity
    from core.client_identity.service import ClientIdentityService
    from core.security.credentials import CredentialRefStore, MemoryCredentialBackend
    identity_root = Path(tempfile.mkdtemp(prefix="fixture-identities-", dir=root))
    identities = ClientIdentityService(identity_root, CredentialRefStore(MemoryCredentialBackend()), config_reader=lambda: {})
    identities.owners.bootstrap(login=owner, now=identities.clock())
    identities.initialize()
    phone = identities.create_session(name="Synthetic Phone")
    client_identity._service = identities
    if not db.get_session("source"):
        db.create_session_placeholder(session_id="source", title="Recovery fixture", user_id=owner, metadata={})
        db.upsert_session_scope_binding({"session_id": "source", "conversation_id": "source", "thread_id": "source",
            "user_id": owner, "workspace_id": "recovery-workspace", "workspace_path": str(root / "workspace"),
            "project_id": "recovery-project", "resolved_scope": "workspace:recovery-workspace", "scope_source": "fixture"})
        for ordinal, (role, content) in enumerate((("user", "First premise"), ("assistant", "First answer"),
                                                   ("user", "Second premise"), ("assistant", "Second answer")), 1):
            db.create_chat_canonical_message(message_id=f"m{ordinal}", session_id="source", run_id=None, ordinal=ordinal,
                role=role, state="completed", nodes=[{"id": f"n{ordinal}", "kind": "narrative", "content": content}],
                content_text=content, metadata={})
    (root / "workspace").mkdir(exist_ok=True)

    @engine.app.api_route("/fixture/conversations/{session_id}/{tail:path}", methods=["GET", "POST", "PATCH"])
    async def proxy(session_id: str, tail: str, request: Request):
        headers = {"x-v8-agent-os-secret": fixture_key, "x-v8-agent-os-user-email": owner}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=engine.app), base_url="http://engine") as client:
            response = await client.request(request.method, f"/v1/sessions/{session_id}/{tail}",
                params=request.query_params, content=await request.body(), headers={**headers, "content-type": "application/json"})
        return JSONResponse(response.json(), status_code=response.status_code)

    @engine.app.api_route("/fixture/phone/conversations/{session_id}/{tail:path}", methods=["GET", "POST", "PATCH"])
    async def phone_proxy(session_id: str, tail: str, request: Request):
        # Exercise real Engine-issued Phone principal checks and the public
        # dispatcher. Only the fixture proxy holds its generated bearer token.
        path = f"/api/client/conversations/{session_id}"
        query = dict(request.query_params)
        if tail in {"history", "snapshot"}:
            query["omitMessages"] = "1" if tail == "snapshot" else "0"
        else:
            path += "/" + tail
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=engine.app), base_url="http://engine") as client:
            response = await client.request(request.method, path, params=query, content=await request.body(),
                headers={"authorization": "Bearer " + phone["accessToken"], "content-type": "application/json"})
        return JSONResponse(response.json(), status_code=response.status_code)

    @engine.app.get("/fixture/info")
    async def info():
        return {"sessionId": "source", "owner": owner, "layer": "real_engine_http_sqlite_synthetic_auth",
                "state": db.get_chat_transcript_state("source")}

    print(json.dumps({"port": args.port, "stateRoot": str(root), "sessionId": "source"}), flush=True)
    uvicorn.run(engine.app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False, lifespan="off")


if __name__ == "__main__":
    main()
