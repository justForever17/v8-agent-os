from __future__ import annotations

import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core import skills_install_service as service
from core.interprocess_lock import interprocess_file_lock
from core.skills_install_service import parse_skill_install_command


def test_parse_skill_install_command_adds_noninteractive_and_global_flags() -> None:
    parsed = parse_skill_install_command("npx skills add signerlabs/ShipSwift")

    assert parsed.source == "signerlabs/ShipSwift"
    assert parsed.global_install is True
    assert parsed.global_flag_added is True
    assert parsed.yes is True
    assert parsed.yes_flag_added is True
    assert parsed.normalized_command == "npx --yes skills add signerlabs/ShipSwift -g"


def test_parse_skill_install_command_accepts_explicit_global_and_skill_alias() -> None:
    parsed = parse_skill_install_command("npx --yes skills add -g -s add-component signerlabs/ShipSwift")

    assert parsed.source == "signerlabs/ShipSwift"
    assert parsed.skill_name == "add-component"
    assert parsed.global_flag_added is False
    assert parsed.yes is True
    assert parsed.yes_flag_added is False
    assert parsed.normalized_command == "npx --yes skills add signerlabs/ShipSwift -g --skill add-component"


def test_parse_skill_install_command_accepts_owner_repo_at_skill() -> None:
    parsed = parse_skill_install_command("npx --yes skills add signerlabs/ShipSwift@add-component -g")

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

    assert result["normalizedCommand"] == "npx --yes skills add signerlabs/ShipSwift -g"
    assert result["warnings"] == [
        "未检测到 `--yes/-y`，已自动按非交互模式执行 Skills 安装。",
        "未检测到 `-g/--global`，已自动按全局安装写入 `~/.agents/skills`。",
    ]


def test_github_clone_uses_windowless_runner(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    destination = tmp_path / "checkout"
    captured: dict[str, object] = {}

    monkeypatch.setattr(service.shutil, "which", lambda name: "git.exe" if name == "git" else None)

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = dict(kwargs)
        destination.mkdir()
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(service, "run_windowless", fake_run)

    result = service._clone_from_github("owner", "repo", destination)

    assert result == destination
    assert captured["command"] == [
        "git",
        "clone",
        "--depth",
        "1",
        "https://github.com/owner/repo.git",
        str(destination),
    ]
    assert captured["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"


def _prepare_skill_install_test(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[service.SkillManifest, Path, Path]:
    source_dir = tmp_path / "source" / "demo-skill"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\nInstructions\n", encoding="utf-8")
    (source_dir / "asset.txt").write_text("complete", encoding="utf-8")
    target_root = tmp_path / "installed"
    lock_root = tmp_path / "locks"
    manifest = service.SkillManifest(
        folder="demo-skill",
        name="demo-skill",
        description="demo",
        source_dir=source_dir,
    )
    monkeypatch.setattr(service, "_target_root", lambda: target_root)
    monkeypatch.setattr(service, "_skill_lock_path", lambda target: lock_root / f"{target.name}.lock")
    monkeypatch.setattr(service, "_skills_registry_lock_path", lambda: lock_root / "registry.lock")
    monkeypatch.setattr("runtimes.extensions.skills.loader.SkillLoader.reload_skills", lambda: None)
    monkeypatch.setattr("core.audit_logger.audit_logger.log", lambda **kwargs: None)
    return manifest, target_root, lock_root


def test_same_skill_concurrent_install_is_idempotent_and_complete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, target_root, _lock_root = _prepare_skill_install_test(monkeypatch, tmp_path)
    real_copytree = shutil.copytree
    staged = threading.Barrier(2)

    def coordinated_copytree(source, destination, *args, **kwargs):
        result = real_copytree(source, destination, *args, **kwargs)
        staged.wait(timeout=5)
        return result

    monkeypatch.setattr(service.shutil, "copytree", coordinated_copytree)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _index: service._install_manifests([manifest], source="test/source", overwrite=False),
                range(2),
            )
        )

    assert sum(len(result["installed"]) for result in results) == 1
    assert sum(len(result["skipped"]) for result in results) == 1
    assert {item["reason"] for result in results for item in result["skipped"]} == {"already_installed"}
    target = target_root / "demo-skill"
    assert (target / "SKILL.md").is_file()
    assert (target / "asset.txt").read_text(encoding="utf-8") == "complete"
    assert not list(target_root.parent.glob(".v8-skill-install-*"))


def test_different_skill_targets_publish_in_parallel(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _manifest, target_root, _lock_root = _prepare_skill_install_test(monkeypatch, tmp_path)
    manifests: list[service.SkillManifest] = []
    for folder in ("alpha", "beta"):
        source_dir = tmp_path / "sources" / folder
        source_dir.mkdir(parents=True)
        (source_dir / "SKILL.md").write_text(f"---\nname: {folder}\n---\n", encoding="utf-8")
        manifests.append(
            service.SkillManifest(
                folder=folder,
                name=folder,
                description=folder,
                source_dir=source_dir,
            )
        )

    real_publish = service._publish_staged_skill
    publishing = threading.Barrier(2)

    def coordinated_publish(staging: Path, target: Path) -> None:
        publishing.wait(timeout=5)
        real_publish(staging, target)

    monkeypatch.setattr(service, "_publish_staged_skill", coordinated_publish)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda manifest: service._install_manifests([manifest], source="test/source", overwrite=False),
                manifests,
            )
        )

    assert {result["installed"][0]["folder"] for result in results} == {"alpha", "beta"}
    assert (target_root / "alpha" / "SKILL.md").is_file()
    assert (target_root / "beta" / "SKILL.md").is_file()


def test_one_skill_cleanup_cannot_remove_another_install_staging_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _manifest, target_root, _lock_root = _prepare_skill_install_test(monkeypatch, tmp_path)
    manifests: dict[str, service.SkillManifest] = {}
    for folder in ("alpha", "beta"):
        source_dir = tmp_path / "interleaved-sources" / folder
        source_dir.mkdir(parents=True)
        (source_dir / "SKILL.md").write_text(f"---\nname: {folder}\n---\n", encoding="utf-8")
        manifests[folder] = service.SkillManifest(
            folder=folder,
            name=folder,
            description=folder,
            source_dir=source_dir,
        )

    real_copytree = shutil.copytree
    beta_prepared = threading.Event()
    alpha_completed = threading.Event()

    def interleaved_copytree(source: Path, destination: Path, *args, **kwargs):
        if Path(source).name == "beta":
            beta_prepared.set()
            assert alpha_completed.wait(timeout=5)
            # This assertion models copy implementations that require their
            # selected staging parent to remain stable across the handoff.
            assert Path(destination).parent.exists()
        return real_copytree(source, destination, *args, **kwargs)

    monkeypatch.setattr(service.shutil, "copytree", interleaved_copytree)
    with ThreadPoolExecutor(max_workers=2) as executor:
        beta_future = executor.submit(
            service._install_manifests,
            [manifests["beta"]],
            source="test/source",
            overwrite=False,
        )
        assert beta_prepared.wait(timeout=5)
        alpha_result = service._install_manifests(
            [manifests["alpha"]],
            source="test/source",
            overwrite=False,
        )
        alpha_completed.set()
        beta_result = beta_future.result(timeout=5)

    assert alpha_result["installed"][0]["folder"] == "alpha"
    assert beta_result["installed"][0]["folder"] == "beta"
    assert (target_root / "alpha" / "SKILL.md").is_file()
    assert (target_root / "beta" / "SKILL.md").is_file()
    assert not list(target_root.parent.glob(".v8-skill-install-*"))


def test_skill_publish_failure_releases_lock_and_removes_staging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest, target_root, lock_root = _prepare_skill_install_test(monkeypatch, tmp_path)

    def fail_publish(_staging: Path, _target: Path) -> None:
        raise OSError("simulated publish failure")

    monkeypatch.setattr(service, "_publish_staged_skill", fail_publish)
    with pytest.raises(OSError, match="simulated publish failure"):
        service._install_manifests([manifest], source="test/source", overwrite=False)

    assert not (target_root / "demo-skill").exists()
    assert not list(target_root.parent.glob(".v8-skill-install-*"))
    with interprocess_file_lock(lock_root / "demo-skill.lock", timeout_seconds=0.2):
        pass

    monkeypatch.setattr(service, "_publish_staged_skill", lambda staging, target: staging.rename(target))
    result = service._install_manifests([manifest], source="test/source", overwrite=False)
    assert result["installed"][0]["folder"] == "demo-skill"
