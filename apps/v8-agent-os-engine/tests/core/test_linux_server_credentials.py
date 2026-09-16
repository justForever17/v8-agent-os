from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor

import pytest

from core.security.credentials import CredentialStoreError
from core.security.server_credentials import LinuxServerCredentialBackend


def test_initialize_restart_read_write_without_dbus(tmp_path, monkeypatch):
    key = tmp_path / "key"
    LinuxServerCredentialBackend.initialize_key(key)
    first = LinuxServerCredentialBackend(tmp_path / "state", key_file=key, instance_id="i-1")
    first.write("target", "secret")
    second = LinuxServerCredentialBackend(tmp_path / "state", key_file=key, instance_id="i-1")
    assert second.read("target") == "secret"


def test_wrong_key_fails_without_overwriting_ciphertext(tmp_path):
    key = tmp_path / "key"
    LinuxServerCredentialBackend.initialize_key(key)
    backend = LinuxServerCredentialBackend(tmp_path / "state", key_file=key)
    backend.write("a", "b")
    before = backend.ciphertext_path.read_bytes()
    wrong = tmp_path / "wrong"
    LinuxServerCredentialBackend.initialize_key(wrong)
    broken = LinuxServerCredentialBackend(tmp_path / "state", key_file=wrong)
    with pytest.raises(CredentialStoreError):
        broken.write("a", "c")
    assert backend.ciphertext_path.read_bytes() == before


def test_key_initialization_is_explicit_and_permissions_are_checked(tmp_path):
    key = tmp_path / "key"
    LinuxServerCredentialBackend.initialize_key(key)
    with pytest.raises(CredentialStoreError):
        LinuxServerCredentialBackend.initialize_key(key)
    if os.name == "posix":
        os.chmod(key, 0o644)
        with pytest.raises(CredentialStoreError):
            LinuxServerCredentialBackend(tmp_path / "state", key_file=key).read("x")


def test_concurrent_writes_do_not_lose_updates(tmp_path):
    key = tmp_path / "key"
    LinuxServerCredentialBackend.initialize_key(key)
    def put(i):
        LinuxServerCredentialBackend(tmp_path / "state", key_file=key).write(f"k{i}", f"v{i}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(put, range(20)))
    backend = LinuxServerCredentialBackend(tmp_path / "state", key_file=key)
    assert all(backend.read(f"k{i}") == f"v{i}" for i in range(20))


def test_missing_key_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv("V8_AGENT_OS_CREDENTIAL_KEY_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    with pytest.raises(CredentialStoreError):
        LinuxServerCredentialBackend(tmp_path / "state")


def _process_write(args):
    state, key, index = args
    LinuxServerCredentialBackend(state, key_file=key).write(f"worker-{index}", f"value-{index}")


def test_independent_processes_serialize_real_ciphertext_updates(tmp_path):
    key, state = tmp_path / "key", tmp_path / "state"
    LinuxServerCredentialBackend.initialize_key(key)
    backend = LinuxServerCredentialBackend(state, key_file=key)
    backend.write('seed', 'initial')
    with ProcessPoolExecutor(max_workers=3) as workers:
        list(workers.map(_process_write, [(str(state), str(key), i) for i in range(9)]))
    assert backend.read('seed') == 'initial'
    assert all(backend.read(f'worker-{i}') == f'value-{i}' for i in range(9))


def test_existing_ciphertext_requires_key_recovery_not_a_new_key(tmp_path):
    key, state = tmp_path / 'key', tmp_path / 'state'
    LinuxServerCredentialBackend.initialize_key(key)
    backend = LinuxServerCredentialBackend(state, key_file=key)
    backend.write('id', 'value')
    original = backend.ciphertext_path.read_bytes()
    key.unlink()
    with pytest.raises(CredentialStoreError, match='restore their key'):
        LinuxServerCredentialBackend.initialize_key(key, state_root=state)
    assert not key.exists()
    assert backend.ciphertext_path.read_bytes() == original


def test_explicit_linux_path_selects_server_backend_without_secret_service(tmp_path, monkeypatch):
    from core.security import credentials
    key = tmp_path / 'key'
    LinuxServerCredentialBackend.initialize_key(key)
    monkeypatch.setenv('V8_AGENT_OS_CREDENTIAL_KEY_FILE', str(key))
    monkeypatch.setenv('V8_AGENT_OS_HOME', str(tmp_path / 'state'))
    backend = credentials._default_backend('linux')
    assert isinstance(backend, LinuxServerCredentialBackend)
    backend.write('target', 'value')
    assert credentials._default_backend('linux').read('target') == 'value'


@pytest.mark.skipif(os.name != 'posix', reason='requires actual Unix ownership and permissions')
def test_unix_rejects_open_parent_and_symbolic_link_without_chmod_repair(tmp_path):
    parent = tmp_path / 'public'
    parent.mkdir(mode=0o755)
    with pytest.raises(CredentialStoreError, match='key directory'):
        LinuxServerCredentialBackend.initialize_key(parent / 'key')
    assert parent.stat().st_mode & 0o777 == 0o755
    key = tmp_path / 'key'
    LinuxServerCredentialBackend.initialize_key(key)
    link = tmp_path / 'link'
    link.symlink_to(key)
    with pytest.raises(CredentialStoreError, match='symbolic link'):
        LinuxServerCredentialBackend(tmp_path / 'state', key_file=link).read('x')


@pytest.mark.skipif(os.name != 'posix', reason='requires actual systemd-style Unix read-only modes')
def test_systemd_credential_mount_and_backup_restore_without_dbus(tmp_path, monkeypatch):
    import shutil
    directory = tmp_path / 'systemd'
    directory.mkdir(mode=0o700)
    key = directory / 'v8-agent-os-credential-key'
    LinuxServerCredentialBackend.initialize_key(key)
    key.chmod(0o400)
    directory.chmod(0o500)
    monkeypatch.delenv('V8_AGENT_OS_CREDENTIAL_KEY_FILE', raising=False)
    monkeypatch.delenv('DBUS_SESSION_BUS_ADDRESS', raising=False)
    monkeypatch.setenv('CREDENTIALS_DIRECTORY', str(directory))
    backend = LinuxServerCredentialBackend(tmp_path / 'state')
    backend.write('secret-reference', 'synthetic-value')
    shutil.copytree(tmp_path / 'state', tmp_path / 'restored')
    restored = LinuxServerCredentialBackend(tmp_path / 'restored')
    assert restored.read('secret-reference') == 'synthetic-value'
    assert b'synthetic-value' not in restored.ciphertext_path.read_bytes()
