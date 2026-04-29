from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass

try:  # pragma: no cover - exercised by fallback tests
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore


_TTL_SECONDS = 5.0
_GPU_TIMEOUT_SECONDS = 0.6
_CACHE: tuple[float, "HostLoadSnapshot"] | None = None


@dataclass(frozen=True)
class HostLoadSnapshot:
    cpu_percent: int | None
    memory_percent: int | None
    gpu_percent: int | None
    process_count: int | None


def _safe_percent(value: object) -> int | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except Exception:
        return None
    if number < 0:
        return None
    return max(0, min(100, int(round(number))))


def _collect_gpu_percent() -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=_GPU_TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:
        return None
    if completed.returncode != 0:
        return None
    values: list[int] = []
    for line in str(completed.stdout or "").splitlines():
        match = re.search(r"\d+(?:\.\d+)?", line)
        value = _safe_percent(match.group(0) if match else None)
        if value is not None:
            values.append(value)
    return max(values) if values else None


def collect_host_load_snapshot(*, use_cache: bool = True) -> HostLoadSnapshot:
    global _CACHE
    now = time.monotonic()
    if use_cache and _CACHE is not None:
        cached_at, snapshot = _CACHE
        if now - cached_at <= _TTL_SECONDS:
            return snapshot

    cpu_percent: int | None = None
    memory_percent: int | None = None
    process_count: int | None = None
    if psutil is not None:
        try:
            cpu_percent = _safe_percent(psutil.cpu_percent(interval=None))
        except Exception:
            cpu_percent = None
        try:
            memory_percent = _safe_percent(psutil.virtual_memory().percent)
        except Exception:
            memory_percent = None
        try:
            process_count = len(psutil.pids())
        except Exception:
            process_count = None

    snapshot = HostLoadSnapshot(
        cpu_percent=cpu_percent,
        memory_percent=memory_percent,
        gpu_percent=_collect_gpu_percent(),
        process_count=process_count,
    )
    _CACHE = (now, snapshot)
    return snapshot


def _format_value(value: int | None, *, percent: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value}%" if percent else str(value)


def render_host_load_line(*, use_cache: bool = True) -> str:
    snapshot = collect_host_load_snapshot(use_cache=use_cache)
    return (
        "Host Load: "
        f"CPU {_format_value(snapshot.cpu_percent, percent=True)}, "
        f"Mem {_format_value(snapshot.memory_percent, percent=True)}, "
        f"GPU {_format_value(snapshot.gpu_percent, percent=True)}, "
        f"Procs {_format_value(snapshot.process_count)}"
    )


def clear_host_load_cache() -> None:
    global _CACHE
    _CACHE = None
