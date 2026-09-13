"""Real CLI/stdio/HTTP transport with a deterministic BFF, no real user state."""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parents[2]
CLI = ENGINE.parent / "v8-agent-os-cli" / "bin" / "v8os.mjs"


@pytest.fixture
def bff(tmp_path):
    state = {"requests": [], "workspace": str(tmp_path), "mode": "normal", "cancel": threading.Event(), "response": threading.Event(), "cancel_subscribed": threading.Event(), "cancel_terminal": threading.Event()}
    state["cancel_terminal"].set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload, status=200):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def stream(self, events, sse=False):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if sse else "application/x-ndjson")
            self.end_headers()
            for event in events:
                body = json.dumps(event, ensure_ascii=False)
                self.wfile.write((f"data: {body}\n\n" if sse else body + "\n").encode())
                self.wfile.flush()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            state["requests"].append((self.path, body))
            if self.path == "/api/client/auth/local-session":
                assert body == {"surface": "cli", "deviceName": "v8os-acp"}
                self.reply({"accessToken": "fixture-client-token"})
                return
            assert self.headers.get("Authorization") == "Bearer fixture-client-token"
            if self.path == "/api/client/projects":
                assert body["workspaceTrustState"] == "trusted"
                self.reply({"id": "project1", "workspaceId": "workspace1"})
            elif self.path == "/api/client/conversations":
                assert body["projectId"] == "project1" and body["workspaceId"] == "workspace1"
                state["workspace"] = body["workspacePath"]
                self.reply({"id": "session1"})
            elif self.path == "/api/client/chat":
                text = body["messages"][0]["content"]
                state["mode"] = text

                def events():
                    yield {"type": "agent_start", "runId": "run1", "data": {"summary": "starting"}}
                    yield {"type": "reasoning_chunk", "runId": "run1", "content": "先验证\n"}
                    if text == "cancel":
                        assert state["cancel"].wait(6), "cancel was blocked behind prompt"
                        yield {"type": "done", "runId": "run1", "status": "cancelled"}
                        return
                    if text in {"approval", "approval_missing", "ask"}:
                        if text != "approval_missing":
                            yield {"type": "custom_event", "name": "approval_requested" if text == "approval" else "ask_user", "runId": "run1", "data": {"id": "approval1" if text == "approval" else "ask1", "question": "批准写入?" if text == "approval" else "选哪个颜色?"}}
                        yield {"type": "done", "runId": "run1", "status": "waiting_input" if text == "ask" else "waiting_approval"}
                        return
                    yield {"type": "text_chunk", "runId": "run1", "messageId": "m2", "content": "中文首段\n"}
                    if text == "eof":
                        return
                    if text == "failure":
                        yield {"type": "done", "runId": "run1", "status": "failed"}
                        return
                    yield {"type": "tool_start", "runId": "run1", "tool": {"toolCallId": "t1", "toolName": "read", "args": {"path": "demo"}}}
                    yield {"type": "tool_result", "runId": "run1", "tool": {"toolCallId": "t1", "toolName": "read", "resultStatus": "completed", "agentVisibleResult": "long result\n" * 1000}}
                    yield {"type": "text_chunk", "runId": "run1", "messageId": "m2", "content": "最后一段\n"}
                    yield {"type": "done", "runId": "run1", "status": "finished"}

                self.stream(events())
            elif self.path == "/api/client/runs/run1/commands/cancel":
                state["cancel"].set()
                self.reply({"ok": True, "status": "cancelled"})
            elif self.path in {"/api/client/approvals/approval1/approve", "/api/client/ask-user/ask1/respond"}:
                state["response"].set()
                self.reply({"ok": True, "resume_scheduled": True, "resumed_run_id": "run1"})
            elif self.path == "/api/client/approvals/approval1/reject":
                self.reply({"ok": True, "status": "rejected"})
            else:
                self.reply({"error": "unexpected path"}, 404)

        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer fixture-client-token"
            if self.path.startswith("/api/client/approvals?"):
                assert "run_id=run1" in self.path and "session_id=session1" in self.path
                self.reply({"approvals": [{"id": "approval1", "run_id": "run1", "status": "pending", "request": {"question": "批准写入?"}}]})
            elif self.path == "/api/client/realtime/sessions/session1/stream":
                if state["cancel"].is_set():
                    state["cancel_subscribed"].set()
                    assert state["cancel_terminal"].wait(5)
                    self.stream([{"type": "done", "runId": "run1", "status": "cancelled"}], sse=True)
                    return
                assert state["response"].is_set(), "resumed before real permission response"
                self.stream([
                    {"type": "custom_event", "name": "ask_user", "runId": "run1", "status": "resolved", "data": {"id": "ask1", "status": "resolved"}},
                    {"type": "text_chunk", "runId": "unrelated", "content": "must not leak"},
                    {"type": "text_chunk", "runId": "run1", "content": "已按输入完成\n"},
                    {"type": "snapshot", "data": {"currentRun": {"id": "run1", "status": "completed"}, "messages": [
                        {"role": "user", "runId": "run1", "content": "do not replay user at resume"},
                        {"role": "assistant", "runId": "run1", "content": "已按输入完成\n"},
                    ]}},
                ], sse=True)
            elif self.path == "/api/client/conversations/session1":
                self.reply({"id": "session1", "summary": {"workspacePath": state["workspace"]}, "messages": [
                    {"id": "m1", "role": "user", "content": "原始问题\n"},
                    {"id": "m2", "role": "assistant", "content": "中文首段\n最后一段\n"},
                ]})
            else:
                self.reply({"error": "unexpected path"}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", state
    server.shutdown()
    server.server_close()


class Client:
    def __init__(self, url, workspace):
        env = {k: v for k, v in os.environ.items() if k not in {"V8OS_CLIENT_TOKEN", "V8OS_ADMIN_TOKEN"}}
        env.update({"V8OS_ADMIN_URL": url, "V8_ENGINE_PYTHON": str(ENGINE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))})
        self.process = subprocess.Popen([shutil.which("node") or "node", str(CLI), "acp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", env=env, cwd=workspace)
        self.incoming = queue.Queue()
        threading.Thread(target=self.read, daemon=True).start()
        self.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1, "clientCapabilities": {"elicitation": {"form": {}}}}})
        assert self.until_response(1)[-1]["result"]["protocolVersion"] == 1
        self.send({"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": str(workspace), "mcpServers": []}})
        assert self.until_response(2)[-1]["result"]["sessionId"] == "session1"

    def read(self):
        for line in self.process.stdout:
            try:
                self.incoming.put(json.loads(line))
            except Exception:
                self.incoming.put({"invalid_stdout": line})

    def send(self, payload):
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def prompt(self, text, request_id=3):
        self.send({"jsonrpc": "2.0", "id": request_id, "method": "session/prompt", "params": {"sessionId": "session1", "prompt": [{"type": "text", "text": text}]}})

    def until_response(self, request_id):
        result = []
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = self.incoming.get(timeout=max(0.01, deadline-time.monotonic()))
            assert "invalid_stdout" not in message
            result.append(message)
            if message.get("id") == request_id and ("result" in message or "error" in message):
                return result
        pytest.fail(f"ACP response {request_id} did not arrive")

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=7)
        except subprocess.TimeoutExpired:
            self.process.kill()
            raise
        stderr = self.process.stderr.read()
        assert "fixture-client-token" not in stderr
        assert self.process.returncode == 0, stderr


@pytest.fixture
def client(bff, tmp_path):
    client = Client(bff[0], tmp_path)
    yield client
    client.close()


def test_real_cli_streams_full_utf8_tool_results_and_reloads_history(client, bff, tmp_path):
    client.prompt("normal")
    result = client.until_response(3)
    updates = [m["params"]["update"] for m in result if m.get("method") == "session/update"]
    assert "".join(u["content"]["text"] for u in updates if u["sessionUpdate"] == "agent_message_chunk") == "中文首段\n最后一段\n"
    tool = next(u for u in updates if u["sessionUpdate"] == "tool_call_update" and u["toolCallId"] == "t1")
    assert tool["content"][0]["content"]["text"] == "long result\n" * 1000
    assert result[-1]["result"]["stopReason"] == "end_turn"
    client.send({"jsonrpc": "2.0", "id": 4, "method": "session/load", "params": {"sessionId": "session1", "cwd": str(tmp_path), "mcpServers": []}})
    replay = client.until_response(4)
    assert [m["params"]["update"]["sessionUpdate"] for m in replay[:-1]] == ["user_message_chunk", "agent_message_chunk"]
    assert all("fixture-client-token" not in str(m) for m in result + replay)


@pytest.mark.parametrize("mode", ["failure", "eof"])
def test_real_cli_preserves_partial_then_reports_failure(client, mode):
    client.prompt(mode)
    result = client.until_response(3)
    assert any(m.get("params", {}).get("update", {}).get("content", {}).get("text") == "中文首段\n" for m in result if m.get("params", {}).get("update", {}).get("sessionUpdate") == "agent_message_chunk")
    assert "error" in result[-1]


def test_cancel_is_read_while_prompt_waits_and_targets_actual_run(client, bff):
    client.prompt("cancel")
    assert client.incoming.get(timeout=5)["method"] == "session/update"
    client.send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "session1"}})
    result = client.until_response(3)
    assert result[-1]["result"]["stopReason"] == "cancelled"
    assert bff[1]["cancel"].is_set()


@pytest.mark.parametrize("mode,expected_method", [("approval", "session/request_permission"), ("approval_missing", "session/request_permission"), ("ask", "elicitation/create")])
def test_permission_and_question_use_distinct_roundtrips_and_resume(client, bff, mode, expected_method):
    if mode.startswith("approval"):
        client.send({"jsonrpc": "2.0", "id": 20, "method": "session/set_mode", "params": {"sessionId": "session1", "modeId": "manual"}})
        assert client.until_response(20)[-1]["result"] == {}
    client.prompt(mode)
    while True:
        message = client.incoming.get(timeout=5)
        assert message.get("id") != 3, "prompt completed before interaction"
        if message.get("method") == expected_method:
            break
    assert not bff[1]["response"].is_set()
    result = {"outcome": {"outcome": "selected", "optionId": "approve"}} if mode.startswith("approval") else {"action": "accept", "content": {"answer": "蓝色"}}
    client.send({"jsonrpc": "2.0", "id": message["id"], "result": result})
    completed = client.until_response(3)
    assert completed[-1]["result"]["stopReason"] == "end_turn"
    assert bff[1]["response"].is_set()
    assert "must not leak" not in str(completed)
    assert "已按输入完成" in str(completed)
    assert str(completed).count("已按输入完成") == 1
    assert "do not replay user" not in str(completed)
    if mode == "ask":
        assert ("/api/client/ask-user/ask1/respond", {"answer": "蓝色"}) in bff[1]["requests"]
    else:
        chat = next(body for path, body in bff[1]["requests"] if path == "/api/client/chat")
        assert chat["data"]["safetyApprovalMode"] == "manual"


def test_unknown_permission_option_cannot_approve_or_resume(client, bff):
    client.prompt("approval")
    while True:
        message = client.incoming.get(timeout=5)
        if message.get("method") == "session/request_permission":
            break
    client.send({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "selected", "optionId": "invented-approval"}}})
    result = client.until_response(3)
    assert result[-1]["error"]["code"] == -32602
    assert not bff[1]["response"].is_set()


def test_same_session_concurrent_prompt_is_rejected_without_second_post(client, bff):
    client.prompt("cancel")
    client.incoming.get(timeout=5)
    client.prompt("second", request_id=4)
    assert client.until_response(4)[-1]["error"]
    assert sum(path == "/api/client/chat" for path, _ in bff[1]["requests"]) == 1
    client.send({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "session1"}})
    assert client.until_response(3)[-1]["result"]["stopReason"] == "cancelled"


def test_waiting_only_permission_cancel_waits_for_engine_terminal(client, bff):
    state = bff[1]
    state["cancel_terminal"].clear()
    client.prompt("approval_missing")
    while True:
        message = client.incoming.get(timeout=5)
        if message.get("method") == "session/request_permission":
            break
    client.send({"jsonrpc": "2.0", "id": message["id"], "result": {"outcome": {"outcome": "cancelled"}}})
    assert state["cancel_subscribed"].wait(5), "cancel did not reach the Engine and reconnect to its stream"
    queued = []
    while not client.incoming.empty():
        queued.append(client.incoming.get_nowait())
    assert not any(m.get("id") == 3 for m in queued), "cancel ACK was mistaken for stopped execution"
    assert not state["response"].is_set(), "cancel was mistaken for approval"
    state["cancel_terminal"].set()
    assert client.until_response(3)[-1]["result"]["stopReason"] == "cancelled"
