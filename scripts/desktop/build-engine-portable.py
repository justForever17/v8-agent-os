"""Build a platform portable Engine archive from a prepared desktop runtime.

The desktop workflow already prepares the native Python runtime for its runner.
This script gives that runtime the same immutable manifest and archive contract
used by the Linux server Engine without copying the Web or Electron payload.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = Path("apps/v8-agent-os-engine")
CLI = Path("apps/v8-agent-os-cli")
TARGETS = {
    "windows-x64": ("windows", "x64", ENGINE / ".python" / "python.exe"),
    "windows-arm64": ("windows", "arm64", ENGINE / ".python" / "python.exe"),
    "macos-x64": ("macos", "x64", ENGINE / ".python" / "bin" / "python3"),
    "macos-arm64": ("macos", "arm64", ENGINE / ".python" / "bin" / "python3"),
}
IGNORED_DIRS = {"__pycache__", ".pytest_cache", ".venv", ".git", ".tmp", "tmp", "reports", "workspace", "node_modules", ".plugin-release-assets", "logs"}
IGNORED_FILES = {".env", ".env.local", ".env.production", "secret.db", "secrets.db"}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    def ignored(_directory: str, names: list[str]) -> set[str]:
        result = set()
        for name in names:
            if name in IGNORED_DIRS or name in IGNORED_FILES or name == "tests" or name == "native" or name.startswith("pytest-of-"):
                result.add(name)
            elif name.endswith((".pyc", ".pyo", ".db", ".log")) or name.startswith(".env"):
                result.add(name)
        return result
    shutil.copytree(source, destination, symlinks=True, ignore=ignored)


def assert_clean_source(source_commit: str) -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if source_commit != head:
        raise ValueError(f"source commit {source_commit} is not the checked out HEAD {head}")
    dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", str(ENGINE), str(CLI)], cwd=ROOT)
    if dirty.returncode != 0:
        raise ValueError("Engine/CLI source is dirty; commit source changes before packaging")
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", str(ENGINE), str(CLI)], cwd=ROOT, text=True)
    unexpected = [line for line in untracked.splitlines() if line and not any(part in IGNORED_DIRS for part in Path(line).parts)]
    if unexpected:
        raise ValueError(f"Untracked Engine/CLI source would make provenance ambiguous: {unexpected[0]}")


def metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.mtime = 0
    member.mode = 0o755 if member.isdir() or member.issym() or member.mode & 0o111 else 0o644
    return member


def build(target: str, output: Path, *, source_commit: str | None = None) -> dict:
    if target not in TARGETS:
        raise ValueError(f"Unsupported desktop Engine target: {target}")
    platform_name, arch, python_relative = TARGETS[target]
    engine_root = ROOT / ENGINE
    cli_root = ROOT / CLI
    python_entry = ROOT / python_relative
    if not (engine_root / "main.py").is_file() or not (cli_root / "bin" / "v8os.mjs").is_file():
        raise ValueError("Engine or CLI source entrypoint is missing")
    if not python_entry.is_file():
        raise ValueError(f"Prepared portable Python runtime is missing: {python_entry}")
    browser_root = engine_root / ".playwright-browsers"
    if not browser_root.is_dir() or (browser_root / "DEGRADED.txt").exists():
        raise ValueError("Standalone Engine requires an embedded Playwright Chromium runtime; desktop discovery-only payload is not publishable")
    browser_files = [item for item in browser_root.rglob("*") if item.is_file()]
    if not browser_files:
        raise ValueError("Standalone Engine browser payload does not contain a Chromium executable")
    if source_commit is None:
        source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if not source_commit or len(source_commit) != 40:
        raise ValueError("source commit must be a full git SHA")
    assert_clean_source(source_commit)
    receipt_path = engine_root / ".python" / "v8os-runtime.json"
    if not receipt_path.is_file():
        raise ValueError("Prepared portable Python runtime receipt is missing")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    expected_receipt_target = target
    if receipt.get("schema") != 1 or receipt.get("profile") != "desktop" or receipt.get("target") != expected_receipt_target:
        raise ValueError("Portable Python runtime receipt does not match target/profile")
    requirements = engine_root / "requirements" / "desktop-preview.txt"
    if not requirements.is_file() or receipt.get("requirementsSha256") != digest(requirements):
        raise ValueError("Portable Python runtime receipt does not match desktop dependency closure")
    if receipt.get("browserIncluded") is not True:
        raise ValueError("Standalone Engine requires browserIncluded=true in the runtime receipt")
    release = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))["release"]
    version = release["version"]
    root_name = f"v8os-engine-{version}-{target}"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"V8OS-Engine-{version}-{target}.tar.gz"
    public_manifest = output / f"V8OS-Engine-{version}-{target}.json"
    if archive.exists() or public_manifest.exists():
        raise FileExistsError(f"Refusing to overwrite {archive}")
    with tempfile.TemporaryDirectory(prefix="v8os-portable-engine-") as temporary:
        staging = Path(temporary) / root_name
        staging.mkdir()
        copy_tree(engine_root, staging / ENGINE)
        copy_tree(cli_root, staging / CLI)
        staged_receipt = dict(receipt)
        staged_receipt["profile"] = "server"
        # The runtime receipt belongs to the Engine runtime root, independent
        # of the interpreter layout (.python/python.exe vs .python/bin/python3).
        # Writing beside the POSIX interpreter leaves the canonical receipt
        # unchanged and makes the release verifier see the desktop profile.
        (staging / ENGINE / ".python" / "v8os-runtime.json").write_text(
            json.dumps(staged_receipt, indent=2) + "\n", encoding="utf-8"
        )
        manifest = {
            "schema": 1,
            "profile": "engine",
            "runtimeProfile": "server",
            "startupProfile": "server",
            "version": version,
            "target": target,
            "platform": platform_name,
            "arch": arch,
            "sourceCommit": source_commit,
            "sourceDirty": False,
            "engineDir": ENGINE.as_posix(),
            "python": python_relative.as_posix(),
            "cli": (CLI / "bin" / "v8os.mjs").as_posix(),
            "node": ">=22",
            "browserIncluded": True,
        }
        (staging / "engine-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        checksums = []
        (staging / "README.md").write_text(
            "# V8OS portable Engine\n\n"
            "Managed by @v8-agent-os/v8-agent-os. Use `v8os start` to launch it.\n"
            "The matching Node.js package owns lifecycle, state and credentials.\n"
            "Host system libraries required by browser/desktop automation remain platform-specific.\n",
            encoding="utf-8",
        )
        for item in sorted(staging.rglob("*")):
            if item.is_file() and item.name != "SHA256SUMS":
                checksums.append(f"{digest(item)}  {item.relative_to(staging).as_posix()}")
        (staging / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")
        with archive.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                tar.add(staging, arcname=root_name, filter=metadata)
    archive_hash = digest(archive)
    public = {
        "schema": 1,
        "profile": "engine",
        "startupProfile": "server",
        "version": version,
        "target": target,
        "sourceCommit": source_commit,
        "root": root_name,
        "asset": archive.name,
        "sha256": archive_hash,
        "runtimeProfile": "server",
    }
    public_manifest.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
    archive.with_suffix(archive.suffix + ".sha256").write_text(f"{archive_hash}  {archive.name}\n", encoding="utf-8")
    return {"asset": str(archive), "manifest": str(public_manifest), "sha256": archive_hash, "bytes": archive.stat().st_size}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-commit", default=None)
    args = parser.parse_args()
    print(json.dumps(build(args.target, args.output.resolve(), source_commit=args.source_commit), indent=2))
