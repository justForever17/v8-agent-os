"""Bind native file commits to the directories observed before Safety review.

This is not a filesystem sandbox. POSIX directory descriptors survive renames;
leaf identity checks remain optimistic against non-cooperating external writers.
"""
from __future__ import annotations

import os
import stat
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class FileIdentityChanged(ValueError):
    def __init__(self) -> None:
        super().__init__("file_identity_changed_before_commit")


class FileCommitPathChanged(ValueError):
    def __init__(self) -> None:
        super().__init__("file_committed_to_moved_directory")


FileIdentity = tuple[int, int, int, int]


def _identity(info: os.stat_result) -> FileIdentity:
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise FileIdentityChanged()
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode), getattr(info, "st_birthtime_ns", 0)


def _path_identity(path: Path | str, *, dir_fd: int | None = None) -> FileIdentity | None:
    try:
        return _identity(os.stat(path, dir_fd=dir_fd, follow_symlinks=False))
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class FileIdentitySnapshot:
    target_path: Path
    directories: tuple[tuple[Path, FileIdentity | None], ...]
    target_identity: FileIdentity | None


def capture_file_identity(target_path: Path) -> FileIdentitySnapshot:
    # Preserve the approved path; resolve() here could adopt a substituted link.
    target = Path(os.path.abspath(target_path))
    directories = tuple((path, _path_identity(path)) for path in reversed(target.parents))
    return FileIdentitySnapshot(target, directories, _path_identity(target))


@contextmanager
def _windows_directory_handle(path: Path) -> Iterator[None]:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # LIST_DIRECTORY | READ_ATTRIBUTES, SHARE_READ | SHARE_WRITE, OPEN_EXISTING,
    # BACKUP_SEMANTICS | OPEN_REPARSE_POINT. Deny DELETE to prevent renames.
    # WRITE sharing is needed by Windows to replace files inside the directory.
    # Metadata-only access does not enforce sharing against directory renames.
    handle = create(str(path), 0x81, 0x3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        close(handle)


@dataclass(frozen=True)
class BoundFileParent:
    snapshot: FileIdentitySnapshot
    directory_identities: tuple[tuple[Path, FileIdentity], ...]
    dir_fd: int | None

    def validate(self, *, check_target: bool = True) -> None:
        # POSIX can rename an open directory. Detect changes observed here;
        # subsequent operations still use its fd, never a replacement directory.
        for path, identity in self.directory_identities:
            if _path_identity(path) != identity:
                raise FileIdentityChanged()
        if check_target:
            target = self.snapshot.target_path
            current = _path_identity(target.name if self.dir_fd is not None else target, dir_fd=self.dir_fd)
            if current != self.snapshot.target_identity:
                raise FileIdentityChanged()


@contextmanager
def bind_file_parent(snapshot: FileIdentitySnapshot) -> Iterator[BoundFileParent]:
    """Acquire short-lived handles only after Safety, before any staging write."""
    with ExitStack() as stack:
        parent_fd = None
        bound_identities = []
        for path, expected in snapshot.directories:
            lookup = path.name if parent_fd is not None else path
            current = _path_identity(lookup, dir_fd=parent_fd)
            if current != expected:
                raise FileIdentityChanged()
            if current is None:
                # Parent is already pinned. Never reuse an unexpectedly created
                # directory; mkdir races fail instead of broadening the write.
                os.mkdir(lookup, dir_fd=parent_fd)
                current = _path_identity(lookup, dir_fd=parent_fd)
            if os.name == "nt":
                stack.enter_context(_windows_directory_handle(path))
                actual = _path_identity(path)
            else:
                descriptor = os.open(lookup, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                stack.callback(os.close, descriptor)
                actual = _identity(os.fstat(descriptor))
                parent_fd = descriptor
            if actual != current or actual is None or actual[2] != stat.S_IFDIR:
                raise FileIdentityChanged()
            bound_identities.append((path, actual))
        bound = BoundFileParent(snapshot, tuple(bound_identities), parent_fd)
        bound.validate(check_target=False)
        yield bound
