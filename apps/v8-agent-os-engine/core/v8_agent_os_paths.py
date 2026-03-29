from __future__ import annotations

"""V8 Agent OS canonical runtime path helpers."""

from pathlib import Path

V8_AGENT_OS_HOME = Path.home() / ".v8-agent-os"
V8_AGENT_OS_CORE_PATH = V8_AGENT_OS_HOME / "core"
OAUTH_CORE_PATH = V8_AGENT_OS_CORE_PATH / "oauth"
OAUTH_PROVIDERS_PATH = OAUTH_CORE_PATH / "providers"
WORKSPACE_HOME = V8_AGENT_OS_HOME / "workspace"
CONFIG_JSON_PATH = V8_AGENT_OS_HOME / "config.json"
COMPUTER_USE_JSON_PATH = V8_AGENT_OS_HOME / "computer_use.json"
PLUGIN_JSON_PATH = V8_AGENT_OS_HOME / "plugin.json"
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


def protected_runtime_paths(*, include_home: bool = True) -> list[str]:
    paths: list[str] = []
    if include_home:
        paths.append(str(V8_AGENT_OS_HOME))
    paths.append(str(STATE_DB_PATH))
    paths.append(str(CHECKPOINT_DB_PATH))
    paths.append(str(V8_AGENT_OS_TMP_PATH))
    return paths
