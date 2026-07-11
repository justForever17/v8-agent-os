from pathlib import Path

from scripts.audit_runtime_surface_residue import scan_runtime_surface_residue


def test_runtime_surface_residual_audit_is_clean() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    assert scan_runtime_surface_residue(repo_root) == []
