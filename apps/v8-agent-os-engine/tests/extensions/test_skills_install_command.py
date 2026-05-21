from __future__ import annotations

import pytest

from core import skills_install_service as service
from core.skills_install_service import parse_skill_install_command


def test_parse_skill_install_command_adds_global_flag() -> None:
    parsed = parse_skill_install_command("npx skills add signerlabs/ShipSwift")

    assert parsed.source == "signerlabs/ShipSwift"
    assert parsed.global_install is True
    assert parsed.global_flag_added is True
    assert parsed.normalized_command == "npx skills add signerlabs/ShipSwift -g"


def test_parse_skill_install_command_accepts_explicit_global_and_skill_alias() -> None:
    parsed = parse_skill_install_command("npx --yes skills add -g -s add-component signerlabs/ShipSwift")

    assert parsed.source == "signerlabs/ShipSwift"
    assert parsed.skill_name == "add-component"
    assert parsed.global_flag_added is False
    assert parsed.yes is True
    assert parsed.normalized_command == "npx --yes skills add signerlabs/ShipSwift -g --skill add-component"


def test_parse_skill_install_command_rejects_project_scope() -> None:
    with pytest.raises(ValueError, match="不支持项目级"):
        parse_skill_install_command("npx skills add signerlabs/ShipSwift --project")


def test_parse_skill_install_command_rejects_agent_target() -> None:
    with pytest.raises(ValueError, match="不支持指定 `--agent`"):
        parse_skill_install_command("npx skills add signerlabs/ShipSwift --agent codex")


def test_install_skill_from_command_reports_normalized_global_command(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    manifest = service.SkillManifest(
        folder="add-component",
        name="add-component",
        description="demo",
        source_dir=tmp_path,
    )

    monkeypatch.setattr(service, "_resolve_source_tree", lambda source, workspace: tmp_path)
    monkeypatch.setattr(service, "_discover_skill_manifests", lambda root: [manifest])
    monkeypatch.setattr(
        service,
        "_install_manifests",
        lambda manifests, source, overwrite: {
            "status": "success",
            "source": source,
            "targetRoot": "~/.agents/skills",
            "installed": [],
            "skipped": [],
            "conflicts": [],
            "warnings": [],
        },
    )

    result = service.install_skill_from_command("npx skills add signerlabs/ShipSwift")

    assert result["normalizedCommand"] == "npx skills add signerlabs/ShipSwift -g"
    assert result["warnings"] == ["未检测到 `-g/--global`，已自动按全局安装写入 `~/.agents/skills`。"]
