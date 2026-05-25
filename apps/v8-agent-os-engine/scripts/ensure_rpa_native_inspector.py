"""Ensure the Windows RPA Native Inspector helper is built.

This script is intentionally idempotent so bootstrap/install flows can call it
on every desktop install. It installs a local .NET 8 SDK when no suitable SDK is
available, then publishes the FlaUI helper from source.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Sequence


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _project_path() -> Path:
    return _engine_root() / "native" / "V8.Rpa.NativeInspector" / "V8.Rpa.NativeInspector.csproj"


def _published_exe() -> Path:
    return (
        _project_path().parent
        / "bin"
        / "Release"
        / "net8.0-windows"
        / "win-x64"
        / "publish"
        / "V8.Rpa.NativeInspector.exe"
    )


def _local_dotnet_dir() -> Path:
    return Path.home() / ".v8-agent-os" / "toolchains" / "dotnet-sdk-8"


def _local_dotnet_exe() -> Path:
    suffix = ".exe" if os.name == "nt" else ""
    return _local_dotnet_dir() / f"dotnet{suffix}"


def _run(command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=str(cwd) if cwd else None,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _has_dotnet_8_sdk(dotnet: Path | str) -> bool:
    result = _run([str(dotnet), "--list-sdks"])
    if result.returncode != 0:
        return False
    return any(line.strip().startswith("8.") for line in result.stdout.splitlines())


def _find_dotnet() -> Path | None:
    env_value = os.environ.get("V8_AGENT_OS_DOTNET")
    if env_value:
        candidate = Path(env_value).expanduser()
        if candidate.exists() and _has_dotnet_8_sdk(candidate):
            return candidate
    local = _local_dotnet_exe()
    if local.exists() and _has_dotnet_8_sdk(local):
        return local
    global_dotnet = shutil.which("dotnet")
    if global_dotnet and _has_dotnet_8_sdk(global_dotnet):
        return Path(global_dotnet)
    return None


def _install_dotnet_8_sdk() -> Path:
    if os.name != "nt":
        raise RuntimeError("Automatic .NET SDK install is currently implemented for Windows bootstrap only.")
    install_dir = _local_dotnet_dir()
    install_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v8-dotnet-install-") as tmp:
        script = Path(tmp) / "dotnet-install.ps1"
        urllib.request.urlretrieve("https://dot.net/v1/dotnet-install.ps1", script)
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Channel",
            "8.0",
            "-InstallDir",
            str(install_dir),
            "-NoPath",
        ]
        result = _run(command)
        if result.returncode != 0:
            raise RuntimeError(f"dotnet-install.ps1 failed:\n{result.stdout}")
    dotnet = _local_dotnet_exe()
    if not dotnet.exists() or not _has_dotnet_8_sdk(dotnet):
        raise RuntimeError(f"Installed .NET SDK was not found or has no 8.x SDK: {dotnet}")
    return dotnet


def _publish(dotnet: Path) -> str:
    project = _project_path()
    if not project.exists():
        raise RuntimeError(f"Native inspector project not found: {project}")
    command = [
        str(dotnet),
        "publish",
        str(project),
        "-c",
        "Release",
        "-r",
        "win-x64",
        "--self-contained",
        "false",
    ]
    result = _run(command, cwd=_repo_root())
    if result.returncode != 0:
        raise RuntimeError(f"Native inspector publish failed:\n{result.stdout}")
    exe = _published_exe()
    if not exe.exists():
        raise RuntimeError(f"Native inspector publish completed but exe is missing: {exe}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the V8 RPA Native Inspector helper.")
    parser.add_argument("--check", action="store_true", help="Only check whether the helper exists.")
    parser.add_argument("--force", action="store_true", help="Publish even when the helper already exists.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable result.")
    parser.add_argument("--skip-sdk-install", action="store_true", help="Do not install local .NET SDK automatically.")
    args = parser.parse_args()

    result: dict[str, object] = {
        "ok": False,
        "platform": platform.system().lower(),
        "project": str(_project_path()),
        "helperPath": str(_published_exe()),
    }
    try:
        if os.name != "nt":
            result.update({"ok": True, "skipped": True, "reason": "windows_only_helper"})
            return _finish(result, args.json)
        exe = _published_exe()
        if exe.exists() and not args.force:
            result.update({"ok": True, "built": True, "skipped": args.check, "reason": "helper_already_published"})
            return _finish(result, args.json)
        if args.check:
            result.update({"ok": False, "built": False, "reason": "helper_not_published"})
            return _finish(result, args.json, exit_code=1)
        dotnet = _find_dotnet()
        if dotnet is None:
            if args.skip_sdk_install:
                raise RuntimeError("No .NET 8 SDK found and --skip-sdk-install was provided.")
            dotnet = _install_dotnet_8_sdk()
        publish_output = _publish(dotnet)
        result.update(
            {
                "ok": True,
                "built": True,
                "dotnet": str(dotnet),
                "publishOutputTail": publish_output[-2000:],
            }
        )
        return _finish(result, args.json)
    except Exception as exc:
        result.update({"ok": False, "error": str(exc)})
        return _finish(result, args.json, exit_code=1)


def _finish(result: dict[str, object], as_json: bool, *, exit_code: int = 0) -> int:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result.get("ok"):
        if result.get("skipped"):
            print(f"RPA Native Inspector: skipped ({result.get('reason')}).")
        else:
            print(f"RPA Native Inspector ready: {result.get('helperPath')}")
    else:
        print(f"RPA Native Inspector unavailable: {result.get('error') or result.get('reason')}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
