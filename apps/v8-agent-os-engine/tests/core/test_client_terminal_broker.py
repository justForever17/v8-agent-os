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


def test_ws_output_path_drains_terminal_output_once(monkeypatch):
    process = _install_fake_session(monkeypatch)

    result = broker.consume_terminal_session_output("term_test")

    assert result["ok"] is True
    assert result["outputDelta"] == "delta-1"
    assert process.output_reads == 1


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
