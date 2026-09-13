"""Real Windows read-handle conflicts at Storage's atomic publication boundary."""
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from core import storage as storage_module


def _manager(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_module, "CONFIG_JSON_PATH", tmp_path / "config.json")
    manager = object.__new__(storage_module.StorageManager)
    manager.base_dir = tmp_path
    manager._config_io_lock = storage_module._CONFIG_IO_LOCK
    manager._config_payload_cache_signature = None
    manager._config_payload_cache_data = None
    return manager


def _reader(path):
    # CPython's Windows file handle allows reading but does not share DELETE,
    # so atomic rename is genuinely denied until this other process closes it.
    child = subprocess.Popen([sys.executable, "-c",
        "import sys\nwith open(sys.argv[1], 'rb') as reader:\n print('ready', flush=True)\n sys.stdin.readline()",
        str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert child.stdout.readline().strip() == "ready"
    return child


def _close_reader(child):
    if child.poll() is None:
        child.communicate("release\n", timeout=5)
    else:
        child.communicate(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="Windows real file sharing semantics")
def test_config_publication_recovers_when_real_reader_releases_without_losing_preimage(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    target = tmp_path / "config.json"
    target.write_text(json.dumps({"runtimeRegistry": {"installProfile": "minimal"}}), encoding="utf-8")
    previous = target.read_bytes()
    reader = _reader(target)
    original = os.replace
    conflicts = []

    def publish(source, destination):
        try:
            return original(source, destination)
        except PermissionError as error:
            if Path(destination) != target:
                raise
            conflicts.append(error.winerror)
            assert target.read_bytes() == previous
            _close_reader(reader)
            raise

    monkeypatch.setattr(storage_module.os, "replace", publish)
    try:
        manager.write_json("config.json", {"runtimeRegistry": {"installProfile": "desktop"}})
    finally:
        _close_reader(reader)
    assert conflicts and set(conflicts) <= {5, 32, 33}
    assert json.loads(target.read_text(encoding="utf-8"))["runtimeRegistry"]["installProfile"] == "desktop"
    assert (tmp_path / "backups/json/config.json.bak").read_bytes() == previous
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows real file sharing semantics")
def test_persistent_reader_failure_is_bounded_and_preserves_config_for_a_later_save(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    target = tmp_path / "config.json"
    target.write_text('{"runtimeRegistry":{"installProfile":"minimal"}}', encoding="utf-8")
    previous = target.read_bytes()
    reader = _reader(target)
    started = time.monotonic()
    try:
        with pytest.raises(PermissionError):
            manager.write_json("config.json", {"runtimeRegistry": {"installProfile": "desktop"}})
        assert time.monotonic() - started < 5
        assert target.read_bytes() == previous
        assert (tmp_path / "backups/json/config.json.bak").read_bytes() == previous
        assert not list(tmp_path.rglob("*.tmp"))
    finally:
        _close_reader(reader)
    manager.write_json("config.json", {"runtimeRegistry": {"installProfile": "desktop"}})
    assert json.loads(target.read_text(encoding="utf-8"))["runtimeRegistry"]["installProfile"] == "desktop"


def test_nonsharing_permission_failure_is_reported_without_retry_or_truncation(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    target = tmp_path / "config.json"
    target.write_text('{"runtimeRegistry":{"installProfile":"minimal"}}', encoding="utf-8")
    previous = target.read_bytes()
    original = os.replace
    denied = PermissionError(errno.EACCES, "fixture permanent permission failure")
    calls = []

    def publish(source, destination):
        if Path(destination) == target:
            calls.append(True)
            raise denied
        return original(source, destination)

    monkeypatch.setattr(storage_module.os, "replace", publish)
    with pytest.raises(PermissionError) as caught:
        manager.write_json("config.json", {"runtimeRegistry": {"installProfile": "desktop"}})
    assert caught.value is denied and len(calls) == 1
    assert target.read_bytes() == previous
    assert not list(tmp_path.rglob("*.tmp"))
