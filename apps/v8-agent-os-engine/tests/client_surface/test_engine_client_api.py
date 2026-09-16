from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request

from api import client_routes, client_realtime
from core import client_identity
from core.client_identity.service import ClientIdentityService
from core.remote_link.phone_gateway import create_phone_gateway_app
from core.security.credentials import CredentialRefStore, MemoryCredentialBackend


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    service = ClientIdentityService(tmp_path, CredentialRefStore(MemoryCredentialBackend()), config_reader=lambda: {})
    owner = service.owners.bootstrap(login="owner", now=service.clock())
    service.initialize()
    pair = service.create_session(name="Phone")
    monkeypatch.setattr(client_identity, "_service", service)
    rows = {"mine": {"id": "mine", "user_id": "owner"}, "foreign": {"id": "foreign", "user_id": "other"}}
    records = {"run-mine": {"session_id": "mine"}, "run-foreign": {"session_id": "foreign"},
               "q-mine": {"session_id": "mine"}, "q-foreign": {"session_id": "foreign"}}
    @contextmanager
    def connection():
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE sessions (id TEXT, user_id TEXT)")
        db.executemany("INSERT INTO sessions VALUES (?, ?)", [(row["id"], row["user_id"]) for row in rows.values()])
        try:
            yield db
        finally:
            db.close()
    database = SimpleNamespace(get_session=lambda key: rows.get(key), get_run_record=lambda key: records.get(key),
                               get_chat_user_message_queue_item=lambda key: records.get(key), get_connection=connection)
    internal = APIRouter()
    calls = []

    @internal.get("/sessions/quick-index")
    def index():
        return {"sessions": [{"id": key, "title": key} for key in rows], "generatedAt": "version-1"}

    @internal.post("/sessions")
    async def create(request: Request):
        body = await request.json()
        calls.append((request.url.path, body, request.scope["state"]["engine_auth_context"], dict(request.headers)))
        key = body.get("id") or "created"
        rows[key] = {"id": key, "user_id": body["userId"]}
        return rows[key]

    @internal.post("/chat/submit")
    async def submit(request: Request):
        body = await request.json()
        calls.append((request.url.path, body, request.scope["state"]["engine_auth_context"], dict(request.headers)))
        return {"ok": True, "sessionId": body["session_id"], "clientMessageId": body["clientMessageId"]}

    @internal.post("/runs/{run_id}/commands/{command}")
    @internal.patch("/chat/queued-messages/{run_id}")
    async def action(run_id: str, request: Request, command: str = "edit"):
        calls.append((run_id, command))
        return {"ok": True}

    @internal.get("/sessions/{session_id}/snapshot")
    async def snapshot(session_id: str, compact: int = 0):
        return {"sessionId": session_id, "latestSeq": 2, "messagesOmitted": bool(compact), "snapshot": {"messages": []}}

    @internal.get("/sessions/{session_id}/history")
    async def history(session_id: str):
        return {"messages": [{"id": "m1", "content": "persisted"}], "ledger": [{"id": "turn1"}]}

    @internal.get("/sessions/{session_id}/turns")
    async def turns(session_id: str, around: str = "", radius: int = 0):
        return {"messages": [{"id": around}], "pageInfo": {"radius": radius}}

    @internal.get("/sessions/{session_id}/runtime-events")
    async def events(session_id: str, after_seq: int = 0, limit: int = 128):
        return {"events": [], "latestSeq": 2}

    app = FastAPI()
    app.state.client_internal_router = internal
    app.state.client_database = database
    app.include_router(client_routes.router)
    try:
        yield SimpleNamespace(app=app, service=service, owner=owner, pair=pair, calls=calls, rows=rows, internal=internal)
    finally:
        # The bare ASGI fixture deliberately skips expensive Engine startup.
        # Native deletion lazily opens the real checkpointer; mirror Engine's
        # lifespan close so its non-daemon aiosqlite worker cannot outlive app.
        checkpoint_module = sys.modules.get("erc.checkpoint_store")
        if checkpoint_module is not None:
            asyncio.run(checkpoint_module.checkpoint_store.close())


def request(fixture, method, path, *, token=True, **kwargs):
    async def run():
        headers = kwargs.pop("headers", {})
        if token:
            headers["authorization"] = "Bearer " + fixture.pair["accessToken"]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app), base_url="http://engine.local") as client:
            return await client.request(method, path, headers=headers, **kwargs)
    return asyncio.run(run())


def test_owner_filter_precedes_pagination_and_forged_headers_do_not_choose_owner(fixture):
    result = request(fixture, "GET", "/api/client/conversations?limit=1", headers={"x-v8-agent-os-user-email": "other"})
    assert result.status_code == 200
    assert [row["id"] for row in result.json()["items"]] == ["mine"]
    assert not result.json()["pageInfo"]["hasMore"]
    denied = request(fixture, "GET", "/api/client/conversations/foreign")
    assert denied.status_code == 404


def test_conversation_mutations_keep_phone_owner_and_cas_contract(fixture):
    @fixture.internal.patch("/sessions/{session_id}/messages/{message_id}")
    async def revise(session_id: str, message_id: str, req: Request):
        body = await req.json()
        fixture.calls.append((session_id, message_id, body))
        return {"sessionId": session_id, "messageId": message_id, "transcriptRevision": 7, "contextEpoch": 2}
    @fixture.internal.post("/sessions/{session_id}/branches")
    async def branch(session_id: str, req: Request):
        body = await req.json()
        fixture.calls.append((session_id, "branch", body))
        return {"sessionId": "child", "sourceSessionId": session_id}
    body = {"content": "user correction", "expectedMessageVersion": 3, "expectedTranscriptRevision": 6, "userId": "other"}
    result = request(fixture, "PATCH", "/api/client/conversations/mine/messages/message-1", json=body)
    assert result.status_code == 200 and result.json()["contextEpoch"] == 2
    assert fixture.calls[-1] == ("mine", "message-1", {**body, "userId": "owner"})
    denied = request(fixture, "PATCH", "/api/client/conversations/foreign/messages/message-1", json=body)
    assert denied.status_code == 404 and len(fixture.calls) == 1
    result = request(fixture, "POST", "/api/client/conversations/mine/branches", json={"turnId": "turn-1", "expectedTranscriptRevision": 7, "userId": "other"})
    assert result.status_code == 200 and result.json()["sessionId"] == "child"
    assert fixture.calls[-1][2]["userId"] == "owner"


def test_expired_or_revoked_auth_is_pre_execution_and_never_dispatches(fixture):
    fixture.service.revoke(fixture.owner["id"], fixture.pair["deviceId"])
    result = request(fixture, "POST", "/api/client/conversations", json={"title": "never"})
    assert result.status_code == 401
    assert result.headers["x-v8-auth-stage"] == "pre_execution"
    assert not fixture.calls


def test_create_and_chat_use_canonical_owner_and_preserve_composer_context(fixture):
    created = request(fixture, "POST", "/api/client/conversations", json={"userId": "other", "title": "mine"})
    assert created.status_code == 200 and created.json()["user_id"] == "owner"
    result = request(fixture, "POST", "/api/client/chat-submit", json={
        "user_id": "other", "conversationId": "mine", "clientMessageId": "stable-1",
        "messages": [{"id": "m1", "role": "user", "content": "task"}],
        "data": {"workspacePath": "/fixture/workspace", "provider": "configured", "model": "configured-model",
                 "supervisorRuntimeMode": "engineering", "safetyApprovalMode": "auto", "contextMentions": [{"id": "m2"}]}})
    assert result.status_code == 200
    _, body, context, headers = fixture.calls[-1]
    assert body["user_id"] == "owner" and body["workspace_path"] == "/fixture/workspace"
    assert body["data"]["supervisorRuntimeMode"] == "engineering"
    assert body["data"]["contextMentions"] == [{"id": "m2"}]
    assert body["clientMessageId"] == "stable-1"
    assert context.subject == fixture.owner["id"]
    assert "x-v8-agent-os-secret" not in headers
    assert headers["x-v8-agent-os-user-email"] == "owner"


@pytest.mark.parametrize("path,method", [("runs/run-foreign/commands/interrupt", "POST"), ("chat-queue/q-foreign", "PATCH")])
def test_foreign_control_target_rejected_before_mutation(fixture, path, method):
    result = request(fixture, method, "/api/client/" + path, json={"content": "never"})
    assert result.status_code == 404
    assert not fixture.calls


def test_turn_query_and_detail_wire_retain_persisted_history(fixture):
    result = request(fixture, "GET", "/api/client/conversations/mine/turns?around=turn9&radius=2")
    assert result.json() == {"messages": [{"id": "turn9"}], "pageInfo": {"radius": 2}}
    detail = request(fixture, "GET", "/api/client/conversations/mine").json()
    assert detail["messages"][0]["content"] == "persisted"
    assert detail["ledger"][0]["id"] == "turn1"
    compact = request(fixture, "GET", "/api/client/conversations/mine?omitMessages=1").json()
    assert compact["messages"] == [] and compact["projection"]["messagesOmitted"]


def test_client_api_is_not_an_arbitrary_privileged_proxy(fixture):
    for path in ("config-broker/network", "../v1/config", "system/reload", "auth/local-session"):
        assert request(fixture, "POST", "/api/client/" + path, json={}).status_code == 404


def test_gateway_dispatches_engine_asgi_with_admin_absent(fixture):
    fixture.app = create_phone_gateway_app(client_app=fixture.app)
    result = request(fixture, "GET", "/api/client/conversations")
    assert result.status_code == 200 and [row["id"] for row in result.json()] == ["mine"]
    assert request(fixture, "GET", "/v1/config-broker/network").status_code == 404


def test_sse_rechecks_revocation_before_next_payload(fixture):
    scope = {"type": "http", "method": "GET", "path": "/api/client/realtime/sessions/mine/stream", "query_string": b"",
             "headers": [(b"authorization", ("Bearer " + fixture.pair["accessToken"]).encode())],
             "app": fixture.app, "state": {}, "server": ("engine.local", 80), "scheme": "http"}
    async def receive():
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}
    async def run():
        chunks = []
        async def send(message):
            if message["type"] == "http.response.body":
                chunk = message.get("body", b"").decode()
                chunks.append(chunk)
                if "event: snapshot" in chunk:
                    fixture.service.revoke(fixture.owner["id"], fixture.pair["deviceId"])
        await asyncio.wait_for(fixture.app(scope, receive, send), timeout=2)
        assert "event: snapshot" in "".join(chunks)
        assert "event: auth_expired" in "".join(chunks)
    asyncio.run(run())


def test_native_session_lifecycle_and_canonical_turns_persist_without_admin(fixture):
    from api import session_workflow_routes
    database = session_workflow_routes.db
    fixture.app.state.client_internal_router = session_workflow_routes.router
    fixture.app.state.client_database = database
    created = request(fixture, "POST", "/api/client/conversations", json={"title": "Engine only", "userId": "forged"})
    assert created.status_code == 200, created.text
    session_id = created.json()["id"]
    assert database.get_session(session_id)["user_id"] == "owner"
    database.create_chat_canonical_message(message_id="native-msg-" + session_id, session_id=session_id, run_id=None,
        ordinal=1, role="user", state="complete", nodes=[{"id": "node1", "type": "text", "content": "durable content"}], content_text="durable content")
    turns = request(fixture, "GET", f"/api/client/conversations/{session_id}/turns?limit=1")
    assert turns.status_code == 200, turns.text
    assert "durable content" in turns.text
    renamed = request(fixture, "PATCH", f"/api/client/conversations/{session_id}", json={"title": "Renamed", "userId": "forged"})
    assert renamed.status_code == 200, renamed.text
    assert database.get_session(session_id)["title"] == "Renamed"
    deleted = request(fixture, "DELETE", f"/api/client/messages/native-msg-{session_id}?sessionId={session_id}")
    assert deleted.status_code == 200, deleted.text
    sync = request(fixture, "GET", f"/api/client/conversations/{session_id}/sync?since=2000-01-01T00:00:00Z")
    assert sync.status_code == 200 and sync.json()["deletions"]
    removed = request(fixture, "DELETE", f"/api/client/conversations/{session_id}")
    assert removed.status_code == 200, removed.text
    assert database.get_session(session_id) is None


def test_phone_cannot_use_local_credential_or_direct_local_capability(fixture):
    for path in ("desktop-pet/config", "runtime/bridge", "ui-preferences/theme"):
        assert request(fixture, "GET", "/api/client/" + path).status_code == 403
    fixture.pair = fixture.service.create_session(name="Web", surface="web", hidden=True)
    fixture.app = create_phone_gateway_app(client_app=fixture.app)
    denied = request(fixture, "GET", "/api/client/conversations")
    assert denied.status_code == 403
    assert denied.json()["detail"] == "phone_device_credential_required"


def test_phone_native_capabilities_keep_actual_process_owner_boundary(fixture, monkeypatch):
    from api import ops_routes
    from core.database import db
    from core.tools.native.command import _bg_processes
    from core.native_tools import _bg_processes as public_processes
    fixture.internal.include_router(ops_routes.router)
    monkeypatch.setattr(db, "get_session", lambda key: fixture.rows.get(key))
    for command_id, session_id in (("own-command", "mine"), ("foreign-command", "foreign")):
        process = SimpleNamespace(session_id=session_id, is_running=True,
            read_output=lambda cursor: {"data": "fixture output", "cursor": 14, "generation": "fixture", "hasMore": False, "totalBytes": 14},
            status_snapshot=lambda: {})
        monkeypatch.setitem(_bg_processes, command_id, process)
        monkeypatch.setitem(public_processes, command_id, process)
    fixture.app = create_phone_gateway_app(client_app=fixture.app)
    allowed = request(fixture, "GET", "/api/client/bg_processes/own-command")
    assert allowed.status_code == 200 and allowed.json()["output"] == "fixture output"
    # Neither a query nor an identity header may rebind the process to mine.
    denied = request(fixture, "POST", "/api/client/bg_processes/foreign-command/input?sessionId=mine",
                     headers={"x-v8-session-id": "mine", "x-v8-agent-os-user-email": "other"}, json={"input_text": "never"})
    assert denied.status_code == 403


def test_runs_list_and_detail_bind_real_owner_and_session_filter(fixture):
    @fixture.internal.get("/runs")
    def runs(session_id: str = ""):
        rows = [{"id": "run-mine", "session_id": "mine"}, {"id": "run-foreign", "session_id": "foreign"}]
        return {"runs": [row for row in rows if not session_id or row["session_id"] == session_id]}
    assert request(fixture, "GET", "/api/client/runs/missing").status_code == 404
    assert request(fixture, "GET", "/api/client/runs/run-foreign").status_code == 404
    assert request(fixture, "GET", "/api/client/runs/run-mine?sessionId=foreign").status_code == 404
    assert request(fixture, "GET", "/api/client/runs?sessionId=mine&session_id=foreign").status_code == 400
    assert request(fixture, "GET", "/api/client/runs?session_id=foreign").status_code == 404
    assert request(fixture, "GET", "/api/client/runs?sessionId=mine").json()["runs"] == [{"id": "run-mine", "session_id": "mine"}]
    assert [row["id"] for row in request(fixture, "GET", "/api/client/runs").json()["runs"]] == ["run-mine"]


def test_desktop_live_adapter_keeps_camel_wire_and_per_device_viewer(fixture, monkeypatch):
    from api import desktop_live_routes
    from core import desktop_live

    class DesktopFixture:
        sessions = {}
        offers = []
        def get_status(self):
            return {"available": True}
        def create_session(self, viewer):
            self.sessions["live-one"] = SimpleNamespace(viewer_id=viewer)
            return {"sessionId": "live-one"}
        def touch_session(self, session_id):
            if session_id not in self.sessions:
                raise RuntimeError("gone")
            return self.sessions[session_id]
        async def create_webrtc_answer(self, session_id, viewer, sdp, kind):
            self.offers.append((session_id, viewer, sdp, kind))
            return {"type": "answer", "sdp": "fixture-answer"}
        async def add_webrtc_candidate(self, session_id, candidate):
            return {"sessionId": session_id, "candidate": candidate}
        async def delete_session(self, session_id):
            return self.sessions.pop(session_id, None) is not None

    service = DesktopFixture()
    monkeypatch.setattr(desktop_live, "desktop_live_service", service)
    monkeypatch.setattr(desktop_live_routes, "_get_desktop_live_service", lambda: service)
    fixture.internal.include_router(desktop_live_routes.router)
    fixture.app = create_phone_gateway_app(client_app=fixture.app)
    assert request(fixture, "POST", "/api/client/desktop-live/prepare").json()["prepared"] is True
    created = request(fixture, "POST", "/api/client/desktop-live/session", json={"viewer_id": "forged"})
    assert created.status_code == 200
    viewer = f"{fixture.owner['id']}:{fixture.pair['deviceId']}"
    assert service.sessions["live-one"].viewer_id == viewer
    offer = request(fixture, "POST", "/api/client/desktop-live/offer", json={"sessionId": "live-one", "viewer_id": "forged", "sdp": "offer"})
    assert offer.status_code == 200 and service.offers == [("live-one", viewer, "offer", "offer")]
    original = fixture.pair
    fixture.pair = fixture.service.create_session(name="Second phone")
    for method, path, data in (("POST", "offer", {"sessionId": "live-one", "sdp": "never"}),
                               ("GET", "stream?sessionId=live-one", None),
                               ("DELETE", "session/live-one", None)):
        assert request(fixture, method, "/api/client/desktop-live/" + path, **({"json": data} if data else {})).status_code == 403
    assert len(service.offers) == 1
    fixture.pair = original
    released = request(fixture, "POST", "/api/client/desktop-live/release", json={"sessionId": "live-one"})
    assert released.status_code == 200 and released.json()["success"] is True
    assert not service.sessions


def test_desktop_stream_revocation_stops_capture_before_next_frame(fixture, monkeypatch):
    from api import desktop_live_routes
    captures = []
    def capture(session_id):
        captures.append(session_id)
        return b"fixture-frame"
    monkeypatch.setattr(desktop_live_routes, "_get_desktop_live_service", lambda: SimpleNamespace(capture_frame=capture))
    principal = fixture.service.verify_access(fixture.pair["accessToken"])
    async def consume():
        chunks = []
        async for chunk in desktop_live_routes._frame_stream("live-one", principal):
            chunks.append(chunk)
            if chunk == b"fixture-frame":
                fixture.service.revoke(fixture.owner["id"], fixture.pair["deviceId"])
        return b"".join(chunks)
    wire = asyncio.run(consume())
    assert captures == ["live-one"]
    assert b"device_session_revoked" in wire


def test_phone_rpa_routes_use_native_schemas_and_canonical_owner(fixture, monkeypatch):
    from api import rpa_routes
    calls = []
    def run(**kwargs):
        calls.append(kwargs)
        return {"accepted": True, **kwargs}
    runtime = SimpleNamespace(
        availability=lambda: {"available": True}, list_drafts=lambda **kwargs: [{"id": "draft-one"}],
        list_templates=lambda **kwargs: [{"id": "template-one"}],
        template_service=SimpleNamespace(summarize_templates=lambda rows: {"count": len(rows)}),
        list_robot_scripts=lambda **kwargs: [{"id": "script-one"}],
        compile_trace_to_draft=lambda run_id, save: {"runId": run_id, "saved": save},
        compile_traces_to_draft=lambda run_ids, save: {"runIds": run_ids, "saved": save},
        run_draft=run, run_template=run, run_existing_flow=run,
    )
    monkeypatch.setattr(rpa_routes, "_rpa_runtime", lambda: runtime)
    monkeypatch.setattr(rpa_routes, "_availability_cache", None)
    monkeypatch.setattr(rpa_routes, "_availability_task", None)
    fixture.internal.include_router(rpa_routes.router)
    fixture.app = create_phone_gateway_app(client_app=fixture.app)
    for endpoint, key in (("availability", "available"), ("drafts", "drafts"), ("templates", "templates"), ("scripts", "scripts")):
        response = request(fixture, "GET", "/api/client/rpa/" + endpoint)
        assert response.status_code == 200 and response.json()[key]
    assert request(fixture, "POST", "/api/client/rpa/compile/run-foreign", json={}).status_code == 404
    assert request(fixture, "POST", "/api/client/rpa/compile", json={"runIds": ["run-mine"]}).json()["runIds"] == ["run-mine"]
    for endpoint, extra in (("drafts/draft-one/run", {}), ("templates/template-one/run", {}), ("run-existing", {"robotFile": "fixture.robot"})):
        response = request(fixture, "POST", "/api/client/rpa/" + endpoint,
                           json={"sessionId": "mine", "userId": "forged", **extra})
        assert response.status_code == 200, response.text
        assert calls[-1]["session_id"] == "mine" and calls[-1]["user_id"] == fixture.owner["id"]
    before = len(calls)
    assert request(fixture, "POST", "/api/client/rpa/drafts/draft-one/run", json={"sessionId": "foreign"}).status_code == 404
    assert len(calls) == before


def test_signed_resource_range_and_revocation_without_bearer(fixture, tmp_path):
    from fastapi.responses import FileResponse
    from core.client_identity.resources import sign_resource_url
    asset = tmp_path / "video.mp4"
    asset.write_bytes(b"0123456789")
    @fixture.internal.get("/artifacts/{artifact_id}/content")
    async def content(artifact_id: str):
        return FileResponse(asset, media_type="video/mp4")
    principal = fixture.service.verify_access(fixture.pair["accessToken"])
    path = sign_resource_url(fixture.service, "/api/client/artifacts/video/content?sessionId=mine", principal, session_id="mine")
    fixture.app = create_phone_gateway_app(client_app=fixture.app)
    response = request(fixture, "GET", path, token=False, headers={"range": "bytes=2-5"})
    assert response.status_code == 206 and response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert "etag" in response.headers
    changed = request(fixture, "GET", path.replace("sessionId=mine", "sessionId=foreign"), token=False)
    assert changed.status_code == 401
    fixture.service.revoke(fixture.owner["id"], fixture.pair["deviceId"])
    assert request(fixture, "GET", path, token=False).status_code == 401


def test_multipart_upload_preserves_bytes_and_canonical_owner(fixture):
    @fixture.internal.post("/chat/upload")
    async def upload(req: Request):
        async with req.form() as form:
            upload_file = form["file"]
            data = await upload_file.read()
        assert form["sessionId"] == "mine"
        assert req.scope["state"]["engine_auth_context"].subject == fixture.owner["id"]
        assert req.headers["x-v8-agent-os-user-email"] == "owner"
        return {"size": len(data), "bytes": list(data)}
    response = request(fixture, "POST", "/api/client/upload", data={"sessionId": "mine"},
                       files={"file": ("raw.bin", b"\x00\xff\x0a", "application/octet-stream")})
    assert response.status_code == 200 and response.json() == {"size": 3, "bytes": [0, 255, 10]}


def test_signed_markdown_and_resource_ref_keep_readable_wire(fixture):
    principal = fixture.service.verify_access(fixture.pair["accessToken"])
    scope = {"type": "http", "method": "GET", "path": "/api/client/conversations/mine", "query_string": b"", "headers": [],
             "app": fixture.app, "state": {"client_session_id": "mine"}, "server": ("engine.local", 80), "scheme": "http"}
    value = client_routes.normalize_client_surface({"content": "![clip](http://127.0.0.1:9530/workspace/clip.mp4)",
        "resourceRef": {"adminPath": "/api/client/artifacts/clip/content?sessionId=mine"}}, Request(scope), principal)
    assert "127.0.0.1" not in value["content"]
    assert "/api/client/workspace/files/clip.mp4?" in value["content"] and "v8sig=" in value["content"]
    assert value["resourceRef"]["adminPath"] == "/api/client/artifacts/clip/content?sessionId=mine"
    assert "v8sig=" in value["resourceRef"]["signedUrl"]


def test_sse_detects_gap_inside_replay_page_and_refreshes_authority(fixture, monkeypatch):
    original = client_realtime.internal_json
    state = {"read": False}
    async def native(req, principal, path, **kwargs):
        if path.endswith("/runtime-events"):
            state["read"] = True
            return {"events": [{"seq": 3, "topic": "run.text.delta", "payload": {"content": "A"}},
                               {"seq": 5, "topic": "run.finished", "payload": {}}], "latestSeq": 5}
        value = await original(req, principal, path, **kwargs)
        if path.endswith("/snapshot") and state["read"]:
            value["latestSeq"] = 5
        return value
    monkeypatch.setattr(client_realtime, "internal_json", native)
    scope = {"type": "http", "method": "GET", "path": "/api/client/realtime/sessions/mine/stream", "query_string": b"",
             "headers": [(b"authorization", ("Bearer " + fixture.pair["accessToken"]).encode())],
             "app": fixture.app, "state": {}, "server": ("engine.local", 80), "scheme": "http"}
    async def run():
        chunks = []
        async def receive():
            await asyncio.sleep(60)
            return {"type": "http.disconnect"}
        async def send(message):
            if message["type"] == "http.response.body":
                text = message.get("body", b"").decode()
                chunks.append(text)
                if "snapshot_gap" in text:
                    fixture.service.revoke(fixture.owner["id"], fixture.pair["deviceId"])
        await asyncio.wait_for(fixture.app(scope, receive, send), timeout=2)
        wire = "".join(chunks)
        assert "snapshot_gap" in wire and "id: mine:5" in wire
        assert "event: runtime" not in wire
    asyncio.run(run())
