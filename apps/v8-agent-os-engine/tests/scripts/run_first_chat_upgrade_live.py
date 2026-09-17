"""Boot a real Engine against synthetic persistent state for upgrade/first-chat audits.

--live permits the configured Supervisor provider. Only model configuration with
OS credential references is imported; user transcripts and raw secrets are not.
Use --seed with an official old checkout, then serve the same state with each
successive checkout. The caller owns ports, browser, timing and process cleanup.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=22930)
    parser.add_argument("--web-port", type=int, default=22927)
    parser.add_argument("--seed", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fault-503", action="store_true", help="Use a local deterministic failing provider, with no real credentials")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required")
    root = args.state.resolve()
    actual = (Path.home()/".v8-agent-os").resolve()
    if root == actual or actual in root.parents:
        parser.error("use a new isolated state outside the real user directory")
    engine = args.repo.resolve()/"apps/v8-agent-os-engine"
    os.environ["V8_AGENT_OS_HOME"] = str(root)
    os.environ["V8_AGENT_OS_DISABLE_BYTECODE"] = "true"
    os.environ["ENGINE_STARTUP_PROFILE"] = "minimal"
    os.environ["ENGINE_INSTALL_PROFILE"] = "minimal"
    sys.path.insert(0, str(engine))
    root.mkdir(parents=True, exist_ok=True)
    if args.seed and (root/"config.json").exists():
        parser.error("refusing to seed an existing state")

    # Guard against accidentally importing legacy plaintext credentials.
    def assert_references_only(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower().replace("_", "") in {"apikey", "secret", "password", "accesstoken", "refreshtoken", "authorization"} and item:
                    raise RuntimeError("model configuration contains plaintext credentials; use governed credential references")
                assert_references_only(item)
        elif isinstance(value, list):
            for item in value:
                assert_references_only(item)

    if args.seed and not args.fault_503:
        configured = json.loads((actual/"config.json").read_text(encoding="utf-8-sig"))["models"]
        assert_references_only(configured)
        # Metadata and credential references only. Hydration remains the product
        # CredentialRefStore + provider adapter, never the audit process/argv.
        (root/"config.json").write_text(json.dumps({"models": configured}), encoding="utf-8")
    if args.fault_503:
        from core.security import credentials
        credentials.credential_ref_store = credentials.CredentialRefStore(credentials.MemoryCredentialBackend())
        class UnavailableProvider(BaseHTTPRequestHandler):
            count = 0
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                type(self).count += 1
                (root/"provider-fault-proof.json").write_text(json.dumps({"requests": type(self).count, "status": 503}), encoding="utf-8")
                payload = json.dumps({"error": {"message": "Synthetic provider unavailable (503)", "type": "service_unavailable", "code": "fixture_503"}}).encode()
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Retry-After", "0")
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *_args): pass
        fault_server = ThreadingHTTPServer(("127.0.0.1", 0), UnavailableProvider)
        threading.Thread(target=fault_server.serve_forever, daemon=True).start()
    from core.storage import storage
    if args.fault_503:
        reference = credentials.credential_ref_store.put(secrets.token_urlsafe(24), namespace="model")
        storage.save_models_config({
            "providers": {"fixture-503": {
                "provider": {"base_url": f"http://127.0.0.1:{fault_server.server_port}/v1", "api_standard": "openai", "is_enabled": True, "credentialRef": reference},
                "models": {"fixture-chat": {"type": "TEXT", "is_enabled": True, "capabilities": {"chat": True, "toolCalling": True, "streaming": True}, "context_window": 32768}},
            }},
            "roles": {"default": "fixture-503::fixture-chat", "supervisor": "fixture-503::fixture-chat"},
        })
    config = storage.get_system_base_config()
    config.setdefault("bridge", {}).update({
        "engineBaseUrl": f"http://127.0.0.1:{args.port}/v1",
        "engineWsBaseUrl": f"ws://127.0.0.1:{args.port}/v1",
        "webBaseUrl": f"http://127.0.0.1:{args.web_port}",
        "allowedOrigins": [f"http://127.0.0.1:{args.web_port}"],
    })
    config.setdefault("remoteLink", {})["phoneGateway"] = {"enabled": False}
    storage.save_system_base_config(config)
    if args.seed:
        from api.session_workflow_routes import create_session
        workspace = root/"workspace"/"retained-project"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace/"retained.txt").write_text("synthetic upgrade fixture\n", encoding="utf-8")
        session = asyncio.run(create_session({"title": "升级保留会话", "userId": "fixture-upgrade-owner",
                                             "workspacePath": str(workspace), "scopeMode": "explicit", "scopeHint": "workspace"}))
        (root/"audit-seed.json").write_text(json.dumps({"sessionId": session.get("sessionId") or session.get("id"),
                                                     "workspace": str(workspace)}), encoding="utf-8")
        print(json.dumps({"seeded": True, "sessionId": session.get("sessionId") or session.get("id")}, ensure_ascii=False))
        return
    import main as engine_main
    import uvicorn
    uvicorn.run(engine_main.app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
