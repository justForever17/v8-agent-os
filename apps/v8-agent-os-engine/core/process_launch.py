from __future__ import annotations

import os
import re
import subprocess
import sys
from typing import Any


_CMD_META_CHARS = re.compile(r'([()\[\]%!^"`<>&|;, *?])')


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


def run_windowless(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run an Engine-owned subprocess without opening a Windows console."""

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
    return subprocess.run(*normalized_args, **kwargs)
