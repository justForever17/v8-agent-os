import { DEFAULT_PORTS, CONFIG_PATH } from "./paths.mjs";
import { fetchJson } from "./http.mjs";
import { readJsonFile } from "./json_file.mjs";

function origin() {
  const configured = String(readJsonFile(CONFIG_PATH, {})?.systemBase?.bridge?.engineBaseUrl || "").trim();
  if (configured) {
    try { const url = new URL(configured); return `${url.protocol}//${url.host}`; } catch { /* default below */ }
  }
  return `http://127.0.0.1:${DEFAULT_PORTS.engine}`;
}

function headers() {
  const secret = String(readJsonFile(CONFIG_PATH, {})?.systemBase?.bridge?.internalSecret || "").trim();
  return secret ? { "x-v8-agent-os-secret": secret } : undefined;
}

export async function engineJson(pathname, options = {}) {
  const response = await fetchJson(`${origin()}${pathname}`, {
    timeoutMs: options.timeoutMs || 10000,
    method: options.method || "GET",
    body: options.body,
    headers: { ...(headers() || {}), ...(options.headers || {}) },
  });
  if (!response.ok) {
    const detail = response.data?.detail?.message || response.data?.detail || response.data?.message || `Engine returned ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.data;
}
