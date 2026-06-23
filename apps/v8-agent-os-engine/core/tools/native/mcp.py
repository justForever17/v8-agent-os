from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from core.mcp_config_service import (
    McpConfigValidationError,
    install_mcp_server_config,
    list_mcp_server_configs,
    mcp_runtime_status_snapshot,
    remove_mcp_server_config,
)

__all__ = ["mcp_server_config"]


def _coerce_string_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
        return [line.strip() for line in text.splitlines() if line.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _coerce_mapping(value: Any) -> dict[str, str]:
    if value in (None, "", {}):
        return {}
    if isinstance(value, dict):
        return {str(key).strip(): str(item) for key, item in value.items() if str(key).strip()}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return {str(key).strip(): str(item) for key, item in parsed.items() if str(key).strip()}
        except Exception:
            pass
        result: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or "=" not in stripped:
                continue
            key, item = stripped.split("=", 1)
            normalized_key = key.strip()
            if normalized_key:
                result[normalized_key] = item.strip()
        return result
    return {}


def _server_payload(
    *,
    name: str,
    type: str,
    command: str,
    args: Any,
    url: str,
    env: Any,
    headers: Any,
    disabled: bool,
) -> dict[str, Any]:
    server: dict[str, Any] = {
        "type": str(type or "").strip().lower(),
        "disabled": bool(disabled),
    }
    normalized_command = str(command or "").strip()
    normalized_url = str(url or "").strip()
    if normalized_command:
        server["command"] = normalized_command
    normalized_args = _coerce_string_list(args)
    if normalized_args:
        server["args"] = normalized_args
    if normalized_url:
        server["url"] = normalized_url
    normalized_env = _coerce_mapping(env)
    if normalized_env:
        server["env"] = normalized_env
    normalized_headers = _coerce_mapping(headers)
    if normalized_headers:
        server["headers"] = normalized_headers
    return {"mcpServers": {str(name or "").strip(): server}}


def _list_markdown() -> str:
    payload = list_mcp_server_configs()
    servers = payload.get("servers") or []
    if not servers:
        return "MCP server 配置为空。\n\n下一步：如用户要求安装 MCP server，调用 `mcp_server_config(mode='mcp_install', ...)`。"
    lines = [f"MCP server 配置共 {payload.get('serverCount', len(servers))} 个："]
    for server in servers:
        target = server.get("command") or server.get("url") or "未设置目标"
        disabled = "（已停用）" if server.get("disabled") else ""
        extras: list[str] = []
        if server.get("argsCount"):
            extras.append(f"args {server.get('argsCount')}")
        if server.get("envKeys"):
            extras.append("env: " + ", ".join(server.get("envKeys") or []))
        if server.get("headerKeys"):
            extras.append("headers: " + ", ".join(server.get("headerKeys") or []))
        suffix = f"；{'; '.join(extras)}" if extras else ""
        lines.append(f"- {server.get('name')}: {server.get('type') or 'unknown'} -> {target}{disabled}{suffix}")
    return "\n".join(lines)


def _status_markdown() -> str:
    payload = mcp_runtime_status_snapshot()
    if payload.get("error"):
        return f"MCP runtime 状态读取失败：{payload.get('error')}"
    health = payload.get("health") or {}
    startup = payload.get("startup") or {}
    status = payload.get("servers") or {}
    lines = [
        f"MCP runtime 状态：{health.get('status') or startup.get('startupState') or 'unknown'}",
        f"- 启动状态：{startup.get('startupState') or 'unknown'}",
        f"- server 数：{len(status) if isinstance(status, dict) else 0}",
    ]
    if isinstance(status, dict):
        for name, server in sorted(status.items()):
            if not isinstance(server, dict):
                continue
            lines.append(f"- {name}: {server.get('status') or server.get('state') or 'unknown'}")
    return "\n".join(lines)


@tool
def mcp_server_config(
    mode: str,
    name: str = "",
    type: str = "",
    command: str = "",
    command_args: Any = None,
    url: str = "",
    env: Any = None,
    headers: Any = None,
    disabled: bool = False,
) -> str:
    """Configure installed MCP servers through the governed Engine config service.

    Use this when the user explicitly asks to install, list, remove, or inspect an MCP server.
    Do not edit `~/.v8-agent-os/mcp.json` directly and do not call Admin login-only APIs.

    Modes:
    - `mcp_install`: add or replace one MCP server. Required: `name`, `type`.
      For `type='stdio'`, provide `command` and optional `command_args` / `env`.
      For `type='http'` or `type='sse'`, provide `url` and optional `headers`.
    - `mcp_list`: list configured MCP servers without exposing secret values.
    - `mcp_remove`: remove one server by `name`.
    - `mcp_status`: inspect current Extensions/MCP runtime status.

    `env` and `headers` may be JSON objects or newline `KEY=value` text. Installing a server only changes MCP config and requests an Extensions refresh; it does not grant permission to bypass runtime gates.
    """
    normalized_mode = str(mode or "").strip().lower()
    try:
        if normalized_mode == "mcp_list":
            return _list_markdown()
        if normalized_mode == "mcp_status":
            return _status_markdown()
        if normalized_mode == "mcp_remove":
            result = remove_mcp_server_config(name)
            if result.get("alreadyAbsent"):
                return f"MCP server `{result.get('removedServer')}` 原本不存在；当前配置未变。"
            return (
                f"已移除 MCP server `{result.get('removedServer')}`。\n"
                f"- 当前 server 数：{result.get('serverCount')}\n"
                "- 已请求 Extensions Runtime 刷新。"
            )
        if normalized_mode == "mcp_install":
            payload = _server_payload(
                name=name,
                type=type,
                command=command,
                args=command_args,
                url=url,
                env=env,
                headers=headers,
                disabled=disabled,
            )
            result = install_mcp_server_config(payload)
            installed = ", ".join(result.get("installedServers") or [])
            replaced = result.get("replacedServers") or []
            lines = [
                f"已配置 MCP server：{installed or str(name or '').strip() or 'unknown'}。",
                f"- 当前 server 数：{result.get('serverCount')}",
                "- 已请求 Extensions Runtime 刷新。",
            ]
            if replaced:
                lines.append("- 注意：同名 server 已被替换：" + ", ".join(replaced))
            lines.append("下一步：可调用 `mcp_server_config(mode='mcp_status')` 查看连接状态。")
            return "\n".join(lines)
        return "mcp_server_config 参数错误：mode 必须是 mcp_install、mcp_list、mcp_remove 或 mcp_status。"
    except McpConfigValidationError as exc:
        return f"MCP server 配置无效：{exc.message}"
    except Exception as exc:
        return f"MCP server 配置失败：{str(exc)}"
