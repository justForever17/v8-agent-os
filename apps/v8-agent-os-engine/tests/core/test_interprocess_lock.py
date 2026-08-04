from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

from core.interprocess_lock import InterProcessLockTimeout, interprocess_file_lock
from core.process_launch import popen_windowless


def test_interprocess_lock_serializes_processes_with_damaged_coordination_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "shared.lock"
    events_path = tmp_path / "events.jsonl"
    lock_path.write_bytes(b"not-json-and-not-owner-metadata")
    script = """
import json
import sys
import time
from pathlib import Path
from core.interprocess_lock import interprocess_file_lock

lock_path = Path(sys.argv[1])
events_path = Path(sys.argv[2])
worker = sys.argv[3]
with interprocess_file_lock(lock_path, timeout_seconds=5):
    with events_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'worker': worker, 'event': 'start', 'at': time.monotonic()}) + '\\n')
    time.sleep(0.15)
    with events_path.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps({'worker': worker, 'event': 'end', 'at': time.monotonic()}) + '\\n')
"""
    processes = [
        popen_windowless(
            [sys.executable, "-c", script, str(lock_path), str(events_path), str(index)],
            stdout=-1,
            stderr=-1,
            text=True,
        )
        for index in range(2)
    ]
    outputs = [process.communicate(timeout=10) for process in processes]
    assert [process.returncode for process in processes] == [0, 0], outputs

    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["start", "end", "start", "end"]
    assert events[1]["at"] <= events[2]["at"]


def test_lock_timeout_is_visible_and_lock_can_be_reacquired(tmp_path: Path) -> None:
    lock_path = tmp_path / "timeout.lock"
    entered = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with interprocess_file_lock(lock_path, timeout_seconds=1):
            entered.set()
            release.wait(timeout=2)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(timeout=1)
    try:
        with pytest.raises(InterProcessLockTimeout, match="等待资源锁超时"):
            with interprocess_file_lock(lock_path, timeout_seconds=0.05):
                pass
    finally:
        release.set()
        holder.join(timeout=2)

    assert not holder.is_alive()
    with interprocess_file_lock(lock_path, timeout_seconds=0.2):
        pass


def test_process_exit_releases_lock_without_cleanup_handler(tmp_path: Path) -> None:
    lock_path = tmp_path / "crash.lock"
    ready_path = tmp_path / "ready"
    script = """
import os
import sys
from pathlib import Path
from core.interprocess_lock import interprocess_file_lock

with interprocess_file_lock(Path(sys.argv[1]), timeout_seconds=2):
    Path(sys.argv[2]).write_text('locked', encoding='utf-8')
    os._exit(23)
"""
    process = popen_windowless(
        [sys.executable, "-c", script, str(lock_path), str(ready_path)],
        stdout=-1,
        stderr=-1,
        text=True,
    )
    output = process.communicate(timeout=10)
    assert process.returncode == 23, output
    assert ready_path.read_text(encoding="utf-8") == "locked"

    with interprocess_file_lock(lock_path, timeout_seconds=0.5):
        pass
