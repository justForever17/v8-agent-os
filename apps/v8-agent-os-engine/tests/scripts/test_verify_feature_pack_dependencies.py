from __future__ import annotations

from pathlib import Path

from scripts.verify_feature_pack_dependencies import verify_dependency_compatibility


def _write_distribution(
    root: Path,
    name: str,
    version: str,
    requirements: tuple[str, ...] = (),
) -> None:
    metadata_root = root / f"{name.replace('-', '_')}-{version}.dist-info"
    metadata_root.mkdir(parents=True)
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {name}",
        f"Version: {version}",
        *(f"Requires-Dist: {requirement}" for requirement in requirements),
        "",
    ]
    (metadata_root / "METADATA").write_text("\n".join(lines), encoding="utf-8")


def test_feature_pack_dependency_check_accepts_compatible_override(tmp_path: Path) -> None:
    base = tmp_path / "base"
    target = tmp_path / "target"
    _write_distribution(base, "consumer", "1.0", ("provider>=2",))
    _write_distribution(base, "provider", "1.0")
    _write_distribution(target, "provider", "2.1")

    result = verify_dependency_compatibility(target, base_paths=[base])

    assert result["ok"] is True
    assert result["conflictCount"] == 0
    assert result["targetPackages"] == ["provider==2.1"]


def test_feature_pack_dependency_check_rejects_base_runtime_conflict(tmp_path: Path) -> None:
    base = tmp_path / "base"
    target = tmp_path / "target"
    _write_distribution(base, "consumer", "1.0", ("provider>=2",))
    _write_distribution(base, "provider", "2.1")
    _write_distribution(target, "provider", "1.5")

    result = verify_dependency_compatibility(target, base_paths=[base])

    assert result["ok"] is False
    assert result["conflictCount"] == 1
    assert result["conflicts"] == [{
        "dependent": "consumer",
        "dependentVersion": "1.0",
        "requirement": "provider>=2",
        "installed": "provider==1.5",
        "installedSource": "feature_pack",
        "reason": "version_conflict",
    }]


def test_feature_pack_dependency_check_rejects_missing_dependency_and_ignores_extra(tmp_path: Path) -> None:
    base = tmp_path / "base"
    target = tmp_path / "target"
    _write_distribution(
        target,
        "reader",
        "1.0",
        ("required-lib>=1", "optional-lib>=1; extra == 'optional'"),
    )

    result = verify_dependency_compatibility(target, base_paths=[base])

    assert result["ok"] is False
    assert result["conflictCount"] == 1
    assert result["conflicts"][0]["requirement"] == "required-lib>=1"
    assert result["conflicts"][0]["reason"] == "dependency_missing"
