import path from "node:path";
import { DEFAULT_PORTS, STATE_ROOT } from "./paths.mjs";
import { fetchJson } from "./http.mjs";
import { ensureDir, readJsonFile, writeJsonFile } from "./json_file.mjs";

const SESSION_FILE = path.join(STATE_ROOT, "runtime", "cli", "local-session.json");

export function adminOrigin() {
  return process.env.V8OS_ADMIN_URL || `http://127.0.0.1:${DEFAULT_PORTS.admin}`;
}

function decodeJwtPayload(token) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) return {};
  try {
    return JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
  } catch {
    return {};
  }
}

function cachedAccessToken() {
  const record = readJsonFile(SESSION_FILE, {});
  const token = String(record.accessToken || "");
  if (!token) return "";
  const payload = decodeJwtPayload(token);
  const exp = Number(payload.exp || 0);
  if (!exp || exp * 1000 <= Date.now() + 60_000) return "";
  return token;
}

export async function issueLocalCliSession() {
  let response;
  try {
    response = await fetchJson(`${adminOrigin()}/api/client/auth/local-session`, {
      method: "POST",
      timeoutMs: 5000,
      body: {
        surface: "cli",
        deviceName: "v8os-cli",
      },
    });
  } catch (error) {
    throw new Error(`无法连接本机 Admin（${adminOrigin()}）。请先运行 v8os start，或设置 V8OS_ADMIN_URL。原始错误：${error.message}`);
  }
  if (!response.ok || !response.data?.accessToken) {
    throw new Error(`无法获取本机 CLI 会话：${response.status} ${response.data?.error || ""}`.trim());
  }
  ensureDir(path.dirname(SESSION_FILE));
  writeJsonFile(SESSION_FILE, {
    createdAt: new Date().toISOString(),
    adminOrigin: adminOrigin(),
    accessToken: response.data.accessToken,
    accessTokenExpiresAt: response.data.accessTokenExpiresAt,
    refreshToken: response.data.refreshToken,
    refreshTokenExpiresAt: response.data.refreshTokenExpiresAt,
  });
  return response.data.accessToken;
}

export async function localCliAccessToken({ force = false } = {}) {
  if (!force) {
    const cached = cachedAccessToken();
    if (cached) return cached;
  }
  return issueLocalCliSession();
}

export async function adminJson(pathname, { method = "GET", body, timeoutMs = 10_000, headers = {} } = {}) {
  const makeRequest = async (token) => {
    try {
      return await fetchJson(`${adminOrigin()}${pathname}`, {
        method,
        body,
        timeoutMs,
        headers: {
          Authorization: `Bearer ${token}`,
          ...headers,
        },
      });
    } catch (error) {
      throw new Error(`无法连接本机 Admin（${adminOrigin()}）。请先运行 v8os start，或设置 V8OS_ADMIN_URL。原始错误：${error.message}`);
    }
  };
  let token = await localCliAccessToken();
  let response = await makeRequest(token);
  if (response.status === 401) {
    token = await localCliAccessToken({ force: true });
    response = await makeRequest(token);
  }
  return response;
}

export function requireOk(response, label) {
  if (response.ok) return response.data;
  const detail = response.data?.detail;
  const detailText = typeof detail === "string"
    ? detail
    : detail && typeof detail === "object"
      ? detail.summary || detail.error || detail.message || detail.reason || JSON.stringify(detail)
      : "";
  const error = response.data?.summary
    || response.data?.error
    || response.data?.message
    || detailText
    || response.data?.rawText
    || "unknown_error";
  const code = typeof detail === "object" && detail ? detail.error : response.data?.error || detailText;
  const workspaceHintCodes = new Set([
    "workspace_binding_required",
    "workspace_trust_required",
    "workspace_side_effect_blocked",
  ]);
  const hint = workspaceHintCodes.has(String(code || "").trim())
    ? " 请先运行 v8os workspace create <path> --select 或 v8os workspace select <path>，确认并信任项目工作区后重试。"
    : "";
  throw new Error(`${label} 失败：${response.status} ${error}${hint}`);
}
