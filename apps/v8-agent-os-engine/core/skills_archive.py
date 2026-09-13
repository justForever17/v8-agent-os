"""One bounded ZIP validator for uploads, GitHub and catalog downloads."""
from __future__ import annotations

import io
import re
import stat
import zipfile
from pathlib import Path

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_EXTRACTED_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 4096
MAX_COMPRESSION_RATIO = 250


class ArchiveValidationError(ValueError):
    pass


def validated_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_ENTRIES:
        raise ArchiveValidationError("压缩包文件数量超过限制。")
    seen: dict[str, bool] = {}
    spellings: dict[str, str] = {}
    total = 0
    for member in members:
        name = member.filename
        parts = name.rstrip("/").split("/")
        if (not name or "\\" in name or name.startswith("/") or
                any(part in {"", ".", ".."} or part.endswith((".", " ")) or
                    re.search(r'[\x00-\x1f:<>"|?*]', part) or
                    re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
                    for part in parts)):
            raise ArchiveValidationError("压缩包包含非法或跨平台歧义路径。")
        mode = member.external_attr >> 16
        if stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or member.flag_bits & 1:
            raise ArchiveValidationError("压缩包不能包含链接、特殊文件或加密条目。")
        key = "/".join(parts).casefold()
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            old_spelling = spellings.setdefault(prefix.casefold(), prefix)
            if old_spelling != prefix:
                raise ArchiveValidationError("压缩包包含大小写冲突的目录。")
        if key in seen:
            raise ArchiveValidationError("压缩包包含重复或大小写冲突的路径。")
        seen[key] = member.is_dir()
        total += member.file_size
        if (member.file_size > MAX_FILE_BYTES or total > MAX_EXTRACTED_BYTES or
                member.file_size > max(member.compress_size, 1) * MAX_COMPRESSION_RATIO):
            raise ArchiveValidationError("压缩包解压大小或压缩比超过限制。")
    for key in seen:
        parts = key.split("/")
        if any(seen.get("/".join(parts[:i])) is False for i in range(1, len(parts))):
            raise ArchiveValidationError("压缩包文件与目录路径冲突。")
    return members


def open_archive(content: bytes) -> zipfile.ZipFile:
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ArchiveValidationError("ZIP 文件不能超过 32 MiB。")
    return zipfile.ZipFile(io.BytesIO(content))


def skill_root(content: bytes) -> str:
    with open_archive(content) as archive:
        files = [m.filename for m in validated_members(archive) if not m.is_dir()]
        if not files:
            raise ArchiveValidationError("压缩包没有文件。")
        # Root packages are first-class; never derive identity from a revision wrapper.
        if "SKILL.md" in files:
            return ""
        roots = {name.split("/")[0] for name in files}
        if len(roots) != 1 or any("/" not in name for name in files):
            raise ArchiveValidationError("请选择一个包含 SKILL.md 的包根，合集请放在一个目录内。")
        if not any(name.endswith("/SKILL.md") for name in files):
            raise ArchiveValidationError("压缩包缺少 SKILL.md。")
        return next(iter(roots))


def extract_archive(content: bytes, destination: Path) -> None:
    with open_archive(content) as archive:
        members = validated_members(archive)  # Validate everything before writing anything.
        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()
        total = 0
        for member in members:
            target = destination.joinpath(*member.filename.rstrip("/").split("/"))
            if not target.resolve().is_relative_to(root):
                raise ArchiveValidationError("解压目标超出安装暂存目录。")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.open(member) as source, target.open("xb") as output:
                while chunk := source.read(64 * 1024):
                    written += len(chunk)
                    total += len(chunk)
                    if written > MAX_FILE_BYTES or total > MAX_EXTRACTED_BYTES:
                        raise ArchiveValidationError("实际解压内容超过限制。")
                    output.write(chunk)
            if written != member.file_size:
                raise ArchiveValidationError("压缩包文件长度不匹配。")
