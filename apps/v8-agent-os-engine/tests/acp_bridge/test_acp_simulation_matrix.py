from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from typing import Any

from acp_bridge.backend import V8PromptResult, V8PromptUpdate, V8SessionRef
from acp_bridge.bridge import AcpBridge
from acp_bridge.stdio_server import run_stdio_server
from acp_bridge.surface import compact_runtime_event


ACP_SIMULATION_MATRIX = [
    {
        "id": "transport.content_length.multi_frame",
        "axis": "transport",
        "risk": "Some editor bridges use LSP-style framed stdio; a parser bug can stall compatibility clients.",
        "coverage": "Two Content-Length requests are answered with framed responses.",
    },
    {
        "id": "transport.content_length.parse_error",
        "axis": "transport",
        "risk": "Malformed framed input must not crash the bridge or leak stack traces.",
        "coverage": "Invalid JSON returns a framed JSON-RPC parse error.",
    },
    {
        "id": "session.lifecycle.external_scope",
        "axis": "session",
        "risk": "ACP sessions must not become ordinary V8OS chat truth.",
        "coverage": "session/new -> session/prompt -> session/cancel stays externally tagged.",
    },
    {
        "id": "workspace.absolute_boundary",
        "axis": "workspace",
        "risk": "Editor clients must not bypass V8OS workspace trust with relative paths.",
        "coverage": "Relative workspacePath is rejected before backend calls.",
    },
    {
        "id": "permission.separation",
        "axis": "permission",
        "risk": "Safety permission, ask_user, and Spec approval have different control loops.",
        "coverage": "Only permission events map to ACP permission requests.",
    },
    {
        "id": "surface.raw_suppression",
        "axis": "surface",
        "risk": "ACP clients should receive readable summaries, not provider/runtime raw JSON.",
        "coverage": "Raw provider payload fields are suppressed while detailRef is preserved.",
    },
    {
        "id": "terminal.escape_sequences",
        "axis": "terminal",
        "risk": "Terminal bridging is unusable if Ctrl+C, Esc, or arrow keys are normalized away.",
        "coverage": "Control characters and ANSI sequences are passed through exactly.",
    },
    {
        "id": "errors.unknown_method",
        "axis": "error",
        "risk": "Unsupported ACP methods must fail cleanly with no side effects.",
        "coverage": "Unknown methods return -32601 and do not touch the backend.",
    },
]

REQUIRED_AXES = {"transport", "session", "workspace", "permission", "surface", "terminal", "error"}


class MatrixBackend:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self.cancelled: list[dict[str, Any]] = []

    def create_session(self, *, title: str | None, workspace_path: str | None, metadata: dict[str, Any]) -> V8SessionRef:
        self.created.append({"title": title, "workspacePath": workspace_path, "metadata": metadata})
        return V8SessionRef(session_id="v8_matrix_session", workspace_path=workspace_path, title=title)

    def load_session(self, *, session_id: str) -> V8SessionRef:
        return V8SessionRef(session_id=session_id, workspace_path="E:/Projects/v8chat/v8-agent-os", title="Loaded")

    def submit_prompt(self, *, session_id: str, prompt: str, metadata: dict[str, Any]) -> V8PromptResult:
        self.prompts.append({"sessionId": session_id, "prompt": prompt, "metadata": metadata})
        return V8PromptResult(
            accepted=True,
            session_id=session_id,
            run_id="run_matrix",
            updates=[
                V8PromptUpdate(
                    kind="status",
                    text="V8OS accepted the ACP prompt.",
                    status="accepted",
                    run_id="run_matrix",
                    detail_ref="detail://matrix",
                )
            ],
        )

    def cancel_session(self, *, session_id: str, run_id: str | None = None) -> dict[str, Any]:
        self.cancelled.append({"sessionId": session_id, "runId": run_id})
        return {"ok": True, "status": "cancelled", "runId": run_id}


def _rpc(method: str, params: dict[str, Any] | None = None, request_id: int | str = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def _content_length_frame(payload: dict[str, Any] | str) -> str:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"


def _parse_content_length_stream(output: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(output):
        header_end = output.find("\r\n\r\n", cursor)
        assert header_end >= 0, output[cursor:]
        header = output[cursor:header_end]
        length_line = next(line for line in header.splitlines() if line.lower().startswith("content-length:"))
        length = int(length_line.split(":", 1)[1].strip())
        body_start = header_end + 4
        body_chars: list[str] = []
        body_bytes = 0
        index = body_start
        while body_bytes < length:
            assert index < len(output), "Content-Length body ended early"
            char = output[index]
            body_chars.append(char)
            body_bytes += len(char.encode("utf-8"))
            index += 1
        assert body_bytes == length
        messages.append(json.loads("".join(body_chars)))
        cursor = index
    return messages


def test_acp_simulation_matrix_declares_required_axes():
    axes = {item["axis"] for item in ACP_SIMULATION_MATRIX}

    assert REQUIRED_AXES <= axes
    assert len({item["id"] for item in ACP_SIMULATION_MATRIX}) == len(ACP_SIMULATION_MATRIX)
    for item in ACP_SIMULATION_MATRIX:
        assert item["id"]
        assert item["risk"]
        assert item["coverage"]


def test_content_length_stream_accepts_multiple_frames_and_preserves_framing():
    backend = MatrixBackend()
    bridge = AcpBridge(backend=backend)
    workspace = str(Path("E:/Projects/v8chat/v8-agent-os").resolve())
    stdin = StringIO(
        _content_length_frame(_rpc("initialize", {"clientInfo": {"name": "matrix-client"}}, request_id=1))
        + _content_length_frame(_rpc("session/new", {"title": "ACP Matrix", "workspacePath": workspace}, request_id=2))
    )
    stdout = StringIO()

    assert run_stdio_server(stdin=stdin, stdout=stdout, bridge=bridge) == 0

    output = stdout.getvalue()
    assert output.startswith("Content-Length:")
    payloads = _parse_content_length_stream(output)
    assert [item["id"] for item in payloads] == [1, 2]
    assert payloads[0]["result"]["_meta"]["v8os"]["canonicalId"] == "acp_bridge"
    assert payloads[1]["result"]["created"] is True
    assert backend.created[0]["metadata"]["source"] == "acp_bridge"


def test_content_length_invalid_json_returns_framed_parse_error():
    stdin = StringIO(_content_length_frame('{"jsonrpc":'))
    stdout = StringIO()

    assert run_stdio_server(stdin=stdin, stdout=stdout, bridge=AcpBridge(backend=MatrixBackend())) == 0

    payloads = _parse_content_length_stream(stdout.getvalue())
    assert len(payloads) == 1
    assert payloads[0]["error"]["code"] == -32700
    assert "Invalid JSON-RPC payload" in payloads[0]["error"]["message"]


def test_unknown_method_is_protocol_error_and_has_no_side_effects():
    backend = MatrixBackend()
    bridge = AcpBridge(backend=backend)

    messages = [item.as_dict() for item in bridge.handle_json_rpc(_rpc("workspace/read", {"path": "README.md"}))]

    assert messages[-1]["error"]["code"] == -32601
    assert backend.created == []
    assert backend.prompts == []
    assert backend.cancelled == []


def test_terminal_input_preserves_control_sequences(monkeypatch):
    import core.client_terminal_broker as terminal

    recorded: list[str] = []

    def fake_input(session_id: str, input_text: str) -> dict[str, Any]:
        recorded.append(input_text)
        return {
            "ok": True,
            "sessionId": session_id,
            "commandId": "cmd_matrix",
            "status": "running",
            "outputDelta": "",
            "screenSnapshot": "",
            "isRunning": True,
            "cols": 80,
            "rows": 24,
        }

    monkeypatch.setattr(terminal, "write_terminal_session_input", fake_input)
    bridge = AcpBridge(backend=MatrixBackend())

    for request_id, sequence in enumerate(["\x03", "\x1b", "\x1b[A", "\r"], start=1):
        result = bridge.handle_json_rpc(_rpc("_v8os/terminal/input", {"terminalId": "term_matrix", "input": sequence}, request_id))
        assert result[-1].as_dict()["result"]["terminalId"] == "term_matrix"

    assert recorded == ["\x03", "\x1b", "\x1b[A", "\r"]


def test_surface_projection_suppresses_runtime_only_raw_fields():
    update = compact_runtime_event(
        {
            "topic": "tool.result",
            "session_id": "v8_matrix",
            "run_id": "run_matrix",
            "payload": {
                "summary": json.dumps(
                    {
                        "providerRawResponse": {"huge": True},
                        "approvalFingerprint": "secret",
                        "ledger": {"private": True},
                    }
                ),
                "rawRef": "raw://matrix",
                "status": "completed",
            },
        }
    )

    assert update["content"] == "tool.result"
    assert "providerRawResponse" not in update["content"]
    assert "approvalFingerprint" not in update["content"]
    assert update["_meta"]["v8os"]["detailRef"] == "raw://matrix"


def test_prompt_lifecycle_stays_external_and_cancel_targets_current_run():
    backend = MatrixBackend()
    bridge = AcpBridge(backend=backend)
    workspace = str(Path("E:/Projects/v8chat/v8-agent-os").resolve())
    created = bridge.handle_json_rpc(_rpc("session/new", {"title": "Lifecycle", "workspacePath": workspace}))[0].as_dict()["result"]

    prompt_messages = [item.as_dict() for item in bridge.handle_json_rpc(_rpc("session/prompt", {"sessionId": created["sessionId"], "prompt": "Run checks"}, 2))]
    cancelled = bridge.handle_json_rpc(_rpc("session/cancel", {"sessionId": created["sessionId"]}, 3))[0].as_dict()["result"]

    assert backend.prompts[0]["metadata"]["source"] == "acp_bridge"
    assert backend.prompts[0]["metadata"]["acpSessionId"] == created["sessionId"]
    assert prompt_messages[0]["method"] == "session/update"
    assert prompt_messages[-1]["result"]["runId"] == "run_matrix"
    assert cancelled["requestedRunId"] == "run_matrix"
    assert backend.cancelled == [{"sessionId": "v8_matrix_session", "runId": "run_matrix"}]


def test_workspace_boundary_rejects_relative_paths():
    backend = MatrixBackend()
    bridge = AcpBridge(backend=backend)

    messages = [item.as_dict() for item in bridge.handle_json_rpc(_rpc("session/new", {"workspacePath": "."}))]

    assert messages[-1]["error"]["code"] == -32602
    assert "workspacePath must be absolute" in messages[-1]["error"]["message"]
    assert backend.created == []
