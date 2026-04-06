import type { NextConfig } from "next";
import { readCanonicalBridge } from "./src/lib/server/bridge-config";

function resolveEngineOrigin() {
  try {
    const base = String(readCanonicalBridge().engineBaseUrl || "").trim().replace(/\/$/, "");
    if (base) {
      return base.replace(/\/v1$/, "");
    }
  } catch {}
  return "http://127.0.0.1:9530";
}

const nextConfig: NextConfig = {
  /* config options here */
  transpilePackages: ["@v8/session-realtime"],
  experimental: {
    externalDir: true,
  },
  async rewrites() {
    return [
      // Proxy background process endpoints (REST + WebSocket) to Engine
      {
        source: '/api/bg_processes/:path*',
        destination: `${resolveEngineOrigin()}/v1/bg_processes/:path*`,
      },
      {
        source: '/api/client/bg_processes/:path*',
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
