import hashlib
import codecs
import pytest
from core.command_output_log import CommandOutputLog


def test_two_observers_resume_independently_with_bounded_utf8_batches(tmp_path):
    log = CommandOutputLog(tmp_path)
    chunk = "中🙂\x1b[31mred\x1b[0m\rprogress\n" * 2048
    expected = hashlib.sha256()
    for _ in range(900):
        log.append(chunk)
        expected.update(chunk.encode())
    assert log.size > 50 * 1024 * 1024
    for _ in range(2):
        cursor = 0
        observed = hashlib.sha256()
        while True:
            result = log.read(cursor)
            data = result["data"].encode()
            assert len(data) <= 65536 and "\ufffd" not in result["data"]
            observed.update(data)
            cursor = result["cursor"]
            if not result["hasMore"]: break
        assert observed.digest() == expected.digest()
        assert cursor == log.size
    with pytest.raises(ValueError): log.read(log.size + 1)


def test_posix_production_reader_preserves_every_split_of_multibyte_text(monkeypatch):
    import threading
    from types import SimpleNamespace
    import core.tools.native.command as command
    raw = "中文🙂\x1b[31m".encode()
    for boundary in range(len(raw)):
        chunks = iter([part for part in [raw[:boundary], raw[boundary:]] if part] + [b""])
        output = []
        process = command.BackgroundProcess.__new__(command.BackgroundProcess)
        process.backend = "posix_pty"; process.fd = 999; process.is_running = True
        process._ingest_output = output.append
        process._deadline_stop = threading.Event(); process.return_code = 0
        process.failure_kind = None; process.termination_reason = None; process.command = "fixture"
        process.process_job = None; process._close_posix_pty_fd = lambda: None
        monkeypatch.setattr(command.os, "read", lambda *_: next(chunks))
        monkeypatch.setattr(command.select, "select", lambda *a: ([999], [], []))
        monkeypatch.setattr(command, "_notify_skills_inventory_command_completed", lambda _: None)
        process._read_output()
        assert "".join(output) == raw.decode()
