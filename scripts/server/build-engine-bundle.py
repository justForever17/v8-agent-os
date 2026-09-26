"""Wrap a verified server source bundle with the portable Engine runtime.

The npm dispatcher downloads this asset on first start.  The source bundle is
kept as the single closure so the Engine and the CLI use the same component
registry and process leases as the desktop Shell.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source(bundle: Path, server: dict) -> None:
    """Bind this asset to the original, checksum-verified source closure."""
    expected = {"schema": 1, "profile": "server", "platform": "linux", "arch": "x64",
                "engine": "apps/v8-agent-os-engine", "cli": "apps/v8-agent-os-cli/bin/v8os.mjs"}
    if any(server.get(key) != value for key, value in expected.items()):
        raise ValueError("Engine asset requires an identified linux-x64 server bundle")
    if server.get("sourceDirty") is not False or not re.fullmatch(r"[0-9a-f]{40}", server.get("sourceCommit", "")):
        raise ValueError("Engine asset requires a clean, identified server bundle")
    if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\.\d+", server.get("version", "")):
        raise ValueError("Invalid server release version")
    listed = set()
    for line in (bundle / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, separator, name = line.partition("  ")
        relative = PurePosixPath(name)
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest) or relative.is_absolute() or ".." in relative.parts or "\\" in name or name in listed:
            raise ValueError("Invalid source checksum entry")
        file = bundle / name
        if not file.is_file() or file.is_symlink() or sha256(file) != digest:
            raise ValueError(f"Source checksum mismatch: {name}")
        listed.add(name)
    if not {"server-manifest.json", "VERSION", expected["cli"], expected["engine"] + "/main.py"} <= listed:
        raise ValueError("Source checksum manifest does not cover release identity and entrypoints")
    # VERSION is the npm/SemVer projection, while the release manifest keeps
    # YYYY.MM.DD.N. Match release-manifest.mjs::toSemver rather than relabeling.
    year, month, day, build_number = map(int, server["version"].split("."))
    semver = f"{year}.{month}.{day}-{build_number}"
    if (bundle / "VERSION").read_text(encoding="utf-8").strip() != semver:
        raise ValueError("VERSION does not match server manifest")
    for file in bundle.rglob("*"):
        name = file.relative_to(bundle).as_posix()
        if file.is_dir() and not file.is_symlink():
            continue
        if name in listed or name == "SHA256SUMS" or "__pycache__" in file.parts:
            continue
        if name.startswith((expected["engine"] + "/.python/", expected["engine"] + "/.playwright-browsers/")):
            continue
        raise ValueError(f"Unidentified file outside portable runtime: {name}")


def validate_links(bundle: Path) -> None:
    for file in bundle.rglob("*"):
        if not file.is_symlink():
            if not file.is_file() and not file.is_dir():
                raise ValueError(f"Unsupported runtime filesystem entry: {file.relative_to(bundle)}")
            continue
        target = os.readlink(file)
        try:
            resolved = file.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError(f"Broken runtime symlink: {file.relative_to(bundle)}") from error
        if os.path.isabs(target) or not resolved.is_relative_to(bundle):
            raise ValueError(f"Runtime symlink escapes package: {file.relative_to(bundle)}")


def archive_metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
    member.uid = member.gid = 0
    member.uname = member.gname = ""
    member.mtime = 0
    # python3 normally points at python3.11. Chromium, its sandbox, Node in
    # Playwright, and native helpers also need their original executable bit.
    member.mode = 0o755 if member.isdir() or member.issym() or member.mode & 0o111 else 0o644
    return member


def build(bundle: Path, output: Path, *, version: str | None = None, source_commit: str | None = None) -> dict:
    bundle = bundle.resolve()
    server_manifest_path = bundle / "server-manifest.json"
    if not server_manifest_path.is_file():
        raise FileNotFoundError(f"server-manifest.json missing from {bundle}")
    server = json.loads(server_manifest_path.read_text(encoding="utf-8"))
    validate_source(bundle, server)
    if (version is not None and version != server["version"]) or (source_commit is not None and source_commit != server["sourceCommit"]):
        raise ValueError("Engine identity override does not match source bundle")
    version, source_commit = server["version"], server["sourceCommit"]
    engine_dir = bundle / "apps" / "v8-agent-os-engine"
    cli_entry = bundle / "apps" / "v8-agent-os-cli" / "bin" / "v8os.mjs"
    python_entry = engine_dir / ".python" / "bin" / "python3"
    if not (engine_dir / "main.py").is_file() or not cli_entry.is_file() or not python_entry.exists():
        raise ValueError("portable Engine bundle is missing main.py, CLI entrypoint or .python runtime")
    validate_links(bundle)
    runtime = json.loads((engine_dir / ".python/v8os-runtime.json").read_text(encoding="utf-8"))
    if runtime.get("profile") != "server" or runtime.get("target") != "linux-x64" or runtime.get("requirementsSha256") != sha256(engine_dir / "requirements/server-linux-x64.lock") or runtime.get("browserIncluded") not in (True, False):
        raise ValueError("Portable runtime receipt does not match server dependency/browser closure")
    root_name = f"v8os-engine-{version}-linux-x64"
    output.mkdir(parents=True, exist_ok=True)
    asset = output / f"V8OS-Engine-{version}-linux-x64.tar.gz"
    manifest_path = output / f"V8OS-Engine-{version}-linux-x64.json"
    checksum_path = asset.with_suffix(asset.suffix + ".sha256")
    if any(file.exists() for file in (asset, manifest_path, checksum_path)):
        raise FileExistsError(f"Refusing to overwrite {asset}")
    with tempfile.TemporaryDirectory(prefix="v8os-engine-build-") as temp:
        staging = Path(temp) / root_name
        shutil.copytree(bundle, staging, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        # The source installer requires a host Python/venv and its shell wrapper
        # bypasses the npm control plane. Neither is a portable product entry.
        for source_only in ("install.sh", "v8os"):
            (staging / source_only).unlink(missing_ok=True)
        (staging / "README.md").write_text(
            "# V8OS portable Engine\n\n"
            "This versioned runtime is managed by @v8-agent-os/v8-agent-os.\n"
            "Install the matching npm package and use its v8os command.\n"
            "Python and server dependencies are included.\n"
            "Lightweight Engine utilizes system-installed Edge/Chrome or discovers host browser at runtime.\n"
            "User configuration, credentials and sessions remain in the separate V8OS state directory.\n",
            encoding="utf-8",
        )
        manifest = {
            "schema": 1,
            "profile": "engine",
            "runtimeProfile": "server",
            "startupProfile": "server",
            "version": version,
            "target": "linux-x64",
            "sourceCommit": source_commit,
            "sourceDirty": False,
            "engineDir": "apps/v8-agent-os-engine",
            "python": "apps/v8-agent-os-engine/.python/bin/python3",
            "cli": "apps/v8-agent-os-cli/bin/v8os.mjs",
            "node": ">=22",
            "browserIncluded": bool(runtime.get("browserIncluded", False)),
        }
        (staging / "engine-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        checksums = []
        for file in sorted(p for p in staging.rglob("*") if p.is_file() and p.relative_to(staging).as_posix() != "SHA256SUMS"):
            checksums.append(f"{sha256(file)}  {file.relative_to(staging).as_posix()}")
        (staging / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8")

        temporary = Path(temp) / "engine.tar.gz"
        with temporary.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                archive.add(staging, arcname=root_name, filter=archive_metadata)
        with temporary.open("rb") as source, asset.open("xb") as destination:
            try:
                shutil.copyfileobj(source, destination)
            except BaseException:
                destination.close()
                asset.unlink(missing_ok=True)
                raise

    digest = sha256(asset)
    public_manifest = {
        "schema": 1,
        "profile": "engine",
        "runtimeProfile": "server",
        "startupProfile": "server",
        "version": version,
        "target": "linux-x64",
        "sourceCommit": source_commit,
        "root": root_name,
        "asset": asset.name,
        "sha256": digest,
    }
    manifest_path.write_text(json.dumps(public_manifest, indent=2) + "\n", encoding="utf-8")
    checksum_path.write_text(f"{digest}  {asset.name}\n", encoding="utf-8")
    return {"asset": str(asset), "manifest": str(manifest_path), "sha256": digest, "bytes": asset.stat().st_size}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.bundle, args.output), indent=2))
