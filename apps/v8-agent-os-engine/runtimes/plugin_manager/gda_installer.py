from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


GDA_PACKAGE = "gda==0.8.1"
GDA_PYTHON = "3.13"
UV_VERSION = "0.8.22"


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _tool_environment(plugin_root: Path) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONUTF8": "1",
        "UV_TOOL_DIR": str(plugin_root / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(plugin_root / "bin"),
        "UV_PYTHON_INSTALL_DIR": str(plugin_root / "python"),
    }


def _run(argv: list[str], *, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=False,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        creationflags=_creation_flags(),
    )


def _uv_command(plugin_root: Path) -> tuple[list[str], dict[str, str]]:
    env = _tool_environment(plugin_root)
    detected = shutil.which("uv")
    if detected:
        return [str(Path(detected).resolve())], env

    bootstrap_root = plugin_root / "bootstrap" / "uv"
    bootstrap_env = {**env, "PYTHONPATH": str(bootstrap_root)}
    probe = _run([sys.executable, "-m", "uv", "--version"], env=bootstrap_env, timeout=15)
    if probe.returncode == 0:
        return [sys.executable, "-m", "uv"], bootstrap_env

    bootstrap_root.mkdir(parents=True, exist_ok=True)
    install = _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--target",
            str(bootstrap_root),
            f"uv=={UV_VERSION}",
        ],
        env=env,
        timeout=600,
    )
    if install.returncode != 0:
        detail = (install.stderr or install.stdout or "uv bootstrap failed").strip()[-2000:]
        raise RuntimeError(detail)
    verify = _run([sys.executable, "-m", "uv", "--version"], env=bootstrap_env, timeout=30)
    if verify.returncode != 0:
        detail = (verify.stderr or verify.stdout or "uv bootstrap verification failed").strip()[-2000:]
        raise RuntimeError(detail)
    return [sys.executable, "-m", "uv"], bootstrap_env


def _gda_executable(plugin_root: Path) -> Path:
    return plugin_root / "bin" / ("gda.exe" if os.name == "nt" else "gda")


def _install_bundled_skill(executable: Path, plugin_root: Path, *, env: dict[str, str]) -> None:
    skill_root = plugin_root / "gda-skill"
    result = _run(
        [str(executable), "skill", "--install", "--dir", str(skill_root), "--json"],
        env=env,
        timeout=60,
    )
    if result.returncode != 0 or not (skill_root / "SKILL.md").is_file():
        detail = (result.stderr or result.stdout or "gda bundled Skill installation failed").strip()
        raise RuntimeError(detail[-2000:])


def install(plugin_root: Path) -> int:
    plugin_root.mkdir(parents=True, exist_ok=True)
    uv, env = _uv_command(plugin_root)
    result = _run(
        [
            *uv,
            "tool",
            "install",
            GDA_PACKAGE,
            "--python",
            GDA_PYTHON,
            "--managed-python",
            "--force",
        ],
        env=env,
        timeout=1200,
    )
    if result.returncode != 0:
        sys.stderr.write((result.stderr or result.stdout or "gda installation failed")[-4000:])
        return result.returncode or 1
    executable = _gda_executable(plugin_root)
    if not executable.is_file():
        sys.stderr.write(f"gda executable was not created: {executable}")
        return 2
    verify = _run([str(executable), "--version"], env=env, timeout=30)
    if verify.returncode != 0 or "0.8.1" not in str(verify.stdout or verify.stderr):
        sys.stderr.write((verify.stderr or verify.stdout or "gda version verification failed")[-2000:])
        return verify.returncode or 3
    try:
        _install_bundled_skill(executable, plugin_root, env=env)
    except RuntimeError as exc:
        sys.stderr.write(str(exc))
        return 4
    sys.stdout.write("gda 0.8.1 ready\n")
    return 0


def version(plugin_root: Path) -> int:
    executable = _gda_executable(plugin_root)
    if not executable.is_file():
        return 1
    result = _run([str(executable), "--version"], env=_tool_environment(plugin_root), timeout=30)
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the pinned gda CLI into a V8OS plugin root.")
    parser.add_argument("mode", choices=("install", "version"))
    parser.add_argument("--plugin-root", required=True)
    args = parser.parse_args()
    root = Path(args.plugin_root).expanduser().resolve()
    return install(root) if args.mode == "install" else version(root)


if __name__ == "__main__":
    raise SystemExit(main())
