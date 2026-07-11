from __future__ import annotations

from pathlib import Path

from scripts.audit_removed_openclaw import scan_removed_openclaw_residue


def test_active_product_surfaces_have_no_removed_openclaw_residue() -> None:
    repo_root = Path(__file__).resolve().parents[4]

    assert scan_removed_openclaw_residue(repo_root) == []


def test_openapi_has_no_removed_plugin_host_routes() -> None:
    from main import app

    paths = [str(path).lower() for path in app.openapi().get("paths", {})]
    assert not [path for path in paths if "openclaw" in path or "plugin-host" in path or "plugin_host" in path]
