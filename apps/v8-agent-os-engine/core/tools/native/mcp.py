from __future__ import annotations

import json
import re
import warnings
from typing import Any

from langchain_core.tools import tool

from core.mcp_config_service import (
    McpConfigValidationError,
    install_mcp_server_config,
    list_mcp_server_configs,
    mcp_runtime_status_snapshot,
    remove_mcp_server_config,
)
from core.config_broker_service import ConfigBrokerError, config_broker_service
from erc.runtime_context import get_runtime_context

__all__ = ["config_broker", "mcp_server_config"]


_SECRET_ARG_RE = re.compile(
    r"(?i)^(?:--?)?(?:api[-_]?key|access[-_]?token|token|secret|password|authorization|cookie)(?:=|:|$)"
)
_SECRET_ENV_ASSIGNMENT_RE = re.compile(r"(?i)^[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|COOKIE)\s*=")


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


def _reject_secret_bearing_args(values: list[str]) -> None:
    for value in values:
        normalized = str(value or "").strip()
        if _SECRET_ARG_RE.search(normalized) or _SECRET_ENV_ASSIGNMENT_RE.search(normalized):
            raise ConfigBrokerError(
                "commandArgs 不能携带凭据；请声明 credentialRequirements 并使用安全动作卡。",
                code="config_secret_in_command_args",
                status_code=422,
            )


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


def _coerce_boolean_mapping(value: Any) -> dict[str, bool]:
    if value in (None, "", {}):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            value = {}
    if not isinstance(value, dict):
        return {}
    return {str(key).strip(): bool(item) for key, item in value.items() if str(key).strip()}


def _runtime_identity() -> tuple[str, str, str]:
    context = dict(get_runtime_context() or {})
    return (
        str(context.get("user_id") or context.get("userId") or "").strip(),
        str(context.get("session_id") or context.get("sessionId") or "").strip(),
        str(context.get("run_id") or context.get("runId") or "").strip(),
    )


@tool
def config_broker(
    mode: str,
    category: str = "",
    query: str = "",
    limit: int = 20,
    offset: int = 0,
    provider_id: str = "",
    provider_name: str = "",
    model_id: str = "",
    model_ref: str = "",
    base_url: str = "",
    api_standard: str = "openai",
    model_type: str = "TEXT",
    context_window: int | None = None,
    max_tokens: int | None = None,
    capabilities: Any = None,
    evidence_refs: list[str] | None = None,
    credential_required: bool = True,
    role: str = "",
    transaction_id: str = "",
    plan_digest: str = "",
    mcp_name: str = "",
    mcp_type: str = "",
    command: str = "",
    command_args: Any = None,
    url: str = "",
    disabled: bool = False,
    credential_requirements: list[dict[str, Any]] | None = None,
) -> str:
    """Inspect and change model/MCP configuration through one recoverable control plane.

    Supervisor only. Use `models` to list models by category, `role_matrix` to
    inspect model consumers, and `recommend` before changing a role. Use
    `agent:<agent-id>` as the role when inspecting or updating one registered
    Subagent; grandchild agents inherit and have no independent model binding.
    `model_prepare` with researched facts and evidence refs, then `commit` with
    the returned transaction_id and plan_digest. Web research is accepted as
    reviewed evidence; it is not silently promoted above a user's saved facts.
    Never pass API keys, tokens, cookies, env values or authorization headers to
    this tool. When a credential is required, `model_prepare` or
    `mcp_prepare_install` returns a one-time UI:// action card for the user.

    MCP modes are `mcp_list`, `mcp_status`, `mcp_prepare_install` and
    `mcp_prepare_remove`. Configuration commits are durable and expose
    `status`/`rollback`. Doctor validates model facts, Safety checks the exact
    credential target, and the transaction service alone commits or restores.
    """

    normalized_mode = str(mode or "").strip().lower()
    owner_id, session_id, run_id = _runtime_identity()
    try:
        if normalized_mode in {"models", "model_list", "inventory"}:
            payload = config_broker_service.inventory(category=category, query=query, limit=limit, offset=offset)
        elif normalized_mode == "role_matrix":
            payload = config_broker_service.role_matrix()
        elif normalized_mode == "recommend":
            payload = config_broker_service.recommend(role=role, limit=limit)
        elif normalized_mode == "model_prepare":
            payload = config_broker_service.prepare_model(
                provider_id=provider_id,
                model_id=model_id,
                provider_name=provider_name,
                base_url=base_url,
                api_standard=api_standard,
                model_type=model_type,
                context_window=context_window,
                max_tokens=max_tokens,
                capabilities=_coerce_boolean_mapping(capabilities),
                evidence_refs=evidence_refs,
                credential_required=credential_required,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "role_prepare":
            payload = config_broker_service.prepare_role_assignment(
                role=role,
                model_ref=model_ref,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "commit":
            transaction = config_broker_service.get_transaction(transaction_id, owner_id=owner_id)
            if not plan_digest or str(transaction.get("planDigest") or "") != str(plan_digest).strip():
                raise ConfigBrokerError("提交需要匹配当前事务的 planDigest。", code="config_plan_digest_mismatch", status_code=409)
            payload = config_broker_service.commit(transaction_id, owner_id=owner_id)
        elif normalized_mode == "status":
            payload = {"ok": True, "mode": "status", **config_broker_service.get_transaction(transaction_id, owner_id=owner_id)}
        elif normalized_mode == "rollback":
            payload = config_broker_service.rollback(transaction_id, owner_id=owner_id)
        elif normalized_mode == "mcp_list":
            payload = config_broker_service.mcp_list()
        elif normalized_mode == "mcp_status":
            payload = config_broker_service.mcp_status()
        elif normalized_mode == "mcp_prepare_install":
            server: dict[str, Any] = {"type": str(mcp_type or "").strip().lower(), "disabled": bool(disabled)}
            if command:
                server["command"] = str(command).strip()
            args = _coerce_string_list(command_args)
            _reject_secret_bearing_args(args)
            if args:
                server["args"] = args
            if url:
                server["url"] = str(url).strip()
            payload = config_broker_service.prepare_mcp(
                operation="install",
                name=mcp_name,
                server=server,
                credential_requirements=credential_requirements,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        elif normalized_mode == "mcp_prepare_remove":
            payload = config_broker_service.prepare_mcp(
                operation="remove",
                name=mcp_name,
                server=None,
                credential_requirements=None,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
            )
        else:
            raise ConfigBrokerError("不支持的 config_broker mode。", code="config_broker_mode_invalid")
        return json.dumps(payload, ensure_ascii=False)
    except ConfigBrokerError as exc:
        return json.dumps(
            {
                "ok": False,
                "mode": normalized_mode,
                "state": "blocked",
                "summary": str(exc),
                "error": {"code": exc.code, "message": str(exc)},
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "mode": normalized_mode,
                "state": "failed",
                "summary": "配置控制面执行失败。",
                "error": {"code": "config_broker_failed", "message": str(exc)},
            },
            ensure_ascii=False,
        )


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
    warnings.warn("mcp_server_config is deprecated; use config_broker", DeprecationWarning, stacklevel=2)
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
