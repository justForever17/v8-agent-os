from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import SandboxContractError


@dataclass(frozen=True)
class WorkspaceTopology:
    """Keep user scope, version scope, and execution scope separate."""

    original_workspace_root: str
    repository_root: str
    workspace_relative_path: str
    worktree_root: str | None = None
    worktree_workspace_root: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "originalWorkspaceRoot": self.original_workspace_root,
            "repositoryRoot": self.repository_root,
            "workspaceRelativePath": self.workspace_relative_path,
            "worktreeRoot": self.worktree_root,
            "worktreeWorkspaceRoot": self.worktree_workspace_root,
        }


def resolve_workspace_topology(
    *,
    workspace_root: str | Path,
    repository_root: str | Path,
    worktree_root: str | Path | None = None,
) -> WorkspaceTopology:
    workspace = Path(workspace_root).expanduser().resolve(strict=False)
    repository = Path(repository_root).expanduser().resolve(strict=False)
    try:
        relative = workspace.relative_to(repository)
    except ValueError as exc:
        raise SandboxContractError("workspace_is_outside_repository") from exc
    relative_value = "." if relative == Path(".") else relative.as_posix()
    resolved_worktree: Path | None = None
    worktree_workspace: Path | None = None
    if worktree_root is not None:
        resolved_worktree = Path(worktree_root).expanduser().resolve(strict=False)
        worktree_workspace = resolved_worktree if relative_value == "." else resolved_worktree / relative
    return WorkspaceTopology(
        original_workspace_root=str(workspace),
        repository_root=str(repository),
        workspace_relative_path=relative_value,
        worktree_root=str(resolved_worktree) if resolved_worktree else None,
        worktree_workspace_root=str(worktree_workspace) if worktree_workspace else None,
    )
