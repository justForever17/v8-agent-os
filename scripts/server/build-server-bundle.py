"""Build the Linux server source distribution from an explicit production closure."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = "apps/v8-agent-os-engine"
CLI = "apps/v8-agent-os-cli"
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


def build(output: Path, *, target: str = "linux-x64") -> dict:
    if target != "linux-x64":
        raise ValueError("Only linux-x64 has a validated production dependency lock")
    release = json.loads((ROOT / "release-manifest.json").read_text(encoding="utf-8"))["release"]
    version = release["version"]
    output.mkdir(parents=True, exist_ok=True)
    asset = output / f"V8OS-Server-{version}-{target}.tar.gz"
    if asset.exists():
        raise FileExistsError(f"Refusing to overwrite {asset}")
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, text=True).splitlines()
    with tempfile.TemporaryDirectory(prefix="v8os-server-build-") as temp:
        bundle = Path(temp) / f"v8os-server-{version}-{target}"
        bundle.mkdir()
        for relative in sorted(set(paths)):
            source = ROOT / relative
            if not include(relative) or not source.is_file() or source.is_symlink():
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
            destination.write_text((ROOT / "scripts/server" / name).read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
            destination.chmod(0o755)
        subprocess.run(["node", str(ROOT / "scripts/server/build-feature-packs.mjs"), str(scripts / "feature-packs.mjs")], check=True, cwd=ROOT)
        manifest = {
            "schema": 1, "profile": "server", "version": version, "platform": "linux", "arch": "x64",
            "engine": ENGINE, "cli": CLI + "/bin/v8os.mjs",
            "sourceCommit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "sourceDirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()),
            "python": "3.11", "node": ">=20", "os": "Ubuntu 22.04/24.04 glibc x64",
        }
        (bundle / "server-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        shutil.copyfile(ROOT / "scripts/server/README.md", bundle / "README.md")
        checksums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(bundle).as_posix()}" for p in sorted(bundle.rglob("*")) if p.is_file()]
        (bundle / "SHA256SUMS").write_text("\n".join(checksums) + "\n", encoding="utf-8", newline="\n")
        def archive_metadata(member: tarfile.TarInfo) -> tarfile.TarInfo:
            member.uid = member.gid = 0
            member.uname = member.gname = ""
            member.mode = 0o755 if member.isdir() or Path(member.name).name in {"v8os", "install.sh"} else 0o644
            return member
        with tarfile.open(asset, "w:gz") as archive:
            archive.add(bundle, arcname=bundle.name, filter=archive_metadata)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    asset.with_suffix(asset.suffix + ".sha256").write_text(f"{digest}  {asset.name}\n", encoding="utf-8", newline="\n")
    return {"asset": str(asset), "bytes": asset.stat().st_size, "sha256": digest, "files": len(checksums)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", default="linux-x64")
    args = parser.parse_args()
    print(json.dumps(build(args.output.resolve(), target=args.target), indent=2))
