from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


class SandboxContractError(ValueError):
    pass


class SandboxEnforcementLevel(str, Enum):
    ENFORCED = "enforced"
    PARTIAL = "partial"
    GUARDED = "guarded"
    UNAVAILABLE = "unavailable"


class SandboxNetworkProfile(str, Enum):
    OFFLINE_ENFORCED = "offline_enforced"
    BROKERED = "brokered"
    NETWORKED_PARTIAL = "networked_partial"


class SandboxLeaseState(str, Enum):
    PLANNED = "planned"
    ACTIVE = "active"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    EXPIRED = "expired"


DEFAULT_ENV_ALLOWLIST = (
    "ALLUSERSPROFILE",
    "APPDATA",
    "CARGO_HOME",
    "CI",
    "COLORTERM",
    "COMSPEC",
    "DOTNET_ROOT",
    "FORCE_COLOR",
    "GOPATH",
    "GOROOT",
    "GRADLE_USER_HOME",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "INCLUDE",
    "JAVA_HOME",
    "LIB",
    "LIBPATH",
    "LOCALAPPDATA",
    "NO_COLOR",
    "NUGET_PACKAGES",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PSMODULEPATH",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "RUSTUP_HOME",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TERM_PROGRAM",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
)


def _resolved_path(value: str | Path) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise SandboxContractError("sandbox_path_required")
    return str(Path(raw).expanduser().resolve(strict=False))


def _normalize_relative_paths(values: Iterable[str | Path]) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw_value in values:
        raw = str(raw_value or "").strip().replace("\\", "/")
        if not raw or raw == ".":
            continue
        path = Path(raw)
        if path.is_absolute() or raw.startswith(("../", "/")) or "/../" in f"/{raw}/":
            raise SandboxContractError(f"sandbox_write_path_must_be_relative:{raw}")
        clean = path.as_posix().lstrip("./")
        if clean and clean not in normalized:
            normalized.append(clean)
    return tuple(normalized)


@dataclass(frozen=True)
class SandboxResourceLimits:
    wall_time_seconds: int = 900
    memory_bytes: int = 2 * 1024**3
    process_count: int = 64
    output_bytes: int = 16 * 1024**2

    def __post_init__(self) -> None:
        if not 1 <= int(self.wall_time_seconds) <= 86_400:
            raise SandboxContractError("sandbox_wall_time_out_of_range")
        if not 64 * 1024**2 <= int(self.memory_bytes) <= 64 * 1024**3:
            raise SandboxContractError("sandbox_memory_limit_out_of_range")
        if not 1 <= int(self.process_count) <= 1024:
            raise SandboxContractError("sandbox_process_limit_out_of_range")
        if not 64 * 1024 <= int(self.output_bytes) <= 1024**3:
            raise SandboxContractError("sandbox_output_limit_out_of_range")

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "SandboxResourceLimits":
        value = dict(payload or {})
        return cls(
            wall_time_seconds=int(value.get("wall_time_seconds") or value.get("wallTimeSeconds") or 900),
            memory_bytes=int(value.get("memory_bytes") or value.get("memoryBytes") or 2 * 1024**3),
            process_count=int(value.get("process_count") or value.get("processCount") or 64),
            output_bytes=int(value.get("output_bytes") or value.get("outputBytes") or 16 * 1024**2),
        )


@dataclass(frozen=True)
class SandboxCapabilities:
    platform: str
    architecture: str
    driver: str
    enforcement_level: SandboxEnforcementLevel
    process_tree_containment: bool
    resource_limits: bool
    filesystem_boundary: bool
    supported_network_profiles: tuple[SandboxNetworkProfile, ...] = field(default_factory=tuple)
    helper_path: str | None = None
    reason: str | None = None

    def supports(self, profile: SandboxNetworkProfile) -> bool:
        return profile in self.supported_network_profiles

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["enforcement_level"] = self.enforcement_level.value
        payload["supported_network_profiles"] = [item.value for item in self.supported_network_profiles]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "SandboxCapabilities":
        value = dict(payload or {})
        return cls(
            platform=str(value.get("platform") or "unknown"),
            architecture=str(value.get("architecture") or "unknown"),
            driver=str(value.get("driver") or "unknown"),
            enforcement_level=SandboxEnforcementLevel(
                value.get("enforcement_level")
                or value.get("enforcementLevel")
                or SandboxEnforcementLevel.UNAVAILABLE.value
            ),
            process_tree_containment=bool(
                value.get("process_tree_containment") or value.get("processTreeContainment")
            ),
            resource_limits=bool(value.get("resource_limits") or value.get("resourceLimits")),
            filesystem_boundary=bool(value.get("filesystem_boundary") or value.get("filesystemBoundary")),
            supported_network_profiles=tuple(
                SandboxNetworkProfile(item)
                for item in list(
                    value.get("supported_network_profiles")
                    or value.get("supportedNetworkProfiles")
                    or []
                )
            ),
            helper_path=str(value.get("helper_path") or value.get("helperPath") or "").strip() or None,
            reason=str(value.get("reason") or "").strip() or None,
        )


@dataclass(frozen=True)
class SandboxPolicy:
    policy_id: str
    lease_id: str
    repository_id: str
    worktree_id: str
    worktree_root: str
    original_workspace_root: str
    base_commit: str
    execution_mode: str
    actor_role: str
    runtime_kind: str
    network_profile: SandboxNetworkProfile = SandboxNetworkProfile.NETWORKED_PARTIAL
    write_set: tuple[str, ...] = field(default_factory=tuple)
    env_allowlist: tuple[str, ...] = DEFAULT_ENV_ALLOWLIST
    env_overrides: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    limits: SandboxResourceLimits = field(default_factory=SandboxResourceLimits)
    version: int = 1

    def __post_init__(self) -> None:
        for field_name in ("policy_id", "lease_id", "repository_id", "worktree_id", "base_commit"):
            if not str(getattr(self, field_name) or "").strip():
                raise SandboxContractError(f"sandbox_{field_name}_required")
        mode = str(self.execution_mode or "").strip().lower()
        if mode not in {"read", "write"}:
            raise SandboxContractError("sandbox_execution_mode_invalid")
        object.__setattr__(self, "execution_mode", mode)
        object.__setattr__(self, "worktree_root", _resolved_path(self.worktree_root))
        object.__setattr__(self, "original_workspace_root", _resolved_path(self.original_workspace_root))
        normalized_write_set = _normalize_relative_paths(self.write_set)
        if mode == "write" and not normalized_write_set:
            raise SandboxContractError("sandbox_write_set_required")
        object.__setattr__(self, "write_set", normalized_write_set)
        normalized_allowlist = tuple(
            dict.fromkeys(str(item or "").strip().upper() for item in self.env_allowlist if str(item or "").strip())
        )
        object.__setattr__(self, "env_allowlist", normalized_allowlist)
        normalized_overrides: list[tuple[str, str]] = []
        for key, value in self.env_overrides:
            normalized_key = str(key or "").strip().upper()
            if not normalized_key or "=" in normalized_key or "\x00" in normalized_key:
                raise SandboxContractError("sandbox_env_override_key_invalid")
            normalized_overrides.append((normalized_key, str(value or "")))
        object.__setattr__(self, "env_overrides", tuple(normalized_overrides))

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["network_profile"] = self.network_profile.value
        return payload

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.as_dict(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def write_set_digest(self) -> str:
        return hashlib.sha256("\n".join(self.write_set).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SandboxPolicy":
        value = dict(payload or {})
        raw_overrides = value.get("env_overrides") or value.get("envOverrides") or []
        overrides = (
            tuple((str(key), str(item)) for key, item in raw_overrides.items())
            if isinstance(raw_overrides, dict)
            else tuple(tuple(item) for item in raw_overrides)
        )
        return cls(
            policy_id=str(value.get("policy_id") or value.get("policyId") or ""),
            lease_id=str(value.get("lease_id") or value.get("leaseId") or ""),
            repository_id=str(value.get("repository_id") or value.get("repositoryId") or ""),
            worktree_id=str(value.get("worktree_id") or value.get("worktreeId") or ""),
            worktree_root=str(value.get("worktree_root") or value.get("worktreeRoot") or ""),
            original_workspace_root=str(
                value.get("original_workspace_root") or value.get("originalWorkspaceRoot") or ""
            ),
            base_commit=str(value.get("base_commit") or value.get("baseCommit") or ""),
            execution_mode=str(value.get("execution_mode") or value.get("executionMode") or ""),
            actor_role=str(value.get("actor_role") or value.get("actorRole") or ""),
            runtime_kind=str(value.get("runtime_kind") or value.get("runtimeKind") or ""),
            network_profile=SandboxNetworkProfile(
                value.get("network_profile") or value.get("networkProfile") or SandboxNetworkProfile.NETWORKED_PARTIAL.value
            ),
            write_set=tuple(value.get("write_set") or value.get("writeSet") or ()),
            env_allowlist=tuple(value.get("env_allowlist") or value.get("envAllowlist") or DEFAULT_ENV_ALLOWLIST),
            env_overrides=overrides,
            limits=SandboxResourceLimits.from_dict(value.get("limits")),
            version=int(value.get("version") or 1),
        )


@dataclass(frozen=True)
class SandboxLease:
    lease_id: str
    policy: SandboxPolicy
    state: SandboxLeaseState
    capabilities: SandboxCapabilities
    created_at: str
    expires_at: str | None = None
    activated_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "leaseId": self.lease_id,
            "policy": self.policy.as_dict(),
            "policyDigest": self.policy.digest,
            "writeSetDigest": self.policy.write_set_digest,
            "state": self.state.value,
            "capabilities": self.capabilities.as_dict(),
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "activatedAt": self.activated_at,
            "finishedAt": self.finished_at,
            "errorCode": self.error_code,
        }


@dataclass(frozen=True)
class GitChangeSetRef:
    repository_id: str
    worktree_id: str
    branch_name: str
    base_commit: str
    commit_id: str
    changed_paths: tuple[str, ...]
    insertions: int = 0
    deletions: int = 0
    status: str = "candidate"

    def as_dict(self) -> dict[str, Any]:
        return {
            "repositoryId": self.repository_id,
            "worktreeId": self.worktree_id,
            "branchName": self.branch_name,
            "baseCommit": self.base_commit,
            "commitId": self.commit_id,
            "changedPaths": list(self.changed_paths),
            "insertions": int(self.insertions),
            "deletions": int(self.deletions),
            "status": self.status,
        }
