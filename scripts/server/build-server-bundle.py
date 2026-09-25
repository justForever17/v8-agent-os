"""Build the Linux server source distribution from an explicit production closure."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_TARGETS = {"linux-x64"}
ENGINE = "apps/v8-agent-os-engine"
CLI = "apps/v8-agent-os-cli"
ADMIN_SOURCE = "apps/v8-agent-os-web/src/admin"
SOURCE_PATHS = (ENGINE, CLI, "scripts/server", ADMIN_SOURCE, "release-manifest.json", "LICENSE", "VERSION")

# These are inert contracts still imported by the shared native-tool registry.
# No desktop driver, executor, capture server or RPA runtime enters this package.
DESKTOP_CONTRACTS = {
    "runtimes/computer_use/__init__.py", "runtimes/computer_use/primitives.py",
    "runtimes/computer_use/types.py", "runtimes/computer_use/verification_contract.py",
    "runtimes/rpa/__init__.py", "runtimes/rpa/promotion_gate.py",
}


def include(relative: str) -> bool:
    parts = Path(relative).parts
    if any(part in {"node_modules", "__pycache__", ".venv", ".python", "tests", ".pytest_cache"} for part in parts):
        return False
    if relative.startswith(ENGINE + "/"):
        sub = relative[len(ENGINE) + 1:]
        if sub.startswith(("runtimes/computer_use/", "runtimes/rpa/")):
            return sub in DESKTOP_CONTRACTS
        if sub in {"desktop_live_bridge_server.py", "core/desktop_live.py"} or sub.startswith("native/"):
            return False
        return sub.endswith((".py", ".json", ".md", ".txt", ".lock", ".yaml", ".yml", ".toml", ".js", ".mjs"))
    return relative.startswith((CLI + "/src/", CLI + "/bin/")) or relative in {CLI + "/package.json", "LICENSE", "VERSION"}


def validate_targets(manifest: dict, target: str) -> None:
    if target not in SUPPORTED_TARGETS:
        raise ValueError("Only linux-x64 has a validated production dependency lock")
    targets = manifest.get("products", {}).get("server", {}).get("targets", {})
    unsupported = {name for name, value in targets.items() if value.get("enabled") or value.get("required")} - SUPPORTED_TARGETS
    if unsupported:
        raise ValueError(f"Server release enables unimplemented targets: {sorted(unsupported)}; add their lock, installer and CI before enabling them")


def _build_input(relative: str) -> bool:
    return (
        include(relative)
        or relative.startswith(("scripts/server/", f"{ADMIN_SOURCE}/"))
        or relative == "release-manifest.json"
    )



def build(output: Path, *, target: str = "linux-x64", allow_dirty: bool = False) -> dict:
    source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    paths = subprocess.check_output(["git", "ls-files", "--cached", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    paths = sorted({name for name in paths if name and _build_input(name)})
    dirty = bool(subprocess.check_output(["git", "diff", "HEAD", "--name-only", "-z", "--", *SOURCE_PATHS], cwd=ROOT))
    if dirty and not allow_dirty:
        raise ValueError("Tracked server build inputs are modified; commit them first or use --allow-dirty for an explicitly marked candidate")
    manifest_data = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8") if dirty else subprocess.check_output(["git", "show", f"{source_commit}:release-manifest.json"], cwd=ROOT))
    validate_targets(manifest_data, target)
    release = manifest_data["release"]
    version = release["version"]
    output.mkdir(parents=True, exist_ok=True)
    asset = output / f"V8OS-Server-{version}-{target}.tar.gz"
    if asset.exists():
        raise FileExistsError(f"Refusing to overwrite {asset}")
    with tempfile.TemporaryDirectory(prefix="v8os-server-build-") as temp:
        # Freeze tracked inputs before invoking the compiler. In particular an
        # untracked TS module must not enter through the installer's import tree.
        source_root = Path(temp) / "source"
        if not dirty:
            snapshot = subprocess.check_output(["git", "archive", "--format=tar", source_commit, "--", *SOURCE_PATHS], cwd=ROOT)
            with tarfile.open(fileobj=io.BytesIO(snapshot)) as tracked:
                for member in tracked.getmembers():
                    if member.name not in paths:
                        continue
                    if not member.isfile():
                        raise ValueError(f"Tracked build input is not a regular file: {member.name}")
                    destination = source_root / member.name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(tracked.extractfile(member).read())
        else:
            for relative in paths:
                source = ROOT / relative
                if source.is_symlink() or not source.is_file():
                    raise ValueError(f"Tracked build input is missing or a symlink: {relative}")
                destination = source_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read_bytes())
        bundle = Path(temp) / f"v8os-server-{version}-{target}"
        bundle.mkdir()
        for relative in paths:
            source = source_root / relative
            if not include(relative):
                continue
            destination = bundle / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        lock = bundle / ENGINE / "requirements/server-linux-x64.lock"
        if not lock.is_file():
            raise FileNotFoundError("Generate and verify requirements/server-linux-x64.lock before packaging")
        scripts = bundle / "scripts/server"
        scripts.mkdir(parents=True)
        for name in ("install.sh", "v8os", "verify_server.py"):
            destination = bundle / name if name != "verify_server.py" else scripts / name
            # Normalize checkout CRLF for executable Linux scripts.
            destination.write_text((source_root / "scripts/server" / name).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
            destination.chmod(0o755)
        shutil.copytree(ROOT / "scripts/server/node_modules", source_root / "scripts/server/node_modules")
        subprocess.run(["node", str(source_root / "scripts/server/build-feature-packs.mjs"), str(scripts / "feature-packs.mjs")], check=True, cwd=source_root)
        manifest = {
            "schema": 1, "profile": "server", "version": version, "platform": "linux", "arch": "x64",
            "engine": ENGINE, "cli": CLI + "/bin/v8os.mjs",
            "sourceCommit": source_commit,
            "sourceDirty": dirty,
            "python": "3.11", "node": ">=20", "os": "Ubuntu 22.04/24.04 glibc x64",
        }
        (bundle / "server-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        shutil.copyfile(source_root / "scripts/server/README.md", bundle / "README.md")
        checksums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(bundle).as_posix()}" for p in sorted(bundle.rglob("*")) if p.is_file()]
        (bundle / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8", newline="\n")
        def archive_metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mode = 0o755 if member.isdir() or Path(member.name).name in {"v8os", "install.sh"} else 0o644
            member.mtime = 0
            return member
        temporary_asset = Path(temp) / "server.tar.gz"
        with temporary_asset.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                archive.add(bundle, arcname=bundle.name, filter=archive_metadata)
        # Failure never leaves a truncated final asset that blocks a rerun.
        with temporary_asset.open("rb") as source, asset.open("xb") as destination:
            try:
                shutil.copyfileobj(source, destination)
            except BaseException:
                destination.close()
                asset.unlink(missing_ok=True)
                raise
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    asset.with_suffix(asset.suffix + ".sha256").write_text(f"{digest}  {asset.name}\n", encoding="utf-8", newline="\n")
    return {"asset": str(asset), "bytes": asset.stat().st_size, "sha256": digest, "files": len(checksums)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", default="linux-x64")
    parser.add_argument("--allow-dirty", action="store_true", help="Build tracked working-tree edits as an explicitly marked candidate; untracked files remain excluded")
    args = parser.parse_args()
    print(json.dumps(build(args.output.resolve(), target=args.target, allow_dirty=args.allow_dirty), indent=2))
