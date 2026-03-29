from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.storage import storage
from scripts.stdout_utf8 import emit_json


FORMAL_RPA_DIRS = {
    "drafts": "drafts",
    "scripts": "scripts",
    "templates": "templates",
    "template_history": "template_history",
}

OFFICIAL_EXAMPLE_FILES = {
    "data_driven.robot",
    "gherkin.robot",
    "keyword_driven.robot",
    "CalculatorLibrary.py",
    "calculator.py",
}

EXCLUDED_CONFIG_FILES = {
    "computer_use.json",
    "computer_use_memory.json",
    "web_fetch_profiles.json",
    "media_download_profiles.json",
    "state.db",
    "checkpoints.db",
    "users.json",
}


def _sha256(filepath: Path) -> str:
    digest = hashlib.sha256()
    with open(filepath, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _copy_preserving_target(source: Path, target: Path, *, copied: list[str], skipped: list[dict[str, str]], conflicts: list[dict[str, str]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
        copied.append(str(target))
        return

    source_hash = _sha256(source)
    target_hash = _sha256(target)
    if source_hash == target_hash:
        skipped.append({"source": str(source), "target": str(target), "reason": "identical"})
        return

    conflicts.append({"source": str(source), "target": str(target), "reason": "target_preserved"})


def _migrate_official_examples(source_root: Path, target_root: Path) -> dict[str, Any]:
    source_dir = source_root / "tmp" / "tests" / "community_robot_samples" / "robotdemo"
    target_dir = target_root / "rpa" / "examples" / "official"
    target_dir.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    skipped: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    excluded: list[str] = []

    if not source_dir.exists():
        return {
            "sourceDir": str(source_dir),
            "targetDir": str(target_dir),
            "copied": copied,
            "skipped": skipped,
            "conflicts": conflicts,
            "excluded": excluded,
        }

    for path in source_dir.iterdir():
        if path.is_dir():
            excluded.append(str(path))
            continue
        if path.name not in OFFICIAL_EXAMPLE_FILES:
            excluded.append(str(path))
            continue
        _copy_preserving_target(path, target_dir / path.name, copied=copied, skipped=skipped, conflicts=conflicts)

    readme_path = target_dir / "README.txt"
    readme_content = (
        "这是从旧版用户根目录导入的 Robot Framework 官方示例。\n"
        "仅保留 .robot 与运行所需的 CalculatorLibrary.py / calculator.py。\n"
        "旧的 output_*、__pycache__ 等测试产物未进入 canonical 示例目录。\n"
    )
    if not readme_path.exists() or readme_path.read_text(encoding="utf-8") != readme_content:
        readme_path.write_text(readme_content, encoding="utf-8")
        if str(readme_path) not in copied:
            copied.append(str(readme_path))
    else:
        skipped.append({"source": "generated:README", "target": str(readme_path), "reason": "identical"})

    return {
        "sourceDir": str(source_dir),
        "targetDir": str(target_dir),
        "copied": copied,
        "skipped": skipped,
        "conflicts": conflicts,
        "excluded": excluded,
    }


def _migrate_formal_rpa_assets(source_root: Path, target_root: Path) -> dict[str, Any]:
    source_dir = source_root / "rpa"
    target_dir = target_root / "rpa"
    copied: list[str] = []
    skipped: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    excluded: list[str] = []

    for source_name, target_name in FORMAL_RPA_DIRS.items():
        src = source_dir / source_name
        dst = target_dir / target_name
        dst.mkdir(parents=True, exist_ok=True)
        if not src.exists():
            excluded.append(str(src))
            continue

        for path in src.iterdir():
            if source_name == "scripts":
                if path.is_dir():
                    excluded.append(str(path))
                    continue
                if path.suffix.lower() != ".robot":
                    excluded.append(str(path))
                    continue
            elif path.is_dir():
                excluded.append(str(path))
                continue

            _copy_preserving_target(path, dst / path.name, copied=copied, skipped=skipped, conflicts=conflicts)

    trust_metrics_source = source_dir / "trust_metrics.json"
    trust_metrics_target = target_dir / "trust_metrics.json"
    if trust_metrics_source.exists():
        _copy_preserving_target(
            trust_metrics_source,
            trust_metrics_target,
            copied=copied,
            skipped=skipped,
            conflicts=conflicts,
        )
    else:
        excluded.append(str(trust_metrics_source))

    (target_dir / "_legacy_tests").mkdir(parents=True, exist_ok=True)

    return {
        "sourceDir": str(source_dir),
        "targetDir": str(target_dir),
        "copied": copied,
        "skipped": skipped,
        "conflicts": conflicts,
        "excluded": excluded,
    }


def run(source_root: Path) -> dict[str, Any]:
    config_report = storage.import_external_legacy_root(source_root)
    target_root = storage.base_dir
    example_report = _migrate_official_examples(source_root, target_root)
    rpa_report = _migrate_formal_rpa_assets(source_root, target_root)
    excluded_config_files = sorted(
        str(source_root / name) for name in EXCLUDED_CONFIG_FILES if (source_root / name).exists()
    )

    report = {
        **config_report,
        "examplesMoved": example_report,
        "formalRpaAssets": rpa_report,
        "excludedConfigFiles": excluded_config_files,
        "excludedArtifacts": sorted(
            set(example_report.get("excluded", []) + rpa_report.get("excluded", []))
        ),
    }

    report_path = Path(config_report["backupDir"]) / "migration-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["reportPath"] = str(report_path)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Import structured config JSON and formal RPA assets from an external legacy v8chat root.")
    parser.add_argument(
        "--source-root",
        default=r"C:\Users\sunny\v8chat",
        help="Legacy v8chat root containing old structured JSON files and RPA assets.",
    )
    args = parser.parse_args()

    source_root = Path(args.source_root).expanduser()
    if not source_root.exists():
        emit_json({"ok": False, "error": "source_root_missing", "sourceRoot": str(source_root)})
        return 1

    report = run(source_root)
    emit_json({"ok": True, **report})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
