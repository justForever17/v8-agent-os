from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import sys
from typing import Iterable, Mapping

from .contracts import (
    SandboxCapabilities,
    SandboxEnforcementLevel,
    SandboxNetworkProfile,
    SandboxPolicy,
)


SANDBOX_HOST_ENV = "V8_SANDBOX_HOST"


def _host_filename() -> str:
    return "v8-sandbox-host.exe" if os.name == "nt" else "v8-sandbox-host"


def _candidate_helper_paths() -> list[Path]:
    explicit = str(os.environ.get(SANDBOX_HOST_ENV) or "").strip()
    engine_root = Path(__file__).resolve().parents[2]
    executable_root = Path(sys.executable).resolve().parent
    values = [
        *(Path(explicit).expanduser() for _ in [0] if explicit),
        engine_root / "bin" / _host_filename(),
        engine_root / "native" / "v8-sandbox-host" / "target" / "release" / _host_filename(),
        executable_root / _host_filename(),
        executable_root.parent / "resources" / "engine" / "bin" / _host_filename(),
    ]
    resolved: list[Path] = []
    for value in values:
        candidate = value.resolve(strict=False)
        if candidate not in resolved:
            resolved.append(candidate)
    return resolved


def locate_sandbox_host() -> Path | None:
    for candidate in _candidate_helper_paths():
        if candidate.is_file():
            return candidate
    discovered = shutil.which(_host_filename())
    return Path(discovered).resolve() if discovered else None


def probe_sandbox_capabilities() -> SandboxCapabilities:
    helper = locate_sandbox_host()
    system = platform.system().lower() or sys.platform
    architecture = platform.machine().lower() or "unknown"
    if helper is None:
        return SandboxCapabilities(
            platform=system,
            architecture=architecture,
            driver="missing",
            enforcement_level=SandboxEnforcementLevel.UNAVAILABLE,
            process_tree_containment=False,
            resource_limits=False,
            filesystem_boundary=False,
            supported_network_profiles=(),
            reason="native_sandbox_host_missing",
        )
    if system == "windows":
        return SandboxCapabilities(
            platform=system,
            architecture=architecture,
            driver="windows_job_object",
            enforcement_level=SandboxEnforcementLevel.PARTIAL,
            process_tree_containment=True,
            resource_limits=True,
            filesystem_boundary=False,
            supported_network_profiles=(SandboxNetworkProfile.NETWORKED_PARTIAL,),
            helper_path=str(helper),
            reason="filesystem_and_network_boundaries_remain_governed",
        )
    if system == "linux":
        return SandboxCapabilities(
            platform=system,
            architecture=architecture,
            driver="linux_process_limits",
            enforcement_level=SandboxEnforcementLevel.PARTIAL,
            process_tree_containment=True,
            resource_limits=True,
            filesystem_boundary=False,
            supported_network_profiles=(SandboxNetworkProfile.NETWORKED_PARTIAL,),
            helper_path=str(helper),
            reason="namespace_or_broker_network_enforcement_not_enabled",
        )
    if system == "darwin":
        return SandboxCapabilities(
            platform=system,
            architecture=architecture,
            driver="macos_process_limits",
            enforcement_level=SandboxEnforcementLevel.PARTIAL,
            process_tree_containment=True,
            resource_limits=True,
            filesystem_boundary=False,
            supported_network_profiles=(SandboxNetworkProfile.NETWORKED_PARTIAL,),
            helper_path=str(helper),
            reason="seatbelt_profile_not_enabled",
        )
    return SandboxCapabilities(
        platform=system,
        architecture=architecture,
        driver="unsupported",
        enforcement_level=SandboxEnforcementLevel.UNAVAILABLE,
        process_tree_containment=False,
        resource_limits=False,
        filesystem_boundary=False,
        supported_network_profiles=(),
        helper_path=str(helper),
        reason="platform_not_supported",
    )


def build_sanitized_environment(
    policy: SandboxPolicy,
    *,
    source: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    source_env = dict(source or os.environ)
    source_by_upper = {str(key).upper(): str(value) for key, value in source_env.items()}
    environment = {
        key: source_by_upper[key]
        for key in policy.env_allowlist
        if key in source_by_upper
    }
    for key, value in policy.env_overrides:
        environment[key] = value
    for key, value in dict(extra or {}).items():
        normalized = str(key or "").strip().upper()
        if normalized in policy.env_allowlist or any(normalized == item[0] for item in policy.env_overrides):
            environment[normalized] = str(value)
    environment.update(
        {
            "V8_SANDBOX_LEASE_ID": policy.lease_id,
            "V8_SANDBOX_POLICY_DIGEST": policy.digest,
            "V8_SANDBOX_WORKTREE": policy.worktree_root,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_PAGER": "cat",
            "PAGER": "cat",
        }
    )
    return environment


def wrap_sandbox_command(
    policy: SandboxPolicy,
    argv: Iterable[str],
    *,
    policy_file: str | Path,
    capabilities: SandboxCapabilities | None = None,
) -> list[str]:
    resolved_capabilities = capabilities or probe_sandbox_capabilities()
    if resolved_capabilities.enforcement_level == SandboxEnforcementLevel.UNAVAILABLE:
        raise RuntimeError(resolved_capabilities.reason or "native_sandbox_unavailable")
    if not resolved_capabilities.supports(policy.network_profile):
        raise RuntimeError(f"sandbox_network_profile_not_enforced:{policy.network_profile.value}")
    helper = str(resolved_capabilities.helper_path or "").strip()
    if not helper:
        raise RuntimeError("native_sandbox_host_missing")
    command = [str(item) for item in argv]
    if not command or not command[0].strip():
        raise ValueError("sandbox_command_required")
    return [helper, "--policy", str(Path(policy_file).resolve(strict=False)), "--", *command]
