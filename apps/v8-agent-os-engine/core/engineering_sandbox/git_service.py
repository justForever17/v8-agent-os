from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping, Sequence

from core.v8_agent_os_paths import V8_AGENT_OS_HOME

from .contracts import GitChangeSetRef, SandboxContractError
from .workspace_topology import WorkspaceTopology, resolve_workspace_topology


MAX_MANAGED_FILE_BYTES = 20 * 1024 * 1024
MANAGED_GITIGNORE_START = "# >>> V8 Agent OS managed ignores >>>"
MANAGED_GITIGNORE_END = "# <<< V8 Agent OS managed ignores <<<"
MANAGED_GITIGNORE_LINES = (
    ".v8-agent-os/",
    ".env",
    ".env.*",
    "!.env.example",
    "*.key",
    "*.pem",
    "*.p12",
    "*.pfx",
    "*.db-shm",
    "*.db-wal",
    "*.log",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".venv/",
    "venv/",
    "node_modules/",
    ".next/",
    "dist/",
    "build/",
    "target/",
    "coverage/",
)


class ManagedGitError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class ManagedRepository:
    repository_id: str
    topology: WorkspaceTopology
    state: str
    head_commit: str | None
    default_branch: str | None
    initialized_by_v8os: bool
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "repositoryId": self.repository_id,
            "topology": self.topology.as_dict(),
            "state": self.state,
            "headCommit": self.head_commit,
            "defaultBranch": self.default_branch,
            "initializedByV8OS": self.initialized_by_v8os,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ManagedWorktree:
    worktree_id: str
    repository_id: str
    branch_name: str
    base_commit: str
    topology: WorkspaceTopology
    state: str = "ready"

    def as_dict(self) -> dict[str, Any]:
        return {
            "worktreeId": self.worktree_id,
            "repositoryId": self.repository_id,
            "branchName": self.branch_name,
            "baseCommit": self.base_commit,
            "topology": self.topology.as_dict(),
            "state": self.state,
        }


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_segment(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip(".-_")
    return normalized[:72] or fallback


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).rstrip("\\/")


def _repository_id(repository_root: Path, workspace_root: Path) -> str:
    topology_key = f"{_path_key(repository_root)}\n{_path_key(workspace_root)}"
    return f"repo_{hashlib.sha256(topology_key.encode('utf-8')).hexdigest()[:20]}"


class ManagedGitService:
    def __init__(self, *, home: Path | None = None, git_executable: str | None = None) -> None:
        self.home = Path(home or V8_AGENT_OS_HOME).expanduser().resolve(strict=False)
        self.worktrees_root = self.home / "worktrees"
        self.runtime_root = self.home / "runtime" / "managed-git"
        self.empty_hooks_root = self.runtime_root / "empty-hooks"
        self.index_root = self.runtime_root / "indexes"
        self.git_executable = str(git_executable or shutil.which("git") or "").strip()
        if not self.git_executable:
            raise ManagedGitError("git_not_installed", "Git is required for managed engineering workspaces.")
        self.empty_hooks_root.mkdir(parents=True, exist_ok=True)
        self.index_root.mkdir(parents=True, exist_ok=True)

    def _environment(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        environment = dict(os.environ)
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": "",
                "GIT_SSH_COMMAND": "ssh -oBatchMode=yes",
                "GIT_PAGER": "cat",
                "PAGER": "cat",
            }
        )
        environment.update({str(key): str(value) for key, value in dict(extra or {}).items()})
        return environment

    def _command_prefix(self) -> list[str]:
        return [
            self.git_executable,
            "-c",
            f"core.hooksPath={self.empty_hooks_root}",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.pager=cat",
            "-c",
            "credential.helper=",
            "-c",
            "protocol.file.allow=never",
        ]

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        env: Mapping[str, str] | None = None,
        text: bool = True,
        input_text: str | bytes | None = None,
    ) -> subprocess.CompletedProcess[Any]:
        result = subprocess.run(
            [*self._command_prefix(), *[str(item) for item in args]],
            cwd=str(cwd),
            env=self._environment(env),
            shell=False,
            capture_output=True,
            text=text,
            input=input_text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            timeout=120,
        )
        if check and result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else str(result.stderr or "")
            raise ManagedGitError(
                "git_command_failed",
                stderr.strip() or f"Git command failed with exit code {result.returncode}.",
                details={"args": list(args), "cwd": str(cwd), "returnCode": result.returncode},
            )
        return result

    def discover_repository_root(self, workspace_root: str | Path) -> Path | None:
        workspace = Path(workspace_root).expanduser().resolve(strict=False)
        if not workspace.exists():
            return None
        result = self.run(["rev-parse", "--show-toplevel"], cwd=workspace, check=False)
        if result.returncode != 0:
            return None
        value = str(result.stdout or "").strip()
        return Path(value).resolve(strict=False) if value else None

    def inspect_repository(self, workspace_root: str | Path) -> ManagedRepository | None:
        workspace = Path(workspace_root).expanduser().resolve(strict=False)
        repository_root = self.discover_repository_root(workspace)
        if repository_root is None:
            return None
        self._assert_repository_safe(repository_root)
        topology = resolve_workspace_topology(workspace_root=workspace, repository_root=repository_root)
        head = self.run(["rev-parse", "--verify", "HEAD"], cwd=repository_root, check=False)
        branch = self.run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd=repository_root, check=False)
        return ManagedRepository(
            repository_id=_repository_id(repository_root, workspace),
            topology=topology,
            state="ready" if head.returncode == 0 else "unborn",
            head_commit=str(head.stdout or "").strip() or None,
            default_branch=str(branch.stdout or "").strip() or None,
            initialized_by_v8os=(repository_root / ".git" / "v8os-managed").exists(),
        )

    @staticmethod
    def directory_has_user_content(path: Path) -> bool:
        if not path.exists():
            return False
        for child in path.iterdir():
            if child.name in {".agents", ".git", ".v8-agent-os"}:
                continue
            return True
        return False

    def ensure_repository(
        self,
        workspace_root: str | Path,
        *,
        allow_initialize: bool,
    ) -> ManagedRepository:
        workspace = Path(workspace_root).expanduser().resolve(strict=False)
        workspace.mkdir(parents=True, exist_ok=True)
        existing = self.inspect_repository(workspace)
        if existing is not None:
            if allow_initialize and existing.state == "unborn" and not self.directory_has_user_content(workspace):
                return self.adopt_repository(workspace)
            return existing
        if not allow_initialize:
            topology = resolve_workspace_topology(workspace_root=workspace, repository_root=workspace)
            return ManagedRepository(
                repository_id=_repository_id(workspace, workspace),
                topology=topology,
                state="adoption_required",
                head_commit=None,
                default_branch=None,
                initialized_by_v8os=False,
                warnings=("existing_non_git_workspace_requires_explicit_adoption",),
            )
        return self.initialize_repository(workspace)

    def initialize_repository(self, workspace_root: str | Path) -> ManagedRepository:
        workspace = Path(workspace_root).expanduser().resolve(strict=False)
        workspace.mkdir(parents=True, exist_ok=True)
        if self.discover_repository_root(workspace) is not None:
            existing = self.inspect_repository(workspace)
            if existing is None:
                raise ManagedGitError("repository_probe_failed", "The existing repository could not be inspected.")
            return existing
        initialized = self.run(["init", "-b", "main"], cwd=workspace, check=False)
        if initialized.returncode != 0:
            self.run(["init"], cwd=workspace)
            self.run(["symbolic-ref", "HEAD", "refs/heads/main"], cwd=workspace)
        marker_root = workspace / ".git"
        marker_root.mkdir(parents=True, exist_ok=True)
        (marker_root / "v8os-managed").write_text(
            json.dumps({"version": 1, "createdAt": _utc_now_iso()}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return self._create_baseline_commit(workspace)

    def adopt_repository(self, workspace_root: str | Path) -> ManagedRepository:
        workspace = Path(workspace_root).expanduser().resolve(strict=False)
        existing = self.inspect_repository(workspace)
        if existing is None:
            return self.initialize_repository(workspace)
        self.ensure_managed_gitignore(existing.topology.repository_root)
        if existing.state == "ready":
            refreshed = self.inspect_repository(workspace)
            if refreshed is None:
                raise ManagedGitError("repository_probe_failed", "The adopted repository could not be inspected.")
            return refreshed
        if existing.state != "unborn":
            raise ManagedGitError(
                "repository_not_adoptable",
                "The repository is not in a state that V8OS can adopt safely.",
                details=existing.as_dict(),
            )
        return self._create_baseline_commit(Path(existing.topology.repository_root))

    def _create_baseline_commit(self, workspace: Path) -> ManagedRepository:
        self.ensure_managed_gitignore(workspace)
        self._assert_repository_safe(workspace)
        candidate_paths = self._candidate_paths(workspace)
        self._assert_no_escaping_symlinks(workspace, candidate_paths)
        excluded_large_files = self._exclude_untracked_large_files(workspace, candidate_paths)
        self._assert_no_large_files(workspace, self._candidate_paths(workspace))
        self.run(["add", "-A", "--"], cwd=workspace)
        self.run(
            [
                "-c",
                "user.name=V8 Agent OS",
                "-c",
                "user.email=v8os@local.invalid",
                "commit",
                "--no-verify",
                "-m",
                "chore: initialize V8OS workspace",
            ],
            cwd=workspace,
        )
        repository = self.inspect_repository(workspace)
        if repository is None:
            raise ManagedGitError("repository_init_failed", "The initialized repository could not be inspected.")
        if not excluded_large_files:
            return repository
        return ManagedRepository(
            repository_id=repository.repository_id,
            topology=repository.topology,
            state=repository.state,
            head_commit=repository.head_commit,
            default_branch=repository.default_branch,
            initialized_by_v8os=repository.initialized_by_v8os,
            warnings=tuple(
                [
                    *repository.warnings,
                    f"excluded_untracked_files_over_20mib:{len(excluded_large_files)}",
                ]
            ),
        )

    def ensure_managed_gitignore(self, repository_root: str | Path) -> bool:
        root = Path(repository_root).expanduser().resolve(strict=False)
        gitignore = root / ".gitignore"
        current = gitignore.read_text(encoding="utf-8", errors="replace") if gitignore.exists() else ""
        if MANAGED_GITIGNORE_START in current and MANAGED_GITIGNORE_END in current:
            return False
        block = "\n".join((MANAGED_GITIGNORE_START, *MANAGED_GITIGNORE_LINES, MANAGED_GITIGNORE_END))
        prefix = current.rstrip()
        next_value = f"{prefix}\n\n{block}\n" if prefix else f"{block}\n"
        gitignore.write_text(next_value, encoding="utf-8")
        return True

    def _assert_repository_safe(self, repository_root: Path) -> None:
        attributes = repository_root / ".gitattributes"
        if attributes.exists():
            risky_lines = []
            for line_number, raw_line in enumerate(attributes.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if re.search(r"(?:^|\s)(?:filter|merge|diff)(?:=|\s|$)", line, re.IGNORECASE):
                    risky_lines.append(line_number)
            if risky_lines:
                raise ManagedGitError(
                    "repository_custom_driver_requires_review",
                    "Repository attributes declare custom filters or drivers.",
                    details={"path": str(attributes), "lines": risky_lines[:20]},
                )
        tracked = self.run(["ls-files", "-s", "-z"], cwd=repository_root, text=False)
        tracked_symlinks: list[str] = []
        for raw_entry in bytes(tracked.stdout or b"").split(b"\0"):
            if not raw_entry:
                continue
            header, separator, raw_path = raw_entry.partition(b"\t")
            if separator and header.startswith(b"120000 "):
                tracked_symlinks.append(raw_path.decode("utf-8", errors="surrogateescape"))
        self._assert_no_escaping_symlinks(repository_root, tracked_symlinks)

    def _candidate_paths(self, repository_root: Path) -> list[str]:
        result = self.run(
            ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repository_root,
            text=False,
        )
        return [item.decode("utf-8", errors="surrogateescape") for item in bytes(result.stdout or b"").split(b"\0") if item]

    def _assert_no_large_files(self, repository_root: Path, paths: Iterable[str]) -> None:
        oversized: list[dict[str, Any]] = []
        for raw in paths:
            relative = str(raw or "").replace("\\", "/").strip("/")
            if not relative:
                continue
            candidate = (repository_root / Path(relative)).resolve(strict=False)
            try:
                candidate.relative_to(repository_root)
            except ValueError:
                continue
            try:
                size = candidate.stat().st_size if candidate.is_file() else 0
            except OSError:
                continue
            if size > MAX_MANAGED_FILE_BYTES:
                oversized.append({"path": relative, "sizeBytes": size})
        if oversized:
            raise ManagedGitError(
                "managed_git_large_file_blocked",
                "Files larger than 20 MiB cannot enter a V8OS-managed change set.",
                details={"limitBytes": MAX_MANAGED_FILE_BYTES, "files": oversized[:20]},
            )

    @staticmethod
    def _assert_no_escaping_symlinks(repository_root: Path, paths: Iterable[str]) -> None:
        root = repository_root.resolve(strict=False)
        violations: list[dict[str, str]] = []
        for raw in paths:
            relative = str(raw or "").replace("\\", "/").strip("/")
            if not relative:
                continue
            candidate = root / Path(relative)
            try:
                if not candidate.is_symlink():
                    continue
                target = (candidate.parent / os.readlink(candidate)).resolve(strict=False)
                target.relative_to(root)
            except ValueError:
                violations.append({"path": relative, "target": str(target)})
            except OSError:
                violations.append({"path": relative, "target": "unreadable"})
        if violations:
            raise ManagedGitError(
                "repository_symlink_escapes_workspace",
                "A repository symlink resolves outside the managed repository boundary.",
                details={"symlinks": violations[:20]},
            )

    def _exclude_untracked_large_files(
        self,
        repository_root: Path,
        paths: Iterable[str],
    ) -> tuple[str, ...]:
        oversized: list[str] = []
        for raw in paths:
            relative = str(raw or "").replace("\\", "/").strip("/")
            if not relative:
                continue
            candidate = (repository_root / Path(relative)).resolve(strict=False)
            try:
                candidate.relative_to(repository_root)
            except ValueError:
                continue
            try:
                if candidate.is_file() and candidate.stat().st_size > MAX_MANAGED_FILE_BYTES:
                    oversized.append(relative)
            except OSError:
                continue
        if not oversized:
            return ()
        exclude_path = repository_root / ".git" / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        current = exclude_path.read_text(encoding="utf-8", errors="replace") if exclude_path.exists() else ""
        lines = list(current.splitlines())
        marker = "# V8 Agent OS: untracked files over 20 MiB"
        if marker not in lines:
            lines.append(marker)
        for relative in oversized:
            escaped = re.sub(r"([\\ *?\[\]#!])", r"\\\1", relative)
            rule = f"/{escaped}"
            if rule not in lines:
                lines.append(rule)
        exclude_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return tuple(oversized)

    def snapshot_base_commit(
        self,
        repository: ManagedRepository,
        *,
        run_id: str,
        source_repository_root: str | Path | None = None,
    ) -> str:
        root = Path(source_repository_root or repository.topology.repository_root).expanduser().resolve(strict=False)
        if not repository.head_commit:
            raise ManagedGitError("repository_has_no_baseline", "A baseline commit is required before creating worktrees.")
        current_head = str(self.run(["rev-parse", "--verify", "HEAD"], cwd=root).stdout or "").strip()
        if not current_head:
            raise ManagedGitError("repository_has_no_baseline", "A baseline commit is required before creating worktrees.")
        status = self.run(
            ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=root,
            text=False,
        )
        status_bytes = bytes(status.stdout or b"")
        if not status_bytes:
            return current_head
        changed_paths = self._changed_paths_from_status(status_bytes)
        self._assert_no_escaping_symlinks(root, changed_paths)
        self._assert_no_large_files(root, changed_paths)
        index_path = self.index_root / f"{_safe_segment(run_id, fallback='run')}-{os.getpid()}.index"
        index_path.unlink(missing_ok=True)
        environment = {"GIT_INDEX_FILE": str(index_path)}
        try:
            self.run(["read-tree", current_head], cwd=root, env=environment)
            self.run(["add", "-A", "--"], cwd=root, env=environment)
            tree = str(self.run(["write-tree"], cwd=root, env=environment).stdout or "").strip()
            base_date = str(
                self.run(["show", "-s", "--format=%aI", current_head], cwd=root).stdout or ""
            ).strip() or "2000-01-01T00:00:00+00:00"
            commit = str(
                self.run(
                    ["commit-tree", tree, "-p", current_head],
                    cwd=root,
                    env={
                        **environment,
                        "GIT_AUTHOR_NAME": "V8 Agent OS",
                        "GIT_AUTHOR_EMAIL": "v8os@local.invalid",
                        "GIT_COMMITTER_NAME": "V8 Agent OS",
                        "GIT_COMMITTER_EMAIL": "v8os@local.invalid",
                        "GIT_AUTHOR_DATE": base_date,
                        "GIT_COMMITTER_DATE": base_date,
                    },
                    input_text="V8OS managed workspace snapshot\n",
                ).stdout
                or ""
            ).strip()
            if not commit:
                raise ManagedGitError("snapshot_commit_missing", "Git did not return a snapshot commit.")
            return commit
        finally:
            index_path.unlink(missing_ok=True)

    def apply_change_set_to_worktree(
        self,
        *,
        target_repository_root: str | Path,
        repository: ManagedRepository,
        change_set: GitChangeSetRef,
        run_id: str,
    ) -> tuple[str, ...]:
        target_root = Path(target_repository_root).expanduser().resolve(strict=False)
        patch_result = self.run(
            ["diff", "--binary", change_set.base_commit, change_set.commit_id, "--"],
            cwd=target_root,
            text=False,
        )
        patch_bytes = bytes(patch_result.stdout or b"")
        if not patch_bytes:
            return ()
        already_applied = self.run(
            ["apply", "--reverse", "--check", "--whitespace=nowarn", "-"],
            cwd=target_root,
            check=False,
            text=False,
            input_text=patch_bytes,
        )
        if already_applied.returncode == 0:
            return change_set.changed_paths
        current_snapshot = self.snapshot_base_commit(
            repository,
            run_id=f"{run_id}-parent-merge-check",
            source_repository_root=target_root,
        )
        base_tree = str(
            self.run(["rev-parse", f"{change_set.base_commit}^{{tree}}"], cwd=target_root).stdout or ""
        ).strip()
        current_tree = str(
            self.run(["rev-parse", f"{current_snapshot}^{{tree}}"], cwd=target_root).stdout or ""
        ).strip()
        if not base_tree:
            raise ManagedGitError(
                "parent_worktree_base_missing",
                "The child worktree base commit is no longer available.",
            )
        if current_tree != base_tree:
            parent_changed = {
                str(item or "").replace("\\", "/").strip()
                for item in str(
                    self.run(
                        ["diff", "--name-only", change_set.base_commit, current_snapshot, "--"],
                        cwd=target_root,
                    ).stdout
                    or ""
                ).splitlines()
                if str(item or "").strip()
            }
            child_changed = {
                str(item or "").replace("\\", "/").strip()
                for item in change_set.changed_paths
                if str(item or "").strip()
            }
            overlap = sorted(parent_changed.intersection(child_changed))
            if overlap:
                raise ManagedGitError(
                    "parent_worktree_changed_since_child_dispatch",
                    "The parent and child changed the same paths while the child was executing.",
                    details={
                        "baseTree": base_tree,
                        "currentTree": current_tree,
                        "overlappingPaths": overlap,
                    },
                )
        self.run(
            ["apply", "--check", "--whitespace=nowarn", "-"],
            cwd=target_root,
            text=False,
            input_text=patch_bytes,
        )
        self.run(
            ["apply", "--whitespace=nowarn", "-"],
            cwd=target_root,
            text=False,
            input_text=patch_bytes,
        )
        return change_set.changed_paths

    def remove_managed_worktree(
        self,
        repository: ManagedRepository,
        *,
        worktree_root: str | Path,
        branch_name: str,
    ) -> None:
        root = Path(worktree_root).expanduser().resolve(strict=False)
        managed_root = self.worktrees_root.resolve(strict=False)
        try:
            root.relative_to(managed_root)
        except ValueError as exc:
            raise ManagedGitError(
                "worktree_cleanup_path_outside_managed_root",
                "Refusing to remove a worktree outside V8OS-managed storage.",
                details={"path": str(root), "managedRoot": str(managed_root)},
            ) from exc
        repository_root = Path(repository.topology.repository_root)
        if root.exists():
            self.run(["worktree", "remove", "--force", str(root)], cwd=repository_root)
        else:
            self.run(["worktree", "prune"], cwd=repository_root, check=False)
        normalized_branch = str(branch_name or "").strip()
        if normalized_branch.startswith("v8os/run/"):
            self.run(["branch", "-D", normalized_branch], cwd=repository_root, check=False)

    def create_worktree(
        self,
        repository: ManagedRepository,
        *,
        worktree_id: str,
        run_id: str,
        base_commit: str | None = None,
        branch_namespace: str = "task",
    ) -> ManagedWorktree:
        if repository.state not in {"ready"} or not repository.head_commit:
            raise ManagedGitError("repository_not_ready", "The workspace repository is not ready for parallel worktrees.")
        root = Path(repository.topology.repository_root)
        resolved_base = str(base_commit or self.snapshot_base_commit(repository, run_id=run_id)).strip()
        safe_worktree = _safe_segment(worktree_id, fallback="task")
        safe_run = _safe_segment(run_id, fallback="run")
        safe_namespace = _safe_segment(branch_namespace, fallback="task")
        branch_name = f"v8os/run/{safe_run}/{safe_namespace}-{safe_worktree}"
        worktree_root = (self.worktrees_root / repository.repository_id / safe_run / safe_worktree).resolve(strict=False)
        if worktree_root.exists():
            raise ManagedGitError("worktree_path_exists", "The managed worktree path already exists.", details={"path": str(worktree_root)})
        worktree_root.parent.mkdir(parents=True, exist_ok=True)
        self.run(["worktree", "add", "-b", branch_name, str(worktree_root), resolved_base], cwd=root)
        topology = resolve_workspace_topology(
            workspace_root=repository.topology.original_workspace_root,
            repository_root=root,
            worktree_root=worktree_root,
        )
        worktree_workspace = Path(str(topology.worktree_workspace_root))
        if not worktree_workspace.exists():
            worktree_workspace.mkdir(parents=True, exist_ok=True)
        return ManagedWorktree(
            worktree_id=worktree_id,
            repository_id=repository.repository_id,
            branch_name=branch_name,
            base_commit=resolved_base,
            topology=topology,
        )

    def integrate_change_sets(
        self,
        repository: ManagedRepository,
        *,
        run_id: str,
        integration_id: str,
        change_sets: Sequence[GitChangeSetRef],
    ) -> tuple[ManagedWorktree, GitChangeSetRef]:
        candidates = [item for item in change_sets if item.status in {"candidate", "no_changes"}]
        if not candidates:
            raise ManagedGitError("integration_change_sets_required", "No candidate change sets were supplied.")
        repository_ids = {item.repository_id for item in candidates}
        base_commits = {item.base_commit for item in candidates}
        if repository_ids != {repository.repository_id} or len(base_commits) != 1:
            raise ManagedGitError(
                "integration_topology_mismatch",
                "Candidate change sets do not share one repository and base commit.",
            )
        base_commit = next(iter(base_commits))
        integration = self.create_worktree(
            repository,
            worktree_id=integration_id,
            run_id=run_id,
            base_commit=base_commit,
            branch_namespace="integration",
        )
        integration_root = Path(str(integration.topology.worktree_root))
        changed_paths: list[str] = []
        current_candidate: GitChangeSetRef | None = None
        try:
            for current_candidate in candidates:
                if current_candidate.status == "no_changes" or current_candidate.commit_id == current_candidate.base_commit:
                    continue
                self.run(
                    [
                        "-c",
                        "user.name=V8 Agent OS",
                        "-c",
                        "user.email=v8os@local.invalid",
                        "cherry-pick",
                        "--no-edit",
                        current_candidate.commit_id,
                    ],
                    cwd=integration_root,
                )
                for path in current_candidate.changed_paths:
                    if path not in changed_paths:
                        changed_paths.append(path)
        except Exception as exc:
            self.run(["cherry-pick", "--abort"], cwd=integration_root, check=False)
            try:
                self.remove_managed_worktree(
                    repository,
                    worktree_root=integration_root,
                    branch_name=integration.branch_name,
                )
            except Exception:
                # Candidate commits remain intact. The original integration error
                # is the actionable failure and carries the orphan path for audit.
                pass
            raise ManagedGitError(
                "integration_conflict",
                "Managed candidate change sets could not be combined without conflicts.",
                details={
                    "integrationWorktree": str(integration_root),
                    "failedCommit": current_candidate.commit_id if current_candidate else None,
                    "error": str(exc),
                },
            ) from exc
        head_commit = str(self.run(["rev-parse", "HEAD"], cwd=integration_root).stdout or "").strip()
        numstat = str(
            self.run(["diff", "--numstat", base_commit, head_commit, "--"], cwd=integration_root).stdout or ""
        )
        insertions = 0
        deletions = 0
        for line in numstat.splitlines():
            parts = line.split("\t", 2)
            if len(parts) >= 2:
                insertions += int(parts[0]) if parts[0].isdigit() else 0
                deletions += int(parts[1]) if parts[1].isdigit() else 0
        return integration, GitChangeSetRef(
            repository_id=repository.repository_id,
            worktree_id=integration.worktree_id,
            branch_name=integration.branch_name,
            base_commit=base_commit,
            commit_id=head_commit,
            changed_paths=tuple(changed_paths),
            insertions=insertions,
            deletions=deletions,
            status="integration_candidate",
        )

    def apply_integration_to_workspace(
        self,
        repository: ManagedRepository,
        *,
        integration: GitChangeSetRef,
        run_id: str,
    ) -> tuple[str, ...]:
        if integration.repository_id != repository.repository_id:
            raise ManagedGitError("integration_repository_mismatch", "The integration candidate belongs to another repository.")
        repository_root = Path(repository.topology.repository_root)
        patch_result = self.run(
            ["diff", "--binary", integration.base_commit, integration.commit_id, "--"],
            cwd=repository_root,
            text=False,
        )
        patch_bytes = bytes(patch_result.stdout or b"")
        if not patch_bytes:
            return ()
        already_applied = self.run(
            ["apply", "--reverse", "--check", "--whitespace=nowarn", "-"],
            cwd=repository_root,
            check=False,
            text=False,
            input_text=patch_bytes,
        )
        if already_applied.returncode == 0:
            return integration.changed_paths
        current_snapshot = self.snapshot_base_commit(repository, run_id=f"{run_id}-promotion-check")
        base_tree = str(
            self.run(["rev-parse", f"{integration.base_commit}^{{tree}}"], cwd=repository_root).stdout or ""
        ).strip()
        current_tree = str(
            self.run(["rev-parse", f"{current_snapshot}^{{tree}}"], cwd=repository_root).stdout or ""
        ).strip()
        if not base_tree or current_tree != base_tree:
            raise ManagedGitError(
                "workspace_changed_since_dispatch",
                "The original workspace changed after worktree dispatch; automatic promotion was stopped.",
                details={"baseTree": base_tree, "currentTree": current_tree},
            )
        self.run(
            ["apply", "--check", "--whitespace=nowarn", "-"],
            cwd=repository_root,
            text=False,
            input_text=patch_bytes,
        )
        self.run(
            ["apply", "--whitespace=nowarn", "-"],
            cwd=repository_root,
            text=False,
            input_text=patch_bytes,
        )
        return integration.changed_paths

    @staticmethod
    def _changed_paths_from_status(payload: bytes) -> tuple[str, ...]:
        values = payload.split(b"\0")
        paths: list[str] = []
        index = 0
        while index < len(values):
            entry = values[index]
            index += 1
            if not entry:
                continue
            decoded = entry.decode("utf-8", errors="surrogateescape")
            status = decoded[:2]
            path = decoded[3:].replace("\\", "/") if len(decoded) > 3 else ""
            if path and path not in paths:
                paths.append(path)
            if ("R" in status or "C" in status) and index < len(values):
                destination = values[index].decode("utf-8", errors="surrogateescape").replace("\\", "/")
                index += 1
                if destination and destination not in paths:
                    paths.append(destination)
        return tuple(paths)

    @staticmethod
    def _path_allowed(path: str, write_set: Iterable[str]) -> bool:
        normalized = path.replace("\\", "/").lstrip("./")
        for raw_rule in write_set:
            rule = str(raw_rule or "").replace("\\", "/").lstrip("./").rstrip("/")
            if not rule:
                continue
            if any(marker in rule for marker in ("*", "?", "[")):
                if fnmatch.fnmatchcase(normalized, rule):
                    return True
            elif normalized == rule or normalized.startswith(f"{rule}/"):
                return True
        return False

    def finalize_worktree(
        self,
        worktree: ManagedWorktree,
        *,
        write_set: Iterable[str],
        commit_message: str,
    ) -> GitChangeSetRef:
        root = Path(str(worktree.topology.worktree_root))
        status = self.run(["status", "--porcelain=v1", "-z", "--untracked-files=all"], cwd=root, text=False)
        changed_paths = self._changed_paths_from_status(bytes(status.stdout or b""))
        if not changed_paths:
            current_head = str(self.run(["rev-parse", "HEAD"], cwd=root).stdout or "").strip()
            if current_head and current_head != worktree.base_commit:
                changed_paths = tuple(
                    str(item or "").replace("\\", "/").strip()
                    for item in str(
                        self.run(
                            ["diff", "--name-only", worktree.base_commit, current_head, "--"],
                            cwd=root,
                        ).stdout
                        or ""
                    ).splitlines()
                    if str(item or "").strip()
                )
                violations = [path for path in changed_paths if not self._path_allowed(path, write_set)]
                if violations:
                    raise ManagedGitError(
                        "worktree_write_set_violation",
                        "The committed task changed paths outside its approved write set.",
                        details={"violations": violations[:40], "writeSet": list(write_set)},
                    )
                self._assert_no_large_files(root, changed_paths)
                numstat = str(
                    self.run(
                        ["diff", "--numstat", worktree.base_commit, current_head, "--"],
                        cwd=root,
                    ).stdout
                    or ""
                )
                insertions = 0
                deletions = 0
                for line in numstat.splitlines():
                    parts = line.split("\t", 2)
                    if len(parts) >= 2:
                        insertions += int(parts[0]) if parts[0].isdigit() else 0
                        deletions += int(parts[1]) if parts[1].isdigit() else 0
                return GitChangeSetRef(
                    repository_id=worktree.repository_id,
                    worktree_id=worktree.worktree_id,
                    branch_name=worktree.branch_name,
                    base_commit=worktree.base_commit,
                    commit_id=current_head,
                    changed_paths=changed_paths,
                    insertions=insertions,
                    deletions=deletions,
                )
        violations = [path for path in changed_paths if not self._path_allowed(path, write_set)]
        if violations:
            raise ManagedGitError(
                "worktree_write_set_violation",
                "The task changed paths outside its approved write set.",
                details={"violations": violations[:40], "writeSet": list(write_set)},
            )
        self._assert_no_escaping_symlinks(root, changed_paths)
        self._assert_no_large_files(root, changed_paths)
        if not changed_paths:
            return GitChangeSetRef(
                repository_id=worktree.repository_id,
                worktree_id=worktree.worktree_id,
                branch_name=worktree.branch_name,
                base_commit=worktree.base_commit,
                commit_id=worktree.base_commit,
                changed_paths=(),
                status="no_changes",
            )
        self.run(["add", "-A", "--"], cwd=root)
        self.run(
            [
                "-c",
                "user.name=V8 Agent OS",
                "-c",
                "user.email=v8os@local.invalid",
                "commit",
                "--no-verify",
                "-m",
                str(commit_message or "V8OS managed task change"),
            ],
            cwd=root,
        )
        commit_id = str(self.run(["rev-parse", "HEAD"], cwd=root).stdout or "").strip()
        numstat = str(self.run(["show", "--format=", "--numstat", "HEAD"], cwd=root).stdout or "")
        insertions = 0
        deletions = 0
        for line in numstat.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 2:
                continue
            if parts[0].isdigit():
                insertions += int(parts[0])
            if parts[1].isdigit():
                deletions += int(parts[1])
        return GitChangeSetRef(
            repository_id=worktree.repository_id,
            worktree_id=worktree.worktree_id,
            branch_name=worktree.branch_name,
            base_commit=worktree.base_commit,
            commit_id=commit_id,
            changed_paths=changed_paths,
            insertions=insertions,
            deletions=deletions,
        )
