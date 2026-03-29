import type { NextConfig } from "next";
import fs from "fs";
import os from "os";
import path from "path";

function resolveEngineOrigin() {
  try {
    const candidates = [
      path.join(os.homedir(), ".v8chat", "config.json"),
      path.join(os.homedir(), ".v8-agent-os", "config.json"),
    ];
    const configPath = candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
    if (fs.existsSync(configPath)) {
      const raw = fs.readFileSync(configPath, "utf-8");
      const parsed = JSON.parse(raw) as { systemBase?: { bridge?: { engineBaseUrl?: string } } };
      const base = String(parsed?.systemBase?.bridge?.engineBaseUrl || "").trim().replace(/\/$/, "");
      if (base) {
        return base.replace(/\/v1$/, "");
      }
    }
  } catch {}
  return "http://127.0.0.1:9530";
}

const nextConfig: NextConfig = {
  /* config options here */
  async rewrites() {
    return [
      // Proxy background process endpoints (REST + WebSocket) to Engine
      {
        source: '/api/bg_processes/:path*',
        destination: `${resolveEngineOrigin()}/v1/bg_processes/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/api/:path*",
        headers: [
          { key: "Access-Control-Allow-Credentials", value: "true" },
          { key: "Access-Control-Allow-Origin", value: "*" }, // In production, replace with specific origin
          { key: "Access-Control-Allow-Methods", value: "GET,DELETE,PATCH,POST,PUT" },
          { key: "Access-Control-Allow-Headers", value: "X-CSRF-Token, X-Requested-With, Accept, Accept-Version, Content-Length, Content-MD5, Content-Type, Date, X-Api-Version" },
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
};

export default nextConfig;
