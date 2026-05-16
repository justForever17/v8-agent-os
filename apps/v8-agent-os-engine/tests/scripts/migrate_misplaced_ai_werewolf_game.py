from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SOURCE = Path.home() / ".v8-agent-os" / "workspace" / "projects" / "ai-werewolf-game"
DEFAULT_TARGET = Path("E:/Projects/test2/ai-werewolf-game")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(root: Path) -> dict:
    files: list[dict] = []
    for item in sorted(root.rglob("*")):
        if item.is_dir():
            continue
        relative = item.relative_to(root).as_posix()
        files.append(
            {
                "path": relative,
                "bytes": item.stat().st_size,
                "sha256": _sha256(item),
            }
        )
    return {
        "root": str(root),
        "fileCount": len(files),
        "totalBytes": sum(int(item["bytes"]) for item in files),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate the accidentally created ai-werewolf-game project into the selected workspace.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--target", default=str(DEFAULT_TARGET))
    parser.add_argument("--apply", action="store_true", help="Actually copy files. Without this flag the script only prints a plan.")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve(strict=False)
    target = Path(args.target).expanduser().resolve(strict=False)
    manifest = {
        "createdAt": _utc_now(),
        "source": str(source),
        "target": str(target),
        "applied": False,
        "status": "dry_run",
        "checks": {},
    }

    if not source.exists() or not source.is_dir():
        manifest["status"] = "blocked"
        manifest["checks"]["source"] = "missing_or_not_directory"
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 2
    if target.exists():
        manifest["status"] = "blocked"
        manifest["checks"]["target"] = "already_exists"
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 3

    source_inventory = _inventory(source)
    manifest["sourceInventory"] = {
        "fileCount": source_inventory["fileCount"],
        "totalBytes": source_inventory["totalBytes"],
        "sampleFiles": [item["path"] for item in source_inventory["files"][:20]],
    }

    if not args.apply:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    target_inventory = _inventory(target)
    source_hashes = {item["path"]: item["sha256"] for item in source_inventory["files"]}
    target_hashes = {item["path"]: item["sha256"] for item in target_inventory["files"]}
    mismatches = [
        path
        for path, digest in source_hashes.items()
        if target_hashes.get(path) != digest
    ]
    manifest.update(
        {
            "applied": True,
            "status": "copied" if not mismatches else "copied_with_mismatch",
            "targetInventory": {
                "fileCount": target_inventory["fileCount"],
                "totalBytes": target_inventory["totalBytes"],
            },
            "mismatches": mismatches[:20],
            "sourceLeftIntact": True,
        }
    )
    manifest_path = target / ".v8-migration-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if not mismatches else 4


if __name__ == "__main__":
    raise SystemExit(main())
