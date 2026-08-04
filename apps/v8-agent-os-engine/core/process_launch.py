from __future__ import annotations

import ctypes
import os
import re
import signal
import subprocess
import sys
from ctypes import wintypes
from typing import Any


_CMD_META_CHARS = re.compile(r'([()\[\]%!^"`<>&|;, *?])')
_PROCESS_TREE_DRAIN_TIMEOUT_SECONDS = 5.0
_PROCESS_REAP_TIMEOUT_SECONDS = 1.0
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobObjectBasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _WindowsProcessJob:
    def __init__(self, kernel32: Any, handle: int) -> None:
        self._kernel32 = kernel32
        self._handle = handle

    def terminate(self) -> bool:
        handle = self._handle
        if handle is None:
            return True
        try:
            return bool(self._kernel32.TerminateJobObject(handle, 1))
        finally:
            self.close()

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        self._kernel32.CloseHandle(handle)


def windowless_subprocess_kwargs() -> dict[str, int]:
    """Return Windows-only flags for Engine-owned background processes."""

    if sys.platform != "win32":
        return {}
    return {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0))}


def _escape_batch_command(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("Windows batch launcher paths cannot contain line breaks")
    return _CMD_META_CHARS.sub(r"^\1", value)


def _escape_batch_argument(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("Windows batch launcher arguments cannot contain line breaks")
    escaped = re.sub(r'(\\*)"', lambda match: match.group(1) * 2 + '\\"', value)
    escaped = re.sub(r"(\\+)$", lambda match: match.group(1) * 2, escaped)
    escaped = _CMD_META_CHARS.sub(r"^\1", f'"{escaped}"')
    # Batch launchers parse forwarded arguments once more than native executables.
    return _CMD_META_CHARS.sub(r"^\1", escaped)


def _normalize_windowless_command(command: Any) -> tuple[Any, str | None]:
    """Route Windows batch launchers through an explicitly hidden cmd.exe."""

    if sys.platform != "win32" or not isinstance(command, (list, tuple)) or not command:
        return command, None
    argv = [str(os.fspath(item)) for item in command]
    if os.path.splitext(argv[0])[1].lower() not in {".bat", ".cmd"}:
        return command, None
    comspec = os.environ.get("COMSPEC") or "cmd.exe"
    shell_command = " ".join(
        [_escape_batch_command(argv[0]), *(_escape_batch_argument(item) for item in argv[1:])]
    )
    command_line = f'{subprocess.list2cmdline([comspec])} /d /s /c "{shell_command}"'
    return command_line, comspec


def _prepare_windowless_invocation(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    normalized_args = list(args)
    command = normalized_args[0] if normalized_args else kwargs.get("args")
    normalized_command, executable = _normalize_windowless_command(command)
    if normalized_args:
        normalized_args[0] = normalized_command
    elif "args" in kwargs:
        kwargs["args"] = normalized_command
    if executable:
        kwargs.setdefault("executable", executable)
    for key, value in windowless_subprocess_kwargs().items():
        kwargs.setdefault(key, value)
    return normalized_args, kwargs


def run_windowless(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run an Engine-owned subprocess without opening a Windows console."""

    normalized_args, kwargs = _prepare_windowless_invocation(args, kwargs)
    return subprocess.run(*normalized_args, **kwargs)


def popen_windowless(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    """Start an Engine-owned background process without opening a Windows console."""

    normalized_args, kwargs = _prepare_windowless_invocation(args, kwargs)
    return subprocess.Popen(*normalized_args, **kwargs)


def _attach_windows_process_job(process: subprocess.Popen[Any]) -> _WindowsProcessJob | None:
    """Attach the exact process handle to a kill-on-close Windows job."""

    if sys.platform != "win32":
        return None
    process_handle = getattr(process, "_handle", None)
    if process_handle is None:
        return None

    job: _WindowsProcessJob | None = None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        job_handle = kernel32.CreateJobObjectW(None, None)
        if not job_handle:
            return None
        job = _WindowsProcessJob(kernel32, job_handle)
        limits = _JobObjectExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job_handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            job.close()
            return None
        if not kernel32.AssignProcessToJobObject(job_handle, wintypes.HANDLE(int(process_handle))):
            job.close()
            return None
        return job
    except (AttributeError, OSError, TypeError, ValueError):
        try:
            if job is None:
                return None
            job.close()
        except OSError:
            pass
        return None


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
                **windowless_subprocess_kwargs(),
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _close_process_pipes(process: subprocess.Popen[Any]) -> None:
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is None:
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def _reap_process_bounded(process: subprocess.Popen[Any]) -> None:
    try:
        process.wait(timeout=_PROCESS_REAP_TIMEOUT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=_PROCESS_REAP_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_windowless_bounded(*args: Any, timeout: float, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run a bounded Engine subprocess and terminate its process tree on timeout."""

    check = bool(kwargs.pop("check", False))
    capture_output = bool(kwargs.pop("capture_output", False))
    input_value = kwargs.pop("input", None)
    if capture_output:
        if kwargs.get("stdout") is not None or kwargs.get("stderr") is not None:
            raise ValueError("stdout and stderr arguments may not be used with capture_output")
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if input_value is not None:
        if kwargs.get("stdin") is not None:
            raise ValueError("stdin and input arguments may not both be used")
        kwargs["stdin"] = subprocess.PIPE
    if sys.platform != "win32":
        kwargs.setdefault("start_new_session", True)

    process = popen_windowless(*args, **kwargs)
    process_job = _attach_windows_process_job(process)
    try:
        stdout, stderr = process.communicate(input=input_value, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if process_job is None or not process_job.terminate():
            _terminate_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=_PROCESS_TREE_DRAIN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as drain_exc:
            # A surviving descendant can keep inherited pipes open even after the
            # direct process exits. Preserve partial output and return control to
            # the governed timeout path instead of waiting on those pipes forever.
            stdout = drain_exc.output if drain_exc.output is not None else exc.output
            stderr = drain_exc.stderr if drain_exc.stderr is not None else exc.stderr
            _reap_process_bounded(process)
            _close_process_pipes(process)
        exc.stdout = stdout
        exc.stderr = stderr
        raise
    finally:
        if process_job is not None:
            process_job.close()

    completed = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    if check:
        completed.check_returncode()
    return completed
