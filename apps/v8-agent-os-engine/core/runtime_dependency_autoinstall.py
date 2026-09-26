from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from core.interprocess_lock import interprocess_file_lock
from core.v8_agent_os_paths import V8_AGENT_OS_HOME

logger = logging.getLogger("core.runtime_dependency_autoinstall")

DOMESTIC_PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
DOMESTIC_PIP_EXTRA_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
OFFICIAL_PIP_INDEX = "https://pypi.org/simple"


def can_reach_pypi(timeout_seconds: float = 1.5) -> bool:
    """Quickly probe if official PyPI is directly accessible."""
    try:
        with socket.create_connection(("pypi.org", 443), timeout=timeout_seconds):
            return True
    except Exception:
        return False


def resolve_pip_index_args() -> list[str]:
    """Determine pip index arguments based on environment or network accessibility."""
    # Respect explicit user-configured PIP_INDEX_URL
    if os.environ.get("PIP_INDEX_URL"):
        return []

    # Check network reachability
    if can_reach_pypi():
        return []

    # If PyPI is blocked or slow (common in domestic environments), switch to Tsinghua & Aliyun mirrors
    return [
        "-i",
        DOMESTIC_PIP_INDEX,
        "--extra-index-url",
        DOMESTIC_PIP_EXTRA_INDEX,
    ]


def is_module_installed(import_name: str) -> bool:
    """Check if a Python module is importable."""
    try:
        return importlib.util.find_spec(import_name) is not None
    except Exception:
        return False


def refresh_installed_runtime_state(import_name: str | None = None) -> None:
    """Invalidate module and dependency caches across the process so newly installed packages are immediately visible."""
    importlib.invalidate_caches()
    if import_name:
        for mod_name in list(sys.modules.keys()):
            if mod_name == import_name or mod_name.startswith(f"{import_name}."):
                # If module was previously in failed/None state or partially imported, delete it so re-import succeeds
                if sys.modules.get(mod_name) is None:
                    sys.modules.pop(mod_name, None)
    try:
        from core.dependency_registry import invalidate_dependency_cache
        invalidate_dependency_cache()
    except Exception:
        pass


def ensure_runtime_dependency(
    package_name: str,
    *,
    import_name: str | None = None,
    timeout_seconds: int = 180,
) -> tuple[bool, str | None]:
    """Ensure an on-demand dependency is installed into the managed Engine runtime.

    Uses the current managed Python interpreter (sys.executable) and applies
    automatic domestic mirror switching if pypi.org is unreachable.
    Guarded by interprocess locking to avoid concurrent installation race conditions.
    """
    effective_import = import_name or package_name.replace("-", "_")
    if is_module_installed(effective_import):
        return True, None

    lock_dir = V8_AGENT_OS_HOME / ".locks"
    try:
        lock_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    safe_pkg_name = re.sub(r"[^A-Za-z0-9_.-]", "_", package_name)
    lock_file = lock_dir / f"dep_install_{safe_pkg_name}.lock"

    try:
        with interprocess_file_lock(lock_file, timeout_seconds=float(timeout_seconds)):
            # Double check after acquiring lock
            refresh_installed_runtime_state(effective_import)
            if is_module_installed(effective_import):
                return True, None

            logger.info("JIT installing on-demand dependency: %s via %s", package_name, sys.executable)
            index_args = resolve_pip_index_args()
            cmd = [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                package_name,
                *index_args,
            ]

            env = dict(os.environ)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
            )

            refresh_installed_runtime_state(effective_import)

            if result.returncode != 0:
                err_summary = result.stderr.strip() or result.stdout.strip() or f"exit code {result.returncode}"
                logger.error("Failed to install dependency %s: %s", package_name, err_summary)
                return False, f"安装依赖 {package_name} 失败: {err_summary}"

            if not is_module_installed(effective_import):
                return False, f"依赖 {package_name} 安装完成，但无法定位导入模块 {effective_import}"

            logger.info("Successfully installed on-demand dependency: %s", package_name)
            return True, None
    except Exception as exc:
        logger.exception("Error during JIT dependency installation for %s", package_name)
        return False, f"按需安装依赖 {package_name} 异常: {exc}"
