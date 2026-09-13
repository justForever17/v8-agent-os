from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Iterable


ENGINE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TEST_ROOT = ENGINE_ROOT / "tests"


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
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.shard_index < 0 or args.shard_index >= args.shard_count:
        raise SystemExit("shard-index must be between 0 and shard-count - 1")
    test_files = discover_test_files(args.test_root.resolve())
    if not test_files:
        raise SystemExit(f"no test_*.py files found below {args.test_root}")
    selected = partition_test_files(test_files, args.shard_count)[args.shard_index]
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
            print(path.as_posix())
        return 0
    command = [sys.executable, "-m", "pytest", *(str(path) for path in targets), "-q", "--tb=short"]
    return subprocess.call(command, cwd=ENGINE_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
