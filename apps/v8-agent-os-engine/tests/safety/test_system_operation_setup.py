import sys
from types import SimpleNamespace

import pytest

from core.system_operations import setup
from core.system_operations.service import SystemOperationError


@pytest.fixture(autouse=True)
def isolated_setup_state(monkeypatch):
    monkeypatch.setattr(setup, "_pending", None)
    monkeypatch.setattr(setup, "_last", {"state": "idle"})


@pytest.mark.parametrize("component,action", [("arbitrary", "install"), ("unlock", "execute"), ("../unlock", "install")])
def test_no_arbitrary_installer_or_action(component, action):
    with pytest.raises(SystemOperationError):
        setup.begin_setup(component, action)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UAC setup contract")
def test_uac_wait_is_async_and_only_one_setup_can_be_in_flight(monkeypatch):
    threads = []
    monkeypatch.setattr(setup.Path, "is_file", lambda _: True)
    monkeypatch.setattr(setup.threading, "Thread", lambda **kwargs: threads.append(kwargs) or SimpleNamespace(start=lambda: None))
    result = setup.begin_setup("privilege", "install")
    assert result["state"] == "awaiting_os_approval"
    assert setup.setup_status()["state"] == "awaiting_os_approval"
    with pytest.raises(SystemOperationError, match="正在等待"):
        setup.begin_setup("unlock", "install")
    executable, argv = threads[0]["args"]
    assert executable.lower().endswith("powershell.exe")
    assert "-ClientSid" in argv and "-File" in argv
    assert "-Unattended" in argv and not any(value.startswith("-Confirm:") for value in argv)
    assert argv[argv.index("-File") + 1].endswith("v8-system-operations\\install.ps1")
    assert "password" not in " ".join(argv).lower()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UAC setup contract")
def test_cancelled_uac_is_not_reported_installed(monkeypatch):
    from win32com.shell import shell
    class Cancelled(Exception):
        winerror = 1223
    def cancel(**kwargs):
        assert kwargs["lpVerb"] == "runas" and kwargs["nShow"] == 0
        raise Cancelled()
    monkeypatch.setattr(shell, "ShellExecuteEx", cancel)
    setup._launch_setup("fixed-installer", [])
    assert setup.setup_status()["state"] == "cancelled"


def test_missing_native_dependency_does_not_leave_setup_waiting_forever(monkeypatch):
    monkeypatch.setitem(sys.modules, "pythoncom", None)
    monkeypatch.setattr(setup, "_last", {"state": "awaiting_os_approval"})
    setup._launch_setup("fixed-installer", [])
    assert setup.setup_status()["state"] == "failed"
