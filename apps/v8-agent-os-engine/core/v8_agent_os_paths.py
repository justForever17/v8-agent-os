from __future__ import annotations

"""V8 Agent OS canonical runtime path helpers."""

import os
from pathlib import Path


def _resolve_v8_agent_os_home() -> Path:
    configured = str(os.environ.get("V8_AGENT_OS_HOME") or "").strip()
    return Path(configured).expanduser().resolve(strict=False) if configured else Path.home() / ".v8-agent-os"


V8_AGENT_OS_HOME = _resolve_v8_agent_os_home()
V8_AGENT_OS_CORE_PATH = V8_AGENT_OS_HOME / "core"
OAUTH_CORE_PATH = V8_AGENT_OS_CORE_PATH / "oauth"
OAUTH_PROVIDERS_PATH = OAUTH_CORE_PATH / "providers"
WORKSPACE_HOME = V8_AGENT_OS_HOME / "workspace"
RUNTIME_DATA_HOME = V8_AGENT_OS_HOME / "runtime-data"
CONFIG_JSON_PATH = V8_AGENT_OS_HOME / "config.json"
MCP_JSON_PATH = V8_AGENT_OS_HOME / "mcp.json"
COMPUTER_USE_JSON_PATH = V8_AGENT_OS_HOME / "computer_use.json"
PLUGIN_MANAGER_ROOT = V8_AGENT_OS_HOME / "plugins"
PLUGIN_MANAGER_BIN_ROOT = V8_AGENT_OS_HOME / "bin"
PLUGIN_MANAGER_CACHE_ROOT = V8_AGENT_OS_HOME / "cache" / "plugin-catalog"
PLUGIN_MANAGER_LOG_ROOT = V8_AGENT_OS_HOME / "logs" / "plugins"
NETWORK_SUPERVISOR_SECRETS_PATH = V8_AGENT_OS_HOME / "network_supervisor_secrets.json"
NETWORK_SUPERVISOR_STATE_PATH = V8_AGENT_OS_HOME / "network_supervisor_state.json"
LEGACY_CONFIG_BACKUP_ROOT = V8_AGENT_OS_HOME / "_legacy_config_backup"
STATE_DB_PATH = V8_AGENT_OS_HOME / "state.db"
CHECKPOINT_DB_PATH = V8_AGENT_OS_HOME / "checkpoints.db"
OBSERVABILITY_DB_PATH = V8_AGENT_OS_HOME / "observability.db"
V8_AGENT_OS_TMP_PATH = V8_AGENT_OS_HOME / "tmp"
V8_AGENT_OS_TEST_TMP_PATH = V8_AGENT_OS_TMP_PATH / "tests"
WORKSPACE_ARTIFACT_NAMESPACE = ".v8-agent-os"


def ensure_v8_agent_os_tmp_path(*, scope: str = "runtime") -> Path:
    normalized = str(scope or "runtime").strip().lower()
    if normalized in {"test", "tests"}:
        path = V8_AGENT_OS_TEST_TMP_PATH
    elif normalized in {"benchmark", "benchmarks"}:
        path = V8_AGENT_OS_TMP_PATH / "benchmarks"
    else:
        path = V8_AGENT_OS_TMP_PATH / normalized if normalized and normalized != "runtime" else V8_AGENT_OS_TMP_PATH
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_path_segment(value: str | None, *, fallback: str) -> str:
    normalized = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-"
        for char in str(value or "").strip()
    ).strip(" .-_")
    return normalized or fallback


def runtime_private_root(runtime_name: str) -> Path:
    return RUNTIME_DATA_HOME / _safe_path_segment(runtime_name, fallback="runtime")


def workspace_download_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root).expanduser() / "downloaded_media"


def workspace_artifacts_root(workspace_root: str | Path) -> Path:
    return Path(workspace_root).expanduser() / WORKSPACE_ARTIFACT_NAMESPACE / "artifacts"


def workspace_artifact_run_root(
    workspace_root: str | Path,
    *,
    session_id: str | None,
    run_id: str | None,
) -> Path:
    root = workspace_artifacts_root(workspace_root)
    root = root / _safe_path_segment(session_id, fallback="session")
    root = root / _safe_path_segment(run_id, fallback="run")
    return root


def workspace_artifact_item_root(
    workspace_root: str | Path,
    *,
    session_id: str | None,
    run_id: str | None,
    artifact_id: str | None,
) -> Path:
    return workspace_artifact_run_root(
        workspace_root,
        session_id=session_id,
        run_id=run_id,
    ) / _safe_path_segment(artifact_id, fallback="artifact")


def protected_runtime_paths(*, include_home: bool = True) -> list[str]:
    paths: list[str] = []
    if include_home:
        paths.append(str(V8_AGENT_OS_HOME))
    paths.append(str(STATE_DB_PATH))
    paths.append(str(CHECKPOINT_DB_PATH))
    paths.append(str(V8_AGENT_OS_TMP_PATH))
    return paths
