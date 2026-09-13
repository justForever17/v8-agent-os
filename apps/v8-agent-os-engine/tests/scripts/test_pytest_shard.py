from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from tests.scripts import run_pytest_shard
from tests.scripts.run_pytest_shard import discover_test_files, partition_test_files


def test_pytest_file_shards_are_deterministic_disjoint_and_exhaustive(tmp_path: Path) -> None:
    files: list[Path] = []
    for index, size in enumerate((40, 30, 20, 10, 5, 3, 2)):
        path = tmp_path / f"test_{index}.py"
        path.write_text("x" * size, encoding="utf-8")
        files.append(path.resolve())

    first = partition_test_files(files, 3)
    second = partition_test_files(reversed(files), 3)
    flattened = [path for shard in first for path in shard]

    assert first == second
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == set(files)
    assert all(first)


def test_pytest_file_discovery_includes_every_default_test_module() -> None:
    files = discover_test_files()

    assert files
    assert all(path.name.startswith("test_") and path.suffix == ".py" for path in files)
    assert Path(__file__).resolve() in files


def test_shard_assignment_is_invariant_to_checkout_newlines(tmp_path: Path) -> None:
    files = [tmp_path / f"test_{index}.py" for index in range(7)]
    content = b"# line\n" * 12
    for path in files:
        path.write_bytes(content)
    expected = partition_test_files(files, 3)

    for path in files[::2]:
        path.write_bytes(content.replace(b"\n", b"\r\n"))

    assert partition_test_files(files, 3) == expected


@pytest.mark.parametrize("succeeds", [True, False])
def test_shard_cli_runs_custom_test_root_and_preserves_failure(tmp_path: Path, succeeds: bool) -> None:
    marker = tmp_path / "executed.txt"
    test_file = tmp_path / "test_shard_fixture.py"
    test_file.write_text(
        "from pathlib import Path\n"
        "def test_fixture():\n"
        f"    Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        f"    assert {succeeds!r}\n",
        encoding="utf-8",
    )
    command = [sys.executable, str(Path(run_pytest_shard.__file__)),
               "--test-root", str(tmp_path), "--shard-index", "0", "--shard-count", "1"]
    listing = subprocess.run([*command, "--list-only"], capture_output=True, text=True, timeout=30)
    assert listing.returncode == 0, listing.stdout + listing.stderr
    assert "test_shard_fixture.py" in listing.stdout
    assert not marker.exists()

    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == (0 if succeeds else 1), result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8") == "executed"
