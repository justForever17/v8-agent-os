import fs from "node:fs";
import { CONFIG_PATH, DEFAULT_PORTS, MCP_CONFIG_PATH } from "./paths.mjs";
import { fetchJson } from "./http.mjs";
import { readJsonFile } from "./json_file.mjs";

async function engineConfigDomain(domain) {
  const response = await fetchJson(`http://127.0.0.1:${DEFAULT_PORTS.engine}/v1/config-registry/${domain}`, { timeoutMs: 3000 });
  if (!response.ok) throw new Error(`Engine config registry returned ${response.status}`);
  return response.data;
}

async function engineRequest(path, options = {}) {
  const response = await fetchJson(`http://127.0.0.1:${DEFAULT_PORTS.engine}${path}`, {
    timeoutMs: options.timeoutMs || 5000,
    method: options.method || "GET",
    body: options.body,
  });
  if (!response.ok) {
    const message = response.data?.detail?.message || response.data?.detail || response.data?.message || `Engine returned ${response.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response.data;
}

function readOfflineDomain(domain) {
  if (domain === "mcp") {
    return { domain: "mcp", data: { config: readJsonFile(MCP_CONFIG_PATH, { mcpServers: {} }) }, source: MCP_CONFIG_PATH };
  }
  const config = readJsonFile(CONFIG_PATH, {});
  if (domain === "system-base") {
    return { domain, data: config?.systemBase ?? config?.["system-base"] ?? null, source: `${CONFIG_PATH}#systemBase` };
  }
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

export async function mcpStatus() {
  try {
    const data = await engineRequest("/v1/mcp/status", { timeoutMs: 5000 });
    return { source: "engine", payload: data };
  } catch (error) {
    const fallback = await listMcpServers();
    return {
      source: "local_fallback",
      message: "Engine 未在线，仅显示本地 MCP 配置；运行状态需启动 Engine 后查看。",
      error: error.message,
      servers: fallback.servers,
    };
  }
}

function optionValue(args, name, fallback = "") {
  const index = args.indexOf(name);
  return index >= 0 ? String(args[index + 1] || fallback) : fallback;
}

function optionValues(args, name) {
  const values = [];
  for (let index = 0; index < args.length; index += 1) {
    if (args[index] === name && args[index + 1] !== undefined) values.push(String(args[index + 1]));
  }
  return values;
}

function keyValueObject(values) {
  const result = {};
  for (const value of values) {
    const separator = value.indexOf("=");
    if (separator <= 0) throw new Error(`Expected KEY=VALUE, got ${value}`);
    const key = value.slice(0, separator).trim();
    const raw = value.slice(separator + 1);
    if (!key) throw new Error(`Expected KEY=VALUE, got ${value}`);
    result[key] = raw;
  }
  return result;
}

export function buildMcpInstallPayload(args) {
  const name = String(args[0] || "").trim();
  if (!name) throw new Error("mcp install requires a server name");
  const type = optionValue(args, "--type", "stdio").toLowerCase();
  if (!["stdio", "http", "sse"].includes(type)) throw new Error("mcp install --type must be stdio, http, or sse");

  const server = { type };
  if (type === "stdio") {
    const command = optionValue(args, "--command");
    if (!command) throw new Error("stdio MCP install requires --command");
    server.command = command;
    const commandArgs = optionValues(args, "--arg");
    if (commandArgs.length) server.args = commandArgs;
    const env = keyValueObject(optionValues(args, "--env"));
    if (Object.keys(env).length) server.env = env;
  } else {
    const url = optionValue(args, "--url");
    if (!url) throw new Error(`${type} MCP install requires --url`);
    server.url = url;
    const headers = keyValueObject(optionValues(args, "--header"));
    if (Object.keys(headers).length) server.headers = headers;
  }
  if (args.includes("--disabled")) server.disabled = true;
  return { mcpServers: { [name]: server } };
}

export async function installMcpServer(args) {
  const payload = buildMcpInstallPayload(args);
  try {
    const data = await engineRequest("/v1/mcp/config", { method: "POST", body: payload, timeoutMs: 8000 });
    return { source: "engine", payload: data, installed: Object.keys(payload.mcpServers) };
  } catch (error) {
    throw new Error(`MCP install 需要 Engine 在线并执行现有校验：${error.message}`);
  }
}

export async function removeMcpServer(name) {
  const normalized = String(name || "").trim();
  if (!normalized) throw new Error("mcp remove requires a server name");
  try {
    const data = await engineRequest(`/v1/mcp/config/${encodeURIComponent(normalized)}`, { method: "DELETE", timeoutMs: 8000 });
    return { source: "engine", payload: data, removed: normalized };
  } catch (error) {
    throw new Error(`MCP remove 需要 Engine 在线并执行现有校验：${error.message}`);
  }
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

export function extractModelRoles(payload) {
  const data = payload?.payload?.data || payload?.data || payload || {};
  return data.roles || data.data?.roles || {};
}

export async function modelRoles() {
  const result = await getConfigDomain("models");
  return { source: result.source, roles: extractModelRoles(result) };
}

export async function setModelRole(role, modelRef) {
  const normalizedRole = String(role || "").trim();
  const normalizedModelRef = String(modelRef || "").trim();
  if (!normalizedRole || !normalizedModelRef) throw new Error("models set-role requires <role> <modelRef>");
  const domain = await engineRequest("/v1/config-registry/models", { timeoutMs: 5000 });
  const data = { ...(domain?.data || {}) };
  data.roles = { ...(data.roles || {}), [normalizedRole]: normalizedModelRef };
  const saved = await engineRequest("/v1/config-registry/models", {
    method: "POST",
    body: { data },
    timeoutMs: 8000,
  });
  return { source: "engine", role: normalizedRole, modelRef: normalizedModelRef, payload: saved };
}

export async function phonePairingSummary() {
  const systemBase = await getConfigDomain("system-base");
  const data = systemBase.payload?.data || systemBase.payload || {};
  return {
    source: systemBase.source,
    remoteLink: data.remoteLink || data.systemBase?.remoteLink || null,
    manifest: data.remoteLinkManifest || null,
  };
}

export async function phonePairingManifest() {
  const summary = await phonePairingSummary();
  const manifest = summary.manifest || {};
  const remoteLink = summary.remoteLink || {};
  const adminUrls = [
    ...(Array.isArray(manifest.adminUrls) ? manifest.adminUrls : []),
    ...(Array.isArray(remoteLink.adminUrls) ? remoteLink.adminUrls : []),
    remoteLink.adminUrl,
    remoteLink.manualUrl,
  ].filter(Boolean);
  return {
    source: summary.source,
    manifest: {
      serverId: manifest.serverId || remoteLink.serverId || "local-v8os",
      instanceId: manifest.instanceId || remoteLink.instanceId || "local",
      adminUrls: [...new Set(adminUrls)],
      surface: "phone",
    },
    remoteLink,
  };
}
