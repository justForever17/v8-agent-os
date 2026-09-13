from __future__ import annotations

from types import SimpleNamespace

import core.client_terminal_broker as broker


class FakeTerminalProcess:
    def __init__(self) -> None:
        self.is_running = True
        self.written: list[str] = []
        self.output_reads = 0
        self.cols = 80
        self.rows = 24

    def status_snapshot(self) -> dict[str, object]:
        return {
            "stable_screen_snapshot": f"screen {self.cols}x{self.rows}",
            "screen_snapshot": f"raw {self.cols}x{self.rows}",
            "awaiting_input": False,
            "uses_tty": True,
            "tty_mode": "pty",
            "cols": self.cols,
            "rows": self.rows,
            "return_code": None,
            "started_at": "2026-06-30T00:00:00+00:00",
            "completed_at": None,
        }

    def get_new_output(self) -> str:
        self.output_reads += 1
        return f"delta-{self.output_reads}"

    def read_output(self, cursor=0, limit=65536):
        data = "delta-1"[cursor:cursor + limit]
        return {"data": data, "cursor": cursor + len(data), "hasMore": False, "generation": "fixture-generation", "totalBytes": 7}

    def write_input(self, value: str) -> None:
        self.written.append(value)

    def resize_terminal(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows


def _install_fake_session(monkeypatch):
    process = FakeTerminalProcess()
    monkeypatch.setattr(
        broker,
        "_manual_terminal_sessions",
        {
            "term_test": {
                "sessionId": "term_test",
                "commandId": "cmd_test",
                "profileId": "pwsh",
                "profileLabel": "PowerShell 7",
                "cwd": "E:/Projects/v8chat",
                "status": "running",
                "createdAt": "2026-06-30T00:00:00+00:00",
                "updatedAt": "2026-06-30T00:00:00+00:00",
            }
        },
    )
    monkeypatch.setattr(broker, "_bg_processes", {"cmd_test": process})
    monkeypatch.setattr(broker, "_prune_stale_background_processes", lambda: None)
    return process


def test_ws_input_path_does_not_drain_terminal_output(monkeypatch):
    process = _install_fake_session(monkeypatch)

    result = broker.write_terminal_session_input("term_test", "echo hi\r")

    assert result["ok"] is True
    assert process.written == ["echo hi\r"]
    assert process.output_reads == 0
    assert result["outputDelta"] == ""


def test_ws_and_rest_observers_have_independent_cursors(monkeypatch):
    process = _install_fake_session(monkeypatch)

    result = broker.consume_terminal_session_output("term_test")

    assert result["ok"] is True
    assert result["outputDelta"] == "delta-1"
    assert process.output_reads == 0
    assert broker.consume_terminal_session_output("term_test")["outputDelta"] == "delta-1"
    assert broker.read_terminal_session("term_test", 0)["outputDelta"] == "delta-1"
    assert broker.consume_terminal_session_output("term_test", result["cursor"])["outputDelta"] == ""


def test_actual_websocket_resets_cursor_and_waits_for_write_ack_without_blocking_input(monkeypatch, tmp_path):
    import threading
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import terminal_routes
    from core.command_output_log import CommandOutputLog

    process = _install_fake_session(monkeypatch)
    log = CommandOutputLog(tmp_path)
    expected = "线🙂\r\n" * 9000
    log.append(expected)
    process.read_output = log.read
    received_input = threading.Event()
    def write_input(text):
        process.written.append(text)
        received_input.set()
    process.write_input = write_input
    consumed = []
    original = terminal_routes.consume_terminal_session_output
    def consume(session, cursor):
        consumed.append(cursor)
        return original(session, cursor)
    monkeypatch.setattr(terminal_routes, "consume_terminal_session_output", consume)
    ticket = broker.issue_terminal_ws_ticket("term_test", user_email="fixture", origin="http://testserver")["ticket"]
    app = FastAPI()
    app.include_router(terminal_routes.router)
    with TestClient(app) as client, client.websocket_connect(
        f"/terminal/sessions/term_test/ws?ticket={ticket}&cursor={log.size + 1}",
        headers={"origin": "http://testserver"},
    ) as websocket:
        snapshot = websocket.receive_json()["session"]
        assert snapshot["outputReset"] is True
        assert snapshot["outputCursor"] == 0 and snapshot["outputDelta"] == ""
        first = websocket.receive_json()
        assert first["type"] == "output" and len(first["data"].encode()) <= 65536
        websocket.send_json({"type": "input", "data": "\x03"})
        assert received_input.wait(2), "ACK backpressure must leave the input reader responsive"
        assert process.written == ["\x03"] and consumed == [0]
        websocket.send_json({"type": "ack", "cursor": first["cursor"]})
        second = websocket.receive_json()
        while second["type"] != "output":
            assert second["session"]["outputCursor"] == first["cursor"]
            second = websocket.receive_json()
        assert first["data"] + second["data"] == expected
        assert second["cursor"] == log.size
        process.is_running = False
        websocket.send_json({"type": "ack", "cursor": second["cursor"]})
        while True:
            status = websocket.receive_json()
            if status["type"] == "status" and status["session"].get("isRunning") is False:
                break
        assert status["session"]["outputCursor"] == log.size
        assert broker.read_terminal_session("term_test", 0)["outputDelta"] == first["data"]


def test_ticket_rejects_other_owner_and_origin(monkeypatch):
    import pytest
    _install_fake_session(monkeypatch)
    broker._manual_terminal_sessions["term_test"]["userEmail"] = "owner@example.test"
    with pytest.raises(PermissionError):
        broker.issue_terminal_ws_ticket("term_test", user_email="other@example.test")
    ticket = broker.issue_terminal_ws_ticket("term_test", user_email="owner@example.test", origin="http://127.0.0.1:22827")["ticket"]
    assert broker.consume_terminal_ws_ticket("term_test", ticket, "https://untrusted.invalid")["reason"] == "origin_mismatch"
    assert broker.consume_terminal_ws_ticket("term_test", ticket, "http://127.0.0.1:22827")["ok"] is True


def test_http_terminal_creation_and_alternate_process_transport_preserve_owner(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api import ops_routes, terminal_routes
    from core import system_base
    from core.database import db
    from core.tools.native import command

    process = _install_fake_session(monkeypatch)
    process.session_id = "manual-terminal:term_test"
    broker._manual_terminal_sessions["term_test"]["userEmail"] = "owner@example.invalid"
    monkeypatch.setattr(command, "_bg_processes", {"term_test": process})
    monkeypatch.setattr(system_base, "get_internal_secret", lambda: "synthetic-test-secret")
    monkeypatch.setattr(terminal_routes, "get_internal_secret", lambda: "synthetic-test-secret")
    monkeypatch.setattr(db, "get_session", lambda session: {"user_id": "owner-id"} if session == "conversation" else None)
    created = []
    def create(**request):
        created.append(request)
        return {"ok": True, "sessionId": "fixture"}
    monkeypatch.setattr(terminal_routes, "create_terminal_session", create)
    app = FastAPI()
    app.include_router(terminal_routes.router)
    app.include_router(ops_routes.router)
    headers = {"x-v8-agent-os-secret": "synthetic-test-secret", "x-v8-agent-os-user-email": "other@example.invalid", "x-v8-agent-os-user-id": "other-id"}
    with TestClient(app) as client:
        assert client.post("/terminal/sessions", headers=headers, json={"conversationId": "conversation"}).status_code == 403
        assert not created
        assert client.post("/bg_processes/term_test/input", headers=headers, json={"input_text": "echo forbidden\r"}).status_code == 403
        assert not process.written
        headers.update({"x-v8-agent-os-user-email": "owner@example.invalid", "x-v8-agent-os-user-id": "owner-id"})
        assert client.post("/terminal/sessions", headers=headers, json={"conversationId": "conversation"}).status_code == 200
        assert len(created) == 1 and created[0]["conversation_id"] == "conversation"
        assert client.post("/terminal/sessions", headers=headers, json={"conversationId": "missing"}).status_code == 404


def test_create_idempotency_rejects_changed_target_and_canonical_scope_conflict(monkeypatch, tmp_path):
    import pytest
    from runtimes.memory.scope_resolution import session_scope_binding_service
    monkeypatch.setattr(broker, "_terminal_create_requests", {})
    monkeypatch.setattr(broker, "_resolve_profile", lambda _: {"command": "fixture", "id": "fixture"})
    monkeypatch.setattr(session_scope_binding_service, "get_binding", lambda _: SimpleNamespace(workspace_path=str(tmp_path), workspace_id="wa", project_id=None))
    calls = []
    class Process(FakeTerminalProcess):
        def __init__(self, command, **kwargs): super().__init__(); calls.append(kwargs)
    monkeypatch.setattr(broker, "BackgroundProcess", Process)
    monkeypatch.setattr(broker, "build_workspace_binding", lambda *a, **kw: SimpleNamespace(side_effects_allowed=True, active_workspace_root=str(tmp_path)))
    monkeypatch.setattr(broker, "_prune_stale_background_processes", lambda: None)
    first = broker.create_terminal_session(conversation_id="a", workspace_id="wa", cwd=str(tmp_path), create_request_id="req-a", user_email="owner")
    second = broker.create_terminal_session(conversation_id="a", workspace_id="wa", cwd=str(tmp_path), create_request_id="req-a", user_email="owner")
    assert first["sessionId"] == second["sessionId"] and len(calls) == 1
    with pytest.raises(RuntimeError, match="terminal_create_request_conflict"):
        broker.create_terminal_session(conversation_id="b", workspace_id="wa", cwd=str(tmp_path), create_request_id="req-a", user_email="owner")
    with pytest.raises(RuntimeError, match="workspace_binding_conflict"):
        broker.create_terminal_session(conversation_id="a", workspace_id="wb", cwd=str(tmp_path), create_request_id="req-b", user_email="owner")


def test_resize_terminal_session_updates_process_dimensions(monkeypatch):
    process = _install_fake_session(monkeypatch)

    result = broker.resize_terminal_session("term_test", cols=132, rows=37)

    assert result["ok"] is True
    assert process.cols == 132
    assert process.rows == 37
    assert result["cols"] == 132
    assert result["rows"] == 37
    assert result["screenSnapshot"] == "screen 132x37"


def test_list_terminal_sessions_filters_by_conversation(monkeypatch):
    process_a = FakeTerminalProcess()
    process_b = FakeTerminalProcess()
    monkeypatch.setattr(
        broker,
        "_manual_terminal_sessions",
        {
            "term_a": {
                "sessionId": "term_a",
                "commandId": "cmd_a",
                "conversationId": "conv_a",
                "profileId": "pwsh",
                "profileLabel": "PowerShell 7",
                "cwd": "E:/Projects/v8chat",
                "status": "running",
                "createdAt": "2026-06-30T00:00:00+00:00",
                "updatedAt": "2026-06-30T00:00:00+00:00",
            },
            "term_b": {
                "sessionId": "term_b",
                "commandId": "cmd_b",
                "conversationId": "conv_b",
                "profileId": "pwsh",
                "profileLabel": "PowerShell 7",
                "cwd": "E:/Projects/v8chat",
                "status": "running",
                "createdAt": "2026-06-30T00:00:01+00:00",
                "updatedAt": "2026-06-30T00:00:01+00:00",
            },
        },
    )
    monkeypatch.setattr(broker, "_bg_processes", {"cmd_a": process_a, "cmd_b": process_b})
    monkeypatch.setattr(broker, "_prune_stale_background_processes", lambda: None)

    result = broker.list_terminal_sessions(conversation_id="conv_a")

    assert result["ok"] is True
    assert [item["sessionId"] for item in result["sessions"]] == ["term_a"]


def test_terminal_ws_ticket_is_single_use(monkeypatch):
    _install_fake_session(monkeypatch)
    monkeypatch.setattr(broker, "_terminal_ws_tickets", {})

    ticket = broker.issue_terminal_ws_ticket("term_test", user_email="owner@example.test")["ticket"]

    assert broker.consume_terminal_ws_ticket("term_test", ticket)["ok"] is True
    assert broker.consume_terminal_ws_ticket("term_test", ticket)["ok"] is False


def test_terminal_ws_ticket_rejects_wrong_session(monkeypatch):
    _install_fake_session(monkeypatch)
    monkeypatch.setattr(broker, "_terminal_ws_tickets", {})

    ticket = broker.issue_terminal_ws_ticket("term_test", user_email="owner@example.test")["ticket"]

    result = broker.consume_terminal_ws_ticket("term_other", ticket)

    assert result["ok"] is False
    assert result["reason"] == "session_mismatch"


def test_terminal_ws_ticket_rejects_expired_ticket(monkeypatch):
    _install_fake_session(monkeypatch)
    monkeypatch.setattr(broker, "_terminal_ws_tickets", {})

    ticket = broker.issue_terminal_ws_ticket("term_test", user_email="owner@example.test")["ticket"]
    broker._terminal_ws_tickets[ticket]["expiresAtEpoch"] = 1

    result = broker.consume_terminal_ws_ticket("term_test", ticket)

    assert result["ok"] is False
    assert result["reason"] in {"expired_ticket", "invalid_ticket"}


def test_terminal_ws_ticket_requires_existing_session(monkeypatch):
    monkeypatch.setattr(broker, "_manual_terminal_sessions", {})
    monkeypatch.setattr(broker, "_terminal_ws_tickets", {})

    try:
        broker.issue_terminal_ws_ticket("term_missing", user_email="owner@example.test")
    except RuntimeError as exc:
        assert "not found" in str(exc).lower()
    else:
        raise AssertionError("missing terminal session should not receive a ws ticket")


def test_managed_command_session_uses_observable_pipe_without_allocating_pty(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeManagedProcess(FakeTerminalProcess):
        def __init__(self, command: str, **kwargs) -> None:
            super().__init__()
            captured.update({"command": command, **kwargs})

        def status_snapshot(self) -> dict[str, object]:
            return {
                **super().status_snapshot(),
                "uses_tty": False,
                "tty_mode": "pipe",
                "interactive": False,
                "backend": "pipe",
            }

    monkeypatch.setattr(
        broker,
        "build_workspace_binding",
        lambda *_args, **_kwargs: SimpleNamespace(
            side_effects_allowed=True,
            active_workspace_root=str(tmp_path),
        ),
    )
    monkeypatch.setattr(broker, "BackgroundProcess", FakeManagedProcess)
    monkeypatch.setattr(broker, "_manual_terminal_sessions", {})
    monkeypatch.setattr(broker, "_bg_processes", {})
    monkeypatch.setattr(broker, "_prune_stale_background_processes", lambda: None)

    result = broker.create_managed_command_session(
        command="npm run dev",
        cwd=str(tmp_path),
        conversation_id="session-project",
        profile_reason="ui_patch_project_dev",
        timeout_seconds=3600,
    )

    assert result["ok"] is True
    assert result["usesTty"] is False
    assert result["ttyMode"] == "pipe"
    assert captured["command"] == "npm run dev"
    assert captured["interactive"] is False
    assert captured["terminal_mode"] == "pipe"
    assert captured["profile_reason"] == "ui_patch_project_dev"
