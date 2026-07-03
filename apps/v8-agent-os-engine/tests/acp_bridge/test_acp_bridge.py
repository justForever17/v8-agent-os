from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from acp_bridge.backend import V8PromptResult, V8PromptUpdate, V8SessionRef
from acp_bridge.bridge import AcpBridge
from acp_bridge.stdio_server import run_stdio_server


class FakeBackend:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.prompts: list[dict] = []
        self.cancelled: list[str] = []

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

    def cancel_session(self, *, session_id):
        self.cancelled.append(session_id)
        return {"ok": True, "status": "cancelled"}


def _rpc(bridge: AcpBridge, method: str, params: dict | None = None, request_id: int = 1):
    return [item.as_dict() for item in bridge.handle_json_rpc({
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    })]


def test_initialize_exposes_external_adapter_not_internal_runtime_protocol():
    bridge = AcpBridge(backend=FakeBackend())

    messages = _rpc(bridge, "initialize", {"clientInfo": {"name": "mock-editor"}})

    result = messages[-1]["result"]
    assert result["agent"]["name"] == "V8OS 编程助手"
    assert result["capabilities"]["sessions"] is True
    assert result["_meta"]["v8os"]["canonicalId"] == "acp_bridge"
    assert result["_meta"]["v8os"]["surface"] == "third_party_agent_client"


def test_session_new_prompt_update_and_cancel_use_v8_session_mapping():
    backend = FakeBackend()
    bridge = AcpBridge(backend=backend)
    workspace = str(Path("E:/Projects/v8chat/v8-agent-os").resolve())

    created = _rpc(bridge, "session/new", {"title": "ACP Smoke", "workspacePath": workspace})[-1]["result"]
    prompt_messages = _rpc(bridge, "session/prompt", {"sessionId": created["sessionId"], "prompt": "检查项目"}, request_id=2)
    cancelled = _rpc(bridge, "session/cancel", {"sessionId": created["sessionId"]}, request_id=3)[-1]["result"]

    assert backend.created[0]["workspacePath"] == workspace
    assert backend.prompts[0]["sessionId"] == "v8_session_1"
    assert backend.prompts[0]["metadata"]["acpSessionId"] == created["sessionId"]
    assert prompt_messages[0]["method"] == "session/update"
    update = prompt_messages[0]["params"]["update"]
    assert update["content"] == "已读取相关文件，下一步会整理修改点。"
    assert not update["content"].startswith("{")
    assert update["_meta"]["v8os"]["toolCallId"] == "call_v8_read"
    assert prompt_messages[-1]["result"]["runId"] == "run_acp"
    assert cancelled["status"] == "cancelled"
    assert backend.cancelled == ["v8_session_1"]


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
