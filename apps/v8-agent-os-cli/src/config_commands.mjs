import fs from "node:fs";
import { CONFIG_PATH, DEFAULT_PORTS, MCP_CONFIG_PATH } from "./paths.mjs";
import { fetchJson } from "./http.mjs";
import { readJsonFile } from "./json_file.mjs";

async function engineConfigDomain(domain) {
  const response = await fetchJson(`http://127.0.0.1:${DEFAULT_PORTS.engine}/v1/config-registry/${domain}`, { timeoutMs: 3000 });
  if (!response.ok) throw new Error(`Engine config registry returned ${response.status}`);
  return response.data;
}

function readOfflineDomain(domain) {
  if (domain === "mcp") {
    return { domain: "mcp", data: { config: readJsonFile(MCP_CONFIG_PATH, { mcpServers: {} }) }, source: MCP_CONFIG_PATH };
  }
  const config = readJsonFile(CONFIG_PATH, {});
  return { domain, data: config?.[domain] ?? null, source: `${CONFIG_PATH}#${domain}` };
}

export async function getConfigDomain(domain) {
  try {
    return { source: "engine", payload: await engineConfigDomain(domain) };
  } catch {
    return { source: "local_fallback", payload: readOfflineDomain(domain) };
  }
}

export async function listConfigDomains() {
  try {
    const response = await fetchJson(`http://127.0.0.1:${DEFAULT_PORTS.engine}/v1/config-registry`, { timeoutMs: 3000 });
    if (response.ok && response.data?.domains) {
      return { source: "engine", domains: response.data.domains.map((item) => ({ domain: item.domain, title: item.title, source: item.source })) };
    }
  } catch {
    // Fall through.
  }
  const config = readJsonFile(CONFIG_PATH, {});
  const domains = Object.keys(config || {}).sort().map((domain) => ({ domain, title: domain, source: `${CONFIG_PATH}#${domain}` }));
  if (fs.existsSync(MCP_CONFIG_PATH)) domains.push({ domain: "mcp", title: "mcp", source: MCP_CONFIG_PATH });
  return { source: "local_fallback", domains };
}

export async function listMcpServers() {
  const result = await getConfigDomain("mcp");
  const config = result.payload?.data?.config || result.payload?.config || result.payload?.data || {};
  const servers = config.mcpServers || config.servers || {};
  return {
    source: result.source,
    servers: Object.entries(servers).map(([name, value]) => ({
      name,
      type: value?.type || value?.transport || (value?.command ? "stdio" : value?.url ? "http" : "unknown"),
      disabled: Boolean(value?.disabled),
    })),
  };
}

export async function modelRoleDoctor() {
  try {
    const response = await fetchJson(`http://127.0.0.1:${DEFAULT_PORTS.engine}/v1/models/role-doctor`, { timeoutMs: 5000 });
    if (response.ok) return { source: "engine", payload: response.data };
  } catch {
    // Fall through.
  }
  const config = readJsonFile(CONFIG_PATH, {});
  const models = config?.models || {};
  return {
    source: "local_fallback",
    payload: {
      message: "Engine 未在线，仅显示本地模型配置摘要，无法完成角色可用性诊断。",
      roles: models.roles || {},
      providerCount: Array.isArray(models.providers) ? models.providers.length : Object.keys(models.providers || {}).length,
      modelCount: Array.isArray(models.models) ? models.models.length : Object.keys(models.models || {}).length,
    },
  };
}

export async function pairingSummary() {
  const systemBase = await getConfigDomain("system-base");
  const data = systemBase.payload?.data || systemBase.payload || {};
  return {
    source: systemBase.source,
    remoteLink: data.remoteLink || data.systemBase?.remoteLink || null,
    manifest: data.remoteLinkManifest || null,
  };
}
