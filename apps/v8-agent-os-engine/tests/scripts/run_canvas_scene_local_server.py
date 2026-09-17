"""Loopback acceptance server using production Canvas/source/artifact routes.

No provider credentials are copied. --live means real local files, FFmpeg and DB;
external generation remains unavailable unless separately configured in this state.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=19531)
    args = parser.parse_args()
    root = args.state_root.expanduser().resolve()
    if root == (Path.home() / ".v8-agent-os").resolve() or root == root.parent:
        parser.error("Use an isolated acceptance state root")
    marker = root / ".canvas-scene-acceptance"
    if root.exists() and any(root.iterdir()) and not marker.is_file():
        parser.error("Existing nonempty state root has no Canvas acceptance marker")
    root.mkdir(parents=True, exist_ok=True)
    marker.write_text("isolated-canvas-scene-v1\n", encoding="utf-8")
    os.environ["V8_AGENT_OS_HOME"] = str(root)
    engine = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(engine))
    os.chdir(engine)
    if not (root / "config.json").exists():
        (root / "config.json").write_text(json.dumps({"providers": {}}), encoding="utf-8")

    from fastapi import FastAPI
    import uvicorn
    from core.database import db
    from runtimes.memory.project_registry import project_registry_service
    from runtimes.memory.scope_resolution import session_scope_binding_service
    from runtimes.memory.models import SessionScopeBinding
    from api.creative_canvas_routes import router as canvas_router
    from api.session_workflow_routes import router as session_router
    from api.chat_realtime_routes import router as chat_router

    workspace = root / "workspace" / "scene-acceptance"
    workspace.mkdir(parents=True, exist_ok=True)
    project_registry_service.save_project({"id": "project-scene", "name": "Scene acceptance", "workspaceId": "workspace-scene", "workspacePath": str(workspace), "workspaceTrustState": "trusted", "workspaceTrustSource": "explicit_acceptance_fixture"})
    for session_id in ("session-scene", "session-other"):
        db.create_or_update_session(session_id, "Scene acceptance", user_id="canvas-acceptance")
        session_scope_binding_service.upsert_binding(SessionScopeBinding(session_id=session_id, project_id="project-scene", workspace_id="workspace-scene", workspace_path=str(workspace), resolved_scope="project:project-scene", scope_source="explicit_acceptance_fixture"))
    app = FastAPI(title="Isolated Canvas scene acceptance")
    app.include_router(canvas_router, prefix="/v1")
    app.include_router(session_router, prefix="/v1")
    app.include_router(chat_router, prefix="/v1")

    @app.get("/health")
    def health():
        return {"status": "ok", "sessionId": "session-scene", "workspacePath": str(workspace), "providerCredentialsCopied": False}

    print(json.dumps({"url": f"http://127.0.0.1:{args.port}", "sessionId": "session-scene", "workspacePath": str(workspace), "stateRoot": str(root)}, ensure_ascii=False), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
