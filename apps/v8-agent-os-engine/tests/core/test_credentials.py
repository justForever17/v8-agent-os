from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import psutil
import pytest

from core.security import credentials
from core.security.credentials import (
    CredentialOperationIndeterminate,
    CredentialRefStore,
    CredentialStoreError,
    LinuxSecretServiceCredentialBackend,
    MacOSKeychainCredentialBackend,
    UnavailableCredentialBackend,
    WindowsCredentialBackend,
)


ENGINE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ENGINE_ROOT.parents[1]
NATIVE_HELPER = ENGINE_ROOT / "core" / "security" / "native_keyring_helper.py"


def _write_helper(path: Path, source: str) -> Path:
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def _configure_helper(backend, path: Path, *, timeout_seconds: float = 2.0):
    backend._helper_path = path
    backend._timeout_seconds = timeout_seconds
    return backend


def _wait_for_process_exit(pid: int, timeout_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while psutil.pid_exists(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not psutil.pid_exists(pid)


class FakeNativeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.failure: Exception | None = None

    def set_password(self, service: str, account: str, value: str) -> None:
        if self.failure:
            raise self.failure
        self.calls.append(("write", service, account))
        self.values[(service, account)] = value

    def get_password(self, service: str, account: str) -> str | None:
        if self.failure:
            raise self.failure
        self.calls.append(("read", service, account))
        return self.values.get((service, account))

    def delete_password(self, service: str, account: str) -> None:
        if self.failure:
            raise self.failure
        self.calls.append(("delete", service, account))
        self.values.pop((service, account), None)


class MissingWindowsCredentialError(RuntimeError):
    winerror = 1168


class FakeWin32Cred:
    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    def __init__(self) -> None:
        self.payload: dict[str, Any] | None = None
        self.value: str | bytes | None = None
        self.deleted = False

    def CredWrite(self, payload: dict[str, Any], flags: int) -> None:
        assert flags == 0
        self.payload = payload

    def CredRead(self, target: str, credential_type: int, flags: int) -> dict[str, Any]:
        assert target == "V8AgentOS/model/test"
        assert credential_type == self.CRED_TYPE_GENERIC
        assert flags == 0
        if self.value is None:
            raise MissingWindowsCredentialError()
        return {"CredentialBlob": self.value}

    def CredDelete(self, target: str, credential_type: int, flags: int) -> None:
        assert target == "V8AgentOS/model/test"
        assert credential_type == self.CRED_TYPE_GENERIC
        assert flags == 0
        if self.value is None:
            raise MissingWindowsCredentialError()
        self.deleted = True


@pytest.mark.parametrize(
    "backend_type",
    [LinuxSecretServiceCredentialBackend, MacOSKeychainCredentialBackend],
)
def test_native_keyring_backend_round_trip_uses_target_as_service(backend_type):
    native = FakeNativeKeyring()
    backend = backend_type(native)

    backend.write("V8AgentOS/model/test", "secret-value")

    assert backend.read("V8AgentOS/model/test") == "secret-value"
    assert backend.delete("V8AgentOS/model/test") is True
    assert backend.delete("V8AgentOS/model/test") is False
    assert native.calls == [
        ("write", "V8AgentOS/model/test", "V8 Agent OS"),
        ("read", "V8AgentOS/model/test", "V8 Agent OS"),
        ("read", "V8AgentOS/model/test", "V8 Agent OS"),
        ("delete", "V8AgentOS/model/test", "V8 Agent OS"),
        ("read", "V8AgentOS/model/test", "V8 Agent OS"),
    ]


def test_native_keyring_failure_is_redacted_and_fail_closed():
    native = FakeNativeKeyring()
    native.failure = RuntimeError("native backend failed")
    backend = LinuxSecretServiceCredentialBackend(native)

    with pytest.raises(CredentialStoreError, match="failed to write Linux Secret Service credential") as error:
        backend.write("V8AgentOS/model/test", "must-not-leak")

    assert "must-not-leak" not in str(error.value)


@pytest.mark.parametrize(
    "backend_type",
    [LinuxSecretServiceCredentialBackend, MacOSKeychainCredentialBackend],
)
def test_native_keyring_helper_round_trip_and_secret_transport_is_pipe_only(tmp_path, backend_type):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.json"
    helper_path = _write_helper(
        tmp_path / "round_trip_helper.py",
        f"""
        import json
        import os
        import sys
        from pathlib import Path

        state_path = Path({str(state_path)!r})
        audit_path = Path({str(audit_path)!r})
        request = json.load(sys.stdin)
        audit_path.write_text(json.dumps({{
            "argv": sys.argv,
            "environment": dict(os.environ),
            "action": request.get("action"),
            "target": request.get("target"),
            "platform": request.get("platform"),
        }}), encoding="utf-8")
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {{}}
        action = request["action"]
        target = request["target"]
        if action == "write":
            state[target] = request["secret"]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            response = {{"protocolVersion": 1, "ok": True, "written": True}}
        elif action == "read":
            secret = state.get(target)
            response = {{"protocolVersion": 1, "ok": True, "found": secret is not None}}
            if secret is not None:
                response["secret"] = secret
        else:
            deleted = target in state
            state.pop(target, None)
            state_path.write_text(json.dumps(state), encoding="utf-8")
            response = {{"protocolVersion": 1, "ok": True, "deleted": deleted}}
        sys.stdout.write(json.dumps(response))
        """,
    )
    backend = _configure_helper(backend_type(), helper_path)
    secret = "pipe-only-secret-7c43f9"

    backend.write("V8AgentOS/model/test", secret)
    write_audit = audit_path.read_text(encoding="utf-8")
    assert secret not in write_audit
    assert backend.read("V8AgentOS/model/test") == secret
    assert backend.delete("V8AgentOS/model/test") is True
    assert backend.delete("V8AgentOS/model/test") is False

    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["argv"] == [str(helper_path)]
    assert secret not in json.dumps(audit["environment"])


def _timeout_helper(tmp_path: Path, name: str) -> tuple[Path, Path, Path]:
    pid_path = tmp_path / f"{name}.pid"
    late_write_path = tmp_path / f"{name}.late"
    helper_path = _write_helper(
        tmp_path / f"{name}_helper.py",
        f"""
        import json
        import os
        import sys
        import time
        from pathlib import Path

        json.load(sys.stdin)
        Path({str(pid_path)!r}).write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(3)
        Path({str(late_write_path)!r}).write_text("late mutation", encoding="utf-8")
        sys.stdout.write(json.dumps({{"protocolVersion": 1, "ok": True}}))
        """,
    )
    return helper_path, pid_path, late_write_path


def test_native_keyring_write_timeout_is_killed_and_reference_is_indeterminate(tmp_path):
    helper_path, pid_path, late_write_path = _timeout_helper(tmp_path, "write")
    backend = _configure_helper(LinuxSecretServiceCredentialBackend(), helper_path, timeout_seconds=0.5)
    store = CredentialRefStore(backend)
    secret = "timeout-secret-must-not-leak-91f2"

    with pytest.raises(CredentialOperationIndeterminate) as captured:
        store.put(secret, reference="cred:v8-model:timeout-write", namespace="model")

    assert captured.value.operation == "write"
    assert captured.value.reference == "cred:v8-model:timeout-write"
    assert secret not in str(captured.value)
    pid = int(pid_path.read_text(encoding="utf-8"))
    _wait_for_process_exit(pid)
    time.sleep(0.2)
    assert not late_write_path.exists()


def test_native_keyring_delete_timeout_is_killed_and_reference_is_indeterminate(tmp_path):
    helper_path, pid_path, late_write_path = _timeout_helper(tmp_path, "delete")
    backend = _configure_helper(LinuxSecretServiceCredentialBackend(), helper_path, timeout_seconds=0.5)
    store = CredentialRefStore(backend)

    with pytest.raises(CredentialOperationIndeterminate) as captured:
        store.delete("cred:v8-model:timeout-delete")

    assert captured.value.operation == "delete"
    assert captured.value.reference == "cred:v8-model:timeout-delete"
    pid = int(pid_path.read_text(encoding="utf-8"))
    _wait_for_process_exit(pid)
    time.sleep(0.2)
    assert not late_write_path.exists()


def test_native_keyring_read_timeout_is_killed_and_fail_closed(tmp_path):
    helper_path, pid_path, late_write_path = _timeout_helper(tmp_path, "read")
    backend = _configure_helper(LinuxSecretServiceCredentialBackend(), helper_path, timeout_seconds=0.5)

    with pytest.raises(CredentialStoreError, match="credential read timed out") as captured:
        backend.read("V8AgentOS/model/timeout-read")

    assert not isinstance(captured.value, CredentialOperationIndeterminate)
    pid = int(pid_path.read_text(encoding="utf-8"))
    _wait_for_process_exit(pid)
    time.sleep(0.2)
    assert not late_write_path.exists()


@pytest.mark.parametrize(
    ("error_code", "message"),
    [("locked", "locked or requires user interaction"), ("unavailable", "is unavailable")],
)
def test_native_keyring_helper_locked_and_unavailable_are_redacted(tmp_path, error_code, message):
    helper_path = _write_helper(
        tmp_path / f"{error_code}_helper.py",
        f"""
        import json
        import sys

        json.load(sys.stdin)
        sys.stdout.write(json.dumps({{
            "protocolVersion": 1,
            "ok": False,
            "errorCode": {error_code!r},
            "message": "sensitive-native-details-must-not-surface",
        }}))
        """,
    )
    backend = _configure_helper(LinuxSecretServiceCredentialBackend(), helper_path)

    with pytest.raises(CredentialStoreError, match=message) as captured:
        backend.read("V8AgentOS/model/test")

    assert "sensitive-native-details-must-not-surface" not in str(captured.value)


@pytest.mark.parametrize(
    "request_payload",
    [
        {"protocolVersion": 1, "platform": "linux", "action": "exec", "target": "V8AgentOS/model/test"},
        {"protocolVersion": 1, "platform": "linux", "action": "read", "target": "C:/private/secret"},
        {"protocolVersion": 1, "platform": "win32", "action": "read", "target": "V8AgentOS/model/test"},
    ],
)
def test_native_keyring_helper_rejects_untrusted_platform_action_and_target(request_payload):
    completed = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-u", str(NATIVE_HELPER)],
        input=json.dumps(request_payload).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=credentials._KeyringCredentialBackend._helper_environment(),
        timeout=2,
        check=False,
    )

    assert json.loads(completed.stdout.decode("utf-8")) == {
        "protocolVersion": 1,
        "ok": False,
        "errorCode": "invalid_request",
    }
    assert completed.stderr == b""


def test_native_keyring_helper_platform_mismatch_does_not_leak_write_secret():
    requested_platform = "darwin" if sys.platform != "darwin" else "linux"
    secret = "platform-mismatch-secret-cc87"
    request = {
        "protocolVersion": 1,
        "platform": requested_platform,
        "action": "write",
        "target": "V8AgentOS/model/test",
        "secret": secret,
    }
    command = [sys.executable, "-I", "-X", "utf8", "-u", str(NATIVE_HELPER)]
    helper_env = credentials._KeyringCredentialBackend._helper_environment()

    completed = subprocess.run(
        command,
        input=json.dumps(request).encode("utf-8"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=helper_env,
        timeout=2,
        check=False,
    )

    assert json.loads(completed.stdout.decode("utf-8"))["errorCode"] == "platform_mismatch"
    assert secret not in " ".join(command)
    assert secret not in json.dumps(helper_env)
    assert secret not in completed.stdout.decode("utf-8")
    assert secret not in completed.stderr.decode("utf-8")


def test_default_backend_selects_only_the_native_platform_backend(monkeypatch):
    windows = object()
    linux = object()
    macos = object()
    monkeypatch.setattr(credentials, "WindowsCredentialBackend", lambda: windows)
    monkeypatch.setattr(credentials, "LinuxSecretServiceCredentialBackend", lambda: linux)
    monkeypatch.setattr(credentials, "MacOSKeychainCredentialBackend", lambda: macos)

    assert credentials._default_backend("win32") is windows
    assert credentials._default_backend("linux") is linux
    assert credentials._default_backend("darwin") is macos


def test_default_backend_preserves_secure_store_unavailable_reason(monkeypatch):
    def unavailable():
        raise CredentialStoreError(
            "Linux Secret Service is unavailable; a running user D-Bus session and Secret Service daemon are required"
        )

    monkeypatch.setattr(credentials, "LinuxSecretServiceCredentialBackend", unavailable)
    backend = credentials._default_backend("linux")

    assert isinstance(backend, UnavailableCredentialBackend)
    with pytest.raises(CredentialStoreError, match="running user D-Bus session"):
        backend.read("V8AgentOS/model/test")


def test_unsupported_platform_has_no_storage_fallback():
    backend = credentials._default_backend("freebsd")

    assert isinstance(backend, UnavailableCredentialBackend)
    with pytest.raises(CredentialStoreError, match="unavailable on platform freebsd"):
        backend.write("V8AgentOS/model/test", "secret")


def test_windows_credential_manager_contract_is_unchanged():
    native = FakeWin32Cred()
    backend = WindowsCredentialBackend(native)

    backend.write("V8AgentOS/model/test", "secret-value")
    assert native.payload == {
        "Type": native.CRED_TYPE_GENERIC,
        "TargetName": "V8AgentOS/model/test",
        "CredentialBlob": "secret-value",
        "Persist": native.CRED_PERSIST_LOCAL_MACHINE,
        "UserName": "V8 Agent OS",
        "Comment": "Managed V8 Agent OS credential. Do not edit manually.",
    }

    native.value = "secret-value"
    assert backend.read("V8AgentOS/model/test") == "secret-value"
    native.value = "secret-value".encode("utf-16-le")
    assert backend.read("V8AgentOS/model/test") == "secret-value"
    assert backend.delete("V8AgentOS/model/test") is True
    assert native.deleted is True
    native.value = None
    assert backend.read("V8AgentOS/model/test") is None
    assert backend.delete("V8AgentOS/model/test") is False


def test_platform_dependencies_and_packaged_runtime_probes_are_explicit():
    linux_requirements = (ENGINE_ROOT / "requirements" / "platform-linux.txt").read_text(encoding="utf-8")
    macos_requirements = (ENGINE_ROOT / "requirements" / "platform-macos.txt").read_text(encoding="utf-8")
    windows_requirements = (ENGINE_ROOT / "requirements" / "platform-windows.txt").read_text(encoding="utf-8")
    runtime_probe = (
        REPO_ROOT / "apps" / "v8-agent-os-shell" / "tests" / "scripts" / "verify_desktop_release_runtime.mjs"
    ).read_text(encoding="utf-8")
    package_config = (REPO_ROOT / "apps" / "v8-agent-os-shell" / "electron-builder.yml").read_text(encoding="utf-8")
    helper_source = NATIVE_HELPER.read_text(encoding="utf-8")

    assert 'keyring==25.7.0 ; sys_platform == "linux"' in linux_requirements
    assert 'keyring==25.7.0 ; sys_platform == "darwin"' in macos_requirements
    assert "keyring" not in windows_requirements.lower()
    assert "keyrings.alt" not in linux_requirements.lower()
    assert "keyrings.alt" not in macos_requirements.lower()
    assert 'requiredModules.windowsCredentialManager = "win32cred"' in runtime_probe
    assert 'requiredModules.macOSKeychainApi = "keyring.backends.macOS.api"' in runtime_probe
    assert 'requiredModules.secretStorage = "secretstorage"' in runtime_probe
    assert "- gnome-keyring" in package_config
    assert "- libpam-gnome-keyring" in package_config
    assert "keyring.backends.SecretService" in helper_source
    assert "keyring.backends.macOS" in helper_source
