from __future__ import annotations

import subprocess
import sys
from typing import Any


def windowless_subprocess_kwargs() -> dict[str, int]:
    """Return Windows-only flags for Engine-owned background processes."""

    if sys.platform != "win32":
        return {}
    return {"creationflags": int(getattr(subprocess, "CREATE_NO_WINDOW", 0))}


def run_windowless(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    """Run an Engine-owned subprocess without opening a Windows console."""

    for key, value in windowless_subprocess_kwargs().items():
        kwargs.setdefault(key, value)
    return subprocess.run(*args, **kwargs)
