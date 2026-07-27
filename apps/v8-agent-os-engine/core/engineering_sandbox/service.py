from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid
from typing import Any, Iterable, Mapping, Sequence

from core.database import db
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME

from .contracts import (
    GitChangeSetRef,
    SandboxCapabilities,
    SandboxEnforcementLevel,
    SandboxLease,
    SandboxLeaseState,
    SandboxNetworkProfile,
    SandboxPolicy,
    SandboxResourceLimits,
)
from .git_service import ManagedGitError, ManagedGitService, ManagedRepository, ManagedWorktree
from .platform_driver import (
    build_sanitized_environment,
    probe_sandbox_capabilities,
    wrap_sandbox_command,
)
from .workspace_topology import WorkspaceTopology


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(_json(dict(payload)) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@dataclass(frozen=True)
class PreparedEngineeringWorkspace:
    repository: ManagedRepository
    worktree: ManagedWorktree
    lease: SandboxLease
    policy_file: str

    @property
    def execution_workspace_root(self) -> str:
        return str(self.worktree.topology.worktree_workspace_root or "")

    def runtime_context(self) -> dict[str, Any]:
        return {
            "workspace_path": self.execution_workspace_root,
            "original_workspace_path": self.repository.topology.original_workspace_root,
            "originalWorkspacePath": self.repository.topology.original_workspace_root,
            "repository_root": self.repository.topology.repository_root,
            "repositoryRoot": self.repository.topology.repository_root,
            "worktree_root": self.worktree.topology.worktree_root,
            "worktreeRoot": self.worktree.topology.worktree_root,
            "worktree_id": self.worktree.worktree_id,
            "worktreeId": self.worktree.worktree_id,
            "sandbox_lease_id": self.lease.lease_id,
            "sandboxLeaseId": self.lease.lease_id,
            "sandbox_policy": self.lease.policy.as_dict(),
            "sandbox_policy_digest": self.lease.policy.digest,
            "sandbox_policy_file": self.policy_file,
            "sandbox_capabilities": self.lease.capabilities.as_dict(),
            "managed_engineering_execution": True,
        }


class EngineeringSandboxService:
    def __init__(
        self,
        *,
        home: Path | None = None,
        git_service: ManagedGitService | None = None,
        database: Any = db,
    ) -> None:
        self.home = Path(home or V8_AGENT_OS_HOME).expanduser().resolve(strict=False)
        self._git_service_uses_product_config = git_service is None
        lane_config = storage.get_engineering_lane_config() if self._git_service_uses_product_config else {}
        self.git = git_service or ManagedGitService(
            home=self.home,
            worktree_placement=str(lane_config.get("worktreePlacement") or "same_volume"),
            worktrees_root=str(lane_config.get("worktreeRoot") or "") or None,
        )
        self.database = database
        self.policy_root = self.home / "runtime" / "sandboxes"
        self._lock = threading.RLock()

    def _sync_worktree_storage_config(self) -> None:
        if not self._git_service_uses_product_config:
            return
        lane_config = storage.get_engineering_lane_config()
        self.git.configure_worktree_storage(
            placement=str(lane_config.get("worktreePlacement") or "same_volume"),
            custom_root=str(lane_config.get("worktreeRoot") or "") or None,
        )

    def ensure_project_repository(
        self,
        *,
        workspace_root: str | Path,
        project_id: str | None,
        allow_initialize: bool,
    ) -> ManagedRepository:
        self._sync_worktree_storage_config()
        repository = self.git.ensure_repository(workspace_root, allow_initialize=allow_initialize)
        self._persist_repository(repository, project_id=project_id)
        return repository

    def project_repository_status(
        self,
        *,
        workspace_root: str | Path,
        project_id: str | None,
    ) -> dict[str, Any]:
        repository = self.ensure_project_repository(
            workspace_root=workspace_root,
            project_id=project_id,
            allow_initialize=False,
        )
        return {
            "workspace": {
                "root": repository.topology.original_workspace_root,
                "role": "user_scope_and_authority",
            },
            "repository": {
                **repository.as_dict(),
                "role": "version_and_merge_truth",
            },
            "worktree": {
                "role": "temporary_parallel_execution_copy",
                "root": None,
            },
            "sandbox": {
                "role": "process_lease_bound_to_one_worktree",
                "capabilities": probe_sandbox_capabilities().as_dict(),
            },
            "adoptionRequired": repository.state in {"adoption_required", "unborn"},
        }

    def adopt_project_repository(
        self,
        *,
        workspace_root: str | Path,
        project_id: str | None,
    ) -> dict[str, Any]:
        repository = self.git.adopt_repository(workspace_root)
        self._persist_repository(repository, project_id=project_id)
        return self.project_repository_status(workspace_root=workspace_root, project_id=project_id)

    def prepare_task_workspace(
        self,
        *,
        workspace_root: str | Path,
        project_id: str | None,
        session_id: str | None,
        run_id: str,
        delegation_id: str | None,
        worktree_id: str | None,
        write_set: Iterable[str],
        actor_role: str,
        runtime_kind: str,
        execution_mode: str = "write",
        network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKED_PARTIAL,
        limits: SandboxResourceLimits | None = None,
        parent_worktree_id: str | None = None,
        worktree_kind: str = "task",
    ) -> PreparedEngineeringWorkspace:
        write_set = tuple(write_set)
        with self._lock:
            repository = self.ensure_project_repository(
                workspace_root=workspace_root,
                project_id=project_id,
                allow_initialize=False,
            )
            if repository.state != "ready":
                raise ManagedGitError(
                    "workspace_repository_adoption_required",
                    "This existing workspace must be explicitly adopted before parallel writes can use managed worktrees.",
                    details=repository.as_dict(),
                )
            base_commit: str | None = None
            if parent_worktree_id:
                parent_row = self._get_worktree_row(parent_worktree_id)
                if parent_row is None:
                    raise ManagedGitError("parent_worktree_not_found", "The parent worktree record does not exist.")
                if str(parent_row["repository_id"]) != repository.repository_id:
                    raise ManagedGitError(
                        "parent_worktree_repository_mismatch",
                        "The parent worktree belongs to another repository.",
                    )
                base_commit = self.git.snapshot_base_commit(
                    repository,
                    run_id=f"{run_id}-{parent_worktree_id}",
                    source_repository_root=str(parent_row["worktree_root"]),
                )
            resolved_worktree_id = str(worktree_id or f"wt_{uuid.uuid4().hex[:20]}")
            existing_row = self._get_worktree_row(resolved_worktree_id)
            if existing_row is not None and str(existing_row["state"] or "") in {
                "ready",
                "active",
                "finalizing",
                "recoverable",
                "retry_requested",
            }:
                existing_lease = self._get_active_lease_for_worktree(resolved_worktree_id)
                contract_lease = existing_lease or self._get_latest_lease_for_worktree(resolved_worktree_id)
                if contract_lease is None:
                    raise ManagedGitError(
                        "managed_worktree_lease_history_missing",
                        "The persisted worktree has no sandbox lease contract and cannot be resumed safely.",
                    )
                contract_policy = SandboxPolicy.from_dict(
                    json.loads(str(contract_lease["policy_json"] or "{}"))
                )
                self._assert_resume_contract(
                    existing_row=existing_row,
                    existing_policy=contract_policy,
                    run_id=run_id,
                    delegation_id=delegation_id,
                    parent_worktree_id=parent_worktree_id,
                    write_set=write_set,
                    actor_role=actor_role,
                    runtime_kind=runtime_kind,
                    execution_mode=execution_mode,
                    network_profile=network_profile,
                )
                if existing_lease is not None:
                    expires_at = self._parse_datetime(existing_lease["expires_at"])
                    if expires_at is not None and expires_at <= _utc_now():
                        self._update_lease_state(
                            str(existing_lease["lease_id"]),
                            SandboxLeaseState.EXPIRED,
                            error_code="sandbox_lease_expired",
                        )
                        self._update_worktree_state(resolved_worktree_id, "recoverable")
                        existing_lease = None
                if existing_lease is not None:
                    policy = SandboxPolicy.from_dict(json.loads(str(existing_lease["policy_json"] or "{}")))
                    capabilities = SandboxCapabilities.from_dict(
                        json.loads(str(existing_lease["capabilities_json"] or "{}"))
                    )
                    topology = self._topology_from_worktree_row(existing_row)
                    restored_worktree = ManagedWorktree(
                        worktree_id=resolved_worktree_id,
                        repository_id=str(existing_row["repository_id"]),
                        branch_name=str(existing_row["branch_name"]),
                        base_commit=str(existing_row["base_commit"]),
                        topology=topology,
                        state=str(existing_row["state"]),
                    )
                    policy_file = self.policy_root / f"{policy.lease_id}.json"
                    expected_policy_file = _json(policy.as_dict()) + "\n"
                    if (
                        not policy_file.exists()
                        or policy_file.read_text(encoding="utf-8", errors="replace") != expected_policy_file
                    ):
                        _atomic_json(policy_file, policy.as_dict())
                    return PreparedEngineeringWorkspace(
                        repository=repository,
                        worktree=restored_worktree,
                        lease=SandboxLease(
                            lease_id=policy.lease_id,
                            policy=policy,
                            state=SandboxLeaseState(str(existing_lease["state"])),
                            capabilities=capabilities,
                            created_at=str(existing_lease["created_at"]),
                            activated_at=str(existing_lease["activated_at"] or "") or None,
                            expires_at=str(existing_lease["expires_at"] or "") or None,
                        ),
                        policy_file=str(policy_file),
                    )
                topology = self._topology_from_worktree_row(existing_row)
                restored_worktree = ManagedWorktree(
                    worktree_id=resolved_worktree_id,
                    repository_id=str(existing_row["repository_id"]),
                    branch_name=str(existing_row["branch_name"]),
                    base_commit=str(existing_row["base_commit"]),
                    topology=topology,
                    state="active",
                )
                if not Path(str(topology.worktree_root or "")).is_dir():
                    self._update_worktree_state(
                        resolved_worktree_id,
                        "failed",
                        error_code="managed_worktree_missing_after_restart",
                    )
                    raise ManagedGitError(
                        "managed_worktree_missing_after_restart",
                        "The persisted worktree directory is missing and cannot be resumed.",
                    )
                self._update_worktree_state(resolved_worktree_id, "active")
                return self._activate_worktree_lease(
                    repository=repository,
                    worktree=restored_worktree,
                    session_id=session_id,
                    run_id=run_id,
                    delegation_id=delegation_id,
                    write_set=write_set,
                    actor_role=actor_role,
                    runtime_kind=runtime_kind,
                    execution_mode=execution_mode,
                    network_profile=network_profile,
                    limits=limits,
                )
            worktree = self.git.create_worktree(
                repository,
                worktree_id=resolved_worktree_id,
                run_id=run_id,
                base_commit=base_commit,
            )
            self._persist_worktree(
                worktree,
                session_id=session_id,
                run_id=run_id,
                delegation_id=delegation_id,
                parent_worktree_id=parent_worktree_id,
                worktree_kind=worktree_kind,
            )
            return self._activate_worktree_lease(
                repository=repository,
                worktree=worktree,
                session_id=session_id,
                run_id=run_id,
                delegation_id=delegation_id,
                write_set=write_set,
                actor_role=actor_role,
                runtime_kind=runtime_kind,
                execution_mode=execution_mode,
                network_profile=network_profile,
                limits=limits,
            )

    def _activate_worktree_lease(
        self,
        *,
        repository: ManagedRepository,
        worktree: ManagedWorktree,
        session_id: str | None,
        run_id: str,
        delegation_id: str | None,
        write_set: Iterable[str],
        actor_role: str,
        runtime_kind: str,
        execution_mode: str,
        network_profile: SandboxNetworkProfile,
        limits: SandboxResourceLimits | None,
    ) -> PreparedEngineeringWorkspace:
        policy = SandboxPolicy(
            policy_id=f"policy_{uuid.uuid4().hex}",
            lease_id=f"sandbox_{uuid.uuid4().hex}",
            repository_id=repository.repository_id,
            worktree_id=worktree.worktree_id,
            worktree_root=str(worktree.topology.worktree_workspace_root or worktree.topology.worktree_root),
            original_workspace_root=repository.topology.original_workspace_root,
            base_commit=worktree.base_commit,
            execution_mode=execution_mode,
            actor_role=actor_role,
            runtime_kind=runtime_kind,
            network_profile=network_profile,
            write_set=tuple(write_set),
            env_overrides=(
                ("PYTHONIOENCODING", "utf-8"),
                ("PYTHONUTF8", "1"),
                ("TERM", "xterm-256color"),
            ),
            limits=limits or SandboxResourceLimits(),
        )
        capabilities = probe_sandbox_capabilities()
        if capabilities.enforcement_level == SandboxEnforcementLevel.UNAVAILABLE:
            self._update_worktree_state(worktree.worktree_id, "blocked", error_code=capabilities.reason)
            raise RuntimeError(capabilities.reason or "native_sandbox_unavailable")
        if not capabilities.supports(network_profile):
            self._update_worktree_state(
                worktree.worktree_id,
                "blocked",
                error_code=f"network_profile_not_enforced:{network_profile.value}",
            )
            raise RuntimeError(f"sandbox_network_profile_not_enforced:{network_profile.value}")
        created_at = _utc_now_iso()
        lease = SandboxLease(
            lease_id=policy.lease_id,
            policy=policy,
            state=SandboxLeaseState.ACTIVE,
            capabilities=capabilities,
            created_at=created_at,
            activated_at=created_at,
            expires_at=(_utc_now() + timedelta(hours=12)).isoformat(),
        )
        policy_file = self.policy_root / f"{lease.lease_id}.json"
        _atomic_json(policy_file, policy.as_dict())
        self._persist_lease(
            lease,
            session_id=session_id,
            run_id=run_id,
            delegation_id=delegation_id,
        )
        return PreparedEngineeringWorkspace(
            repository=repository,
            worktree=worktree,
            lease=lease,
            policy_file=str(policy_file),
        )

    def materialize_task_dependencies(
        self,
        *,
        worktree_id: str,
        run_id: str,
        change_sets: Iterable[Mapping[str, Any]],
    ) -> tuple[PreparedEngineeringWorkspace, GitChangeSetRef]:
        """Make accepted upstream changes the immutable baseline of one task.

        The dependency patch is committed before the worker starts, then both
        the worktree record and active sandbox policy move to that commit.  A
        downstream finalization therefore reports only the downstream delta,
        while crash recovery can reconstruct the exact accepted dependency
        chain from preserved Git objects.
        """

        parsed: list[GitChangeSetRef] = []
        seen_commits: set[tuple[str, str]] = set()
        for raw in change_sets:
            item = dict(raw or {})
            change_set = GitChangeSetRef(
                repository_id=str(item.get("repositoryId") or item.get("repository_id") or ""),
                worktree_id=str(item.get("worktreeId") or item.get("worktree_id") or ""),
                branch_name=str(item.get("branchName") or item.get("branch_name") or ""),
                base_commit=str(item.get("baseCommit") or item.get("base_commit") or ""),
                commit_id=str(item.get("commitId") or item.get("commit_id") or ""),
                changed_paths=tuple(item.get("changedPaths") or item.get("changed_paths") or ()),
                insertions=int(item.get("insertions") or 0),
                deletions=int(item.get("deletions") or 0),
                status=str(item.get("status") or "candidate"),
            )
            key = (change_set.repository_id, change_set.commit_id)
            if (
                not change_set.repository_id
                or not change_set.base_commit
                or not change_set.commit_id
                or key in seen_commits
                or change_set.status == "no_changes"
                or change_set.commit_id == change_set.base_commit
            ):
                continue
            seen_commits.add(key)
            parsed.append(change_set)
        if not parsed:
            raise ManagedGitError(
                "dependency_change_sets_required",
                "No effective dependency change sets were supplied.",
            )

        with self._lock:
            row = self._get_worktree_row(worktree_id)
            if row is None:
                raise ManagedGitError(
                    "dependency_target_worktree_not_found",
                    "The dependent managed worktree record does not exist.",
                )
            if str(row["run_id"] or "").strip() != str(run_id or "").strip():
                raise ManagedGitError(
                    "dependency_target_run_mismatch",
                    "The dependent worktree belongs to another runtime run.",
                )
            if str(row["change_set_json"] or "").strip():
                raise ManagedGitError(
                    "dependency_target_already_finalized",
                    "Dependencies cannot be injected after the dependent task was finalized.",
                )
            repository = self._get_repository(str(row["repository_id"] or ""))
            if repository is None:
                raise ManagedGitError(
                    "managed_repository_not_found",
                    "The dependent worktree repository record is missing.",
                )
            if any(item.repository_id != repository.repository_id for item in parsed):
                raise ManagedGitError(
                    "dependency_chain_repository_mismatch",
                    "Upstream changes belong to another repository.",
                )
            lease_row = self._get_active_lease_for_worktree(worktree_id)
            if lease_row is None:
                raise ManagedGitError(
                    "dependency_target_lease_missing",
                    "The dependent worktree has no active sandbox lease.",
                )
            policy = SandboxPolicy.from_dict(json.loads(str(lease_row["policy_json"] or "{}")))
            topology = self._topology_from_worktree_row(row)
            worktree = ManagedWorktree(
                worktree_id=str(row["worktree_id"]),
                repository_id=str(row["repository_id"]),
                branch_name=str(row["branch_name"]),
                base_commit=str(row["base_commit"]),
                topology=topology,
                state=str(row["state"]),
            )
            descriptor = "\n".join(
                f"{item.repository_id}:{item.base_commit}:{item.commit_id}"
                for item in parsed
            )
            chain_digest = hashlib.sha256(descriptor.encode("utf-8")).hexdigest()
            chain_id = f"dependency_{worktree_id}_{chain_digest[:16]}"
            closure = self.git.compose_change_set_chain(
                repository,
                run_id=run_id,
                chain_id=chain_id,
                change_sets=parsed,
            )
            target_root = Path(str(topology.worktree_root))
            closure_tree = str(
                self.git.run(
                    ["rev-parse", f"{closure.commit_id}^{{tree}}"],
                    cwd=target_root,
                ).stdout
                or ""
            ).strip()
            current_head = str(
                self.git.run(["rev-parse", "HEAD"], cwd=target_root).stdout or ""
            ).strip()
            current_tree = str(
                self.git.run(
                    ["rev-parse", f"{current_head}^{{tree}}"],
                    cwd=target_root,
                ).stdout
                or ""
            ).strip()
            status = self.git.run(
                ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
                cwd=target_root,
                text=False,
            )
            if status.stdout:
                dirty_snapshot = self.git.snapshot_base_commit(
                    repository,
                    run_id=f"{run_id}-{worktree_id}-dependency-recovery",
                    source_repository_root=target_root,
                )
                dirty_tree = str(
                    self.git.run(
                        ["rev-parse", f"{dirty_snapshot}^{{tree}}"],
                        cwd=target_root,
                    ).stdout
                    or ""
                ).strip()
                if dirty_tree != closure_tree:
                    raise ManagedGitError(
                        "dependency_target_not_clean",
                        "The dependent worktree changed before its dependency baseline was ready.",
                        details={"worktreeId": worktree_id},
                    )
            elif current_tree != closure_tree:
                self.git.apply_change_set_to_worktree(
                    target_repository_root=target_root,
                    repository=repository,
                    change_set=closure,
                    run_id=f"{run_id}-{worktree_id}-dependency-materialize",
                )

            baseline = self.git.finalize_worktree(
                worktree,
                write_set=closure.changed_paths,
                commit_message=f"V8OS dependency baseline for {worktree_id}",
            )
            updated_policy = SandboxPolicy.from_dict(
                {
                    **policy.as_dict(),
                    "base_commit": baseline.commit_id,
                }
            )
            policy_file = self.policy_root / f"{policy.lease_id}.json"
            previous_policy_payload = policy.as_dict()
            _atomic_json(policy_file, updated_policy.as_dict())
            now = _utc_now_iso()

            def _write() -> None:
                with self.database.get_connection() as conn:
                    conn.execute(
                        "UPDATE engineering_worktrees SET base_commit = ?, updated_at = ? WHERE worktree_id = ?",
                        (baseline.commit_id, now, worktree_id),
                    )
                    conn.execute(
                        """
                        UPDATE sandbox_execution_leases
                        SET policy_digest = ?, policy_json = ?, updated_at = ?
                        WHERE lease_id = ? AND state IN ('active', 'finalizing')
                        """,
                        (
                            updated_policy.digest,
                            _json(updated_policy.as_dict()),
                            now,
                            policy.lease_id,
                        ),
                    )
                    conn.commit()

            try:
                self.database._run_write_with_retry(_write)
            except Exception:
                _atomic_json(policy_file, previous_policy_payload)
                raise

            updated_worktree = ManagedWorktree(
                worktree_id=worktree.worktree_id,
                repository_id=worktree.repository_id,
                branch_name=worktree.branch_name,
                base_commit=baseline.commit_id,
                topology=worktree.topology,
                state=worktree.state,
            )
            updated_lease = SandboxLease(
                lease_id=policy.lease_id,
                policy=updated_policy,
                state=SandboxLeaseState(str(lease_row["state"])),
                capabilities=SandboxCapabilities.from_dict(
                    json.loads(str(lease_row["capabilities_json"] or "{}"))
                ),
                created_at=str(lease_row["created_at"]),
                activated_at=str(lease_row["activated_at"] or "") or None,
                expires_at=str(lease_row["expires_at"] or "") or None,
            )
            return (
                PreparedEngineeringWorkspace(
                    repository=repository,
                    worktree=updated_worktree,
                    lease=updated_lease,
                    policy_file=str(policy_file),
                ),
                closure,
            )

    def wrap_runtime_command(
        self,
        runtime_context: Mapping[str, Any],
        argv: Sequence[str],
    ) -> tuple[list[str], dict[str, str]]:
        policy_payload = runtime_context.get("sandbox_policy") or runtime_context.get("sandboxPolicy")
        policy_file = str(
            runtime_context.get("sandbox_policy_file") or runtime_context.get("sandboxPolicyFile") or ""
        ).strip()
        if not isinstance(policy_payload, dict) or not policy_file:
            raise RuntimeError("sandbox_lease_required")
        policy = SandboxPolicy.from_dict(policy_payload)
        expected_digest = str(
            runtime_context.get("sandbox_policy_digest")
            or runtime_context.get("sandboxPolicyDigest")
            or ""
        ).strip()
        if not expected_digest or expected_digest != policy.digest:
            raise RuntimeError("sandbox_policy_digest_mismatch")
        resolved_policy_file = Path(policy_file).expanduser().resolve(strict=False)
        expected_policy_file = (self.policy_root / f"{policy.lease_id}.json").resolve(strict=False)
        if resolved_policy_file != expected_policy_file or not resolved_policy_file.is_file():
            raise RuntimeError("sandbox_policy_file_invalid")
        disk_policy = SandboxPolicy.from_dict(
            json.loads(resolved_policy_file.read_text(encoding="utf-8"))
        )
        if disk_policy.digest != policy.digest:
            raise RuntimeError("sandbox_policy_file_digest_mismatch")
        lease_row = self._get_active_lease_for_worktree(policy.worktree_id)
        if (
            lease_row is None
            or str(lease_row["lease_id"] or "") != policy.lease_id
            or str(lease_row["policy_digest"] or "") != policy.digest
            or str(lease_row["state"] or "") != SandboxLeaseState.ACTIVE.value
        ):
            raise RuntimeError("sandbox_lease_not_active")
        expires_at = self._parse_datetime(lease_row["expires_at"])
        if expires_at is not None and expires_at <= _utc_now():
            self._update_lease_state(
                policy.lease_id,
                SandboxLeaseState.EXPIRED,
                error_code="sandbox_lease_expired",
            )
            self._update_worktree_state(policy.worktree_id, "recoverable")
            raise RuntimeError("sandbox_lease_expired")
        capabilities = probe_sandbox_capabilities()
        wrapped = wrap_sandbox_command(
            policy,
            argv,
            policy_file=resolved_policy_file,
            capabilities=capabilities,
        )
        environment = build_sanitized_environment(policy)
        return wrapped, environment

    def finalize_task_workspace(
        self,
        *,
        worktree_id: str,
        commit_message: str,
    ) -> GitChangeSetRef:
        row = self._get_worktree_row(worktree_id)
        if row is None:
            raise ManagedGitError("worktree_not_found", "The managed worktree record does not exist.")
        existing_change_set = str(row["change_set_json"] or "").strip()
        if existing_change_set:
            payload = json.loads(existing_change_set)
            return GitChangeSetRef(
                repository_id=str(payload.get("repositoryId") or row["repository_id"]),
                worktree_id=str(payload.get("worktreeId") or worktree_id),
                branch_name=str(payload.get("branchName") or row["branch_name"]),
                base_commit=str(payload.get("baseCommit") or row["base_commit"]),
                commit_id=str(payload.get("commitId") or row["base_commit"]),
                changed_paths=tuple(payload.get("changedPaths") or ()),
                insertions=int(payload.get("insertions") or 0),
                deletions=int(payload.get("deletions") or 0),
                status=str(payload.get("status") or "candidate"),
            )
        lease_row = self._get_active_lease_for_worktree(worktree_id)
        if lease_row is None:
            raise RuntimeError("sandbox_lease_not_found")
        policy = SandboxPolicy.from_dict(json.loads(str(lease_row["policy_json"] or "{}")))
        topology = WorkspaceTopology(
            original_workspace_root=str(row["original_workspace_root"]),
            repository_root=str(row["repository_root"]),
            workspace_relative_path=str(row["workspace_relative_path"] or "."),
            worktree_root=str(row["worktree_root"]),
            worktree_workspace_root=str(row["worktree_workspace_root"]),
        )
        worktree = ManagedWorktree(
            worktree_id=str(row["worktree_id"]),
            repository_id=str(row["repository_id"]),
            branch_name=str(row["branch_name"]),
            base_commit=str(row["base_commit"]),
            topology=topology,
            state=str(row["state"]),
        )
        repository_write_set = self._repository_write_set(topology, policy.write_set)
        self._update_worktree_state(worktree_id, "finalizing")
        self._update_lease_state(policy.lease_id, SandboxLeaseState.FINALIZING)
        try:
            change_set = self.git.finalize_worktree(
                worktree,
                write_set=repository_write_set,
                commit_message=commit_message,
            )
        except Exception as exc:
            error_code = getattr(exc, "code", None) or exc.__class__.__name__
            self._update_worktree_state(worktree_id, "failed", error_code=str(error_code))
            self._update_lease_state(policy.lease_id, SandboxLeaseState.FAILED, error_code=str(error_code))
            raise
        row = self._get_worktree_row(worktree_id)
        completed_state = (
            "integration_candidate"
            if row is not None and str(row["worktree_kind"] or "").strip() in {"integration", "supervisor_integration"}
            else "candidate"
        )
        self._complete_worktree(worktree_id, change_set, state=completed_state)
        self._update_lease_state(policy.lease_id, SandboxLeaseState.COMPLETED)
        return change_set

    def merge_child_change_set_to_parent(self, *, child_worktree_id: str, run_id: str) -> dict[str, Any]:
        child_row = self._get_worktree_row(child_worktree_id)
        if child_row is None:
            raise ManagedGitError("child_worktree_not_found", "The child worktree record does not exist.")
        parent_worktree_id = str(child_row["parent_worktree_id"] or "").strip()
        if not parent_worktree_id:
            return {"status": "not_nested", "changedPaths": []}
        if str(child_row["state"] or "").strip() == "merged_to_parent":
            existing_payload = json.loads(str(child_row["change_set_json"] or "{}"))
            return {
                "status": "merged_to_parent",
                "parentWorktreeId": parent_worktree_id,
                "childWorktreeId": child_worktree_id,
                "commitId": existing_payload.get("commitId"),
                "changedPaths": list(existing_payload.get("changedPaths") or []),
                "idempotent": True,
            }
        parent_row = self._get_worktree_row(parent_worktree_id)
        if parent_row is None:
            raise ManagedGitError("parent_worktree_not_found", "The parent worktree record does not exist.")
        payload = json.loads(str(child_row["change_set_json"] or "{}"))
        change_set = GitChangeSetRef(
            repository_id=str(payload.get("repositoryId") or child_row["repository_id"]),
            worktree_id=str(payload.get("worktreeId") or child_worktree_id),
            branch_name=str(payload.get("branchName") or child_row["branch_name"]),
            base_commit=str(payload.get("baseCommit") or child_row["base_commit"]),
            commit_id=str(payload.get("commitId") or ""),
            changed_paths=tuple(payload.get("changedPaths") or ()),
            insertions=int(payload.get("insertions") or 0),
            deletions=int(payload.get("deletions") or 0),
            status=str(payload.get("status") or "candidate"),
        )
        repository = self._get_repository(change_set.repository_id)
        if repository is None:
            raise ManagedGitError("managed_repository_not_found", "The managed repository record is missing.")
        changed_paths = self.git.apply_change_set_to_worktree(
            target_repository_root=str(parent_row["worktree_root"]),
            repository=repository,
            change_set=change_set,
            run_id=run_id,
        )
        self._update_worktree_state(child_worktree_id, "merged_to_parent")
        return {
            "status": "merged_to_parent",
            "parentWorktreeId": parent_worktree_id,
            "childWorktreeId": child_worktree_id,
            "commitId": change_set.commit_id,
            "changedPaths": list(changed_paths),
        }

    def build_run_integration(
        self,
        *,
        run_id: str,
        invocation_id: str,
        change_sets: Iterable[Mapping[str, Any]],
    ) -> tuple[ManagedWorktree, GitChangeSetRef]:
        parsed: list[GitChangeSetRef] = []
        for raw in change_sets:
            item = dict(raw or {})
            parsed.append(
                GitChangeSetRef(
                    repository_id=str(item.get("repositoryId") or item.get("repository_id") or ""),
                    worktree_id=str(item.get("worktreeId") or item.get("worktree_id") or ""),
                    branch_name=str(item.get("branchName") or item.get("branch_name") or ""),
                    base_commit=str(item.get("baseCommit") or item.get("base_commit") or ""),
                    commit_id=str(item.get("commitId") or item.get("commit_id") or ""),
                    changed_paths=tuple(item.get("changedPaths") or item.get("changed_paths") or ()),
                    insertions=int(item.get("insertions") or 0),
                    deletions=int(item.get("deletions") or 0),
                    status=str(item.get("status") or "candidate"),
                )
            )
        if not parsed or not parsed[0].repository_id:
            raise ManagedGitError("integration_change_sets_required", "No managed Git candidates were supplied.")
        repository = self._get_repository(parsed[0].repository_id)
        if repository is None:
            raise ManagedGitError("managed_repository_not_found", "The managed repository record is missing.")
        if any(item.repository_id != repository.repository_id for item in parsed):
            raise ManagedGitError(
                "integration_topology_mismatch",
                "Candidate change sets do not belong to one repository.",
            )
        candidate_descriptor = "\n".join(
            f"{item.repository_id}:{item.base_commit}:{item.commit_id}"
            for item in parsed
        )
        candidate_digest = hashlib.sha256(candidate_descriptor.encode("utf-8")).hexdigest()
        integration_id = "integration_" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"v8os:{run_id}:{invocation_id}:{parsed[0].repository_id}:{candidate_digest}",
        ).hex[:24]
        existing = self._get_worktree_row(integration_id)
        if existing is not None and str(existing["change_set_json"] or "").strip():
            payload = json.loads(str(existing["change_set_json"]))
            topology = self._topology_from_worktree_row(existing)
            return (
                ManagedWorktree(
                    worktree_id=integration_id,
                    repository_id=str(existing["repository_id"]),
                    branch_name=str(existing["branch_name"]),
                    base_commit=str(existing["base_commit"]),
                    topology=topology,
                    state=str(existing["state"]),
                ),
                GitChangeSetRef(
                    repository_id=str(payload.get("repositoryId") or ""),
                    worktree_id=str(payload.get("worktreeId") or integration_id),
                    branch_name=str(payload.get("branchName") or ""),
                    base_commit=str(payload.get("baseCommit") or ""),
                    commit_id=str(payload.get("commitId") or ""),
                    changed_paths=tuple(payload.get("changedPaths") or ()),
                    insertions=int(payload.get("insertions") or 0),
                    deletions=int(payload.get("deletions") or 0),
                    status=str(payload.get("status") or "integration_candidate"),
                ),
            )
        integration_candidates = parsed
        if len({item.base_commit for item in parsed}) > 1:
            integration_candidates = [
                self.git.compose_change_set_chain(
                    repository,
                    run_id=run_id,
                    chain_id=f"integration-source-{candidate_digest[:16]}",
                    change_sets=parsed,
                )
            ]
        integration, combined = self.git.integrate_change_sets(
            repository,
            run_id=run_id,
            integration_id=integration_id,
            change_sets=integration_candidates,
        )
        self._persist_worktree(
            integration,
            session_id=None,
            run_id=run_id,
            delegation_id=invocation_id,
            parent_worktree_id=None,
            worktree_kind="integration",
        )
        self._complete_worktree(integration_id, combined, state="integration_candidate")
        self._mark_change_sets_integrated(parsed, integration_id=integration_id)
        return integration, combined

    def associate_worktree_delegation(self, *, worktree_id: str, delegation_id: str) -> None:
        normalized = str(delegation_id or "").strip()
        if not normalized:
            return
        now = _utc_now_iso()

        def _write() -> None:
            with self.database.get_connection() as conn:
                conn.execute(
                    "UPDATE engineering_worktrees SET delegation_id = ?, updated_at = ? WHERE worktree_id = ?",
                    (normalized, now, worktree_id),
                )
                conn.execute(
                    "UPDATE sandbox_execution_leases SET delegation_id = ?, updated_at = ? WHERE worktree_id = ?",
                    (normalized, now, worktree_id),
                )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def managed_workspace_for_delegation(self, delegation_id: str) -> dict[str, Any] | None:
        normalized = str(delegation_id or "").strip()
        if not normalized:
            return None
        with self.database.get_connection() as conn:
            row = conn.execute(
                """
                SELECT wt.*, lease.lease_id, lease.policy_digest, lease.capabilities_json,
                       lease.policy_json, lease.state AS lease_state,
                       repo.original_workspace_root, repo.repository_root,
                       repo.workspace_relative_path
                FROM engineering_worktrees wt
                JOIN managed_git_repositories repo ON repo.repository_id = wt.repository_id
                LEFT JOIN sandbox_execution_leases lease ON lease.worktree_id = wt.worktree_id
                WHERE wt.delegation_id = ?
                ORDER BY lease.created_at DESC, wt.created_at DESC LIMIT 1
                """,
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        policy = json.loads(str(row["policy_json"] or "{}"))
        return {
            "workspace_path": str(row["worktree_workspace_root"]),
            "original_workspace_path": str(row["original_workspace_root"]),
            "repository_root": str(row["repository_root"]),
            "worktree_root": str(row["worktree_root"]),
            "worktree_id": str(row["worktree_id"]),
            "sandbox_lease_id": str(row["lease_id"] or "") or None,
            "sandbox_policy": policy or None,
            "sandbox_policy_digest": str(row["policy_digest"] or "") or None,
            "sandbox_policy_file": (
                str(self.policy_root / f"{row['lease_id']}.json") if row["lease_id"] else None
            ),
            "sandbox_capabilities": json.loads(str(row["capabilities_json"] or "{}")),
            "worktree_state": str(row["state"] or ""),
            "lease_state": str(row["lease_state"] or ""),
        }

    def promote_run_integration(self, *, run_id: str) -> dict[str, Any]:
        row = self._get_latest_run_integration(run_id)
        if row is None or not str(row["change_set_json"] or "").strip():
            return {"ok": True, "status": "no_managed_changes", "changedPaths": []}
        payload = json.loads(str(row["change_set_json"]))
        if str(row["state"] or "").strip() in {"delivered", "cleaned"}:
            artifact_rebind = self._rebind_delivered_artifacts(
                run_id=run_id,
                workspace_root=str(row["original_workspace_root"]),
            )
            cleanup = (
                self.cleanup_accepted_run_worktrees(run_id=run_id)
                if artifact_rebind.get("ok") is True
                else self._deferred_artifact_cleanup(artifact_rebind)
            )
            return {
                "ok": True,
                "status": "delivered",
                "worktreeId": str(row["worktree_id"]),
                "commitId": payload.get("commitId"),
                "changedPaths": list(payload.get("changedPaths") or []),
                "recoveryRef": payload.get("recoveryRef"),
                "artifactRebind": artifact_rebind,
                "cleanup": cleanup,
                "idempotent": True,
            }
        integration = GitChangeSetRef(
            repository_id=str(payload.get("repositoryId") or ""),
            worktree_id=str(payload.get("worktreeId") or row["worktree_id"]),
            branch_name=str(payload.get("branchName") or row["branch_name"]),
            base_commit=str(payload.get("baseCommit") or row["base_commit"]),
            commit_id=str(payload.get("commitId") or ""),
            changed_paths=tuple(payload.get("changedPaths") or ()),
            insertions=int(payload.get("insertions") or 0),
            deletions=int(payload.get("deletions") or 0),
            status=str(payload.get("status") or "integration_candidate"),
        )
        repository = self._get_repository(integration.repository_id)
        if repository is None:
            raise ManagedGitError("managed_repository_not_found", "The managed repository record is missing.")
        changed = self.git.apply_integration_to_workspace(repository, integration=integration, run_id=run_id)
        self._update_worktree_state(str(row["worktree_id"]), "delivered")
        artifact_rebind = self._rebind_delivered_artifacts(
            run_id=run_id,
            workspace_root=repository.topology.original_workspace_root,
        )
        cleanup = (
            self.cleanup_accepted_run_worktrees(run_id=run_id)
            if artifact_rebind.get("ok") is True
            else self._deferred_artifact_cleanup(artifact_rebind)
        )
        return {
            "ok": True,
            "status": "delivered",
            "worktreeId": str(row["worktree_id"]),
            "commitId": integration.commit_id,
            "changedPaths": list(changed),
            "artifactRebind": artifact_rebind,
            "cleanup": cleanup,
        }

    def _rebind_delivered_artifacts(self, *, run_id: str, workspace_root: str) -> dict[str, Any]:
        try:
            from core.artifact_store import ArtifactStore

            return ArtifactStore(database=self.database).rebind_managed_workspace_artifacts(
                run_id=run_id,
                workspace_root=workspace_root,
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "blocked",
                "rebound": 0,
                "invalidated": 0,
                "skipped": 0,
                "errorCode": "artifact_rebind_failed",
                "error": str(getattr(exc, "code", None) or exc),
            }

    @staticmethod
    def _deferred_artifact_cleanup(artifact_rebind: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "removed": 0,
            "removedPolicyFiles": 0,
            "preservedRefs": [],
            "failures": [
                {
                    "worktreeId": "run",
                    "error": str(artifact_rebind.get("errorCode") or "artifact_rebind_incomplete"),
                }
            ],
            "deferred": True,
        }

    def record_run_integration_decision(self, *, run_id: str, decision: str) -> None:
        normalized = str(decision or "").strip().lower()
        state = {"retry": "retry_requested", "ignored": "ignored"}.get(normalized)
        if not state:
            return
        row = self._get_latest_run_integration(run_id)
        if row is not None:
            self._update_worktree_state(str(row["worktree_id"]), state)

    def abort_run_workspaces(self, *, run_id: str, error_code: str) -> dict[str, Any]:
        """Close non-terminal worktrees without deleting their recovery evidence."""

        normalized_run_id = str(run_id or "").strip()
        normalized_error = str(error_code or "run_aborted").strip() or "run_aborted"
        if not normalized_run_id:
            return {"worktreeIds": [], "leaseIds": [], "errorCode": normalized_error}
        now = _utc_now_iso()
        open_worktree_states = (
            "planned",
            "ready",
            "active",
            "finalizing",
            "recoverable",
            "retry_requested",
        )
        open_lease_states = (
            SandboxLeaseState.PLANNED.value,
            SandboxLeaseState.ACTIVE.value,
            SandboxLeaseState.FINALIZING.value,
        )

        def _write() -> tuple[list[str], list[str]]:
            with self.database.get_connection() as conn:
                worktree_rows = conn.execute(
                    f"""
                    SELECT worktree_id FROM engineering_worktrees
                    WHERE run_id = ? AND state IN ({','.join('?' for _ in open_worktree_states)})
                    """,
                    (normalized_run_id, *open_worktree_states),
                ).fetchall()
                lease_rows = conn.execute(
                    f"""
                    SELECT lease_id FROM sandbox_execution_leases
                    WHERE run_id = ? AND state IN ({','.join('?' for _ in open_lease_states)})
                    """,
                    (normalized_run_id, *open_lease_states),
                ).fetchall()
                worktree_ids = [str(row["worktree_id"]) for row in worktree_rows]
                lease_ids = [str(row["lease_id"]) for row in lease_rows]
                if worktree_ids:
                    conn.execute(
                        f"""
                        UPDATE engineering_worktrees
                        SET state = 'cancelled', error_code = ?, finished_at = ?, updated_at = ?
                        WHERE worktree_id IN ({','.join('?' for _ in worktree_ids)})
                        """,
                        (normalized_error, now, now, *worktree_ids),
                    )
                if lease_ids:
                    conn.execute(
                        f"""
                        UPDATE sandbox_execution_leases
                        SET state = ?, error_code = ?, finished_at = ?, updated_at = ?
                        WHERE lease_id IN ({','.join('?' for _ in lease_ids)})
                        """,
                        (SandboxLeaseState.FAILED.value, normalized_error, now, now, *lease_ids),
                    )
                conn.commit()
                return worktree_ids, lease_ids

        worktree_ids, lease_ids = self.database._run_write_with_retry(_write)
        return {
            "worktreeIds": worktree_ids,
            "leaseIds": lease_ids,
            "errorCode": normalized_error,
        }

    def mark_task_workspace_failed(self, *, worktree_id: str, error_code: str) -> None:
        row = self._get_worktree_row(worktree_id)
        if row is None:
            return
        self._update_worktree_state(worktree_id, "failed", error_code=error_code)
        lease = self._get_active_lease_for_worktree(worktree_id)
        if lease is not None:
            self._update_lease_state(
                str(lease["lease_id"]),
                SandboxLeaseState.FAILED,
                error_code=error_code,
            )

    def preserve_task_workspace_unmerged(self, *, worktree_id: str, reason: str) -> None:
        """Close a finalized candidate without promoting or deleting its evidence."""

        row = self._get_worktree_row(worktree_id)
        if row is None:
            return
        normalized_reason = str(reason or "delegation_result_rejected").strip() or "delegation_result_rejected"
        self._update_worktree_state(worktree_id, "ignored", error_code=normalized_reason)
        lease = self._get_active_lease_for_worktree(worktree_id)
        if lease is not None:
            self._update_lease_state(
                str(lease["lease_id"]),
                SandboxLeaseState.COMPLETED,
                error_code=normalized_reason,
            )

    def reconcile_startup(self) -> dict[str, Any]:
        now = _utc_now()
        expired = 0
        missing = 0
        restored = 0
        with self.database.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT wt.*, lease.lease_id, lease.expires_at, lease.state AS lease_state
                FROM engineering_worktrees wt
                LEFT JOIN sandbox_execution_leases lease ON lease.worktree_id = wt.worktree_id
                    AND lease.state IN ('active', 'finalizing')
                WHERE wt.state IN ('ready', 'active', 'recoverable', 'finalizing', 'retry_requested')
                ORDER BY wt.created_at ASC
                """
            ).fetchall()
        for row in rows:
            worktree_id = str(row["worktree_id"])
            if not Path(str(row["worktree_root"] or "")).is_dir():
                self._update_worktree_state(
                    worktree_id,
                    "failed",
                    error_code="managed_worktree_missing_after_restart",
                )
                if row["lease_id"]:
                    self._update_lease_state(
                        str(row["lease_id"]),
                        SandboxLeaseState.FAILED,
                        error_code="managed_worktree_missing_after_restart",
                    )
                missing += 1
                continue
            expires_at = self._parse_datetime(row["expires_at"])
            if row["lease_id"] and expires_at is not None and expires_at <= now:
                self._update_lease_state(
                    str(row["lease_id"]),
                    SandboxLeaseState.EXPIRED,
                    error_code="sandbox_lease_expired",
                )
                self._update_worktree_state(worktree_id, "recoverable")
                expired += 1
            else:
                restored += 1
        return {"restored": restored, "expired": expired, "missing": missing}

    def cleanup_accepted_run_worktrees(self, *, run_id: str) -> dict[str, Any]:
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id:
            return {"removed": 0, "preservedRefs": [], "failures": []}
        self._sync_worktree_storage_config()
        accepted_states = ("delivered", "integrated", "merged_to_parent")
        placeholders = ",".join("?" for _ in accepted_states)
        with self.database.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT wt.*, repo.original_workspace_root, repo.repository_root,
                       repo.workspace_relative_path, repo.state AS repository_state,
                       repo.head_commit, repo.default_branch, repo.initialized_by_v8os,
                       repo.metadata_json
                FROM engineering_worktrees wt
                JOIN managed_git_repositories repo ON repo.repository_id = wt.repository_id
                WHERE wt.run_id = ? AND wt.state IN ({placeholders})
                ORDER BY CASE wt.worktree_kind WHEN 'integration' THEN 1 ELSE 0 END, wt.created_at ASC
                """,
                (normalized_run_id, *accepted_states),
            ).fetchall()
        return self._cleanup_worktree_rows(rows, preserve_recovery_refs=True)

    def cleanup_accepted_worktrees(self, *, limit_runs: int = 50) -> dict[str, Any]:
        with self.database.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT run_id
                FROM engineering_worktrees
                WHERE (
                    state = 'delivered'
                    OR (
                        worktree_kind IN ('integration', 'supervisor_integration')
                        AND state = 'cleaned'
                    )
                )
                  AND COALESCE(run_id, '') <> ''
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (max(1, int(limit_runs)),),
            ).fetchall()
        removed = 0
        preserved_refs: list[str] = []
        failures: list[dict[str, str]] = []
        for row in rows:
            result = self.cleanup_accepted_run_worktrees(run_id=str(row["run_id"]))
            removed += int(result.get("removed") or 0)
            preserved_refs.extend(str(item) for item in result.get("preservedRefs") or [])
            failures.extend(dict(item) for item in result.get("failures") or [])
        return {"removed": removed, "preservedRefs": preserved_refs, "failures": failures[:20]}

    def _cleanup_worktree_rows(
        self,
        rows: Sequence[Any],
        *,
        preserve_recovery_refs: bool,
    ) -> dict[str, Any]:
        removed = 0
        removed_policy_files = 0
        preserved_refs: list[str] = []
        failures: list[dict[str, str]] = []
        for row in rows:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            repository = ManagedRepository(
                repository_id=str(row["repository_id"]),
                topology=WorkspaceTopology(
                    original_workspace_root=str(row["original_workspace_root"]),
                    repository_root=str(row["repository_root"]),
                    workspace_relative_path=str(row["workspace_relative_path"] or "."),
                ),
                state=str(row["repository_state"]),
                head_commit=str(row["head_commit"] or "") or None,
                default_branch=str(row["default_branch"] or "") or None,
                initialized_by_v8os=bool(row["initialized_by_v8os"]),
                warnings=tuple(metadata.get("warnings") or ()),
            )
            worktree_id = str(row["worktree_id"])
            try:
                change_set = json.loads(str(row["change_set_json"] or "{}"))
                commit_id = str(change_set.get("commitId") or "").strip()
                if preserve_recovery_refs and commit_id:
                    recovery_ref = self.git.preserve_change_set_ref(
                        repository,
                        run_id=str(row["run_id"] or "run"),
                        worktree_id=worktree_id,
                        commit_id=commit_id,
                    )
                    change_set["recoveryRef"] = recovery_ref
                    self._update_change_set_payload(worktree_id, change_set)
                    preserved_refs.append(recovery_ref)
                lease_ids = self._lease_ids_for_worktree(worktree_id)
                self.git.remove_managed_worktree(
                    repository,
                    worktree_root=str(row["worktree_root"]),
                    branch_name=str(row["branch_name"]),
                )
                self._update_worktree_state(worktree_id, "cleaned")
                for lease_id in lease_ids:
                    policy_file = self.policy_root / f"{lease_id}.json"
                    if policy_file.exists():
                        policy_file.unlink(missing_ok=True)
                        removed_policy_files += 1
                removed += 1
            except Exception as exc:
                failures.append({"worktreeId": worktree_id, "error": str(getattr(exc, "code", None) or exc)})
        return {
            "removed": removed,
            "removedPolicyFiles": removed_policy_files,
            "preservedRefs": preserved_refs,
            "failures": failures[:20],
        }

    def _update_change_set_payload(self, worktree_id: str, payload: Mapping[str, Any]) -> None:
        now = _utc_now_iso()

        def _write() -> None:
            with self.database.get_connection() as conn:
                conn.execute(
                    "UPDATE engineering_worktrees SET change_set_json = ?, updated_at = ? WHERE worktree_id = ?",
                    (_json(dict(payload)), now, worktree_id),
                )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def cleanup_terminal_worktrees(
        self,
        *,
        older_than_days: float = 1,
        abandoned_after_days: float = 7,
        limit: int = 100,
    ) -> dict[str, Any]:
        self._sync_worktree_storage_config()
        terminal_cutoff = (_utc_now() - timedelta(days=max(0.0, float(older_than_days)))).isoformat()
        abandoned_cutoff = (
            _utc_now() - timedelta(days=max(0.0, float(abandoned_after_days)))
        ).isoformat()
        terminal_states = (
            "delivered",
            "integrated",
            "merged_to_parent",
            "ignored",
            "failed",
            "blocked",
            "cancelled",
        )
        abandoned_states = ("recoverable", "integration_candidate")
        terminal_placeholders = ",".join("?" for _ in terminal_states)
        abandoned_placeholders = ",".join("?" for _ in abandoned_states)
        with self.database.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT wt.*, repo.original_workspace_root, repo.repository_root,
                       repo.workspace_relative_path, repo.state AS repository_state,
                       repo.head_commit, repo.default_branch, repo.initialized_by_v8os,
                       repo.metadata_json
                FROM engineering_worktrees wt
                JOIN managed_git_repositories repo ON repo.repository_id = wt.repository_id
                WHERE (
                    (wt.state IN ({terminal_placeholders}) AND wt.updated_at < ?)
                    OR (
                        wt.state IN ({abandoned_placeholders})
                        AND wt.updated_at < ?
                        AND NOT EXISTS (
                            SELECT 1 FROM sandbox_execution_leases lease
                            WHERE lease.worktree_id = wt.worktree_id
                              AND lease.state IN ('active', 'finalizing')
                        )
                    )
                )
                ORDER BY wt.updated_at ASC LIMIT ?
                """,
                (
                    *terminal_states,
                    terminal_cutoff,
                    *abandoned_states,
                    abandoned_cutoff,
                    max(1, int(limit)),
                ),
            ).fetchall()
        accepted_states = {"delivered", "integrated", "merged_to_parent"}
        accepted_rows = [row for row in rows if str(row["state"] or "").strip() in accepted_states]
        discarded_rows = [row for row in rows if str(row["state"] or "").strip() not in accepted_states]
        accepted_cleanup = self._cleanup_worktree_rows(accepted_rows, preserve_recovery_refs=True)
        discarded_cleanup = self._cleanup_worktree_rows(discarded_rows, preserve_recovery_refs=False)
        cleanup = {
            "removed": int(accepted_cleanup.get("removed") or 0)
            + int(discarded_cleanup.get("removed") or 0),
            "removedPolicyFiles": int(accepted_cleanup.get("removedPolicyFiles") or 0)
            + int(discarded_cleanup.get("removedPolicyFiles") or 0),
            "preservedRefs": list(accepted_cleanup.get("preservedRefs") or []),
            "failures": [
                *(accepted_cleanup.get("failures") or []),
                *(discarded_cleanup.get("failures") or []),
            ][:20],
            "discarded": int(discarded_cleanup.get("removed") or 0),
        }
        stale_index_files = 0
        stale_index_cutoff = (_utc_now() - timedelta(days=1)).timestamp()
        for index_file in self.git.index_root.glob("*.index"):
            try:
                if index_file.is_file() and index_file.stat().st_mtime < stale_index_cutoff:
                    index_file.unlink(missing_ok=True)
                    stale_index_files += 1
            except OSError:
                continue
        return {
            **cleanup,
            "removedStaleIndexes": stale_index_files,
            "terminalRetentionDays": max(0.0, float(older_than_days)),
            "abandonedRetentionDays": max(0.0, float(abandoned_after_days)),
        }

    @staticmethod
    def _repository_write_set(topology: WorkspaceTopology, write_set: Iterable[str]) -> tuple[str, ...]:
        relative_root = str(topology.workspace_relative_path or ".").replace("\\", "/").strip("/")
        values: list[str] = []
        for raw in write_set:
            relative = str(raw or "").replace("\\", "/").lstrip("./")
            if not relative:
                continue
            value = relative if relative_root in {"", "."} else f"{relative_root}/{relative}"
            if value not in values:
                values.append(value)
        return tuple(values)

    @staticmethod
    def _assert_resume_contract(
        *,
        existing_row: Any,
        existing_policy: SandboxPolicy,
        run_id: str,
        delegation_id: str | None,
        parent_worktree_id: str | None,
        write_set: Iterable[str],
        actor_role: str,
        runtime_kind: str,
        execution_mode: str,
        network_profile: SandboxNetworkProfile,
    ) -> None:
        requested_policy = SandboxPolicy.from_dict(
            {
                **existing_policy.as_dict(),
                "write_set": tuple(write_set),
                "actor_role": actor_role,
                "runtime_kind": runtime_kind,
                "execution_mode": execution_mode,
                "network_profile": network_profile.value,
            }
        )
        comparisons: dict[str, tuple[Any, Any]] = {
            "runId": (str(existing_row["run_id"] or ""), str(run_id or "")),
            "parentWorktreeId": (
                str(existing_row["parent_worktree_id"] or ""),
                str(parent_worktree_id or ""),
            ),
            "actorRole": (existing_policy.actor_role, requested_policy.actor_role),
            "runtimeKind": (existing_policy.runtime_kind, requested_policy.runtime_kind),
            "executionMode": (existing_policy.execution_mode, requested_policy.execution_mode),
            "networkProfile": (
                existing_policy.network_profile.value,
                requested_policy.network_profile.value,
            ),
            "writeSet": (existing_policy.write_set, requested_policy.write_set),
        }
        existing_delegation = str(existing_row["delegation_id"] or "")
        requested_delegation = str(delegation_id or "")
        if existing_delegation and requested_delegation:
            comparisons["delegationId"] = (existing_delegation, requested_delegation)
        mismatches = {
            key: {"existing": current, "requested": requested}
            for key, (current, requested) in comparisons.items()
            if current != requested
        }
        if mismatches:
            raise ManagedGitError(
                "managed_worktree_resume_contract_mismatch",
                "A persisted worktree cannot be resumed with a different sandbox contract.",
                details={"mismatches": mismatches},
            )

    @staticmethod
    def _parse_datetime(value: Any) -> datetime | None:
        token = str(value or "").strip()
        if not token:
            return None
        try:
            parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)

    def _mark_change_sets_integrated(
        self,
        change_sets: Iterable[GitChangeSetRef],
        *,
        integration_id: str,
    ) -> None:
        ids = [item.worktree_id for item in change_sets if str(item.worktree_id or "").strip()]
        if not ids:
            return
        now = _utc_now_iso()

        def _write() -> None:
            with self.database.get_connection() as conn:
                for worktree_id in ids:
                    conn.execute(
                        """
                        UPDATE engineering_worktrees
                        SET state = 'integrated', error_code = NULL, updated_at = ?
                        WHERE worktree_id = ? AND state IN ('candidate', 'ready', 'active')
                        """,
                        (now, worktree_id),
                    )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def _persist_repository(self, repository: ManagedRepository, *, project_id: str | None) -> None:
        now = _utc_now_iso()

        def _write() -> None:
            with self.database.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO managed_git_repositories (
                        repository_id, project_id, original_workspace_root, repository_root,
                        workspace_relative_path, state, head_commit, default_branch,
                        initialized_by_v8os, metadata_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(repository_id) DO UPDATE SET
                        project_id = excluded.project_id,
                        original_workspace_root = excluded.original_workspace_root,
                        repository_root = excluded.repository_root,
                        workspace_relative_path = excluded.workspace_relative_path,
                        state = excluded.state,
                        head_commit = excluded.head_commit,
                        default_branch = excluded.default_branch,
                        initialized_by_v8os = excluded.initialized_by_v8os,
                        metadata_json = excluded.metadata_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        repository.repository_id,
                        project_id,
                        repository.topology.original_workspace_root,
                        repository.topology.repository_root,
                        repository.topology.workspace_relative_path,
                        repository.state,
                        repository.head_commit,
                        repository.default_branch,
                        int(repository.initialized_by_v8os),
                        _json({"warnings": list(repository.warnings)}),
                        now,
                        now,
                    ),
                )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def _persist_worktree(
        self,
        worktree: ManagedWorktree,
        *,
        session_id: str | None,
        run_id: str,
        delegation_id: str | None,
        parent_worktree_id: str | None,
        worktree_kind: str = "task",
    ) -> None:
        now = _utc_now_iso()

        def _write() -> None:
            with self.database.get_connection() as conn:
                repository = conn.execute(
                    "SELECT * FROM managed_git_repositories WHERE repository_id = ?",
                    (worktree.repository_id,),
                ).fetchone()
                if repository is None:
                    raise RuntimeError("managed_repository_not_persisted")
                conn.execute(
                    """
                    INSERT INTO engineering_worktrees (
                        worktree_id, repository_id, session_id, run_id, delegation_id,
                        parent_worktree_id, worktree_kind, branch_name, base_commit, worktree_root,
                        worktree_workspace_root, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        worktree.worktree_id,
                        worktree.repository_id,
                        session_id,
                        run_id,
                        delegation_id,
                        parent_worktree_id,
                        worktree_kind,
                        worktree.branch_name,
                        worktree.base_commit,
                        worktree.topology.worktree_root,
                        worktree.topology.worktree_workspace_root,
                        worktree.state,
                        now,
                        now,
                    ),
                )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def _persist_lease(
        self,
        lease: SandboxLease,
        *,
        session_id: str | None,
        run_id: str,
        delegation_id: str | None,
    ) -> None:
        now = _utc_now_iso()

        def _write() -> None:
            with self.database.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO sandbox_execution_leases (
                        lease_id, policy_id, policy_digest, write_set_digest, repository_id,
                        worktree_id, session_id, run_id, delegation_id, actor_role,
                        runtime_kind, execution_mode, network_profile, state,
                        capabilities_json, policy_json, created_at, activated_at,
                        expires_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        lease.lease_id,
                        lease.policy.policy_id,
                        lease.policy.digest,
                        lease.policy.write_set_digest,
                        lease.policy.repository_id,
                        lease.policy.worktree_id,
                        session_id,
                        run_id,
                        delegation_id,
                        lease.policy.actor_role,
                        lease.policy.runtime_kind,
                        lease.policy.execution_mode,
                        lease.policy.network_profile.value,
                        lease.state.value,
                        _json(lease.capabilities.as_dict()),
                        _json(lease.policy.as_dict()),
                        lease.created_at,
                        lease.activated_at,
                        lease.expires_at,
                        now,
                    ),
                )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def _update_worktree_state(self, worktree_id: str, state: str, *, error_code: str | None = None) -> None:
        now = _utc_now_iso()

        def _write() -> None:
            with self.database.get_connection() as conn:
                conn.execute(
                    "UPDATE engineering_worktrees SET state = ?, error_code = ?, updated_at = ? WHERE worktree_id = ?",
                    (state, error_code, now, worktree_id),
                )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def _complete_worktree(
        self,
        worktree_id: str,
        change_set: GitChangeSetRef,
        *,
        state: str = "candidate",
    ) -> None:
        now = _utc_now_iso()

        def _write() -> None:
            with self.database.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE engineering_worktrees
                    SET state = ?, change_set_json = ?, error_code = NULL,
                        updated_at = ?, finished_at = ?
                    WHERE worktree_id = ?
                    """,
                    (state, _json(change_set.as_dict()), now, now, worktree_id),
                )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def _update_lease_state(
        self,
        lease_id: str,
        state: SandboxLeaseState,
        *,
        error_code: str | None = None,
    ) -> None:
        now = _utc_now_iso()
        finished_at = now if state in {SandboxLeaseState.COMPLETED, SandboxLeaseState.FAILED, SandboxLeaseState.BLOCKED} else None

        def _write() -> None:
            with self.database.get_connection() as conn:
                conn.execute(
                    """
                    UPDATE sandbox_execution_leases
                    SET state = ?, error_code = ?, updated_at = ?,
                        finished_at = COALESCE(?, finished_at)
                    WHERE lease_id = ?
                    """,
                    (state.value, error_code, now, finished_at, lease_id),
                )
                conn.commit()

        self.database._run_write_with_retry(_write)

    def _get_worktree_row(self, worktree_id: str):
        with self.database.get_connection() as conn:
            return conn.execute(
                """
                SELECT wt.*, repo.original_workspace_root, repo.repository_root,
                       repo.workspace_relative_path
                FROM engineering_worktrees wt
                JOIN managed_git_repositories repo ON repo.repository_id = wt.repository_id
                WHERE wt.worktree_id = ?
                """,
                (worktree_id,),
            ).fetchone()

    @staticmethod
    def _topology_from_worktree_row(row: Any) -> WorkspaceTopology:
        return WorkspaceTopology(
            original_workspace_root=str(row["original_workspace_root"]),
            repository_root=str(row["repository_root"]),
            workspace_relative_path=str(row["workspace_relative_path"] or "."),
            worktree_root=str(row["worktree_root"]),
            worktree_workspace_root=str(row["worktree_workspace_root"]),
        )

    def _get_repository(self, repository_id: str) -> ManagedRepository | None:
        with self.database.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM managed_git_repositories WHERE repository_id = ?",
                (repository_id,),
            ).fetchone()
        if row is None:
            return None
        topology = WorkspaceTopology(
            original_workspace_root=str(row["original_workspace_root"]),
            repository_root=str(row["repository_root"]),
            workspace_relative_path=str(row["workspace_relative_path"] or "."),
        )
        metadata = json.loads(str(row["metadata_json"] or "{}"))
        return ManagedRepository(
            repository_id=str(row["repository_id"]),
            topology=topology,
            state=str(row["state"]),
            head_commit=str(row["head_commit"] or "") or None,
            default_branch=str(row["default_branch"] or "") or None,
            initialized_by_v8os=bool(row["initialized_by_v8os"]),
            warnings=tuple(metadata.get("warnings") or ()),
        )

    def _get_latest_run_integration(self, run_id: str):
        with self.database.get_connection() as conn:
            return conn.execute(
                """
                SELECT wt.*, repo.original_workspace_root, repo.repository_root,
                       repo.workspace_relative_path
                FROM engineering_worktrees wt
                JOIN managed_git_repositories repo ON repo.repository_id = wt.repository_id
                WHERE wt.run_id = ? AND wt.worktree_kind IN ('integration', 'supervisor_integration')
                  AND wt.state IN ('integration_candidate', 'delivered', 'cleaned', 'retry_requested', 'ignored')
                ORDER BY wt.created_at DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()

    def _get_active_lease_for_worktree(self, worktree_id: str):
        with self.database.get_connection() as conn:
            return conn.execute(
                """
                SELECT * FROM sandbox_execution_leases
                WHERE worktree_id = ? AND state IN ('active', 'finalizing')
                ORDER BY created_at DESC LIMIT 1
                """,
                (worktree_id,),
            ).fetchone()

    def _get_latest_lease_for_worktree(self, worktree_id: str):
        with self.database.get_connection() as conn:
            return conn.execute(
                """
                SELECT * FROM sandbox_execution_leases
                WHERE worktree_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (worktree_id,),
            ).fetchone()

    def _lease_ids_for_worktree(self, worktree_id: str) -> tuple[str, ...]:
        with self.database.get_connection() as conn:
            rows = conn.execute(
                "SELECT lease_id FROM sandbox_execution_leases WHERE worktree_id = ?",
                (worktree_id,),
            ).fetchall()
        return tuple(str(row["lease_id"]) for row in rows if str(row["lease_id"] or "").strip())


_service: EngineeringSandboxService | None = None
_service_lock = threading.Lock()


def get_engineering_sandbox_service() -> EngineeringSandboxService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = EngineeringSandboxService()
    return _service
