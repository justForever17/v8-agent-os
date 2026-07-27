from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from core.database import DatabaseManager
from core.engineering_sandbox.contracts import (
    SandboxContractError,
    SandboxNetworkProfile,
    SandboxPolicy,
)
from core.engineering_sandbox.git_service import ManagedGitError, ManagedGitService
from core.engineering_sandbox.delegation import (
    EngineeringWorkspaceIsolationError,
    prepare_delegated_engineering_workspace,
)
from core.engineering_sandbox.platform_driver import (
    build_sanitized_environment,
    locate_sandbox_host,
)
from core.engineering_sandbox.service import EngineeringSandboxService
from core.engineering_sandbox.workspace_topology import resolve_workspace_topology
from graph.parallel_support import (
    _delegation_summary_allows_changeset_promotion,
    _fail_managed_branch_workspace,
    _finalize_managed_branch_workspace,
)
from core.delegation_broker import normalize_task_brief, task_brief_query_text
from core.engineering_capsule import bind_engineering_task_workspace, derive_grandchild_engineering_task


def _is_test_worktree(path: Path, managed_roots: list[Path]) -> bool:
    resolved = path.resolve(strict=False)
    return any(resolved.is_relative_to(root.resolve(strict=False)) for root in managed_roots)


@pytest.fixture(autouse=True)
def _cleanup_test_managed_worktrees(tmp_path: Path):
    yield

    managed_roots = [path for path in tmp_path.rglob(".v8os-worktrees") if path.is_dir()]
    if not managed_roots:
        return

    repository_roots = [git_dir.parent for git_dir in tmp_path.rglob(".git") if git_dir.is_dir()]
    for repository_root in repository_roots:
        listed = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repository_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if listed.returncode != 0:
            continue
        for line in listed.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            worktree_root = Path(line.removeprefix("worktree ").strip())
            if not _is_test_worktree(worktree_root, managed_roots):
                continue
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(worktree_root)],
                cwd=str(repository_root),
                capture_output=True,
                text=True,
                check=False,
            )
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=str(repository_root),
            capture_output=True,
            text=True,
            check=False,
        )

    for managed_root in managed_roots:
        shutil.rmtree(managed_root, ignore_errors=True)


def test_managed_git_retries_windows_dll_initialization_failure(monkeypatch, tmp_path: Path) -> None:
    from core.engineering_sandbox import git_service as git_service_module

    calls: list[dict] = []
    responses = [
        subprocess.CompletedProcess(["git"], 0xC0000142, stdout="", stderr=""),
        subprocess.CompletedProcess(["git"], 0, stdout="head\n", stderr=""),
    ]

    def _run(*_args, **kwargs):
        calls.append(dict(kwargs))
        return responses.pop(0)

    delays: list[float] = []
    monkeypatch.setattr(git_service_module.subprocess, "run", _run)
    monkeypatch.setattr(
        git_service_module,
        "_is_windows_dll_init_failure",
        lambda return_code: return_code == 0xC0000142,
    )
    monkeypatch.setattr(git_service_module.time, "sleep", delays.append)
    service = ManagedGitService(home=tmp_path / "v8-home", git_executable="git")

    result = service.run(["rev-parse", "HEAD"], cwd=tmp_path)

    assert result.returncode == 0
    assert result.stdout == "head\n"
    assert len(calls) == 2
    assert delays == [0.05]


def test_workspace_topology_preserves_monorepo_subdirectory(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    workspace = repository / "apps" / "demo"
    worktree = tmp_path / "managed-worktree"
    workspace.mkdir(parents=True)

    topology = resolve_workspace_topology(
        workspace_root=workspace,
        repository_root=repository,
        worktree_root=worktree,
    )

    assert topology.original_workspace_root == str(workspace.resolve())
    assert topology.repository_root == str(repository.resolve())
    assert topology.workspace_relative_path == "apps/demo"
    assert topology.worktree_root == str(worktree.resolve())
    assert topology.worktree_workspace_root == str((worktree / "apps" / "demo").resolve())


def test_failed_agent_branch_closes_managed_worktree_lease(monkeypatch) -> None:
    captured: dict[str, str] = {}

    class FakeService:
        def mark_task_workspace_failed(self, *, worktree_id: str, error_code: str) -> None:
            captured.update(worktree_id=worktree_id, error_code=error_code)

    monkeypatch.setattr(
        "core.engineering_sandbox.service.get_engineering_sandbox_service",
        lambda: FakeService(),
    )

    result = _fail_managed_branch_workspace(
        {
            "engineeringWorkspace": {
                "worktree_id": "worktree-failed",
                "sandbox_lease_id": "lease-failed",
                "sandbox_policy_digest": "digest-failed",
            }
        },
        "Safety intervention: encoded command",
    )

    assert captured == {
        "worktree_id": "worktree-failed",
        "error_code": "safety_intervention_encoded_command",
    }
    assert result["sandboxEvidence"]["state"] == "failed"
    assert result["sandboxEvidence"]["leaseId"] == "lease-failed"


def test_write_task_rejects_managed_worktree_with_no_changed_paths(monkeypatch) -> None:
    from core.engineering_sandbox.contracts import GitChangeSetRef

    class FakeService:
        def finalize_task_workspace(self, *, worktree_id: str, commit_message: str):
            assert worktree_id == "worktree-no-change"
            assert commit_message.startswith("V8OS delegated task:")
            return GitChangeSetRef(
                repository_id="repo-test",
                worktree_id=worktree_id,
                branch_name="v8os/test",
                base_commit="base",
                commit_id="base",
                changed_paths=(),
                status="no_changes",
            )

        def preserve_task_workspace_unmerged(self, *, worktree_id: str, reason: str) -> None:
            assert worktree_id == "worktree-no-change"
            assert reason == "managed_worktree_no_declared_changes"

    monkeypatch.setattr(
        "core.engineering_sandbox.service.get_engineering_sandbox_service",
        lambda: FakeService(),
    )

    result = _finalize_managed_branch_workspace(
        {
            "taskBriefId": "write-required",
            "taskBrief": {
                "taskBriefId": "write-required",
                "writeRequired": True,
                "writeSet": ["src/sandbox_live.py"],
            },
            "engineeringWorkspace": {
                "worktree_id": "worktree-no-change",
                "sandbox_lease_id": "lease-no-change",
                "sandbox_policy_digest": "policy-no-change",
            },
        },
        {"status": "ok", "summary": "claimed success"},
    )

    assert result["status"] == "error"
    assert result["error"] == "managed_worktree_no_declared_changes"
    assert result["gitChangeSet"]["changedPaths"] == []
    assert result["sandboxEvidence"]["state"] == "no_changes"


def test_write_set_violation_quarantines_worker_claim_and_exposes_bounded_repair(monkeypatch) -> None:
    class FakeService:
        def finalize_task_workspace(self, *, worktree_id: str, commit_message: str):
            raise ManagedGitError(
                "worktree_write_set_violation",
                "The task changed paths outside its approved write set.",
                details={
                    "violations": ["baseline/.tmp/probe.json", "baseline/.tmp/probe.pretty.json"],
                    "writeSet": ["baseline/manifest.json"],
                },
            )

    monkeypatch.setattr(
        "core.engineering_sandbox.service.get_engineering_sandbox_service",
        lambda: FakeService(),
    )

    result = _finalize_managed_branch_workspace(
        {
            "taskBriefId": "baseline-implementation",
            "taskBrief": {
                "taskBriefId": "baseline-implementation",
                "writeRequired": True,
                "writeSet": ["baseline/manifest.json"],
            },
            "engineeringWorkspace": {
                "worktree_id": "worktree-write-set-violation",
                "sandbox_lease_id": "lease-write-set-violation",
                "sandbox_policy_digest": "policy-write-set-violation",
            },
        },
        {
            "status": "ok",
            "summary": "All baseline files landed and verification passed.",
            "resultText": "Implementation complete.",
            "artifactRefs": [{"path": "baseline/manifest.json", "kind": "workspace_artifact"}],
        },
    )

    assert result["status"] == "error"
    assert result["error"] == "managed_worktree_finalize_failed:worktree_write_set_violation"
    assert result["workerReportedSummary"].startswith("All baseline files landed")
    assert result["summary"].startswith("Managed worktree rejected")
    assert result["artifactRefsAccepted"] is False
    assert result["artifactRefs"][0]["accepted"] is False
    assert result["artifactRefs"][0]["state"] == "quarantined_unmerged"
    assert result["sandboxEvidence"]["violations"] == [
        "baseline/.tmp/probe.json",
        "baseline/.tmp/probe.pretty.json",
    ]
    assert "route one bounded retry" in result["repairAction"]
    assert "Do not inspect" in result["repairAction"]


def test_failed_managed_worktree_is_preserved_but_never_merged(monkeypatch) -> None:
    from core.engineering_sandbox.contracts import GitChangeSetRef

    merge_calls: list[str] = []

    class FakeService:
        def finalize_task_workspace(self, *, worktree_id: str, commit_message: str):
            assert worktree_id == "worktree-failed-candidate"
            assert commit_message.startswith("V8OS delegated task:")
            return GitChangeSetRef(
                repository_id="repo-test",
                worktree_id=worktree_id,
                branch_name="v8os/failed-candidate",
                base_commit="base",
                commit_id="failed-candidate",
                changed_paths=("src/sandbox_live.py",),
                status="candidate",
            )

        def merge_child_change_set_to_parent(self, *, child_worktree_id: str, run_id: str):
            merge_calls.append(child_worktree_id)
            raise AssertionError("failed delegation must not merge into its parent")

        def preserve_task_workspace_unmerged(self, *, worktree_id: str, reason: str) -> None:
            assert worktree_id == "worktree-failed-candidate"
            assert reason == "required_child_delegation_missing"

    monkeypatch.setattr(
        "core.engineering_sandbox.service.get_engineering_sandbox_service",
        lambda: FakeService(),
    )

    result = _finalize_managed_branch_workspace(
        {
            "taskBriefId": "failed-write",
            "taskBrief": {
                "taskBriefId": "failed-write",
                "writeRequired": True,
                "writeSet": ["src/sandbox_live.py"],
            },
            "engineeringWorkspace": {
                "worktree_id": "worktree-failed-candidate",
                "parent_worktree_id": "parent-worktree",
                "sandbox_lease_id": "lease-failed-candidate",
                "sandbox_policy_digest": "policy-failed-candidate",
            },
        },
        {
            "status": "blocked",
            "error": "required_child_delegation_missing",
            "summary": "The direct subagent omitted its required child delegation.",
        },
    )

    assert merge_calls == []
    assert result["status"] == "blocked"
    assert result["error"] == "required_child_delegation_missing"
    assert result["gitChangeSet"]["changedPaths"] == ["src/sandbox_live.py"]
    assert "parentWorktreeMerge" not in result
    assert result["sandboxEvidence"]["state"] == "preserved_unmerged"
    assert result["sandboxEvidence"]["mergeEligibility"] == "rejected"
    assert result["artifactRefs"][-1]["accepted"] is False


def test_successful_managed_child_changeset_merges_to_parent(monkeypatch) -> None:
    from core.engineering_sandbox.contracts import GitChangeSetRef

    class FakeService:
        def finalize_task_workspace(self, *, worktree_id: str, commit_message: str):
            return GitChangeSetRef(
                repository_id="repo-test",
                worktree_id=worktree_id,
                branch_name="v8os/success-candidate",
                base_commit="base",
                commit_id="success-candidate",
                changed_paths=("src/sandbox_live.py",),
                status="candidate",
            )

        def merge_child_change_set_to_parent(self, *, child_worktree_id: str, run_id: str):
            assert child_worktree_id == "worktree-success-candidate"
            assert run_id == "run-success"
            return {
                "status": "merged_to_parent",
                "parentWorktreeId": "parent-worktree",
                "childWorktreeId": child_worktree_id,
                "changedPaths": ["src/sandbox_live.py"],
            }

    monkeypatch.setattr(
        "core.engineering_sandbox.service.get_engineering_sandbox_service",
        lambda: FakeService(),
    )

    result = _finalize_managed_branch_workspace(
        {
            "taskBriefId": "successful-write",
            "invocationId": "run-success",
            "taskBrief": {
                "taskBriefId": "successful-write",
                "writeRequired": True,
                "writeSet": ["src/sandbox_live.py"],
            },
            "engineeringWorkspace": {
                "worktree_id": "worktree-success-candidate",
                "parent_worktree_id": "parent-worktree",
            },
        },
        {"status": "ok", "summary": "implemented and verified"},
    )

    assert result["status"] == "ok"
    assert result["parentWorktreeMerge"]["status"] == "merged_to_parent"
    assert result["sandboxEvidence"]["state"] == "completed"
    assert result["artifactRefs"][-1]["accepted"] is True


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        ({"status": "ok"}, True),
        ({"status": "completed"}, True),
        ({"status": "degraded", "canContinueParent": True}, True),
        ({"status": "degraded", "canContinueParent": False}, False),
        ({"status": "blocked", "artifactRefs": ["git://candidate"]}, False),
        ({"status": "error", "changedFiles": ["src/a.py"]}, False),
        ({"artifactRefs": ["git://candidate"]}, False),
    ],
)
def test_delegation_changeset_promotion_requires_explicit_success(summary, expected) -> None:
    assert _delegation_summary_allows_changeset_promotion(summary) is expected


def test_aborting_run_closes_only_nonterminal_worktrees_and_leases(tmp_path: Path) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    service = EngineeringSandboxService(
        home=tmp_path / "v8-home",
        git_service=ManagedGitService(home=tmp_path / "v8-home"),
        database=database,
    )
    now = "2026-07-20T00:00:00+00:00"
    with database.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO managed_git_repositories(
                repository_id, original_workspace_root, repository_root,
                workspace_relative_path, state, created_at, updated_at
            ) VALUES ('repo', ?, ?, '.', 'ready', ?, ?)
            """,
            (str(tmp_path), str(tmp_path), now, now),
        )
        for worktree_id, state in (("open", "active"), ("done", "candidate")):
            conn.execute(
                """
                INSERT INTO engineering_worktrees(
                    worktree_id, repository_id, run_id, worktree_kind, branch_name,
                    base_commit, worktree_root, worktree_workspace_root, state,
                    created_at, updated_at
                ) VALUES (?, 'repo', 'run-cancel', 'task', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    worktree_id,
                    f"v8/{worktree_id}",
                    "a" * 40,
                    str(tmp_path / worktree_id),
                    str(tmp_path / worktree_id),
                    state,
                    now,
                    now,
                ),
            )
        for lease_id, worktree_id, state in (
            ("lease-open", "open", "active"),
            ("lease-done", "done", "completed"),
        ):
            conn.execute(
                """
                INSERT INTO sandbox_execution_leases(
                    lease_id, policy_id, policy_digest, write_set_digest,
                    repository_id, worktree_id, run_id, actor_role, runtime_kind,
                    execution_mode, network_profile, state, capabilities_json,
                    policy_json, created_at, updated_at
                ) VALUES (?, ?, 'digest', 'write-digest', 'repo', ?, 'run-cancel',
                          'direct_subagent', 'engineering', 'write',
                          'networked_partial', ?, '{}', '{}', ?, ?)
                """,
                (lease_id, f"policy-{lease_id}", worktree_id, state, now, now),
            )
        conn.commit()

    result = service.abort_run_workspaces(run_id="run-cancel", error_code="run_cancelled")

    assert result["worktreeIds"] == ["open"]
    assert result["leaseIds"] == ["lease-open"]
    with database.get_connection() as conn:
        worktrees = {
            row["worktree_id"]: (row["state"], row["error_code"])
            for row in conn.execute(
                "SELECT worktree_id, state, error_code FROM engineering_worktrees"
            )
        }
        leases = {
            row["lease_id"]: (row["state"], row["error_code"])
            for row in conn.execute(
                "SELECT lease_id, state, error_code FROM sandbox_execution_leases"
            )
        }
    assert worktrees["open"] == ("cancelled", "run_cancelled")
    assert worktrees["done"] == ("candidate", None)
    assert leases["lease-open"] == ("failed", "run_cancelled")
    assert leases["lease-done"] == ("completed", None)


def test_one_monorepo_keeps_distinct_workspace_binding_identities(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    repository_root = tmp_path / "repo"
    first_workspace = repository_root / "apps" / "first"
    second_workspace = repository_root / "apps" / "second"
    first_workspace.mkdir(parents=True)
    second_workspace.mkdir(parents=True)
    (first_workspace / "README.md").write_text("first\n", encoding="utf-8")
    (second_workspace / "README.md").write_text("second\n", encoding="utf-8")
    service.initialize_repository(repository_root)

    first = service.inspect_repository(first_workspace)
    second = service.inspect_repository(second_workspace)

    assert first is not None and second is not None
    assert first.repository_id != second.repository_id
    assert first.topology.repository_root == second.topology.repository_root
    assert first.topology.workspace_relative_path == "apps/first"
    assert second.topology.workspace_relative_path == "apps/second"


def test_parallel_branch_uses_its_own_worktree_instead_of_parent_context(monkeypatch) -> None:
    from graph import parallel_support

    captured: dict = {}

    class _Binding:
        def as_dict(self):
            return {"activeWorkspaceRoot": captured["workspace_path"]}

    def _binding(context, *, runtime_kind):
        captured.update(context)
        captured["runtime_kind_argument"] = runtime_kind
        return _Binding()

    monkeypatch.setattr(parallel_support, "build_workspace_binding", _binding)
    managed = {
        "workspace_path": "C:/managed/child",
        "original_workspace_path": "C:/projects/app",
        "worktree_id": "child-worktree",
        "sandbox_policy": {"lease_id": "lease"},
        "sandbox_policy_digest": "digest",
        "managed_engineering_execution": True,
    }
    context = parallel_support._runtime_context_from_parallel_state(
        {
            "workspace_path": "C:/managed/parent",
            "original_workspace_path": "C:/projects/app",
            "run_id": "run",
            "current_route_context": {},
        },
        branch={
            "delegationDepth": 1,
            "engineeringWorkspace": managed,
            "taskBrief": {},
        },
    )

    assert context["workspace_path"] == "C:/managed/child"
    assert context["original_workspace_path"] == "C:/projects/app"
    assert context["worktree_id"] == "child-worktree"
    assert context["managed_engineering_execution"] is True
    assert context["actor_role"] == "direct_subagent"
    assert captured["runtime_kind_argument"] == "subagent"


def test_write_policy_requires_explicit_relative_write_set(tmp_path: Path) -> None:
    with pytest.raises(SandboxContractError, match="write_set_required"):
        SandboxPolicy(
            policy_id="policy",
            lease_id="lease",
            repository_id="repo",
            worktree_id="worktree",
            worktree_root=str(tmp_path),
            original_workspace_root=str(tmp_path),
            base_commit="a" * 40,
            execution_mode="write",
            actor_role="direct_subagent",
            runtime_kind="engineering",
        )

    with pytest.raises(SandboxContractError, match="must_be_relative"):
        SandboxPolicy(
            policy_id="policy",
            lease_id="lease",
            repository_id="repo",
            worktree_id="worktree",
            worktree_root=str(tmp_path),
            original_workspace_root=str(tmp_path),
            base_commit="a" * 40,
            execution_mode="write",
            actor_role="direct_subagent",
            runtime_kind="engineering",
            write_set=("../outside.txt",),
        )


def test_sanitized_environment_does_not_inherit_secrets(tmp_path: Path) -> None:
    policy = SandboxPolicy(
        policy_id="policy",
        lease_id="lease",
        repository_id="repo",
        worktree_id="worktree",
        worktree_root=str(tmp_path),
        original_workspace_root=str(tmp_path),
        base_commit="a" * 40,
        execution_mode="read",
        actor_role="supervisor",
        runtime_kind="engineering",
    )

    environment = build_sanitized_environment(
        policy,
        source={"PATH": "bin", "HOME": "home", "OPENAI_API_KEY": "secret", "TOKEN": "secret"},
    )

    assert environment["PATH"] == "bin"
    assert environment["HOME"] == "home"
    assert "OPENAI_API_KEY" not in environment
    assert "TOKEN" not in environment
    assert environment["V8_SANDBOX_POLICY_DIGEST"] == policy.digest


def test_engineering_command_fails_closed_without_a_lease() -> None:
    from core.tools.native.command import _sandbox_launch

    with pytest.raises(RuntimeError, match="sandbox_lease_required_for_engineering_command"):
        _sandbox_launch({"runtime_kind": "engineering"}, [sys.executable, "--version"])

    argv, environment = _sandbox_launch(
        {"runtime_kind": "chat", "managed_engineering_execution": False},
        [sys.executable, "--version"],
    )
    assert argv == [sys.executable, "--version"]
    assert environment is None

    direct_subagent_argv, direct_subagent_environment = _sandbox_launch(
        {
            "runtime_kind": "subagent",
            "engineering_capsule_mode": "write",
            "managed_engineering_execution": False,
        },
        [sys.executable, "--version"],
    )
    assert direct_subagent_argv == [sys.executable, "--version"]
    assert direct_subagent_environment is None


def _git_service(tmp_path: Path) -> ManagedGitService:
    return ManagedGitService(home=tmp_path / "v8-home")


def test_git_init_creates_managed_baseline_and_blocks_large_files(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")

    repository = service.ensure_repository(workspace, allow_initialize=True)

    assert repository.state == "ready"
    assert repository.initialized_by_v8os is True
    assert repository.head_commit
    assert "V8 Agent OS managed ignores" in (workspace / ".gitignore").read_text(encoding="utf-8")

    oversized = workspace / "oversized.bin"
    with oversized.open("wb") as handle:
        handle.truncate(20 * 1024 * 1024 + 1)
    with pytest.raises(ManagedGitError) as error:
        service.snapshot_base_commit(service.inspect_repository(workspace), run_id="large-file")  # type: ignore[arg-type]
    assert error.value.code == "managed_git_large_file_blocked"


def test_non_git_workspace_keeps_direct_engineering_available_without_initializing_git(tmp_path: Path) -> None:
    service = EngineeringSandboxService(
        home=tmp_path / "v8-home",
        git_service=_git_service(tmp_path),
        database=DatabaseManager(tmp_path / "state.db"),
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    status = service.project_repository_status(workspace_root=workspace, project_id="demo")

    assert status["repository"]["state"] == "adoption_required"
    assert status["parallelIsolation"] == {
        "optional": True,
        "enabled": False,
        "setupRequired": True,
        "directExecutionAvailable": True,
        "setupEffects": [
            "create_git_repository_if_missing",
            "create_v8os_baseline_commit",
        ],
    }
    assert not (workspace / ".git").exists()


def test_worktrees_default_to_hidden_root_on_repository_volume(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)

    worktree = service.create_worktree(repository, worktree_id="task", run_id="run")
    worktree_root = Path(str(worktree.topology.worktree_root))

    assert worktree_root.is_relative_to(tmp_path / ".v8os-worktrees")
    assert not worktree_root.is_relative_to(workspace)


def test_custom_worktree_root_must_share_repository_volume(monkeypatch, tmp_path: Path) -> None:
    from core.engineering_sandbox import git_service as git_service_module

    custom_root = tmp_path / "custom-worktrees"
    service = ManagedGitService(
        home=tmp_path / "v8-home",
        worktree_placement="custom",
        worktrees_root=custom_root,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)
    monkeypatch.setattr(git_service_module, "_same_storage_volume", lambda _left, _right: False)

    with pytest.raises(ManagedGitError) as error:
        service.create_worktree(repository, worktree_id="task", run_id="run")

    assert error.value.code == "managed_worktree_root_cross_volume"


def test_explicit_adoption_completes_an_unborn_repository(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service.run(["init", "-b", "main"], cwd=workspace)

    before = service.ensure_repository(workspace, allow_initialize=False)
    adopted = service.adopt_repository(workspace)

    assert before.state == "unborn"
    assert adopted.state == "ready"
    assert adopted.head_commit
    assert "V8 Agent OS managed ignores" in (workspace / ".gitignore").read_text(encoding="utf-8")


def test_repository_adoption_blocks_symlinks_that_escape_the_boundary(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside.txt"
    workspace.mkdir()
    outside.write_text("private\n", encoding="utf-8")
    link = workspace / "outside-link.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")

    with pytest.raises(ManagedGitError) as error:
        service.initialize_repository(workspace)

    assert error.value.code == "repository_symlink_escapes_workspace"


def test_dirty_snapshot_does_not_move_user_head_or_index(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("base\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)
    original_head = repository.head_commit
    (workspace / "file.txt").write_text("local change\n", encoding="utf-8")

    snapshot = service.snapshot_base_commit(repository, run_id="dirty")
    repeated_snapshot = service.snapshot_base_commit(repository, run_id="dirty-repeat")

    current_head = service.run(["rev-parse", "HEAD"], cwd=workspace).stdout.strip()
    status = service.run(["status", "--porcelain"], cwd=workspace).stdout
    assert snapshot != original_head
    assert repeated_snapshot == snapshot
    assert current_head == original_head
    assert "file.txt" in status


def test_worktree_finalization_enforces_workspace_write_set(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "allowed.txt").write_text("base\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)
    worktree = service.create_worktree(repository, worktree_id="task-1", run_id="run-1")
    worktree_root = Path(str(worktree.topology.worktree_root))
    (worktree_root / "outside.txt").write_text("not allowed\n", encoding="utf-8")

    with pytest.raises(ManagedGitError) as error:
        service.finalize_worktree(worktree, write_set=("allowed.txt",), commit_message="test")

    assert error.value.code == "worktree_write_set_violation"
    assert error.value.details["violations"] == ["outside.txt"]


def test_parallel_change_sets_merge_in_integration_worktree_then_promote(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)
    base = service.snapshot_base_commit(repository, run_id="run")
    first = service.create_worktree(repository, worktree_id="one", run_id="run", base_commit=base)
    second = service.create_worktree(repository, worktree_id="two", run_id="run", base_commit=base)
    Path(str(first.topology.worktree_root), "one.txt").write_text("one\n", encoding="utf-8")
    Path(str(second.topology.worktree_root), "two.txt").write_text("two\n", encoding="utf-8")
    first_change = service.finalize_worktree(first, write_set=("one.txt",), commit_message="one")
    second_change = service.finalize_worktree(second, write_set=("two.txt",), commit_message="two")

    integration, combined = service.integrate_change_sets(
        repository,
        run_id="run",
        integration_id="integration",
        change_sets=(first_change, second_change),
    )

    assert not (workspace / "one.txt").exists()
    assert Path(str(integration.topology.worktree_root), "one.txt").read_text(encoding="utf-8") == "one\n"
    assert Path(str(integration.topology.worktree_root), "two.txt").read_text(encoding="utf-8") == "two\n"
    promoted = service.apply_integration_to_workspace(repository, integration=combined, run_id="run")
    assert set(promoted) == {"one.txt", "two.txt"}
    assert (workspace / "one.txt").read_text(encoding="utf-8") == "one\n"
    assert (workspace / "two.txt").read_text(encoding="utf-8") == "two\n"


def test_dependency_changes_become_downstream_baseline_not_downstream_output(tmp_path: Path) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    git = _git_service(tmp_path)
    service = EngineeringSandboxService(home=tmp_path / "v8-home", git_service=git, database=database)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    service.ensure_project_repository(
        workspace_root=workspace,
        project_id="dependency-demo",
        allow_initialize=True,
    )
    upstream = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="dependency-demo",
        session_id="session",
        run_id="run",
        delegation_id="delegation-upstream",
        worktree_id="task-upstream",
        write_set=("upstream.txt",),
        actor_role="direct_subagent",
        runtime_kind="engineering",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    downstream = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="dependency-demo",
        session_id="session",
        run_id="run",
        delegation_id="delegation-downstream",
        worktree_id="task-downstream",
        write_set=("downstream.txt",),
        actor_role="direct_subagent",
        runtime_kind="engineering",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    Path(upstream.execution_workspace_root, "upstream.txt").write_text("accepted upstream\n", encoding="utf-8")
    upstream_change = service.finalize_task_workspace(
        worktree_id=upstream.worktree.worktree_id,
        commit_message="test: upstream",
    )

    materialized, dependency_baseline = service.materialize_task_dependencies(
        worktree_id=downstream.worktree.worktree_id,
        run_id="run",
        change_sets=(upstream_change.as_dict(),),
    )

    assert Path(materialized.execution_workspace_root, "upstream.txt").read_text(encoding="utf-8") == "accepted upstream\n"
    assert dependency_baseline.changed_paths == ("upstream.txt",)
    assert materialized.worktree.base_commit == materialized.lease.policy.base_commit
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT base_commit FROM engineering_worktrees WHERE worktree_id = ?",
            (downstream.worktree.worktree_id,),
        ).fetchone()
    assert row is not None
    assert str(row["base_commit"]) == materialized.worktree.base_commit

    Path(materialized.execution_workspace_root, "downstream.txt").write_text("downstream only\n", encoding="utf-8")
    downstream_change = service.finalize_task_workspace(
        worktree_id=downstream.worktree.worktree_id,
        commit_message="test: downstream",
    )

    assert downstream_change.changed_paths == ("downstream.txt",)
    service.build_run_integration(
        run_id="run",
        invocation_id="dependent-delivery",
        change_sets=(upstream_change.as_dict(), downstream_change.as_dict()),
    )
    service.promote_run_integration(run_id="run")
    assert (workspace / "upstream.txt").read_text(encoding="utf-8") == "accepted upstream\n"
    assert (workspace / "downstream.txt").read_text(encoding="utf-8") == "downstream only\n"


def test_supervisor_acceptance_preserves_refs_and_removes_physical_worktrees(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    git = _git_service(tmp_path)
    service = EngineeringSandboxService(home=tmp_path / "v8-home", git_service=git, database=database)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    database.create_or_update_session("session", "Managed delivery", user_id="user")
    database.create_run_record("run", "session", user_id="user", run_type="chat")
    service.ensure_project_repository(workspace_root=workspace, project_id="demo", allow_initialize=True)
    prepared = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="demo",
        session_id="session",
        run_id="run",
        delegation_id="delegation",
        worktree_id="task",
        write_set=("result.txt",),
        actor_role="direct_subagent",
        runtime_kind="engineering",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    task_root = Path(str(prepared.worktree.topology.worktree_root))
    candidate_file = Path(prepared.execution_workspace_root, "result.txt")
    candidate_file.write_text("done\n", encoding="utf-8")
    database.add_runtime_artifact(
        "art-managed-result",
        "document",
        "text/plain",
        session_id="session",
        run_id="run",
        title="result.txt",
        source_path=str(candidate_file),
        workspace_path="result.txt",
        preview_url="/v1/artifacts/art-managed-result/content",
        metadata={
            "origin": "agent_file_write",
            "storageClass": "workspace",
            "pathPlane": "workspace_artifact",
            "workspaceRelativePath": "result.txt",
            "managedExecution": True,
            "deliveryState": "candidate",
        },
    )
    change_set = service.finalize_task_workspace(worktree_id="task", commit_message="test: result")
    integration, _combined = service.build_run_integration(
        run_id="run",
        invocation_id="supervisor",
        change_sets=(change_set.as_dict(),),
    )
    integration_root = Path(str(integration.topology.worktree_root))

    original_rebind = service._rebind_delivered_artifacts  # pylint: disable=protected-access
    monkeypatch.setattr(
        service,
        "_rebind_delivered_artifacts",
        lambda **_kwargs: {
            "ok": False,
            "status": "blocked",
            "errorCode": "artifact_rebind_failed",
        },
    )
    deferred = service.promote_run_integration(run_id="run")

    assert deferred["status"] == "delivered"
    assert deferred["cleanup"]["deferred"] is True
    assert task_root.exists()
    assert integration_root.exists()

    monkeypatch.setattr(service, "_rebind_delivered_artifacts", original_rebind)
    delivered = service.promote_run_integration(run_id="run")

    assert delivered["status"] == "delivered"
    assert delivered["idempotent"] is True
    assert delivered["cleanup"]["removed"] == 2
    assert delivered["artifactRebind"]["rebound"] == 1
    assert len(delivered["cleanup"]["preservedRefs"]) == 2
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "done\n"
    assert not task_root.exists()
    assert not integration_root.exists()
    rebound_artifact = database.get_runtime_artifact("art-managed-result")
    assert rebound_artifact is not None
    assert rebound_artifact["sourcePath"] == str(workspace / "result.txt")
    assert rebound_artifact["workspacePath"] == "result.txt"
    assert rebound_artifact["metadata"]["deliveryState"] == "delivered"
    assert Path(str(rebound_artifact["sourcePath"])).read_text(encoding="utf-8") == "done\n"
    for recovery_ref in delivered["cleanup"]["preservedRefs"]:
        git.run(["show-ref", "--verify", recovery_ref], cwd=workspace)
    with database.get_connection() as conn:
        states = {
            str(row["worktree_id"]): str(row["state"])
            for row in conn.execute(
                "SELECT worktree_id, state FROM engineering_worktrees WHERE run_id = 'run'"
            ).fetchall()
        }
    assert states["task"] == "cleaned"
    assert states[integration.worktree_id] == "cleaned"

    replay = service.promote_run_integration(run_id="run")

    assert replay["status"] == "delivered"
    assert replay["idempotent"] is True
    assert replay["recoveryRef"]

    direct = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="demo",
        session_id="session",
        run_id="direct-run",
        delegation_id=None,
        worktree_id="direct-task",
        write_set=("direct.txt",),
        actor_role="supervisor",
        runtime_kind="engineering",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    direct_root = Path(str(direct.worktree.topology.worktree_root))
    Path(direct.execution_workspace_root, "direct.txt").write_text("accepted\n", encoding="utf-8")
    service.finalize_task_workspace(worktree_id="direct-task", commit_message="test: direct")
    service._update_worktree_state("direct-task", "delivered")  # pylint: disable=protected-access

    direct_cleanup = service.cleanup_accepted_worktrees()

    assert direct_cleanup["removed"] == 1
    assert not direct_root.exists()


def test_grandchild_snapshots_parent_worktree_and_merges_back(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)
    parent = service.create_worktree(repository, worktree_id="parent", run_id="run")
    parent_root = Path(str(parent.topology.worktree_root))
    (parent_root / "parent.txt").write_text("parent pending\n", encoding="utf-8")
    child_base = service.snapshot_base_commit(
        repository,
        run_id="child",
        source_repository_root=parent_root,
    )
    child = service.create_worktree(
        repository,
        worktree_id="child",
        run_id="run",
        base_commit=child_base,
    )
    child_root = Path(str(child.topology.worktree_root))
    assert (child_root / "parent.txt").read_text(encoding="utf-8") == "parent pending\n"
    (child_root / "child.txt").write_text("child result\n", encoding="utf-8")
    child_change = service.finalize_worktree(child, write_set=("child.txt",), commit_message="child")

    merged = service.apply_change_set_to_worktree(
        target_repository_root=parent_root,
        repository=repository,
        change_set=child_change,
        run_id="run",
    )

    assert merged == ("child.txt",)
    assert (parent_root / "parent.txt").read_text(encoding="utf-8") == "parent pending\n"
    assert (parent_root / "child.txt").read_text(encoding="utf-8") == "child result\n"

    replayed = service.apply_change_set_to_worktree(
        target_repository_root=parent_root,
        repository=repository,
        change_set=child_change,
        run_id="run-replay",
    )
    assert replayed == ("child.txt",)


def test_disjoint_sibling_change_sets_merge_but_overlapping_paths_stop(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)
    parent = service.create_worktree(repository, worktree_id="parent", run_id="run")
    parent_root = Path(str(parent.topology.worktree_root))
    child_base = service.snapshot_base_commit(
        repository,
        run_id="children",
        source_repository_root=parent_root,
    )
    first = service.create_worktree(
        repository,
        worktree_id="first-child",
        run_id="run",
        base_commit=child_base,
    )
    second = service.create_worktree(
        repository,
        worktree_id="second-child",
        run_id="run",
        base_commit=child_base,
    )
    overlap = service.create_worktree(
        repository,
        worktree_id="overlap-child",
        run_id="run",
        base_commit=child_base,
    )
    Path(str(first.topology.worktree_root), "first.txt").write_text("first\n", encoding="utf-8")
    Path(str(second.topology.worktree_root), "second.txt").write_text("second\n", encoding="utf-8")
    Path(str(overlap.topology.worktree_root), "first.txt").write_text("conflict\n", encoding="utf-8")
    first_change = service.finalize_worktree(first, write_set=("first.txt",), commit_message="first")
    second_change = service.finalize_worktree(second, write_set=("second.txt",), commit_message="second")
    overlap_change = service.finalize_worktree(overlap, write_set=("first.txt",), commit_message="overlap")

    service.apply_change_set_to_worktree(
        target_repository_root=parent_root,
        repository=repository,
        change_set=first_change,
        run_id="run",
    )
    service.apply_change_set_to_worktree(
        target_repository_root=parent_root,
        repository=repository,
        change_set=second_change,
        run_id="run",
    )
    with pytest.raises(ManagedGitError) as error:
        service.apply_change_set_to_worktree(
            target_repository_root=parent_root,
            repository=repository,
            change_set=overlap_change,
            run_id="run",
        )

    assert (parent_root / "first.txt").read_text(encoding="utf-8") == "first\n"
    assert (parent_root / "second.txt").read_text(encoding="utf-8") == "second\n"
    assert error.value.code == "parent_worktree_changed_since_child_dispatch"


def test_candidate_finalization_and_delivery_are_crash_replay_safe(tmp_path: Path) -> None:
    service = _git_service(tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("base\n", encoding="utf-8")
    repository = service.ensure_repository(workspace, allow_initialize=True)
    worktree = service.create_worktree(repository, worktree_id="task", run_id="run")
    Path(str(worktree.topology.worktree_root), "result.txt").write_text("done\n", encoding="utf-8")

    first = service.finalize_worktree(worktree, write_set=("result.txt",), commit_message="task")
    replayed = service.finalize_worktree(worktree, write_set=("result.txt",), commit_message="task replay")
    integration, combined = service.integrate_change_sets(
        repository,
        run_id="run",
        integration_id="integration",
        change_sets=(first,),
    )
    delivered = service.apply_integration_to_workspace(repository, integration=combined, run_id="run")
    delivered_replay = service.apply_integration_to_workspace(
        repository,
        integration=combined,
        run_id="run-replay",
    )

    assert replayed.commit_id == first.commit_id
    assert replayed.changed_paths == first.changed_paths
    assert Path(str(integration.topology.worktree_root), "result.txt").is_file()
    assert delivered == ("result.txt",)
    assert delivered_replay == ("result.txt",)


@pytest.mark.skipif(locate_sandbox_host() is None, reason="native sandbox host has not been built")
def test_service_runs_command_in_managed_worktree_and_returns_change_set(tmp_path: Path) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    git = ManagedGitService(home=tmp_path / "v8-home")
    service = EngineeringSandboxService(home=tmp_path / "v8-home", git_service=git, database=database)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("baseline\n", encoding="utf-8")
    (workspace / "spawn_detached.py").write_text(
        """from pathlib import Path
import subprocess
import sys
subprocess.Popen([
    sys.executable,
    "-c",
    "import time; from pathlib import Path; time.sleep(1.2); Path('orphan-marker.txt').write_text('leaked', encoding='utf-8')",
])
""",
        encoding="utf-8",
    )
    service.ensure_project_repository(workspace_root=workspace, project_id="demo", allow_initialize=True)

    prepared = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="demo",
        session_id="session",
        run_id="run",
        delegation_id="delegation",
        worktree_id="task",
        write_set=("result.txt",),
        actor_role="direct_subagent",
        runtime_kind="engineering",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    resumed = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="demo",
        session_id="session",
        run_id="run",
        delegation_id="delegation",
        worktree_id="task",
        write_set=("result.txt",),
        actor_role="direct_subagent",
        runtime_kind="engineering",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    assert resumed.lease.lease_id == prepared.lease.lease_id
    with pytest.raises(ManagedGitError) as mismatch:
        service.prepare_task_workspace(
            workspace_root=workspace,
            project_id="demo",
            session_id="session",
            run_id="run",
            delegation_id="delegation",
            worktree_id="task",
            write_set=("other.txt",),
            actor_role="direct_subagent",
            runtime_kind="engineering",
            network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
        )
    assert mismatch.value.code == "managed_worktree_resume_contract_mismatch"
    detached_argv, detached_environment = service.wrap_runtime_command(
        prepared.runtime_context(),
        [sys.executable, "spawn_detached.py"],
    )
    detached = subprocess.run(
        detached_argv,
        cwd=prepared.execution_workspace_root,
        env=detached_environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert detached.returncode == 0, detached.stderr
    time.sleep(1.8)
    assert not Path(prepared.execution_workspace_root, "orphan-marker.txt").exists()
    wrapped, environment = service.wrap_runtime_command(
        prepared.runtime_context(),
        [sys.executable, "-c", "from pathlib import Path; Path('result.txt').write_text('ok', encoding='utf-8')"],
    )
    result = subprocess.run(
        wrapped,
        cwd=prepared.execution_workspace_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    change_set = service.finalize_task_workspace(worktree_id="task", commit_message="test: managed change")
    assert change_set.status == "candidate"
    assert change_set.changed_paths == ("result.txt",)
    assert change_set.commit_id != change_set.base_commit

    verification = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="demo",
        session_id="session",
        run_id="verify-run",
        delegation_id="verify-delegation",
        worktree_id="verify-task",
        write_set=(),
        actor_role="direct_subagent",
        runtime_kind="engineering",
        execution_mode="read",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    verify_argv, verify_environment = service.wrap_runtime_command(
        verification.runtime_context(),
        [sys.executable, "-c", "from pathlib import Path; Path('unexpected.txt').write_text('bad')"],
    )
    verify_result = subprocess.run(
        verify_argv,
        cwd=verification.execution_workspace_root,
        env=verify_environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert verify_result.returncode == 0
    with pytest.raises(ManagedGitError) as verification_error:
        service.finalize_task_workspace(worktree_id="verify-task", commit_message="verify")
    assert verification_error.value.code == "worktree_write_set_violation"
    assert not (workspace / "unexpected.txt").exists()

    failed = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="demo",
        session_id="session",
        run_id="cleanup-run",
        delegation_id="cleanup-delegation",
        worktree_id="failed-task",
        write_set=("failed.txt",),
        actor_role="direct_subagent",
        runtime_kind="engineering",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    failed_root = Path(failed.worktree.topology.worktree_root or "")
    failed_policy = Path(failed.policy_file)
    service.mark_task_workspace_failed(worktree_id="failed-task", error_code="test_failure")
    with database.get_connection() as conn:
        conn.execute(
            "UPDATE engineering_worktrees SET state = 'cancelled', updated_at = '2000-01-01T00:00:00+00:00' WHERE worktree_id = 'failed-task'"
        )
        conn.commit()
    cleanup = service.cleanup_terminal_worktrees(older_than_days=1)

    assert cleanup["removed"] == 1
    assert cleanup["removedPolicyFiles"] == 1
    assert cleanup["discarded"] == 1
    assert cleanup["preservedRefs"] == []
    assert not failed_root.exists()
    assert not failed_policy.exists()

    abandoned_roots: dict[str, Path] = {}
    for worktree_id, state, updated_at in (
        ("recoverable-old", "recoverable", "2000-01-01T00:00:00+00:00"),
        ("candidate-old", "integration_candidate", "2000-01-01T00:00:00+00:00"),
        ("recoverable-fresh", "recoverable", "2999-01-01T00:00:00+00:00"),
    ):
        abandoned = service.prepare_task_workspace(
            workspace_root=workspace,
            project_id="demo",
            session_id="session",
            run_id=f"{worktree_id}-run",
            delegation_id=f"{worktree_id}-delegation",
            worktree_id=worktree_id,
            write_set=(f"{worktree_id}.txt",),
            actor_role="direct_subagent",
            runtime_kind="engineering",
            network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
        )
        abandoned_roots[worktree_id] = Path(abandoned.worktree.topology.worktree_root or "")
        with database.get_connection() as conn:
            conn.execute(
                "UPDATE engineering_worktrees SET state = ?, updated_at = ? WHERE worktree_id = ?",
                (state, updated_at, worktree_id),
            )
            conn.execute(
                "UPDATE sandbox_execution_leases SET state = 'expired' WHERE worktree_id = ?",
                (worktree_id,),
            )
            conn.commit()

    abandoned_cleanup = service.cleanup_terminal_worktrees(
        older_than_days=1,
        abandoned_after_days=7,
    )

    assert abandoned_cleanup["removed"] == 2
    assert abandoned_cleanup["discarded"] == 2
    assert abandoned_cleanup["preservedRefs"] == []
    assert not abandoned_roots["recoverable-old"].exists()
    assert not abandoned_roots["candidate-old"].exists()
    assert abandoned_roots["recoverable-fresh"].exists()


def test_top_level_read_only_inspection_keeps_original_workspace(monkeypatch, tmp_path: Path) -> None:
    allocation_attempted = False

    def _unexpected_service():
        nonlocal allocation_attempted
        allocation_attempted = True
        raise AssertionError("read-only inspection must not allocate a managed worktree")

    monkeypatch.setattr(
        "core.engineering_sandbox.delegation.get_engineering_sandbox_service",
        _unexpected_service,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_brief = normalize_task_brief(
        {
            "taskBriefId": "inspect-original",
            "goal": "Compare README.md with package.json and return line evidence.",
            "context": {"workspacePath": str(workspace)},
            "readOnly": True,
            "readSet": ["README.md", "package.json"],
            "verificationContract": ["Return cited evidence without modifying files."],
            "expectedOutputs": ["README.md and package.json evidence"],
            "acceptanceContract": "Return a conclusion with line references.",
        }
    )

    prepared = prepare_delegated_engineering_workspace(
        base_state={
            "run_id": "run-read-only",
            "session_id": "session-read-only",
            "project_id": "demo",
            "workspace_path": str(workspace),
        },
        task_brief=task_brief,
        delegation_id="delegation-read-only",
        current_depth=0,
        runtime_context={"run_id": "run-read-only", "workspace_path": str(workspace)},
    )

    assert prepared is None
    assert allocation_attempted is False


def test_serial_low_risk_write_projects_direct_strategy_without_git_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    allocation_attempted = False

    def _unexpected_service():
        nonlocal allocation_attempted
        allocation_attempted = True
        raise AssertionError("serial low-risk writes must not probe or initialize Git")

    monkeypatch.setattr(
        "core.engineering_sandbox.delegation.get_engineering_sandbox_service",
        _unexpected_service,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_brief = normalize_task_brief(
        {
            "taskBriefId": "direct-write",
            "goal": "Write one bounded local HTML file.",
            "context": {"workspacePath": str(workspace)},
            "writeRequired": True,
            "writeSet": ["index.html"],
            "expectedOutputs": ["index.html"],
            "acceptanceContract": "index.html contains the requested heading.",
        }
    )

    prepared = prepare_delegated_engineering_workspace(
        base_state={
            "run_id": "run-direct",
            "session_id": "session-direct",
            "project_id": "project-direct",
            "workspace_path": str(workspace),
        },
        task_brief=task_brief,
        delegation_id="delegation-direct",
        current_depth=0,
        runtime_context={"run_id": "run-direct", "workspace_path": str(workspace)},
    )

    assert prepared is not None
    assert prepared["engineering_workspace_strategy"] == "direct"
    assert prepared["managed_engineering_execution"] is False
    assert Path(prepared["workspace_path"]) == workspace.resolve()
    assert allocation_attempted is False

    bound = bind_engineering_task_workspace(
        task_brief,
        workspace_path=prepared["workspace_path"],
        original_workspace_path=prepared["original_workspace_path"],
        workspace_strategy=prepared["engineering_workspace_strategy"],
    )
    query = task_brief_query_text(bound)
    assert '"mode": "direct"' in query
    assert '"mutationBoundary": "native_file_tools_with_capsule_write_set"' in query
    assert '"shellBoundary": "read_and_validation_commands_only"' in query


def test_grandchild_verification_reuses_parent_snapshot_without_allocating_another_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    allocation_attempted = False

    def _unexpected_service():
        nonlocal allocation_attempted
        allocation_attempted = True
        raise AssertionError("verification must inherit the parent view without another worktree")

    monkeypatch.setattr(
        "core.engineering_sandbox.delegation.get_engineering_sandbox_service",
        _unexpected_service,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("baseline\n", encoding="utf-8")
    parent_brief = normalize_task_brief(
        {
            "taskBriefId": "parent-write",
            "goal": "Write the parent result.",
            "context": {"workspacePath": str(workspace)},
            "writeRequired": True,
            "writeSet": ["result.txt"],
            "expectedOutputs": ["result.txt"],
            "acceptanceContract": "result.txt contains the requested result.",
        }
    )
    verification_brief = derive_grandchild_engineering_task(
        parent_brief,
        normalize_task_brief({
            "taskBriefId": "verify-child",
            "goal": "Independently verify the parent result.",
            "context": {"workspacePath": str(workspace)},
            "readOnly": True,
            "readSet": ["result.txt"],
            "verificationContract": ["result.txt contains parent-change"],
            "expectedOutputs": ["verification evidence"],
            "acceptanceContract": "Return independent verification evidence.",
        }),
    )

    child = prepare_delegated_engineering_workspace(
        base_state={
            "run_id": "run-nested",
            "session_id": "session",
            "project_id": "demo",
            "original_workspace_path": str(workspace),
            "worktree_id": "parent-task",
        },
        task_brief=verification_brief,
        delegation_id="delegation-grandchild",
        current_depth=1,
        runtime_context={"run_id": "run-nested", "workspace_path": str(workspace)},
    )

    assert child is None
    assert allocation_attempted is False
    assert any(
        "Do not create temporary evidence" in item
        for item in verification_brief["behaviorScope"]
    )


def test_isolation_required_write_does_not_silently_fall_back_in_non_git_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = EngineeringSandboxService(
        home=tmp_path / "v8-home",
        git_service=_git_service(tmp_path),
        database=DatabaseManager(tmp_path / "state.db"),
    )
    monkeypatch.setattr(
        "core.engineering_sandbox.delegation.get_engineering_sandbox_service",
        lambda: service,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    task_brief = normalize_task_brief(
        {
            "taskBriefId": "parallel-write",
            "goal": "Write the isolated result.",
            "context": {"workspacePath": str(workspace)},
            "writeRequired": True,
            "writeSet": ["result.txt"],
            "expectedOutputs": ["result.txt"],
            "acceptanceContract": "result.txt contains the requested result.",
        }
    )

    with pytest.raises(EngineeringWorkspaceIsolationError) as error:
        prepare_delegated_engineering_workspace(
            base_state={
                "run_id": "run-parallel",
                "session_id": "session-parallel",
                "project_id": "demo",
                "workspace_path": str(workspace),
            },
            task_brief=task_brief,
            delegation_id="delegation-parallel",
            current_depth=0,
            runtime_context={"run_id": "run-parallel", "workspace_path": str(workspace)},
            parallel_dispatch=True,
        )

    assert error.value.code == "git_parallel_isolation_not_enabled"
    assert error.value.details["directExecutionAvailable"] is True
    assert not (workspace / ".git").exists()
