import fs from "node:fs";
import { CONFIG_PATH, DEFAULT_PORTS, MCP_CONFIG_PATH } from "./paths.mjs";
import { fetchJson } from "./http.mjs";
import { readJsonFile } from "./json_file.mjs";

async function engineConfigDomain(domain) {
  const response = await fetchJson(`${engineOrigin()}/v1/config-registry/${domain}`, { timeoutMs: 3000, headers: engineHeaders() });
  if (!response.ok) throw new Error(`Engine config registry returned ${response.status}`);
  return response.data;
}

function engineOrigin() {
  const configured = String(readJsonFile(CONFIG_PATH, {})?.systemBase?.bridge?.engineBaseUrl || "").trim();
  if (configured) {
    try {
      const url = new URL(configured);
      return `${url.protocol}//${url.host}`;
    } catch {
      // Fall through to the governed default.
    }
  }
  return `http://127.0.0.1:${DEFAULT_PORTS.engine}`;
}

function engineHeaders() {
  const localConfig = readJsonFile(CONFIG_PATH, {});
  const internalSecret = String(localConfig?.systemBase?.bridge?.internalSecret || "").trim();
  return internalSecret ? { "x-v8-agent-os-secret": internalSecret } : undefined;
}

async function engineRequest(path, options = {}) {
  const response = await fetchJson(`${engineOrigin()}${path}`, {
    timeoutMs: options.timeoutMs || 5000,
    method: options.method || "GET",
    body: options.body,
    headers: engineHeaders(),
  });
  if (!response.ok) {
    const message = response.data?.detail?.message || response.data?.detail || response.data?.message || `Engine returned ${response.status}`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return response.data;
}

export async function getNetworkConfig() {
  return { source: "engine", payload: await engineRequest("/v1/config-broker/network", { timeoutMs: 5000 }) };
}

export async function getNetworkSchema() {
  return { source: "engine", payload: await engineRequest("/v1/config-broker/network/schema", { timeoutMs: 5000 }) };
}

export async function prepareNetworkConfig(settings) {
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) {
    throw new Error("config network prepare requires a JSON object");
  }
  return {
    source: "engine",
    payload: await engineRequest("/v1/config-broker/network/prepare", {
      method: "POST",
      body: { settings },
      timeoutMs: 10000,
    }),
  };
}

export async function commitNetworkConfig(transactionId, planDigest) {
  const id = String(transactionId || "").trim();
  if (!id) throw new Error("config network commit requires a transaction id");
  return { source: "engine", payload: await engineRequest(`/v1/config-broker/transactions/${encodeURIComponent(id)}/commit`, {
    method: "POST", body: planDigest ? { planDigest } : {}, timeoutMs: 10000,
  }) };
}

export async function rollbackNetworkConfig(transactionId) {
  const id = String(transactionId || "").trim();
  if (!id) throw new Error("config network rollback requires a transaction id");
  return { source: "engine", payload: await engineRequest(`/v1/config-broker/transactions/${encodeURIComponent(id)}/rollback`, {
    method: "POST", timeoutMs: 10000,
  }) };
}

export async function getClientGatewayConfig() {
  return { source: "engine", payload: await engineRequest("/v1/config-broker/client-gateway", { timeoutMs: 5000 }) };
}

export async function getClientGatewaySchema() {
  return { source: "engine", payload: await engineRequest("/v1/config-broker/client-gateway/schema", { timeoutMs: 5000 }) };
}

export async function prepareClientGatewayConfig(settings) {
  if (!settings || typeof settings !== "object" || Array.isArray(settings)) throw new Error("config phone prepare requires a JSON object");
  return { source: "engine", payload: await engineRequest("/v1/config-broker/client-gateway/prepare", {
    method: "POST", body: { settings }, timeoutMs: 10000,
  }) };
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
    const response = await fetchJson(`${engineOrigin()}/v1/config-registry`, { timeoutMs: 3000, headers: engineHeaders() });
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

function containsCredentialArgument(value) {
  const normalized = String(value || "").trim();
  return /^(?:--?)?(?:api[-_]?key|access[-_]?token|token|secret|password|authorization|cookie)(?:=|:|$)/i.test(normalized)
    || /^[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|COOKIE)\s*=/i.test(normalized);
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
    if (commandArgs.some(containsCredentialArgument)) throw new Error("Do not pass credentials through --arg; use the Web/Phone secure action card");
    if (commandArgs.length) server.args = commandArgs;
    if (optionValues(args, "--env").length) throw new Error("Do not pass MCP secrets with --env KEY=VALUE; use the Web/Phone secure action card");
  } else {
    const url = optionValue(args, "--url");
    if (!url) throw new Error(`${type} MCP install requires --url`);
    server.url = url;
    if (optionValues(args, "--header").length) throw new Error("Do not pass MCP secrets with --header KEY=VALUE; use the Web/Phone secure action card");
  }
  if (args.includes("--disabled")) server.disabled = true;
  return { mcpServers: { [name]: server } };
}

export async function installMcpServer(args) {
  const payload = buildMcpInstallPayload(args);
  try {
    const name = Object.keys(payload.mcpServers)[0];
    const prepared = await engineRequest("/v1/config-broker/mcp/prepare", {
      method: "POST",
      body: { operation: "install", name, server: payload.mcpServers[name] },
      timeoutMs: 8000,
    });
    const committed = await engineRequest(`/v1/config-broker/transactions/${encodeURIComponent(prepared.transactionId)}/commit`, {
      method: "POST",
      body: { planDigest: prepared.planDigest },
      timeoutMs: 10000,
    });
    return { source: "engine", payload: committed, installed: [name], transactionId: prepared.transactionId };
  } catch (error) {
    throw new Error(`MCP install 需要 Engine 在线并执行现有校验：${error.message}`);
  }
}

export async function removeMcpServer(name) {
  const normalized = String(name || "").trim();
  if (!normalized) throw new Error("mcp remove requires a server name");
  try {
    const prepared = await engineRequest("/v1/config-broker/mcp/prepare", {
      method: "POST",
      body: { operation: "remove", name: normalized },
      timeoutMs: 8000,
    });
    const committed = await engineRequest(`/v1/config-broker/transactions/${encodeURIComponent(prepared.transactionId)}/commit`, {
      method: "POST",
      body: { planDigest: prepared.planDigest },
      timeoutMs: 10000,
    });
    return { source: "engine", payload: committed, removed: normalized, transactionId: prepared.transactionId };
  } catch (error) {
    throw new Error(`MCP remove 需要 Engine 在线并执行现有校验：${error.message}`);
  }
}

export async function modelRoleDoctor() {
  try {
    const response = await fetchJson(`${engineOrigin()}/v1/models/role-doctor`, { timeoutMs: 5000, headers: engineHeaders() });
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
  try {
    return { source: "engine", roles: (await engineRequest("/v1/config-broker/roles", { timeoutMs: 5000 })).roles || [] };
  } catch {
    const result = await getConfigDomain("models");
    return { source: result.source, roles: extractModelRoles(result) };
  }
}

export async function setModelRole(role, modelRef) {
  const normalizedRole = String(role || "").trim();
  const normalizedModelRef = String(modelRef || "").trim();
  if (!normalizedRole || !normalizedModelRef) throw new Error("models set-role requires <role> <modelRef>");
  const prepared = await engineRequest("/v1/config-broker/roles/prepare", {
    method: "POST",
    body: { role: normalizedRole, modelRef: normalizedModelRef },
    timeoutMs: 8000,
  });
  const saved = await engineRequest(`/v1/config-broker/transactions/${encodeURIComponent(prepared.transactionId)}/commit`, {
    method: "POST",
    body: { planDigest: prepared.planDigest },
    timeoutMs: 8000,
  });
  return { source: "engine", role: normalizedRole, modelRef: normalizedModelRef, transactionId: prepared.transactionId, payload: saved };
}

/** Read a Supervisor-owned config transaction through the Engine authority. */
export async function getConfigTransaction(transactionId) {
  const id = String(transactionId || "").trim();
  if (!id) throw new Error("config transaction show requires a transaction id");
  return { source: "engine", payload: await engineRequest(`/v1/config-broker/transactions/${encodeURIComponent(id)}`, { timeoutMs: 5000 }) };
}

/** Roll back a config transaction using the broker's target-scoped CAS checks. */
export async function rollbackConfigTransaction(transactionId) {
  const id = String(transactionId || "").trim();
  if (!id) throw new Error("config transaction rollback requires a transaction id");
  return { source: "engine", payload: await engineRequest(`/v1/config-broker/transactions/${encodeURIComponent(id)}/rollback`, { method: "POST", timeoutMs: 10000 }) };
}

export async function modelInventory({ category = "", query = "", limit = 20 } = {}) {
  const params = new URLSearchParams();
  if (category) params.set("category", category);
  if (query) params.set("query", query);
  params.set("limit", String(limit));
  return { source: "engine", payload: await engineRequest(`/v1/config-broker/models?${params.toString()}`, { timeoutMs: 5000 }) };
}

export async function recommendModel(role, limit = 5) {
  const params = new URLSearchParams({ role: String(role || ""), limit: String(limit) });
  return { source: "engine", payload: await engineRequest(`/v1/config-broker/recommend?${params.toString()}`, { timeoutMs: 5000 }) };
}

export async function phonePairingSummary() {
  const systemBase = await getConfigDomain("system-base");
  const data = systemBase.payload?.data || systemBase.payload || {};
  let gateway = null;
  try { gateway = (await getClientGatewayConfig()).payload; } catch { /* Engine may be offline; keep the manifest fallback. */ }
  return {
    source: systemBase.source,
    remoteLink: data.remoteLink || data.systemBase?.remoteLink || null,
    manifest: gateway ? await clientIdentityRequest("/link-manifest", { timeoutMs: 5000 }) : null,
    gateway,
  };
}

export async function phonePairingManifest() {
  return { source: "engine", manifest: await clientIdentityRequest("/link-manifest", { timeoutMs: 5000 }) };
}

async function clientIdentityRequest(path, options = {}) {
  return engineRequest(`/v1/client-identity${path}`, options);
}

export async function phoneOwner() {
  return { source: "engine", payload: await clientIdentityRequest("/owner", { timeoutMs: 5000 }) };
}

export async function phoneInitialize({ login = "owner", name = "" } = {}) {
  const owner = await phoneOwner();
  let result = { user: owner.payload?.user || null };
  if (!owner.payload?.initialized) {
    result = await clientIdentityRequest("/bootstrap", {
      method: "POST", body: { login, name }, timeoutMs: 10_000,
    });
  }
  // Establish the hidden trusted-local session in the same governed owner path.
  const localSession = await clientIdentityRequest("/local-session", {
    method: "POST", body: { surface: "cli", deviceName: "v8os-cli" }, timeoutMs: 10_000,
  });
  const safeLocalSession = localSession && typeof localSession === "object"
    ? Object.fromEntries(Object.entries(localSession).filter(([key]) => !/token|secret|credential|password/i.test(key)))
    : null;
  return { source: "engine", initialized: true, owner: result.user || null, localSession: safeLocalSession };
}

export async function phonePairingTicket({ deviceName = "", ttlMs = 300000, baseUrl = "" } = {}) {
  if (!String(baseUrl || "").trim()) throw new Error("config phone pair requires --base-url with a reachable HTTPS URL");
  return { source: "engine", payload: await clientIdentityRequest("/pairing-ticket", {
    method: "POST", body: { deviceName, ttlMs, adminBaseUrl: baseUrl }, timeoutMs: 10_000,
  }) };
}

export async function phoneDevices() {
  return { source: "engine", payload: await clientIdentityRequest("/devices", { timeoutMs: 5000 }) };
}

export async function revokePhoneDevice(deviceId) {
  const id = String(deviceId || "").trim();
  if (!id) throw new Error("config phone revoke requires a device id");
  return { source: "engine", payload: await clientIdentityRequest(`/devices/${encodeURIComponent(id)}`, { method: "DELETE", timeoutMs: 10_000 }) };
}
