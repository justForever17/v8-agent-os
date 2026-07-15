from __future__ import annotations

import platform
import shutil
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def _detect_command_environment_cached() -> tuple[str, str, str]:
    os_name = platform.system() or sys.platform
    if sys.platform == "win32":
        if shutil.which("powershell.exe"):
            shell_dialect = "powershell"
        elif shutil.which("pwsh"):
            shell_dialect = "pwsh"
        else:
            shell_dialect = "cmd"
    elif shutil.which("bash"):
        shell_dialect = "bash"
    else:
        shell_dialect = "sh"
    labels = {
        "powershell": "PowerShell",
        "pwsh": "PowerShell Core",
        "cmd": "cmd.exe",
        "bash": "Bash",
        "sh": "POSIX sh",
    }
    return os_name, shell_dialect, labels.get(shell_dialect, shell_dialect)


def detect_command_environment() -> dict[str, str]:
    os_name, shell_dialect, command_language = _detect_command_environment_cached()
    return {
        "osName": os_name,
        "shellDialect": shell_dialect,
        "commandLanguage": command_language,
    }


def default_shell_dialect() -> str:
    return detect_command_environment()["shellDialect"]


__all__ = ["default_shell_dialect", "detect_command_environment"]
