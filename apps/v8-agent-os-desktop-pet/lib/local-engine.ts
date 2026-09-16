import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export type LocalBridge = { engineBaseUrl?: string; engineWsBaseUrl?: string; internalSecret?: string };
export function readLocalBridge(): LocalBridge {
  const root = process.env.V8_AGENT_OS_HOME || path.join(os.homedir(), ".v8-agent-os");
  try { return JSON.parse(fs.readFileSync(path.join(root, "config.json"), "utf8"))?.systemBase?.bridge || {}; }
  catch { return {}; }
}

export function localEngineOrigin(value = process.env.V8_ENGINE_BASE_URL || readLocalBridge().engineBaseUrl || "http://127.0.0.1:9530/v1") {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.search || url.hash || !["", "/", "/v1"].includes(url.pathname)) throw new Error("local_engine_origin_required");
  return url.origin;
}

export function engineClientTarget(origin: string, resource: string) {
  let decoded = resource.split(/[?#]/, 1)[0];
  for (let i = 0; i < 4; i++) {
    const next = decodeURIComponent(decoded);
    if (next === decoded) break;
    decoded = next;
  }
  if (!decoded.startsWith("/api/client/") || decoded.includes("\\") || decoded.split("/").some(part => part === "." || part === "..")) throw new Error("invalid_client_resource");
  const url = new URL(resource, origin);
  if (url.origin !== origin || !url.pathname.startsWith("/api/client/")) throw new Error("invalid_client_resource");
  return url.href;
}
