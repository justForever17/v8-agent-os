from __future__ import annotations

import io
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml

from runtimes.extensions.skills.loader import SkillLoader


_SUPPORTED_NPX_FLAGS = {"-y", "--yes"}
_ENGINE_VENV_ROOT = Path(__file__).resolve().parents[1] / ".venv"


class SkillInstallValidationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass(slots=True)
class ParsedSkillInstallCommand:
    raw_command: str
    source: str
    skill_name: str | None
    overwrite: bool


@dataclass(slots=True)
class SkillManifest:
    folder: str
    name: str
    description: str
    source_dir: Path


def get_skill_dependency_policy() -> dict[str, Any]:
    return {
        "mode": "engine_venv_only",
        "pythonTarget": str(_ENGINE_VENV_ROOT),
        "systemWideInstallAllowed": False,
        "nodeGlobalInstallAllowed": False,
    }


def _parse_yaml_frontmatter(content: str) -> dict[str, Any]:
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


def parse_skill_install_command(command: str) -> ParsedSkillInstallCommand:
    stripped = str(command or "").strip()
    if not stripped:
        raise ValueError("安装命令不能为空。")

    tokens = shlex.split(stripped, posix=os.name != "nt")
    if not tokens or tokens[0] != "npx":
        raise ValueError("当前只支持以 `npx skills add ...` 开头的安装命令。")

    index = 1
    while index < len(tokens) and tokens[index] in _SUPPORTED_NPX_FLAGS:
        index += 1

    if index >= len(tokens) or tokens[index] != "skills":
        raise ValueError("当前仅支持 `npx skills add <source>` 形式的命令。")
    index += 1

    if index >= len(tokens) or tokens[index] != "add":
        raise ValueError("当前仅支持 `skills add` 命令。")
    index += 1

    if index >= len(tokens):
        raise ValueError("请提供要安装的 Skills 来源。")

    source = tokens[index]
    index += 1

    skill_name: str | None = None
    overwrite = False

    while index < len(tokens):
        token = tokens[index]
        if token == "--skill":
            index += 1
            if index >= len(tokens):
                raise ValueError("`--skill` 后必须紧跟要安装的 Skills 名称。")
            skill_name = tokens[index]
            index += 1
            continue
        if token == "--overwrite":
            overwrite = True
            index += 1
            continue
        raise ValueError(f"不支持的安装参数：{token}")

    return ParsedSkillInstallCommand(
        raw_command=stripped,
        source=source,
        skill_name=skill_name,
        overwrite=overwrite,
    )


def _target_root() -> Path:
    target = Path.home() / ".agents" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _resolve_local_source(source: str) -> Path | None:
    candidate = Path(source).expanduser()
    if candidate.exists():
        return candidate.resolve()
    return None


def _parse_github_source(source: str) -> tuple[str, str] | None:
    normalized = source.strip()
    short_match = re.fullmatch(r"(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)", normalized)
    if short_match:
        return short_match.group("owner"), short_match.group("repo")

    url_match = re.fullmatch(
        r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?",
        normalized,
    )
    if url_match:
        return url_match.group("owner"), url_match.group("repo")
    return None


def _clone_from_github(owner: str, repo: str, destination: Path) -> Path:
    repo_url = f"https://github.com/{owner}/{repo}.git"
    if shutil.which("git"):
        subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, str(destination)],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return destination

    with httpx.Client(timeout=60.0, follow_redirects=True) as client:
        meta = client.get(f"https://api.github.com/repos/{owner}/{repo}")
        meta.raise_for_status()
        default_branch = str(meta.json().get("default_branch") or "main")
        archive = client.get(f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{default_branch}")
        archive.raise_for_status()

    _safe_extract_zip_bytes(archive.content, destination)
    extracted_children = [child for child in destination.iterdir() if child.is_dir()]
    if len(extracted_children) == 1:
        return extracted_children[0]
    return destination


def _resolve_source_tree(source: str, workspace: Path) -> Path:
    local_source = _resolve_local_source(source)
    if local_source is not None:
        return local_source

    github_source = _parse_github_source(source)
    if github_source is None:
        raise ValueError("当前只支持本地目录、GitHub 仓库地址或 owner/repo 形式的 Skills 来源。")

    owner, repo = github_source
    return _clone_from_github(owner, repo, workspace / repo)


def _safe_extract_zip_bytes(content: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for member in archive.infolist():
            member_path = destination / member.filename
            resolved_target = member_path.resolve(strict=False)
            if not str(resolved_target).startswith(str(destination.resolve(strict=False))):
                raise ValueError("压缩包包含非法路径，已拒绝导入。")
        archive.extractall(destination)


def _safe_extract_zip_file(archive_path: Path, destination: Path) -> None:
    with archive_path.open("rb") as handle:
        _safe_extract_zip_bytes(handle.read(), destination)


def _validate_skill_zip_layout(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            file_members = []
            top_level_entries: set[str] = set()
            root_level_files: list[str] = []
            skill_md_relpaths: list[str] = []

            for member in archive.infolist():
                normalized = str(member.filename or "").replace("\\", "/").strip()
                if not normalized or normalized.startswith("__MACOSX/"):
                    continue

                clean_parts = [part for part in normalized.split("/") if part and part != "."]
                if not clean_parts:
                    continue

                top_level_entries.add(clean_parts[0])
                if member.is_dir():
                    continue

                file_members.append(clean_parts)
                if len(clean_parts) == 1:
                    root_level_files.append(clean_parts[0])
                if clean_parts[-1].lower() == "skill.md":
                    skill_md_relpaths.append("/".join(clean_parts))

            if not file_members:
                raise SkillInstallValidationError(
                    "empty_archive",
                    "压缩包中没有可导入的文件。",
                )
            if root_level_files:
                raise SkillInstallValidationError(
                    "invalid_root_structure",
                    "压缩包顶层必须只有一个目录，不能直接放散文件。",
                    {"rootFiles": root_level_files[:8]},
                )
            if len(top_level_entries) != 1:
                raise SkillInstallValidationError(
                    "multiple_root_directories",
                    "压缩包顶层必须只包含一个目录。",
                    {"rootEntries": sorted(top_level_entries)},
                )
            if not skill_md_relpaths:
                raise SkillInstallValidationError(
                    "missing_skill_manifest",
                    "压缩包内至少需要包含一个 SKILL.md 文件。",
                )

            return next(iter(top_level_entries))
    except zipfile.BadZipFile as exc:
        raise SkillInstallValidationError(
            "invalid_zip",
            "上传文件不是合法的 ZIP 压缩包。",
        ) from exc


def _discover_skill_manifests(root: Path) -> list[SkillManifest]:
    candidates: list[Path] = []
    for preferred_root in (root / ".agents" / "skills", root / "skills"):
        if preferred_root.exists() and preferred_root.is_dir():
            candidates.extend(sorted(preferred_root.glob("*/SKILL.md")))

    if not candidates:
        for skill_md in root.rglob("SKILL.md"):
            lowered_parts = {part.lower() for part in skill_md.parts}
            if ".git" in lowered_parts or "node_modules" in lowered_parts or "__pycache__" in lowered_parts:
                continue
            candidates.append(skill_md)

    manifests: list[SkillManifest] = []
    seen_folders: set[str] = set()
    for skill_md in candidates:
        folder = skill_md.parent.name
        if folder in seen_folders:
            continue
        seen_folders.add(folder)
        content = skill_md.read_text(encoding="utf-8", errors="ignore")
        frontmatter = _parse_yaml_frontmatter(content)
        manifests.append(
            SkillManifest(
                folder=folder,
                name=str(frontmatter.get("name") or folder),
                description=str(frontmatter.get("description") or ""),
                source_dir=skill_md.parent,
            )
        )
    return manifests


def _select_manifests(manifests: list[SkillManifest], *, skill_name: str | None) -> list[SkillManifest]:
    if not skill_name:
        return manifests

    normalized = skill_name.strip().lower()
    selected = [
        manifest
        for manifest in manifests
        if manifest.folder.lower() == normalized or manifest.name.strip().lower() == normalized
    ]
    if not selected:
        raise ValueError(f"未在来源仓库中找到名为 `{skill_name}` 的 Skill。")
    return selected


def _install_manifests(
    manifests: list[SkillManifest],
    *,
    source: str,
    overwrite: bool,
) -> dict[str, Any]:
    target_root = _target_root()
    installed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    for manifest in manifests:
        target_dir = target_root / manifest.folder
        if target_dir.exists():
            if not overwrite:
                conflicts.append(
                    {
                        "name": manifest.name,
                        "folder": manifest.folder,
                        "path": str(target_dir),
                        "reason": "already_exists",
                    }
                )
                continue
            shutil.rmtree(target_dir)

        shutil.copytree(manifest.source_dir, target_dir)
        installed.append(
            {
                "name": manifest.name,
                "folder": manifest.folder,
                "path": str(target_dir),
                "overwritten": overwrite,
            }
        )

    if not installed and conflicts:
        raise ValueError("目标目录中已存在同名 Skill；如需覆盖，请在命令中追加 `--overwrite`。")

    SkillLoader.reload_skills()
    return {
        "status": "success",
        "source": source,
        "targetRoot": str(target_root),
        "installed": installed,
        "skipped": skipped,
        "conflicts": conflicts,
        "warnings": [],
    }


def install_skill_from_command(command: str) -> dict[str, Any]:
    parsed = parse_skill_install_command(command)
    with tempfile.TemporaryDirectory(prefix="v8chat-skill-install-") as temp_dir:
        source_tree = _resolve_source_tree(parsed.source, Path(temp_dir))
        manifests = _discover_skill_manifests(source_tree)
        if not manifests:
            raise ValueError("来源中没有发现任何合法的 Skill 目录。")
        selected = _select_manifests(manifests, skill_name=parsed.skill_name)
        return _install_manifests(selected, source=parsed.source, overwrite=parsed.overwrite)


def install_skills_from_zip(file_name: str, content: bytes) -> dict[str, Any]:
    if not str(file_name or "").lower().endswith(".zip"):
        raise SkillInstallValidationError("invalid_file_type", "当前仅支持 ZIP 压缩包导入。")

    with tempfile.TemporaryDirectory(prefix="v8chat-skill-zip-") as temp_dir:
        extract_root = Path(temp_dir) / "unzipped"
        root_folder = _validate_skill_zip_layout(content)
        _safe_extract_zip_bytes(content, extract_root)
        manifests = _discover_skill_manifests(extract_root / root_folder)
        if not manifests:
            raise SkillInstallValidationError(
                "missing_skill_manifest",
                "压缩包结构已解压，但没有发现任何合法的 Skill 目录。",
                {"rootFolder": root_folder},
            )
        return _install_manifests(manifests, source=file_name, overwrite=False)
