from __future__ import annotations

import json
from pathlib import Path
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
from core.engineering_sandbox.delegation import prepare_delegated_engineering_workspace
from core.engineering_sandbox.platform_driver import (
    build_sanitized_environment,
    locate_sandbox_host,
)
from core.engineering_sandbox.service import EngineeringSandboxService
from core.engineering_sandbox.workspace_topology import resolve_workspace_topology
from graph.parallel_support import _fail_managed_branch_workspace
from core.delegation_broker import normalize_task_brief
from core.engineering_capsule import derive_grandchild_engineering_task


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
            "UPDATE engineering_worktrees SET updated_at = '2000-01-01T00:00:00+00:00' WHERE worktree_id = 'failed-task'"
        )
        conn.commit()
    cleanup = service.cleanup_terminal_worktrees(older_than_days=1)

    assert cleanup["removed"] == 1
    assert cleanup["removedPolicyFiles"] == 1
    assert not failed_root.exists()
    assert not failed_policy.exists()


@pytest.mark.skipif(locate_sandbox_host() is None, reason="native sandbox host has not been built")
def test_grandchild_verification_gets_separate_read_only_snapshot_of_parent_changes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database = DatabaseManager(tmp_path / "state.db")
    git = ManagedGitService(home=tmp_path / "v8-home")
    service = EngineeringSandboxService(home=tmp_path / "v8-home", git_service=git, database=database)
    monkeypatch.setattr(
        "core.engineering_sandbox.delegation.get_engineering_sandbox_service",
        lambda: service,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("baseline\n", encoding="utf-8")
    service.ensure_project_repository(workspace_root=workspace, project_id="demo", allow_initialize=True)
    parent = service.prepare_task_workspace(
        workspace_root=workspace,
        project_id="demo",
        session_id="session",
        run_id="run-nested",
        delegation_id="delegation-parent",
        worktree_id="parent-task",
        write_set=("result.txt",),
        actor_role="direct_subagent",
        runtime_kind="engineering",
        network_profile=SandboxNetworkProfile.NETWORKED_PARTIAL,
    )
    Path(parent.execution_workspace_root, "result.txt").write_text("parent-change\n", encoding="utf-8")
    parent_brief = normalize_task_brief(
        {
            "taskBriefId": "parent-write",
            "goal": "Write the parent result.",
            "context": {"workspacePath": parent.execution_workspace_root},
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
            "context": {"workspacePath": parent.execution_workspace_root},
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

    assert child is not None
    assert child["worktree_id"] != "parent-task"
    assert child["parent_worktree_id"] == "parent-task"
    assert child["engineering_capsule_mode"] == "verify"
    assert child["sandbox_policy"]["actor_role"] == "grandchild"
    assert child["sandbox_policy"]["execution_mode"] == "read"
    assert Path(child["workspace_path"], "result.txt").read_text(encoding="utf-8") == "parent-change\n"
