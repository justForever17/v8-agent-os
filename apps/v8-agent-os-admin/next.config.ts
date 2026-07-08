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

function resolveEngineOrigin(phase: string) {
  try {
    const base = String(readBridgeConfig(phase).engineBaseUrl || "").trim().replace(/\/$/, "");
    if (base) {
      return base.replace(/\/v1$/, "");
    }
  } catch {}
  return "http://127.0.0.1:9530";
}

const createNextConfig = (phase: string): NextConfig => ({
  /* config options here */
  output: "standalone",
  transpilePackages: ["@v8/session-realtime"],
  experimental: {
    externalDir: true,
  },
  async rewrites() {
    return [
      // Proxy background process endpoints (REST + WebSocket) to Engine
      {
        source: '/api/bg_processes/:path*',
        destination: `${resolveEngineOrigin(phase)}/v1/bg_processes/:path*`,
      },
      {
        source: '/api/client/bg_processes/:path*',
        destination: `${resolveEngineOrigin(phase)}/v1/bg_processes/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/api/:path*",
        headers: [
          { key: "Access-Control-Allow-Origin", value: "*" }, // In production, replace with specific origin
          { key: "Access-Control-Allow-Methods", value: "GET,DELETE,PATCH,POST,PUT" },
          { key: "Access-Control-Allow-Headers", value: "Authorization, X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Content-Type, Date, X-Api-Version" },
        ]
      },
      {
        source: "/3d/:path*",
        headers: [
          { key: "Access-Control-Allow-Credentials", value: "true" },
          { key: "Access-Control-Allow-Origin", value: "*" },
          { key: "Access-Control-Allow-Methods", value: "GET,HEAD,OPTIONS" },
          { key: "Access-Control-Allow-Headers", value: "X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Content-Type, Date, X-Api-Version" },
        ]
      }
    ]
  }
});

export default createNextConfig;
