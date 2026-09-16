"""One active Engine per canonical state root; reuse the OS-backed file lock."""
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Iterator

from core.interprocess_lock import InterProcessLockTimeout, interprocess_file_lock


class EngineStateInUse(RuntimeError):
    code = "engine_state_in_use"


@contextmanager
def engine_state_owner(home: Path) -> Iterator[None]:
    # The file stays in place. Deleting it would let a new inode acquire a
    # second lock while the original Engine still holds its descriptor.
    lock_path = Path(home).resolve() / "runtime" / "engine-instance.lock"
    with ExitStack() as stack:
        try:
            stack.enter_context(interprocess_file_lock(lock_path, timeout_seconds=0))
        except InterProcessLockTimeout as exc:
            raise EngineStateInUse(
                "engine_state_in_use: 此状态目录已有 Engine 在运行。"
                "请连接现有实例；需要独立实例时使用不同的状态目录。"
            ) from exc
        yield
