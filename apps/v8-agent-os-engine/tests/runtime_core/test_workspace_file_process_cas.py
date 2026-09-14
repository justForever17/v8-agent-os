from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.process_launch import popen_windowless


ENGINE_ROOT = Path(__file__).resolve().parents[2]
WORKER = r"""
import json
import sys
import time
from pathlib import Path
from core.tools.native import workspace_file as files

target, signals = Path(sys.argv[1]), Path(sys.argv[2])
name, mode, expected = sys.argv[3:6]

def signal(event):
    (signals / (name + '.' + event)).touch()

def wait(event):
    deadline = time.monotonic() + 20
    while not (signals / event).exists():
        if time.monotonic() > deadline:
            raise TimeoutError(event)
        time.sleep(0.01)

original_fsync = files.os.fsync
def fsync(fd):
    original_fsync(fd)
    signal('prepared')
files.os.fsync = fsync

original_fingerprint = files._file_state_fingerprint
def fingerprint(path, **kwargs):
    version = original_fingerprint(path, **kwargs)
    signal('checked')
    if mode == 'pause_after_compare':
        wait('release')
    return version
files._file_state_fingerprint = fingerprint

original_replace = files.os.replace
def replace(source, destination, **kwargs):
    if mode == 'fail':
        raise PermissionError('injected replace failure')
    if mode == 'cancel':
        raise KeyboardInterrupt('injected cancellation')
    if mode == 'hold':
        signal('holding')
        wait('release')
    return original_replace(source, destination, **kwargs)
files.os.replace = replace

try:
    files._atomic_write_text(target, name, expected_version=expected)
    result = {'outcome': 'committed'}
except BaseException as error:
    result = {'outcome': type(error).__name__, 'error': str(error)}
result['stateRoot'] = __import__('os').environ['V8_AGENT_OS_HOME']
(signals / (name + '.result')).write_text(json.dumps(result), encoding='utf-8')
if mode in ('fail', 'cancel'):
    # Stay alive so process exit cannot hide a leaked lock after an exception.
    wait('release')
"""


def _wait_for(path: Path, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


@contextmanager
def _writer(target: Path, signals: Path, name: str, mode: str, version: str):
    env = os.environ.copy()
    env['V8_AGENT_OS_HOME'] = str(signals / ('state-' + name))
    # Engine-specific temporary directories must not split the CAS lock owner.
    temporary_root = signals / ('temp-' + name)
    temporary_root.mkdir()
    env.update(TEMP=str(temporary_root), TMP=str(temporary_root), TMPDIR=str(temporary_root))
    process = popen_windowless(
        [sys.executable, '-c', WORKER, str(target), str(signals), name, mode, version],
        cwd=str(ENGINE_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)


def _result(signals: Path, name: str) -> dict:
    path = signals / (name + '.result')
    assert _wait_for(path), f'{name} did not finish'
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.mark.parametrize('existing', [True, False], ids=['same-version', 'both-missing'])
def test_process_writers_from_different_state_roots_have_one_cas_winner(tmp_path, existing):
    from core.tools.native import workspace_file as files

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'nested').mkdir()
    target = workspace / 'target.txt'
    if existing:
        target.write_text('base', encoding='utf-8')
    version = files._file_state_fingerprint(target)
    alias = workspace / 'nested' / '..' / target.name
    with _writer(target, tmp_path, 'first', 'pause_after_compare', version):
        assert _wait_for(tmp_path / 'first.checked')
        with _writer(alias, tmp_path, 'second', 'normal', version):
            assert _wait_for(tmp_path / 'second.prepared')
            # The old RLock lets the second process read the stale version and
            # commit while the first is paused. A file lock holds it before CAS.
            _wait_for(tmp_path / 'second.checked', timeout=1.0)
            (tmp_path / 'release').touch()
            first, second = _result(tmp_path, 'first'), _result(tmp_path, 'second')
    assert first['stateRoot'] != second['stateRoot']
    assert [first['outcome'], second['outcome']] == ['committed', 'ValueError'], (first, second)
    assert second['error'] == 'file_changed_before_commit'
    assert target.read_text(encoding='utf-8') == 'first'
    assert sorted(p.name for p in workspace.iterdir()) == ['nested', 'target.txt']


@pytest.mark.parametrize('mode,error', [('fail', 'PermissionError'), ('cancel', 'KeyboardInterrupt')])
def test_failed_or_cancelled_writer_releases_lock_while_process_stays_alive(tmp_path, mode, error):
    from core.tools.native import workspace_file as files

    target = tmp_path / 'target.txt'
    target.write_text('base', encoding='utf-8')
    version = files._file_state_fingerprint(target)
    with _writer(target, tmp_path, 'failed', mode, version) as failed:
        assert _result(tmp_path, 'failed')['outcome'] == error
        assert failed.poll() is None
        assert target.read_text(encoding='utf-8') == 'base'
        assert not list(tmp_path.glob('*.v8os-tmp'))
        with _writer(target, tmp_path, 'next', 'normal', version):
            assert _result(tmp_path, 'next')['outcome'] == 'committed'
        assert failed.poll() is None
        (tmp_path / 'release').touch()
    assert target.read_text(encoding='utf-8') == 'next'


def test_terminated_lock_holder_releases_waiter_without_deleting_coordination_file(tmp_path):
    from core.tools.native import workspace_file as files

    target = tmp_path / 'target.txt'
    target.write_text('base', encoding='utf-8')
    version = files._file_state_fingerprint(target)
    lock_path = files._workspace_file_lock_path(target)
    with _writer(target, tmp_path, 'holder', 'hold', version) as holder:
        assert _wait_for(tmp_path / 'holder.holding')
        assert lock_path.is_file()
        lock_identity = lock_path.stat().st_ino
        with _writer(target, tmp_path, 'waiter', 'normal', version):
            assert _wait_for(tmp_path / 'waiter.prepared')
            assert not _wait_for(tmp_path / 'waiter.checked', timeout=0.3)
            holder.terminate()
            holder.wait(timeout=10)
            assert _result(tmp_path, 'waiter')['outcome'] == 'committed'
    assert target.read_text(encoding='utf-8') == 'waiter'
    assert lock_path.stat().st_ino == lock_identity
