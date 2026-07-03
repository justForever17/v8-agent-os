from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from acp_bridge.backend import AdminBffBackend, V8PromptResult, V8PromptUpdate, V8SessionRef
from acp_bridge.bridge import AcpBridge
from acp_bridge.stdio_server import run_stdio_server
from api.session_workflow_routes import _is_hidden_compat_session


class FakeBackend:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.prompts: list[dict] = []
        self.cancelled: list[dict] = []

    def create_session(self, *, title, workspace_path, metadata):
        self.created.append({"title": title, "workspacePath": workspace_path, "metadata": metadata})
        return V8SessionRef(session_id="v8_session_1", workspace_path=workspace_path, title=title)

    def load_session(self, *, session_id):
        return V8SessionRef(session_id=session_id, workspace_path="E:/Projects/v8chat/test", title="Loaded")

    def submit_prompt(self, *, session_id, prompt, metadata):
        self.prompts.append({"sessionId": session_id, "prompt": prompt, "metadata": metadata})
        return V8PromptResult(
            accepted=True,
            session_id=session_id,
            run_id="run_acp",
            updates=[
                V8PromptUpdate(
                    kind="tool_result",
                    text="已读取相关文件，下一步会整理修改点。",
                    status="completed",
                    run_id="run_acp",
                    tool_call_id="call_v8_read",
                    detail_ref="detail://read",
                )
            ],
        )

    def cancel_session(self, *, session_id, run_id=None):
        self.cancelled.append({"sessionId": session_id, "runId": run_id})
        return {"ok": True, "status": "cancelled", "runId": run_id}


def _rpc(bridge: AcpBridge, method: str, params: dict | None = None, request_id: int = 1):
    return [item.as_dict() for item in bridge.handle_json_rpc({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    })]


class CaptureAdminBackend(AdminBffBackend):
    def __init__(self) -> None:
        super().__init__(admin_url="http://admin.local", engine_url="http://engine.local", bearer_token="test")
        self.requests: list[dict] = []

    def _request(self, method: str, url: str, payload: dict | None = None):
        self.requests.append({"method": method, "url": url, "payload": payload})
        if url.endswith("/api/client/conversations"):
            return {"id": "v8_acp_session"}
        if url.endswith("/api/client/chat-submit"):
            return {"accepted": True, "runId": "run_acp", "conversationId": payload.get("conversationId")}
        if url.endswith("/runs/cancel"):
            return {"ok": True, "status": "cancelled", "runId": payload.get("runId")}
        return {}


def test_initialize_exposes_external_adapter_not_internal_runtime_protocol():
    bridge = AcpBridge(backend=FakeBackend())

    messages = _rpc(bridge, "initialize", {"clientInfo": {"name": "mock-editor"}})

    result = messages[-1]["result"]
    assert result["agent"]["name"] == "V8OS 编程助手"
    assert result["protocolVersion"] == 1
    assert result["agentInfo"]["name"] == "V8OS 编程助手"
    assert result["agentCapabilities"]["loadSession"] is True
    assert result["capabilities"]["sessions"] is True
    assert result["_meta"]["v8os"]["canonicalId"] == "acp_bridge"
    assert result["_meta"]["v8os"]["surface"] == "third_party_agent_client"
    assert result["_meta"]["v8os"]["launch"]["command"] == "v8os acp"


def test_session_new_prompt_update_and_cancel_use_v8_session_mapping():
    backend = FakeBackend()
    bridge = AcpBridge(backend=backend)
    workspace = str(Path("E:/Projects/v8chat/v8-agent-os").resolve())

    created = _rpc(bridge, "session/new", {"title": "ACP Smoke", "workspacePath": workspace})[-1]["result"]
    prompt_messages = _rpc(bridge, "session/prompt", {"sessionId": created["sessionId"], "prompt": "检查项目"}, request_id=2)
    cancelled = _rpc(bridge, "session/cancel", {"sessionId": created["sessionId"]}, request_id=3)[-1]["result"]

    assert backend.created[0]["workspacePath"] == workspace
    assert backend.created[0]["metadata"]["source"] == "acp_bridge"
    assert backend.prompts[0]["sessionId"] == "v8_session_1"
    assert backend.prompts[0]["metadata"]["acpSessionId"] == created["sessionId"]
    assert prompt_messages[0]["method"] == "session/update"
    update = prompt_messages[0]["params"]["update"]
    assert update["content"] == "已读取相关文件，下一步会整理修改点。"
    assert not update["content"].startswith("{")
    assert update["_meta"]["v8os"]["toolCallId"] == "call_v8_read"
    assert prompt_messages[-1]["result"]["runId"] == "run_acp"
    assert cancelled["requestedRunId"] == "run_acp"
    assert cancelled["cancelledRunId"] == "run_acp"
    assert cancelled["status"] == "cancelled"
    assert backend.cancelled == [{"sessionId": "v8_session_1", "runId": "run_acp"}]


def test_admin_backend_marks_acp_sessions_and_cancels_specific_run():
    backend = CaptureAdminBackend()

    session = backend.create_session(
        title="ACP Session",
        workspace_path="E:/Projects/v8chat/v8-agent-os",
        metadata={"scopeHint": "workspace"},
    )
    result = backend.submit_prompt(
        session_id=session.session_id,
        prompt="检查项目",
        metadata={"acpSessionId": "acp_test", "workspacePath": "E:/Projects/v8chat/v8-agent-os"},
    )
    cancelled = backend.cancel_session(session_id=session.session_id, run_id=result.run_id)

    create_payload = backend.requests[0]["payload"]
    prompt_payload = backend.requests[1]["payload"]
    cancel_payload = backend.requests[2]["payload"]
    assert create_payload["externalSurface"] == "acp_bridge"
    assert create_payload["metadata"]["historyGroup"] == "external_agent_clients"
    assert prompt_payload["data"]["compatIngressDiagnostics"]["externalSurface"] == "acp_bridge"
    assert cancel_payload == {"sessionId": "v8_acp_session", "runId": "run_acp"}
    assert cancelled["status"] == "cancelled"


def test_acp_session_is_external_history_group_not_hidden_network_compat():
    assert _is_hidden_compat_session(
        {"id": "acp_visible"},
        {"externalSurface": "acp_bridge", "source": "acp_bridge", "historyGroup": "external_agent_clients"},
    ) is False
    assert _is_hidden_compat_session(
        {"id": "network_openai_hidden"},
        {"transport": "network_supervisor_openai"},
    ) is True


def test_session_load_can_restore_existing_v8_session():
    bridge = AcpBridge(backend=FakeBackend())

    loaded = _rpc(bridge, "session/load", {"v8SessionId": "v8_existing"})[-1]["result"]

    assert loaded["created"] is False
    assert loaded["_meta"]["v8os"]["sessionId"] == "v8_existing"
    assert loaded["title"] == "Loaded"


def test_relative_workspace_path_is_rejected_before_backend():
    backend = FakeBackend()
    bridge = AcpBridge(backend=backend)

    messages = _rpc(bridge, "session/new", {"workspacePath": "relative/path"})

    assert "error" in messages[-1]
    assert messages[-1]["error"]["code"] == -32602
    assert backend.created == []


def test_permission_classification_keeps_approval_ask_user_and_spec_separate():
    bridge = AcpBridge(backend=FakeBackend())

    safety = _rpc(bridge, "_v8os/permission/classify", {"kind": "safety.file_write"})[-1]["result"]
    ask_user = _rpc(bridge, "_v8os/permission/classify", {"kind": "ask_user"}, request_id=2)[-1]["result"]
    spec = _rpc(bridge, "_v8os/permission/classify", {"kind": "spec_approval"}, request_id=3)[-1]["result"]

    assert safety == {
        "kind": "permission",
        "mapsToAcpPermission": True,
        "mapsToAskUser": False,
        "mapsToSpecApproval": False,
    }
    assert ask_user["mapsToAcpPermission"] is False
    assert ask_user["mapsToAskUser"] is True
    assert spec["mapsToAcpPermission"] is False
    assert spec["mapsToSpecApproval"] is True


def test_permission_request_and_response_are_acp_native_events():
    bridge = AcpBridge(backend=FakeBackend())
    created = _rpc(
        bridge,
        "session/new",
        {"title": "ACP Permission", "workspacePath": str(Path("E:/Projects/v8chat/v8-agent-os").resolve())},
    )[-1]["result"]

    requested = _rpc(
        bridge,
        "_v8os/permission/request",
        {
            "sessionId": created["sessionId"],
            "kind": "safety.command",
            "reason": "需要运行项目测试命令。",
            "action": "run command",
        },
        request_id=2,
    )
    request_result = requested[-1]["result"]

    assert request_result["status"] == "pending"
    assert request_result["permissionId"].startswith("perm_")
    assert requested[0]["method"] == "session/request_permission"
    assert requested[0]["id"] == request_result["requestId"]
    assert requested[0]["params"]["options"][0]["optionId"] == "approve"
    assert requested[1]["method"] == "session/update"

    responded = bridge.handle_json_rpc({
        "jsonrpc": "2.0",
        "id": request_result["requestId"],
        "result": {"outcome": {"optionId": "approve"}},
    })
    responded_payload = responded[0].as_dict()

    assert responded_payload["params"]["update"]["kind"] == "permission_response"
    assert responded_payload["params"]["update"]["status"] == "approved"


def test_ask_user_and_spec_approval_are_not_mapped_to_acp_permission_request():
    bridge = AcpBridge(backend=FakeBackend())
    created = _rpc(
        bridge,
        "session/new",
        {"title": "ACP Permission", "workspacePath": str(Path("E:/Projects/v8chat/v8-agent-os").resolve())},
    )[-1]["result"]

    result = _rpc(
        bridge,
        "_v8os/permission/request",
        {"sessionId": created["sessionId"], "kind": "spec_approval"},
        request_id=2,
    )[-1]["result"]

    assert result["status"] == "not_acp_permission"
    assert result["recommendedChannel"] == "spec_approval"


def test_runtime_event_projection_is_compact_markdown_not_raw_json():
    bridge = AcpBridge(backend=FakeBackend())

    message = bridge.project_runtime_event(
        acp_session_id="acp_test",
        event={
            "topic": "tool.result",
            "session_id": "v8_session",
            "run_id": "run_1",
            "payload": {
                "summary": '{"providerRawResponse": {"huge": true}}',
                "toolCallId": "call_v8_tool",
                "rawRef": "raw://tool",
            },
        },
    ).as_dict()

    update = message["params"]["update"]
    assert update["content"] == "tool.result"
    assert update["_meta"]["v8os"]["detailRef"] == "raw://tool"
    assert "providerRawResponse" not in update["content"]


def test_runtime_event_projection_carries_artifacts_and_file_changes():
    bridge = AcpBridge(backend=FakeBackend())

    artifact_message = bridge.project_runtime_event(
        acp_session_id="acp_test",
        event={
            "topic": "artifact.created",
            "payload": {
                "artifacts": [
                    {
                        "artifactId": "art_1",
                        "title": "demo.mp4",
                        "mimeType": "video/mp4",
                        "downloadUrl": "/api/artifacts/art_1/content",
                    }
                ],
            },
        },
    ).as_dict()
    file_message = bridge.project_runtime_event(
        acp_session_id="acp_test",
        event={
            "topic": "files.changed",
            "payload": {
                "fileChanges": [
                    {"path": "src/App.tsx", "status": "modified", "additions": 4, "deletions": 1}
                ],
            },
        },
    ).as_dict()

    artifact_update = artifact_message["params"]["update"]
    file_update = file_message["params"]["update"]
    assert artifact_update["kind"] == "artifact"
    assert artifact_update["artifacts"][0]["id"] == "art_1"
    assert "providerRawResponse" not in artifact_update["content"]
    assert file_update["kind"] == "file_edit"
    assert file_update["fileChanges"][0]["path"] == "src/App.tsx"


def test_terminal_methods_map_to_client_terminal_broker(monkeypatch):
    import core.client_terminal_broker as terminal

    created = {
        "ok": True,
        "sessionId": "term_test",
        "commandId": "cmd_test",
        "status": "running",
        "outputDelta": "ready",
        "screenSnapshot": "PS>",
        "isRunning": True,
        "cols": 80,
        "rows": 24,
        "cwd": "E:/Projects/v8chat",
    }
    monkeypatch.setattr(terminal, "create_terminal_session", lambda **_: created)
    monkeypatch.setattr(terminal, "write_terminal_session_input", lambda session_id, input_text: {**created, "outputDelta": input_text})
    monkeypatch.setattr(terminal, "resize_terminal_session", lambda session_id, cols, rows: {**created, "cols": cols, "rows": rows})
    monkeypatch.setattr(terminal, "terminate_terminal_session", lambda session_id: {**created, "status": "stopped", "isRunning": False})

    bridge = AcpBridge(backend=FakeBackend())

    terminal_created = _rpc(bridge, "_v8os/terminal/create", {"cwd": "E:/Projects/v8chat"})[-1]["result"]
    typed = _rpc(bridge, "_v8os/terminal/input", {"terminalId": "term_test", "input": "\x03"}, request_id=2)[-1]["result"]
    resized = _rpc(bridge, "_v8os/terminal/resize", {"terminalId": "term_test", "cols": 132, "rows": 37}, request_id=3)[-1]["result"]
    killed = _rpc(bridge, "_v8os/terminal/kill", {"terminalId": "term_test"}, request_id=4)[-1]["result"]

    assert terminal_created["terminalId"] == "term_test"
    assert terminal_created["output"] == "ready"
    assert typed["output"] == "\x03"
    assert resized["cols"] == 132
    assert resized["rows"] == 37
    assert killed["isRunning"] is False


def test_stdio_server_accepts_newline_json_rpc():
    backend = FakeBackend()
    bridge = AcpBridge(backend=backend)
    stdin = StringIO(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
    stdout = StringIO()

    assert run_stdio_server(stdin=stdin, stdout=stdout, bridge=bridge) == 0

    payload = json.loads(stdout.getvalue().strip())
    assert payload["id"] == 1
    assert payload["result"]["agent"]["displayName"] == "V8OS Agent"


def _content_length_frame(payload: dict) -> str:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"


def _content_length_payload(output: str) -> dict:
    header, sep, body = output.partition("\r\n\r\n")
    assert sep
    name, _, value = header.partition(":")
    assert name.lower() == "content-length"
    assert len(body.encode("utf-8")) == int(value.strip())
    return json.loads(body)


def test_stdio_server_accepts_content_length_json_rpc():
    backend = FakeBackend()
    bridge = AcpBridge(backend=backend)
    stdin = StringIO(_content_length_frame({"jsonrpc": "2.0", "id": "测试-7", "method": "initialize", "params": {}}))
    stdout = StringIO()

    assert run_stdio_server(stdin=stdin, stdout=stdout, bridge=bridge) == 0

    payload = _content_length_payload(stdout.getvalue())
    assert payload["id"] == "测试-7"
    assert payload["result"]["agent"]["displayName"] == "V8OS Agent"
