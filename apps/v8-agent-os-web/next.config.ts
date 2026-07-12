import type { NextConfig } from "next";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { PHASE_PRODUCTION_BUILD } from "next/constants";

function isProductionBuildPhase(phase: string) {
  return phase === PHASE_PRODUCTION_BUILD || process.env.V8_NEXT_BUILD === "1";
}

function readBridgeConfig(phase: string) {
  if (isProductionBuildPhase(phase)) {
    return {};
  }
  try {
    const configPath = path.join(os.homedir(), ".v8-agent-os", "config.json");
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    return config?.bridge && typeof config.bridge === "object" ? config.bridge : {};
  } catch {
    return {};
  }
}

function resolveAdminApiBaseUrl(phase: string) {
  try {
    const value = String(readBridgeConfig(phase).adminBaseUrl || "").trim();
    if (value) {
      return value.replace(/\/$/, "");
    }
  } catch {}
  return "http://127.0.0.1:9528/api";
}

function resolveEngineOrigin(phase: string) {
  try {
    const value = String(readBridgeConfig(phase).engineBaseUrl || "").trim();
    if (value) {
      return value.replace(/\/v1\/?$/, "").replace(/\/$/, "");
    }
  } catch {}
  return "http://127.0.0.1:9530";
}

const createNextConfig = (phase: string): NextConfig => ({
  /* config options here */
  output: "standalone",
  reactCompiler: true,
  transpilePackages: ["@v8/session-realtime"],
  experimental: {
    externalDir: true,
  },
  async rewrites() {
    return {
      beforeFiles: [
        {
          source: "/api/terminal-ws/:path*",
          destination: `${resolveEngineOrigin(phase)}/v1/terminal/:path*`,
        },
        {
          source: "/api/client/bg_processes/:path*",
          destination: `${resolveEngineOrigin(phase)}/v1/bg_processes/:path*`,
        },
        {
          source: "/api/bg_processes/:path*",
          destination: `${resolveEngineOrigin(phase)}/v1/bg_processes/:path*`,
        },
      ],
      fallback: [
        {
          source: "/api/:path*",
          destination: `${resolveAdminApiBaseUrl(phase)}/:path*`,
        },
      ],
    };
  },
});

export default createNextConfig;
