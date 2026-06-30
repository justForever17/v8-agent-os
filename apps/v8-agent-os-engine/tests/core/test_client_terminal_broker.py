from __future__ import annotations

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
