from pathlib import Path

from scripts.audit_runtime_surface_residue import managed_cli_skill_is_pinned, scan_runtime_surface_residue


def test_managed_cli_pin_contract_follows_the_install_transport(tmp_path: Path) -> None:
    npm_profile = {
        "ownership": "managed",
        "install": {"argv": ["npm", "install", "--prefix", "{pluginRoot}", "@scope/tool@1.2.3"]},
    }
    custom_profile = {
        "ownership": "managed",
        "install": {"argv": ["{enginePython}", "{engineRoot}/runtimes/plugin_manager/gda_installer.py", "install", "--plugin-root", "{pluginRoot}"]},
    }

    assert managed_cli_skill_is_pinned({"revision": "1.2.3"}, npm_profile)
    assert not managed_cli_skill_is_pinned({"revision": "1.2.4"}, npm_profile)
    assert managed_cli_skill_is_pinned({"revision": "a" * 40}, custom_profile)
    assert not managed_cli_skill_is_pinned({"revision": "latest"}, custom_profile)
    assert not managed_cli_skill_is_pinned({"revision": "a" * 40}, {**custom_profile, "ownership": "external"})
    assert not managed_cli_skill_is_pinned({"revision": "a" * 40}, {"ownership": "managed"})
    assert not managed_cli_skill_is_pinned({"revision": "a" * 40}, {
        "ownership": "managed", "install": {"argv": ["uv", "tool", "install", "example-tool@latest"]},
    })
    assert not managed_cli_skill_is_pinned({"revision": "a" * 40}, custom_profile, repo_root=tmp_path)
    installer = tmp_path / "apps/v8-agent-os-engine/runtimes/plugin_manager/gda_installer.py"
    installer.parent.mkdir(parents=True)
    installer.write_text('GDA_PACKAGE = "gda==latest"\n', encoding="utf-8")
    assert not managed_cli_skill_is_pinned({"revision": "a" * 40}, custom_profile, repo_root=tmp_path)
    installer.write_text('GDA_PACKAGE = "gda==0.8.1"\n', encoding="utf-8")
    assert managed_cli_skill_is_pinned({"revision": "a" * 40}, custom_profile, repo_root=tmp_path)


def test_runtime_surface_residual_audit_is_clean() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    assert scan_runtime_surface_residue(repo_root) == []
