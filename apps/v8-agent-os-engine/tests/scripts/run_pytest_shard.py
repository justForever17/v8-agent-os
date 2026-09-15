from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable


ENGINE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_ROOT = ENGINE_ROOT / "tests"

# These contracts require a matching host capability or an installed external
# fixture.  Ubuntu CI cannot produce evidence for them; they remain in the
# normal local shard and are covered by the Windows/isolated live gates.
PORTABLE_CI_EXCLUDES = {
    "tests/runtime_core/test_windows_uia_selector_recovery.py",
    "tests/runtime_core/test_command_process_launch.py",
    "tests/runtime_core/test_engineering_sandbox.py",
    "tests/runtime_core/test_computer_use_shortcut_registry.py",
    "tests/safety/test_windows_profile_protection.py",
    "tests/safety/test_native_workspace_approval.py",
    "tests/safety/test_safety_guardian_workspace_commands.py",
    "tests/plugin_manager/test_plugin_manager_runtime.py",
    "tests/model_control/test_model_protocol_registry.py",
    "tests/memory/test_memory_visual_enrichment.py",
    "tests/workspace_artifacts/test_scoped_workspace_resource.py",
}

PORTABLE_CI_DESELECTS = {
    "tests/runtime_core/test_runtime_episode_runner.py::test_runtime_runner_extracts_missing_skill_root_from_goal_text",
    "tests/runtime_core/test_runtime_orchestration_contracts.py::test_direct_delegation_injects_upstream_handoff_content_instead_of_fake_file_paths",
    "tests/chat_runtime/test_supervisor_runtime_finalization.py::test_supervisor_engineering_context_starts_in_bound_workspace_without_worktree",
}


def _source_size(path: Path) -> int:
    # Git checkout newline conversion must not change the CI assignment.
    return len(path.read_bytes().replace(b"\r\n", b"\n"))


def discover_test_files(test_root: Path = DEFAULT_TEST_ROOT) -> list[Path]:
    return sorted(
        (path.resolve() for path in test_root.rglob("test_*.py") if path.is_file()),
        key=lambda path: path.as_posix(),
    )


def partition_test_files(test_files: Iterable[Path], shard_count: int) -> list[list[Path]]:
    if shard_count < 1:
        raise ValueError("shard_count must be at least 1")
    shards: list[list[Path]] = [[] for _ in range(shard_count)]
    weights = [0] * shard_count
    files = [Path(path).resolve() for path in test_files]
    sizes = {path: _source_size(path) for path in files}
    for path in sorted(files, key=lambda item: (-sizes[item], item.as_posix())):
        shard_index = min(range(shard_count), key=lambda index: (weights[index], index))
        shards[shard_index].append(path)
        weights[shard_index] += sizes[path]
    for shard in shards:
        shard.sort(key=lambda path: path.as_posix())
    return shards


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one deterministic, exhaustive Engine pytest file shard.")
    parser.add_argument("--shard-index", type=int, required=True, help="Zero-based shard index.")
    parser.add_argument("--shard-count", type=int, required=True, help="Total number of shards.")
    parser.add_argument("--test-root", type=Path, default=DEFAULT_TEST_ROOT)
    parser.add_argument("--list-only", action="store_true", help="Print the selected files without running pytest.")
    parser.add_argument("--portable-ci", action="store_true",
                        help="Exclude tests requiring a non-Ubuntu host or external installed fixture; report them as pending.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("shard-index must be between 0 and shard-count - 1")
    test_files = discover_test_files(args.test_root.resolve())
    if not test_files:
        raise SystemExit(f"no test_*.py files found below {args.test_root}")
    selected = partition_test_files(test_files, args.shard_count)[args.shard_index]
    if args.portable_ci:
        def relative(path: Path) -> str:
            return path.relative_to(ENGINE_ROOT).as_posix()
        excluded = [path for path in selected if relative(path) in PORTABLE_CI_EXCLUDES]
        selected = [path for path in selected if relative(path) not in PORTABLE_CI_EXCLUDES]
        print(f"Portable CI pending files: {len(excluded)} (host/external capability contracts)", flush=True)
    if not selected:
        raise SystemExit(f"pytest shard {args.shard_index} is empty")
    targets = [path.relative_to(ENGINE_ROOT) if path.is_relative_to(ENGINE_ROOT) else path for path in selected]
    total_bytes = sum(_source_size(path) for path in selected)
    print(
        f"Engine pytest shard {args.shard_index + 1}/{args.shard_count}: "
        f"{len(selected)} files, {total_bytes} source bytes",
        flush=True,
    )
    if args.list_only:
        for path in targets:
            # Keep list-only output decodable under Windows' legacy console
            # code page when a temporary test root contains non-ASCII text.
            print(path.as_posix().encode("ascii", "backslashreplace").decode("ascii"))
        return 0
    command = [sys.executable, "-m", "pytest", *(str(path) for path in targets), "-q", "--tb=short"]
    if args.portable_ci:
        command.extend(["--deselect", *PORTABLE_CI_DESELECTS])
    return subprocess.call(command, cwd=ENGINE_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
