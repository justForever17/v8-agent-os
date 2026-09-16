import { DEFAULT_PORTS, CONFIG_PATH } from "./paths.mjs";
import { fetchJson } from "./http.mjs";
import { readJsonFile } from "./json_file.mjs";

function connection() {
  // Read target and proof atomically from one configuration snapshot. An env
  // endpoint override is not authority to send this instance's service proof.
  const bridge = readJsonFile(CONFIG_PATH, {})?.systemBase?.bridge || {};
  const configured = String(bridge.engineBaseUrl || `http://127.0.0.1:${DEFAULT_PORTS.engine}`).trim();
  let target;
  try { target = new URL(configured); } catch { throw new Error("Engine 地址配置无效，请检查 systemBase.bridge.engineBaseUrl。"); }
  if (!["http:", "https:"].includes(target.protocol) || target.username || target.password) {
    throw new Error("Engine 地址必须是无内嵌凭据的 HTTP(S) 地址。");
  }
  const override = String(process.env.V8OS_ENGINE_URL || process.env.V8_AGENT_OS_ENGINE_URL || "").trim();
  if (override) {
    let overrideOrigin;
    try { overrideOrigin = new URL(override).origin; } catch { /* refused below */ }
    if (overrideOrigin !== target.origin) throw new Error("Engine 环境地址与本机配置不一致；未发送凭据。请在对应实例配置中设置 Engine 地址。");
  }
  return { origin: target.origin, secret: String(bridge.internalSecret || "").trim() };
}

export async function engineResponse(pathname, options = {}) {
  if (!String(pathname).startsWith("/v1/") || String(pathname).includes("\\")) throw new Error("CLI Engine 请求必须使用本机 /v1/ 路径。");
  const { origin, secret } = connection();
  const headers = {};
  for (const [name, value] of Object.entries(options.headers || {})) {
    if (!["authorization", "cookie", "x-v8-agent-os-secret", "host"].includes(name.toLowerCase())) headers[name] = value;
  }
  if (secret) headers["x-v8-agent-os-secret"] = secret;
  // No automatic replay: a 401 or lost reply to a write does not establish
  // whether the server performed it. The caller must reconcile the result.
  return fetchJson(`${origin}${pathname}`, {
    timeoutMs: options.timeoutMs || 10000,
    method: options.method || "GET",
    body: options.body,
    headers,
  });
}

export async function engineJson(pathname, options = {}) {
  const response = await engineResponse(pathname, options);
  if (!response.ok) {
    const detail = response.data?.detail?.message || response.data?.detail || response.data?.message || `Engine returned ${response.status}`;
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
    throw error;
  }
  return response.data;
}
