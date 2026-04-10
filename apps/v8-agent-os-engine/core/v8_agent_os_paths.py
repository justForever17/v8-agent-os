from __future__ import annotations

"""V8 Agent OS canonical runtime path helpers."""

from pathlib import Path

V8_AGENT_OS_HOME = Path.home() / ".v8-agent-os"
V8_AGENT_OS_CORE_PATH = V8_AGENT_OS_HOME / "core"
OAUTH_CORE_PATH = V8_AGENT_OS_CORE_PATH / "oauth"
OAUTH_PROVIDERS_PATH = OAUTH_CORE_PATH / "providers"
WORKSPACE_HOME = V8_AGENT_OS_HOME / "workspace"
RUNTIME_DATA_HOME = V8_AGENT_OS_HOME / "runtime-data"
CONFIG_JSON_PATH = V8_AGENT_OS_HOME / "config.json"
COMPUTER_USE_JSON_PATH = V8_AGENT_OS_HOME / "computer_use.json"
PLUGIN_JSON_PATH = V8_AGENT_OS_HOME / "plugin.json"
NETWORK_SUPERVISOR_SECRETS_PATH = V8_AGENT_OS_HOME / "network_supervisor_secrets.json"
NETWORK_SUPERVISOR_STATE_PATH = V8_AGENT_OS_HOME / "network_supervisor_state.json"
OPENCLAW_DEFAULT_STATE_ROOT = Path.home() / ".openclaw"
PLUGIN_HOST_ROOT = V8_AGENT_OS_HOME / "plugins" / "openclaw"
PLUGIN_EXTENSIONS_ROOT = PLUGIN_HOST_ROOT / "extensions"
PLUGIN_HOST_TOOLING_ROOT = PLUGIN_HOST_ROOT / "tooling" / "openclaw-cli"
PLUGIN_INSTALL_LOG_ROOT = V8_AGENT_OS_HOME / "logs" / "plugins"
LEGACY_CONFIG_BACKUP_ROOT = V8_AGENT_OS_HOME / "_legacy_config_backup"
STATE_DB_PATH = V8_AGENT_OS_HOME / "state.db"
CHECKPOINT_DB_PATH = V8_AGENT_OS_HOME / "checkpoints.db"
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


def openclaw_outbound_media_root(*segments: str) -> Path:
    root = OPENCLAW_DEFAULT_STATE_ROOT / "media" / "outbound"
    for segment in segments:
        normalized = _safe_path_segment(segment, fallback="segment")
        root = root / normalized
    return root


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
