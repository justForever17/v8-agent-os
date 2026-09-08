from __future__ import annotations

import re
import shutil
import threading
import time
from dataclasses import dataclass

from core.process_launch import run_windowless

try:  # pragma: no cover - exercised by fallback tests
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None  # type: ignore


_TTL_SECONDS = 5.0
_GPU_TIMEOUT_SECONDS = 0.6
_CACHE: tuple[float, "HostLoadSnapshot"] | None = None
_CACHE_LOCK = threading.Lock()
_CACHE_GENERATION = 0
_SAMPLER: threading.Thread | None = None
_RETRY_AFTER = 0.0


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
        completed = run_windowless(
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


def _sample_host_load_snapshot() -> HostLoadSnapshot:
    # Slow GPU process startup must not age CPU/memory values before publication.
    gpu_percent = _collect_gpu_percent()
    cpu_percent: int | None = None
    memory_percent: int | None = None
    process_count: int | None = None
    if psutil is not None:
        try:
            # A new sampling thread has no psutil baseline; a non-blocking
            # first call would report an unmeasured zero as real CPU usage.
            cpu_percent = _safe_percent(psutil.cpu_percent(interval=0.1))
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

    return HostLoadSnapshot(
        cpu_percent=cpu_percent,
        memory_percent=memory_percent,
        gpu_percent=gpu_percent,
        process_count=process_count,
    )


def collect_host_load_snapshot(*, use_cache: bool = True) -> HostLoadSnapshot:
    """Explicit diagnostic collection may wait for real host probes."""
    global _CACHE
    with _CACHE_LOCK:
        generation = _CACHE_GENERATION
        if use_cache and _CACHE is not None:
            cached_at, snapshot = _CACHE
            if time.monotonic() - cached_at <= _TTL_SECONDS:
                return snapshot
    snapshot = _sample_host_load_snapshot()
    with _CACHE_LOCK:
        if generation == _CACHE_GENERATION:
            _CACHE = (time.monotonic(), snapshot)
    return snapshot


def _refresh_prompt_snapshot(generation: int) -> None:
    global _CACHE, _SAMPLER, _RETRY_AFTER
    try:
        snapshot = _sample_host_load_snapshot()
    except Exception:
        with _CACHE_LOCK:
            if generation == _CACHE_GENERATION:
                _RETRY_AFTER = time.monotonic() + _TTL_SECONDS
    else:
        with _CACHE_LOCK:
            if generation == _CACHE_GENERATION:
                _CACHE = (time.monotonic(), snapshot)
                _RETRY_AFTER = 0.0
    finally:
        with _CACHE_LOCK:
            _SAMPLER = None


def _prompt_snapshot() -> tuple[HostLoadSnapshot, str]:
    global _SAMPLER, _RETRY_AFTER
    sampler = None
    with _CACHE_LOCK:
        now = time.monotonic()
        cached = _CACHE
        age = max(0.0, now - cached[0]) if cached else None
        if cached is not None and age <= _TTL_SECONDS:
            return cached[1], ""
        if _SAMPLER is None and now >= _RETRY_AFTER:
            generation = _CACHE_GENERATION
            sampler = threading.Thread(
                target=_refresh_prompt_snapshot, args=(generation,),
                name="v8os-host-load-sample", daemon=True,
            )
            _SAMPLER = sampler
        status = "sampling" if _SAMPLER is not None else "unavailable; retry pending"
    if sampler is not None:
        try:
            sampler.start()
        except Exception:
            with _CACHE_LOCK:
                _SAMPLER = None
                if generation == _CACHE_GENERATION:
                    _RETRY_AFTER = time.monotonic() + _TTL_SECONDS
            status = "unavailable; retry pending"
    if cached is None:
        return HostLoadSnapshot(None, None, None, None), f" ({status})"
    return cached[1], f" (sample age: {age:.1f}s; {status})"


def _format_value(value: int | None, *, percent: bool = False) -> str:
    if value is None:
        return "n/a"
    return f"{value}%" if percent else str(value)


def render_host_load_line(*, use_cache: bool = True) -> str:
    # Prompt preparation must never wait for subprocess creation or GPU probes.
    snapshot, status = _prompt_snapshot() if use_cache else (collect_host_load_snapshot(use_cache=False), "")
    return (
        "Host Load: "
        f"CPU {_format_value(snapshot.cpu_percent, percent=True)}, "
        f"Mem {_format_value(snapshot.memory_percent, percent=True)}, "
        f"GPU {_format_value(snapshot.gpu_percent, percent=True)}, "
        f"Procs {_format_value(snapshot.process_count)}{status}"
    )


def clear_host_load_cache() -> None:
    global _CACHE, _CACHE_GENERATION, _RETRY_AFTER
    with _CACHE_LOCK:
        _CACHE = None
        _CACHE_GENERATION += 1
        _RETRY_AFTER = 0.0
        # Keep an in-flight sampler reserved until it exits; clear must not
        # create a second probe or allow the old generation to refill the cache.
