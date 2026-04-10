from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SUPPORTED_WEIXIN_PLUGIN_ID = "openclaw-weixin"
SUPPORTED_WEIXIN_PACKAGE = "@tencent-weixin/openclaw-weixin"
PATCH_SENTINEL = "__V8_AGENT_OS_PLUGIN_HOST_HANDOFF_PATCH_V4__"
LEGACY_PATCH_SENTINELS = (
    "__V8_AGENT_OS_PLUGIN_HOST_HANDOFF_PATCH__",
    "__V8_AGENT_OS_PLUGIN_HOST_HANDOFF_PATCH_V2__",
    "__V8_AGENT_OS_PLUGIN_HOST_HANDOFF_PATCH_V3__",
)
PATCH_MARKER = f"/* {PATCH_SENTINEL} */"

PROCESS_MESSAGE_RELATIVE_PATH = Path("src") / "messaging" / "process-message.ts"
HANDOFF_MODULE_RELATIVE_PATH = Path("src") / "messaging" / "process-message.v8-agent-os-handoff.ts"
MONITOR_RELATIVE_PATH = Path("src") / "monitor" / "monitor.ts"
PACKAGE_JSON_RELATIVE_PATH = Path("package.json")
BACKUP_FILE_NAME = "process-message.v8-agent-os-orig.ts"
HANDOFF_MODULE_SENTINEL = "__V8_AGENT_OS_PLUGIN_HOST_HANDOFF_ENTRY_V1__"
HANDOFF_MODULE_MARKER = f"/* {HANDOFF_MODULE_SENTINEL} */"
MONITOR_BACKUP_FILE_NAME = "monitor.v8-agent-os-orig.ts"
MONITOR_PATCH_SENTINEL = "__V8_AGENT_OS_PLUGIN_HOST_MONITOR_PATCH_V1__"
MONITOR_PATCH_MARKER = f"/* {MONITOR_PATCH_SENTINEL} */"
ACCOUNTS_RELATIVE_PATH = Path("src") / "auth" / "accounts.ts"
ACCOUNTS_BACKUP_FILE_NAME = "accounts.v8-agent-os-orig.ts"
ACCOUNTS_PATCH_SENTINEL = "__V8_AGENT_OS_PLUGIN_HOST_CONFIG_PRESERVE_PATCH_V1__"
ACCOUNTS_PATCH_MARKER = f"/* {ACCOUNTS_PATCH_SENTINEL} */"
GATEWAY_CMD_RELATIVE_PATH = Path("gateway.cmd")
GATEWAY_BACKUP_FILE_NAME = "gateway.v8-agent-os-orig.cmd"
GATEWAY_PATCH_SENTINEL = "__V8_AGENT_OS_PLUGIN_HOST_GATEWAY_PATCH_V1__"

HELPER_ANCHOR = """/**
 * Process a single inbound message: route → download media → dispatch reply.
 * Extracted from the monitor loop to keep monitoring and message handling separate.
 */
"""

ROUTE_ANCHOR = """  const route = deps.channelRuntime.routing.resolveAgentRoute({
"""
MONITOR_IMPORT_ANCHOR = 'import { processOneMessage } from "../messaging/process-message.js";'
HANDOFF_MONITOR_IMPORT = 'import { processOneMessage } from "../messaging/process-message.v8-agent-os-handoff.js";'
ACCOUNTS_RELOAD_ANCHOR = """export async function triggerWeixinChannelReload(): Promise<void> {
  try {
    const { loadConfig, writeConfigFile } = await import("openclaw/plugin-sdk/config-runtime");
    const cfg = loadConfig();
    const channels = (cfg.channels ?? {}) as Record<string, unknown>;
    const existing = (channels["openclaw-weixin"] as Record<string, unknown> | undefined) ?? {};
    const updated: OpenClawConfig = {
      ...cfg,
      channels: {
        ...channels,
        "openclaw-weixin": {
          ...existing,
          channelConfigUpdatedAt: new Date().toISOString(),
        },
      },
    };
    await writeConfigFile(updated);
    logger.info("triggerWeixinChannelReload: wrote channel config to openclaw.json");
  } catch (err) {
    logger.warn(`triggerWeixinChannelReload: failed to update config: ${String(err)}`);
  }
}"""


def _handoff_module_content(process_message_content: str) -> str:
    if HANDOFF_MODULE_MARKER in process_message_content:
        return process_message_content
    return process_message_content.replace(
        PATCH_MARKER,
        PATCH_MARKER + "\n" + HANDOFF_MODULE_MARKER,
        1,
    )


def _patched_accounts_reload_block() -> str:
    return f"""export async function triggerWeixinChannelReload(): Promise<void> {{
  try {{
    const configPath = resolveOpenClawConfigPath();
    let current: (OpenClawConfig & Record<string, unknown>) | null = null;
    if (fs.existsSync(configPath)) {{
      try {{
        const raw = fs.readFileSync(configPath, "utf-8");
        const parsed = raw.trim() ? JSON.parse(raw) : {{}};
        if (parsed && typeof parsed === "object") {{
          current = parsed as OpenClawConfig & Record<string, unknown>;
        }}
      }} catch {{
        current = null;
      }}
    }}
    if (!current) {{
      const {{ loadConfig }} = await import("openclaw/plugin-sdk/config-runtime");
      current = (loadConfig() as OpenClawConfig & Record<string, unknown>) ?? {{}};
    }}
    const channels = (current.channels ?? {{}}) as Record<string, unknown>;
    const existing = (channels["openclaw-weixin"] as Record<string, unknown> | undefined) ?? {{}};
    const updated: OpenClawConfig & Record<string, unknown> = {{
      ...current,
      channels: {{
        ...channels,
        "openclaw-weixin": {{
          ...existing,
          channelConfigUpdatedAt: new Date().toISOString(),
        }},
      }},
    }};
    fs.writeFileSync(configPath, JSON.stringify(updated, null, 2), "utf-8");
    logger.info("triggerWeixinChannelReload: wrote channel config to openclaw.json");
  }} catch (err) {{
    logger.warn(`triggerWeixinChannelReload: failed to update config: ${{String(err)}}`);
  }}
}}
{ACCOUNTS_PATCH_MARKER}"""

def _helper_block(engine_base_url: str) -> str:
    inbound_url = f"{engine_base_url.rstrip('/')}/v1/plugin-host/inbound"
    inbound_url_literal = json.dumps(inbound_url, ensure_ascii=False)
    return f"""const V8_HANDOFF_HEADER = "openclaw-weixin";
const V8_HANDOFF_DEFAULT_URL = {inbound_url_literal};

async function resolveV8HandoffConfig(): Promise<{{ handoffUrl: string; handoffToken: string }}> {{
  const envUrl = (process.env.V8_AGENT_OS_PLUGIN_HOST_INBOUND_URL?.trim() || "").trim();
  const envToken = (process.env.V8_AGENT_OS_PLUGIN_HOST_HANDOFF_TOKEN?.trim() || "").trim();
  if (envUrl && envToken) {{
    return {{
      handoffUrl: envUrl,
      handoffToken: envToken,
    }};
  }}
  try {{
    const fsMod = await import("node:fs");
    const {{ resolveStateDir }} = await import("../storage/state-dir.js");
    const configPath = path.join(resolveStateDir(), "openclaw.json");
    if (fsMod.existsSync(configPath)) {{
      const raw = fsMod.readFileSync(configPath, "utf-8");
      const parsed = raw.trim() ? JSON.parse(raw) : {{}};
      const bridgeConfig = parsed && typeof parsed === "object"
        ? ((parsed.plugins?.entries?.["openclaw-v8-bridge"]?.config ?? {{}}) as Record<string, unknown>)
        : {{}};
      const configUrl = typeof bridgeConfig.v8InboundUrl === "string" ? bridgeConfig.v8InboundUrl.trim() : "";
      const configToken = typeof bridgeConfig.handoffToken === "string" ? bridgeConfig.handoffToken.trim() : "";
      return {{
        handoffUrl: envUrl || configUrl || V8_HANDOFF_DEFAULT_URL,
        handoffToken: envToken || configToken,
      }};
    }}
  }} catch {{
    // ignore and fall back to env/defaults
  }}
  return {{
    handoffUrl: envUrl || V8_HANDOFF_DEFAULT_URL,
    handoffToken: envToken,
  }};
}}

async function handoffInboundToV8(args: {{
  full: WeixinMessage;
  ctx: import("./inbound.js").WeixinMsgContext;
  deps: ProcessMessageDeps;
  commandAuthorized: boolean;
  textBody: string;
}}): Promise<{{ sessionId?: string; response?: string }}> {{
  const {{ handoffUrl, handoffToken }} = await resolveV8HandoffConfig();
  if (!handoffUrl) {{
    throw new Error("V8 PluginHostRuntime inbound handoff URL is not configured.");
  }}
  const payload = {{
    channelType: "openclaw-weixin",
    chatType: "p2p",
    remoteId: args.ctx.To || args.full.from_user_id || "",
    accountId: args.deps.accountId,
    senderId: args.full.from_user_id || "",
    senderName: args.full.from_user_id || "",
    text: args.ctx.Body || args.textBody || "",
    messageId: args.full.message_id != null ? String(args.full.message_id) : "",
    timestamp: args.full.create_time_ms || Date.now(),
    contextToken: args.ctx.context_token || "",
    mediaPath: args.ctx.MediaPath,
    mediaUrl: args.ctx.MediaUrl,
    metadata: {{
      source: "openclaw-weixin",
      provider: "openclaw-weixin",
      account_id: args.deps.accountId,
      session_id: args.full.session_id || "",
      group_id: args.full.group_id || "",
      message_state: args.full.message_state ?? null,
      command_authorized: args.commandAuthorized,
      message_sid: args.ctx.MessageSid,
      media_type: args.ctx.MediaType || "",
      transport_managed_by: "openclaw",
      inbound_ownership: "v8",
    }},
  }};
  const headers: Record<string, string> = {{
    "Content-Type": "application/json",
    "X-V8-Agent-OS-Plugin-Host-Handoff": V8_HANDOFF_HEADER,
  }};
  if (handoffToken) {{
    headers.Authorization = `Bearer ${{handoffToken}}`;
    headers["X-V8-Agent-OS-Plugin-Host-Handoff-Token"] = handoffToken;
  }}
  const response = await fetch(handoffUrl, {{
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  }});
  const raw = await response.text();
  let data: {{ sessionId?: string; response?: string; detail?: string }} = {{}};
  if (raw.trim()) {{
    try {{
      data = JSON.parse(raw) as typeof data;
    }} catch {{
      data = {{}};
    }}
  }}
  if (!response.ok) {{
    const detail = data.detail || raw || `HTTP ${{response.status}}`;
    throw new Error(`V8 inbound handoff failed: ${{detail}}`);
  }}
  return data;
}}

{PATCH_MARKER}

{HELPER_ANCHOR}"""


HANDOFF_BLOCK = """  const preflightContextToken = getContextTokenFromMsgContext(ctx);
  if (preflightContextToken) {
    setContextToken(deps.accountId, full.from_user_id ?? "", preflightContextToken);
  }
  const v8HandoffUrl = (process.env.V8_AGENT_OS_PLUGIN_HOST_INBOUND_URL?.trim() || V8_HANDOFF_DEFAULT_URL).trim();
  if (v8HandoffUrl) {
    if (debug) {
      debugTrace.push("│ handoff: inbound ownership forwarded to V8 PluginHostRuntime");
    }
    try {
      await handoffInboundToV8({
        full,
        ctx,
        deps,
        commandAuthorized,
        textBody,
      });
      logger.info(`handoffToV8: inbound ownership transferred to V8 for from=${ctx.To} accountId=${deps.accountId}`);
      return;
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : String(err);
      logger.error(`handoffToV8: FAILED to=${ctx.To} err=${errMsg}`);
      deps.errLog(`v8 handoff failed: ${errMsg}`);
      void sendWeixinErrorNotice({
        to: ctx.To,
        contextToken: preflightContextToken,
        message: `⚠️ V8 PluginHostRuntime 接管失败：${errMsg}`,
        baseUrl: deps.baseUrl,
        token: deps.token,
        errLog: deps.errLog,
      });
      throw err;
    }
  }
"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_paths(plugin_dir: Path) -> tuple[Path, Path, Path, Path, Path]:
    plugin_dir = Path(plugin_dir)
    return (
        plugin_dir / PACKAGE_JSON_RELATIVE_PATH,
        plugin_dir / PROCESS_MESSAGE_RELATIVE_PATH,
        plugin_dir / HANDOFF_MODULE_RELATIVE_PATH,
        plugin_dir / MONITOR_RELATIVE_PATH,
        plugin_dir / ACCOUNTS_RELATIVE_PATH,
    )


def _resolve_gateway_paths(openclaw_root: Path) -> tuple[Path, Path]:
    openclaw_root = Path(openclaw_root)
    return openclaw_root / GATEWAY_CMD_RELATIVE_PATH, openclaw_root / GATEWAY_BACKUP_FILE_NAME


def inspect_weixin_handoff_patch(plugin_dir: Path) -> dict[str, Any]:
    package_json_path, process_message_path, handoff_module_path, monitor_path, accounts_path = _resolve_paths(plugin_dir)
    package_manifest = _read_json(package_json_path)
    version = str(package_manifest.get("version") or "").strip()
    package_name = str(package_manifest.get("name") or "").strip()

    if not process_message_path.exists():
        return {
            "pluginId": SUPPORTED_WEIXIN_PLUGIN_ID,
            "packageName": package_name or SUPPORTED_WEIXIN_PACKAGE,
            "version": version or None,
            "handoffReady": False,
            "inboundOwnership": "delegated",
            "supported": False,
            "reason": f"缺少入站处理文件：{process_message_path}",
        }

    content = process_message_path.read_text(encoding="utf-8")
    handoff_module_ready = handoff_module_path.exists() and HANDOFF_MODULE_MARKER in handoff_module_path.read_text(encoding="utf-8")
    monitor_ready = monitor_path.exists() and HANDOFF_MONITOR_IMPORT in monitor_path.read_text(encoding="utf-8")
    config_preserve_ready = accounts_path.exists() and ACCOUNTS_PATCH_MARKER in accounts_path.read_text(encoding="utf-8")
    supported = package_name == SUPPORTED_WEIXIN_PACKAGE
    patched = PATCH_MARKER in content
    legacy_patched = (PATCH_SENTINEL in content and not patched) or any(sentinel in content for sentinel in LEGACY_PATCH_SENTINELS)
    if not supported:
        return {
            "pluginId": SUPPORTED_WEIXIN_PLUGIN_ID,
            "packageName": package_name or SUPPORTED_WEIXIN_PACKAGE,
            "version": version or None,
            "handoffReady": False,
            "inboundOwnership": "delegated",
            "supported": False,
            "reason": f"当前仅支持 {SUPPORTED_WEIXIN_PACKAGE} 的受管 handoff，当前版本为 {package_name or 'unknown'}@{version or 'unknown'}。",
        }
    if patched:
        return {
            "pluginId": SUPPORTED_WEIXIN_PLUGIN_ID,
            "packageName": package_name,
            "version": version,
            "handoffReady": handoff_module_ready and monitor_ready and config_preserve_ready,
            "inboundOwnership": "v8_owned",
            "supported": True,
            "reason": None if handoff_module_ready and monitor_ready and config_preserve_ready else "当前 handoff patch 已存在，但 monitor import、独立 handoff 模块或配置保真补丁仍未全部进入强制接管态。",
            "modulePath": str(handoff_module_path),
            "monitorPath": str(monitor_path),
            "monitorPatched": monitor_ready,
            "handoffModuleReady": handoff_module_ready,
            "accountsPath": str(accounts_path),
            "configPreserveReady": config_preserve_ready,
        }
    if legacy_patched:
        return {
            "pluginId": SUPPORTED_WEIXIN_PLUGIN_ID,
            "packageName": package_name,
            "version": version,
            "handoffReady": False,
            "inboundOwnership": "delegated",
            "supported": True,
            "reason": "当前检测到旧版 handoff patch，真实入站仍可能先在 OpenClaw 插件内执行路由。需要升级到 V3 handoff。",
        }
    return {
        "pluginId": SUPPORTED_WEIXIN_PLUGIN_ID,
        "packageName": package_name,
        "version": version,
        "handoffReady": False,
        "inboundOwnership": "delegated",
        "supported": True,
        "reason": "当前 OpenClaw sidecar 仍在插件内直接调度 main agent，尚未切到 V8 handoff。",
        "modulePath": str(handoff_module_path),
        "monitorPath": str(monitor_path),
        "monitorPatched": False,
        "handoffModuleReady": False,
        "accountsPath": str(accounts_path),
        "configPreserveReady": config_preserve_ready,
    }


def inspect_gateway_launcher_patch(openclaw_root: Path, *, engine_base_url: str) -> dict[str, Any]:
    gateway_cmd_path, _backup_path = _resolve_gateway_paths(openclaw_root)
    if not gateway_cmd_path.exists():
        return {
            "handoffReady": False,
            "supported": False,
            "reason": f"缺少 gateway 启动脚本：{gateway_cmd_path}",
            "gatewayCmdPath": str(gateway_cmd_path),
        }
    content = gateway_cmd_path.read_text(encoding="utf-8")
    expected_url = f'{engine_base_url.rstrip("/")}/v1/plugin-host/inbound'
    sentinel_ok = GATEWAY_PATCH_SENTINEL in content
    url_ok = f'set "V8_AGENT_OS_PLUGIN_HOST_INBOUND_URL={expected_url}"' in content
    allow_unconfigured = "--allow-unconfigured" in content
    if sentinel_ok and url_ok and allow_unconfigured:
        return {
            "handoffReady": True,
            "supported": True,
            "reason": None,
            "gatewayCmdPath": str(gateway_cmd_path),
            "inboundUrl": expected_url,
            "allowUnconfigured": True,
        }
    return {
        "handoffReady": False,
        "supported": True,
        "reason": "当前 gateway 启动脚本尚未完整注入 V8 inbound handoff 环境变量或 --allow-unconfigured 兜底参数。",
        "gatewayCmdPath": str(gateway_cmd_path),
        "inboundUrl": expected_url,
        "allowUnconfigured": allow_unconfigured,
    }


def ensure_weixin_handoff_patch(plugin_dir: Path, *, engine_base_url: str) -> dict[str, Any]:
    package_json_path, process_message_path, handoff_module_path, monitor_path, accounts_path = _resolve_paths(plugin_dir)
    status = inspect_weixin_handoff_patch(plugin_dir)
    if status.get("handoffReady"):
        status["patched"] = False
        return status
    if not status.get("supported"):
        return status

    backup_path = process_message_path.with_name(BACKUP_FILE_NAME)
    source_path = backup_path if backup_path.exists() else process_message_path
    content = source_path.read_text(encoding="utf-8")
    if HELPER_ANCHOR not in content or ROUTE_ANCHOR not in content:
        raise RuntimeError("openclaw-weixin 当前源码结构与受管 handoff 模板不匹配，拒绝静默 patch。")

    if not backup_path.exists():
        backup_path.write_text(content, encoding="utf-8")

    updated = content.replace(HELPER_ANCHOR, _helper_block(engine_base_url), 1)
    updated = updated.replace(ROUTE_ANCHOR, HANDOFF_BLOCK + ROUTE_ANCHOR, 1)
    process_message_path.write_text(updated, encoding="utf-8")
    handoff_module_path.write_text(_handoff_module_content(updated), encoding="utf-8")

    monitor_backup_path = monitor_path.with_name(MONITOR_BACKUP_FILE_NAME)
    monitor_source_path = monitor_backup_path if monitor_backup_path.exists() else monitor_path
    monitor_content = monitor_source_path.read_text(encoding="utf-8")
    if MONITOR_IMPORT_ANCHOR not in monitor_content:
        raise RuntimeError("openclaw-weixin monitor 当前源码结构与受管 handoff 模板不匹配，拒绝静默 patch。")
    if not monitor_backup_path.exists():
        monitor_backup_path.write_text(monitor_content, encoding="utf-8")
    patched_monitor = monitor_content.replace(MONITOR_IMPORT_ANCHOR, f"{MONITOR_PATCH_MARKER}\n{HANDOFF_MONITOR_IMPORT}", 1)
    monitor_path.write_text(patched_monitor, encoding="utf-8")

    accounts_backup_path = accounts_path.with_name(ACCOUNTS_BACKUP_FILE_NAME)
    accounts_source_path = accounts_backup_path if accounts_backup_path.exists() else accounts_path
    accounts_content = accounts_source_path.read_text(encoding="utf-8")
    if ACCOUNTS_RELOAD_ANCHOR not in accounts_content:
        raise RuntimeError("openclaw-weixin accounts 当前源码结构与配置保真模板不匹配，拒绝静默 patch。")
    if not accounts_backup_path.exists():
        accounts_backup_path.write_text(accounts_content, encoding="utf-8")
    patched_accounts = accounts_content.replace(ACCOUNTS_RELOAD_ANCHOR, _patched_accounts_reload_block(), 1)
    accounts_path.write_text(patched_accounts, encoding="utf-8")

    refreshed = inspect_weixin_handoff_patch(plugin_dir)
    refreshed["patched"] = True
    refreshed["backupPath"] = str(backup_path)
    refreshed["monitorBackupPath"] = str(monitor_backup_path)
    refreshed["packageManifestPath"] = str(package_json_path)
    refreshed["processMessagePath"] = str(process_message_path)
    refreshed["handoffModulePath"] = str(handoff_module_path)
    refreshed["monitorPath"] = str(monitor_path)
    refreshed["accountsPath"] = str(accounts_path)
    refreshed["accountsBackupPath"] = str(accounts_backup_path)
    return refreshed


def ensure_gateway_launcher_patch(
    openclaw_root: Path,
    *,
    engine_base_url: str,
    handoff_token: str | None = None,
) -> dict[str, Any]:
    gateway_cmd_path, backup_path = _resolve_gateway_paths(openclaw_root)
    status = inspect_gateway_launcher_patch(openclaw_root, engine_base_url=engine_base_url)
    if status.get("handoffReady") and (
        not handoff_token or f'set "V8_AGENT_OS_PLUGIN_HOST_HANDOFF_TOKEN={handoff_token}"' in gateway_cmd_path.read_text(encoding="utf-8")
    ):
        status["patched"] = False
        return status
    if not status.get("supported"):
        return status

    source_path = backup_path if backup_path.exists() else gateway_cmd_path
    content = source_path.read_text(encoding="utf-8")

    if not backup_path.exists():
        backup_path.write_text(content, encoding="utf-8")

    expected_url = f'{engine_base_url.rstrip("/")}/v1/plugin-host/inbound'
    injected_lines = [
        f"rem {GATEWAY_PATCH_SENTINEL}",
        f'set "V8_AGENT_OS_PLUGIN_HOST_INBOUND_URL={expected_url}"',
    ]
    if handoff_token:
        injected_lines.append(f'set "V8_AGENT_OS_PLUGIN_HOST_HANDOFF_TOKEN={handoff_token}"')
    injected_block = '\n'.join(injected_lines)
    state_dir_anchor = 'set "OPENCLAW_STATE_DIR='
    if state_dir_anchor in content:
        replacement = injected_block + "\n" + state_dir_anchor
        updated = content.replace(state_dir_anchor, replacement, 1)
    else:
        lines = content.splitlines()
        launch_index = next(
            (
                index
                for index, line in enumerate(lines)
                if '"D:\\Program Files\\node.exe"' in line
                or "dist\\index.js gateway" in line
                or re.search(r'\bgateway\b.*--port\b', line, re.IGNORECASE)
            ),
            -1,
        )
        if launch_index < 0:
            raise RuntimeError("gateway.cmd 当前结构与受管 handoff 模板不匹配，拒绝静默 patch。")
        lines.insert(launch_index, injected_block)
        updated = "\n".join(lines)
        if content.endswith("\n") or content.endswith("\r\n"):
            updated += "\n"
    lines = updated.splitlines()
    launch_index = next(
        (
            index
            for index, line in enumerate(lines)
            if ("dist\\index.js" in line or "openclaw.mjs" in line) and re.search(r"\bgateway\b", line, re.IGNORECASE)
        ),
        -1,
    )
    if launch_index < 0:
        raise RuntimeError("gateway.cmd 启动行未找到，拒绝静默注入 --allow-unconfigured。")
    if "--allow-unconfigured" not in lines[launch_index]:
        lines[launch_index] = lines[launch_index].rstrip() + " --allow-unconfigured"
    updated = "\n".join(lines)
    if content.endswith("\n") or content.endswith("\r\n"):
        updated += "\n"
    gateway_cmd_path.write_text(updated, encoding="utf-8")

    refreshed = inspect_gateway_launcher_patch(openclaw_root, engine_base_url=engine_base_url)
    refreshed["patched"] = True
    refreshed["backupPath"] = str(backup_path)
    return refreshed
