from __future__ import annotations

from pathlib import Path
from typing import Any

from core.interprocess_lock import interprocess_file_lock
from core.storage import storage
from core.v8_agent_os_paths import V8_AGENT_OS_HOME


_MCP_CONFIG_LOCK_TIMEOUT_SECONDS = 30.0


class _LazyExtensionsRuntimeService:
    def __getattr__(self, name: str):
        from runtimes.extensions.runtime import extensions_runtime_service

        return getattr(extensions_runtime_service, name)


extensions_runtime_service = _LazyExtensionsRuntimeService()


def _mcp_config_lock_path() -> Path:
    return V8_AGENT_OS_HOME / "locks" / "extensions" / "mcp-config.lock"


class McpConfigValidationError(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


def validate_mcp_server_map(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(config, dict):
        raise McpConfigValidationError("invalid_payload", "MCP 配置必须是 JSON 对象。")

    server_map = config.get("mcpServers") if "mcpServers" in config else config
    if not isinstance(server_map, dict):
        raise McpConfigValidationError("invalid_server_map", "`mcpServers` 必须是对象映射。")
    if not server_map:
        raise McpConfigValidationError("empty_server_map", "MCP 配置中至少需要包含一个 server。")

    normalized: dict[str, dict[str, Any]] = {}
    for raw_name, raw_server in server_map.items():
        server_name = str(raw_name or "").strip()
        if not server_name:
            raise McpConfigValidationError("empty_server_name", "MCP server 名称不能为空。")
        if not isinstance(raw_server, dict):
            raise McpConfigValidationError(
                "invalid_server_payload",
                f"MCP server `{server_name}` 的配置必须是对象。",
            )

        server = dict(raw_server)
        if "command" in server and not isinstance(server.get("command"), str):
            raise McpConfigValidationError("invalid_command", f"MCP server `{server_name}` 的 command 必须是字符串。")
        if "url" in server and not isinstance(server.get("url"), str):
            raise McpConfigValidationError("invalid_url", f"MCP server `{server_name}` 的 url 必须是字符串。")
        if "args" in server and not isinstance(server.get("args"), list):
            raise McpConfigValidationError("invalid_args", f"MCP server `{server_name}` 的 args 必须是数组。")
        if "env" in server and not isinstance(server.get("env"), dict):
            raise McpConfigValidationError("invalid_env", f"MCP server `{server_name}` 的 env 必须是对象。")
        if "headers" in server and not isinstance(server.get("headers"), dict):
            raise McpConfigValidationError("invalid_headers", f"MCP server `{server_name}` 的 headers 必须是对象。")

        disabled = bool(server.get("disabled", False))
        raw_type = str(server.get("type") or server.get("transport") or "").strip().lower()
        type_aliases = {
            "stdio": "stdio",
            "http": "http",
            "streamable_http": "http",
            "streamable-http": "http",
            "sse": "sse",
        }
        transport_type = type_aliases.get(raw_type)
        if not transport_type:
            raise McpConfigValidationError(
                "missing_or_invalid_type",
                f"MCP server `{server_name}` 必须声明 type: stdio、http 或 sse。",
            )
        server["type"] = transport_type
        server.pop("transport", None)
        command = str(server.get("command") or "").strip()
        url = str(server.get("url") or "").strip()
        if not disabled and transport_type == "stdio" and not command:
            raise McpConfigValidationError(
                "missing_command",
                f"MCP server `{server_name}` 使用 stdio 时必须提供 command。",
            )
        if not disabled and transport_type in {"http", "sse"} and not url:
            raise McpConfigValidationError(
                "missing_url",
                f"MCP server `{server_name}` 使用 {transport_type} 时必须提供 url。",
            )
        if not disabled and not command and not url:
            raise McpConfigValidationError(
                "missing_target",
                f"MCP server `{server_name}` 至少需要提供 command 或 url。",
            )
        normalized[server_name] = server

    return normalized


def request_mcp_inventory_refresh(reason: str) -> None:
    try:
        extensions_runtime_service.request_mcp_inventory_refresh(reason=reason)
    except Exception:
        # Config save should remain the source of truth even if the async inventory refresh queue is unavailable.
        return


def install_mcp_server_config(config: dict[str, Any], *, refresh_reason: str = "mcp_config_tool_install") -> dict[str, Any]:
    new_servers = validate_mcp_server_map(config)
    with interprocess_file_lock(
        _mcp_config_lock_path(),
        timeout_seconds=_MCP_CONFIG_LOCK_TIMEOUT_SECONDS,
    ):
        existing = storage.get_mcp_config() or {"mcpServers": {}}
        existing_servers = existing.get("mcpServers", {})
        if not isinstance(existing_servers, dict):
            existing_servers = {}
        next_servers = dict(existing_servers)
        replaced_servers = sorted(name for name in new_servers if name in next_servers)
        next_servers.update(new_servers)
        storage.save_mcp_config({"mcpServers": next_servers})
    request_mcp_inventory_refresh(refresh_reason)
    return {
        "status": "success",
        "installedServers": sorted(new_servers),
        "replacedServers": replaced_servers,
        "serverCount": len(next_servers),
        "refreshRequested": True,
    }


def remove_mcp_server_config(server_name: str, *, refresh_reason: str = "mcp_config_tool_remove") -> dict[str, Any]:
    normalized_name = str(server_name or "").strip()
    if not normalized_name:
        raise McpConfigValidationError("empty_server_name", "MCP server 名称不能为空。")
    with interprocess_file_lock(
        _mcp_config_lock_path(),
        timeout_seconds=_MCP_CONFIG_LOCK_TIMEOUT_SECONDS,
    ):
        existing = storage.get_mcp_config() or {"mcpServers": {}}
        existing_servers = existing.get("mcpServers", {})
        if not isinstance(existing_servers, dict):
            existing_servers = {}
        removed = normalized_name in existing_servers
        next_servers = dict(existing_servers)
        next_servers.pop(normalized_name, None)
        if removed:
            storage.save_mcp_config({"mcpServers": next_servers})
    request_mcp_inventory_refresh(refresh_reason)
    return {
        "status": "success",
        "removedServer": normalized_name,
        "alreadyAbsent": not removed,
        "serverCount": len(next_servers),
        "refreshRequested": removed,
    }


def list_mcp_server_configs() -> dict[str, Any]:
    existing = storage.get_mcp_config() or {"mcpServers": {}}
    server_map = existing.get("mcpServers", {})
    if not isinstance(server_map, dict):
        server_map = {}
    servers: list[dict[str, Any]] = []
    for name, payload in sorted(server_map.items()):
        server = payload if isinstance(payload, dict) else {}
        env = server.get("env") if isinstance(server.get("env"), dict) else {}
        headers = server.get("headers") if isinstance(server.get("headers"), dict) else {}
        servers.append(
            {
                "name": str(name),
                "type": str(server.get("type") or server.get("transport") or ""),
                "disabled": bool(server.get("disabled", False)),
                "command": str(server.get("command") or ""),
                "url": str(server.get("url") or ""),
                "argsCount": len(server.get("args") or []) if isinstance(server.get("args"), list) else 0,
                "envKeys": sorted(str(key) for key in env),
                "headerKeys": sorted(str(key) for key in headers),
            }
        )
    return {"servers": servers, "serverCount": len(servers)}


def mcp_runtime_status_snapshot() -> dict[str, Any]:
    try:
        return {
            "servers": extensions_runtime_service.get_mcp_status(),
            "health": extensions_runtime_service.get_mcp_health_summary(),
            "startup": extensions_runtime_service.get_mcp_startup_status(),
        }
    except Exception as exc:
        return {"error": str(exc)}
