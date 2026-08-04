from __future__ import annotations

import errno
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class InterProcessLockTimeout(TimeoutError):
    def __init__(self, path: Path, timeout_seconds: float):
        self.path = path
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"等待资源锁超时（{timeout_seconds:.1f}s）：{path}。"
            "另一个 V8 Agent OS 进程可能仍在修改该资源，请稍后重试。"
        )


_LOCAL_LOCKS_GUARD = threading.Lock()
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_BLOCKED_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}


def _local_lock_for(path: Path) -> threading.Lock:
    key = os.path.normcase(str(path.resolve(strict=False)))
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.Lock())


def _try_acquire_file_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        # Windows permits locking a byte range past EOF, so an empty or
        # previously damaged coordination file does not need repair first.
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_file_lock(handle) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_lock_contention(error: OSError) -> bool:
    return (
        isinstance(error, BlockingIOError)
        or error.errno in _BLOCKED_ERRNOS
        or getattr(error, "winerror", None) in {32, 33}
    )


@contextmanager
def interprocess_file_lock(
    path: Path,
    *,
    timeout_seconds: float = 15.0,
    poll_interval_seconds: float = 0.05,
) -> Iterator[None]:
    """Serialize one filesystem resource across threads and Engine processes.

    The coordination file is intentionally persistent. Its contents are not
    trusted or parsed, and the OS releases the held lock when a process exits.
    This avoids stale owner metadata and unlink/recreate races between waiters.
    """

    lock_path = Path(path).expanduser().resolve(strict=False)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = max(0.0, float(timeout_seconds))
    poll_interval = max(0.01, float(poll_interval_seconds))
    deadline = time.monotonic() + timeout
    local_lock = _local_lock_for(lock_path)
    local_remaining = max(0.0, deadline - time.monotonic())
    if not local_lock.acquire(timeout=local_remaining):
        raise InterProcessLockTimeout(lock_path, timeout)

    handle = None
    acquired = False
    try:
        handle = lock_path.open("a+b", buffering=0)
        while True:
            try:
                _try_acquire_file_lock(handle)
                acquired = True
                break
            except OSError as exc:
                if not _is_lock_contention(exc):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise InterProcessLockTimeout(lock_path, timeout) from exc
                time.sleep(min(poll_interval, remaining))
        yield
    finally:
        if acquired and handle is not None:
            try:
                _release_file_lock(handle)
            except OSError:
                pass
        if handle is not None:
            handle.close()
        local_lock.release()
