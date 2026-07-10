from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[1]
ENGINE_PORTABLE_RUNTIME_DIR = ENGINE_ROOT / ".python"
ENGINE_VENV_DIR = ENGINE_ROOT / ".venv"
EXPECTED_ENGINE_PYTHON = ENGINE_VENV_DIR / ("Scripts" if os.name == "nt" else "bin") / (
    "python.exe" if os.name == "nt" else "python"
)
ENGINE_RUNTIME_DIRS = (ENGINE_PORTABLE_RUNTIME_DIR, ENGINE_VENV_DIR)


def _runtime_python_paths(runtime_root: Path) -> tuple[Path, ...]:
    if os.name == "nt":
        executable_root = runtime_root if runtime_root.name == ".python" else runtime_root / "Scripts"
        return executable_root / "python.exe", executable_root / "pythonw.exe"
    return (runtime_root / "bin" / "python",)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_runtime_identity(interpreter_path: Path, prefix_path: Path) -> dict[str, Path | None]:
    expected_root: Path | None = None
    expected_interpreter: Path | None = None
    actual_root: Path | None = None

    for runtime_root in ENGINE_RUNTIME_DIRS:
        python_paths = _runtime_python_paths(runtime_root)
        available_python = next((candidate for candidate in python_paths if candidate.exists()), None)
        if expected_root is None and available_python is not None:
            expected_root = runtime_root.resolve()
            expected_interpreter = available_python.resolve()
        if runtime_root.exists() and (
            _same_path(prefix_path, runtime_root) or _path_within(interpreter_path, runtime_root)
        ):
            actual_root = runtime_root.resolve()

    return {
        "actualRoot": actual_root,
        "expectedRoot": expected_root,
        "expectedInterpreter": expected_interpreter,
    }


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _argv_contains(flag: str) -> bool:
    return any(str(arg).strip().lower() == flag.lower() for arg in sys.argv)


def inspect_engine_runtime() -> dict[str, Any]:
    interpreter = Path(sys.executable).resolve()
    prefix = Path(sys.prefix).resolve()
    runtime_identity = _resolve_runtime_identity(interpreter, prefix)
    actual_runtime_root = runtime_identity["actualRoot"]
    expected_runtime_root = runtime_identity["expectedRoot"]
    expected_interpreter = runtime_identity["expectedInterpreter"]
    interpreter_path = str(interpreter)
    expected_path = str(expected_interpreter) if expected_interpreter else None
    interpreter_drift = bool(
        expected_runtime_root
        and (actual_runtime_root is None or not _same_path(actual_runtime_root, expected_runtime_root))
    )
    reload_enabled = _env_flag("ENGINE_RELOAD") or _argv_contains("--reload")
    launch_mode = "windows_reload_dev" if os.name == "nt" and reload_enabled else ("reload_dev" if reload_enabled else "prod_like")
    launcher_drift = bool(reload_enabled and interpreter_drift)

    warnings: list[str] = []
    if expected_path and interpreter_drift:
        warnings.append(f"解释器漂移：当前 {interpreter_path}，期望 {expected_path}")
    if os.name == "nt" and reload_enabled:
        warnings.append("Windows 下 --reload 仅建议用于开发辅助，不建议作为稳定验证或长期运行入口。")

    return {
        "interpreterPath": interpreter_path,
        "expectedInterpreterPath": expected_path,
        "interpreterVariant": "windowed" if interpreter.name.lower() == "pythonw.exe" else "console",
        "managedRuntimeRoot": str(actual_runtime_root) if actual_runtime_root else None,
        "expectedRuntimeRoot": str(expected_runtime_root) if expected_runtime_root else None,
        "prefix": str(prefix),
        "basePrefix": str(Path(getattr(sys, "base_prefix", sys.prefix)).resolve()),
        "reload": reload_enabled,
        "interpreterDrift": interpreter_drift,
        "launcherDrift": launcher_drift,
        "launchMode": launch_mode,
        "canonicalVenvPresent": EXPECTED_ENGINE_PYTHON.exists(),
        "managedRuntimePresent": expected_runtime_root is not None,
        "warnings": warnings,
    }
