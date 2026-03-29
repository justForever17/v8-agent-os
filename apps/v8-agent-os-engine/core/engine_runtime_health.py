from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


ENGINE_ROOT = Path(__file__).resolve().parents[1]
ENGINE_VENV_DIR = ENGINE_ROOT / ".venv"
EXPECTED_ENGINE_PYTHON = ENGINE_VENV_DIR / ("Scripts" if os.name == "nt" else "bin") / (
    "python.exe" if os.name == "nt" else "python"
)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "")).strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _argv_contains(flag: str) -> bool:
    return any(str(arg).strip().lower() == flag.lower() for arg in sys.argv)


def inspect_engine_runtime() -> dict[str, Any]:
    interpreter_path = str(Path(sys.executable).resolve())
    expected_path = str(EXPECTED_ENGINE_PYTHON.resolve()) if EXPECTED_ENGINE_PYTHON.exists() else None
    interpreter_drift = bool(expected_path and Path(interpreter_path) != Path(expected_path))
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
        "prefix": str(Path(sys.prefix).resolve()),
        "basePrefix": str(Path(getattr(sys, "base_prefix", sys.prefix)).resolve()),
        "reload": reload_enabled,
        "interpreterDrift": interpreter_drift,
        "launcherDrift": launcher_drift,
        "launchMode": launch_mode,
        "canonicalVenvPresent": EXPECTED_ENGINE_PYTHON.exists(),
        "warnings": warnings,
    }
